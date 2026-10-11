"""Experimental Spotify web player, owned profile and scoped metadata/audio IPC."""
import hashlib
import base64
import json
import math
import shutil
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import quote, urlsplit
import re
import httpx

from .playback_host import find_edge_browser


class SpotifyError(RuntimeError):
    pass


def spotify_url(url="", query=""):
    if query:
        return "https://open.spotify.com/search/" + quote(str(query).strip()[:300], safe="")
    if not url:
        return "https://open.spotify.com/"
    import re
    parsed = urlsplit(str(url))
    if (parsed.scheme != "https" or parsed.netloc != "open.spotify.com"
            or not re.fullmatch(r"/(?:|(?:track|album|playlist|artist)/[a-zA-Z0-9]+/?|collection/(?:tracks|playlists)/?|search(?:/[^/?#]+(?:/tracks)?)?/?)", parsed.path)):
        raise SpotifyError("Используйте ссылку на трек или плейлист open.spotify.com.")
    return "https://open.spotify.com" + parsed.path


def finite(value, default=0.0):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (ValueError, TypeError):
        return default


def cover_sources(url):
    match = re.fullmatch(r"https://i\.scdn\.co/image/([a-zA-Z0-9]{1,128})", url or "")
    if not match:
        return ()
    # Public Spotify image CDNs, carrying the same image ID without credentials.
    return tuple("https://" + host + "/image/" + match[1] for host in (
        "i.scdn.co", "image-cdn-ak.spotifycdn.com", "image-cdn-fa.spotifycdn.com"))


class SpotifyBrowser:
    def __init__(self, plugin):
        self.plugin = plugin
        self.profile = plugin.data_dir / "spotify-browser-profile"
        self.process = None
        self.login_process = None
        self.active = False
        self.state = {"message": "Откройте Spotify и войдите в аккаунт."}
        self.lock = threading.RLock()
        self.pending = {}
        self.sequence = 0
        self.ready = threading.Event()
        self.updated_at = 0.0
        self.connecting = False
        self.cancel = threading.Event()
        self.connection_error = ""
        self.hidden = bool(plugin._read_settings().get("spotify_hidden", True))
        self.browser_pid = 0
        self.covers = {}
        self.cover_jobs = set()
        self.cover_retry = {}
        self.audio = None
        self.jobs = {}
        self.restore_checked = False
        self._meter_request = None

    def set_visualizer(self, enabled):
        """Notify the helper without waiting on its stdout reader thread."""
        with self.lock:
            process = self.process
            if not process or process.poll() is not None:
                self._meter_request = None
                return
            requested = (process, bool(enabled))
            if requested == self._meter_request:
                return
            try:
                process.stdin.write(json.dumps({"id": 0, "action": "visualizer", "enabled": bool(enabled)}) + "\n")
                process.stdin.flush()
                self._meter_request = requested
            except OSError:
                self._meter_request = None

    def _publish_bands(self, process, bands):
        if not isinstance(bands, list) or len(bands) != 48:
            return
        if not all(isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n) and 0 <= n <= 1 for n in bands):
            return
        with self.lock:
            if self.process is not process or not self.active:
                return
            p = self.plugin
            with p._playback_lock:
                settings = p._visualizer_settings
                if (p._playback_state.get("service") != "spotify" or p._playback_state.get("status") != "playing"
                        or settings["mode"] != "music" or not (settings["widget"] or settings["background"])):
                    return
                p._music_audio_bands = list(bands)
                p._music_audio_sample_at = time.monotonic()
                p._music_audio_sample_revision = p._playback_state.get("revision", 0)

    def restore_saved(self):
        if self.restore_checked:
            return
        self.restore_checked = True
        if self.plugin._read_settings().get("spotify_auto_connect", True) and (self.profile / "Default").is_dir():
            self.activate()

    def submit(self, operation, params):
        operations = {
            "search": lambda: self.browse("search", query=str(params.get("query", "")), limit=20),
            "playlists": lambda: self.browse("playlists"),
            "tracks": lambda: self.browse("playlist_tracks", playlist_id=str(params.get("playlist_id", "")), limit=100),
            "playlist": lambda: self.playlist(str(params.get("playlist_id", "")), str(params.get("playlist_name", ""))),
            "track": lambda: self.start("https://open.spotify.com/track/" + str(params.get("track_id", ""))),
            "start": lambda: self.start(str(params.get("url", ""))),
            "mix": self.personal_mix,
        }
        if operation not in operations:
            return {"error": "Неизвестное действие Spotify."}
        with self.lock:
            if any(job["status"] == "loading" for job in self.jobs.values()):
                return {"error": "Дождитесь завершения предыдущего действия Spotify."}
            while len(self.jobs) >= 16:
                self.jobs.pop(next(iter(self.jobs)))
            job_id = f"spotify-{time.time_ns()}"
            self.jobs[job_id] = {"job_id": job_id, "status": "loading"}
        def run():
            try:
                result = operations[operation]()
            except Exception as exc:
                result = {"error": str(exc)}
            with self.lock:
                self.jobs[job_id].update(status="done", result=result)
        threading.Thread(target=run, daemon=True, name="spotify-ui-job").start()
        return {"job_id": job_id, "status": "loading"}

    def job_status(self, job_id):
        with self.lock:
            return dict(self.jobs.get(job_id, {"error": "Действие Spotify устарело. Повторите запрос."}))

    def _forget_mixes(self):
        try:
            (self.plugin.data_dir / "spotify-mixes.json").unlink(missing_ok=True)
        except OSError:
            raise SpotifyError("Не удалось очистить сохранённые миксы Spotify.")

    def personal_mix(self):
        rows = self.browse("mixes").get("playlists", [])
        cache = self.plugin.data_dir / "spotify-mixes.json"
        def clean(values):
            return [{"playlist_id": r["playlist_id"], "title": str(r.get("title", "Микс Spotify"))[:300]}
                    for r in values[:20] if isinstance(r, dict) and isinstance(r.get("playlist_id"), str)
                    and re.fullmatch(r"[a-zA-Z0-9]{1,100}", r["playlist_id"])] if isinstance(values, list) else []
        rows = clean(rows)
        if rows:
            try:
                temporary = cache.with_suffix(".tmp")
                temporary.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
                temporary.replace(cache)
            except OSError:
                pass  # A read-only cache must not prevent playback.
        else:
            try:
                rows = clean(json.loads(cache.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError):
                pass
        if not rows:
            raise SpotifyError("Spotify пока не показал персональный микс (daylist или Daily Mix). Откройте раздел «Для тебя» в Spotify и послушайте музыку, затем повторите.")
        result = self.playlist(rows[0]["playlist_id"])
        result["mix_title"] = rows[0]["title"]
        return result

    def _cache_cover(self, url):
        sources = cover_sources(url)
        if not sources or url in self.covers or url in self.cover_jobs or len(self.cover_jobs) >= 3 or time.monotonic() < self.cover_retry.get(url, 0):
            return
        self.cover_jobs.add(url)
        def fetch():
            try:
                for source in sources:
                    try:
                        with httpx.stream("GET", source, follow_redirects=False, timeout=3) as response:
                            mime = response.headers.get("content-type", "").split(";", 1)[0]
                            if response.status_code != 200 or mime not in {"image/jpeg", "image/png", "image/webp"}:
                                continue
                            data = bytearray()
                            for chunk in response.iter_bytes():
                                data.extend(chunk)
                                if len(data) > 128000:
                                    break
                            if not data or len(data) > 128000:
                                continue
                        encoded = "data:" + mime + ";base64," + base64.b64encode(data).decode("ascii")
                        with self.lock:
                            if len(self.covers) >= 8:
                                self.covers.pop(next(iter(self.covers)))
                            self.covers[url] = encoded
                        return
                    except httpx.HTTPError:
                        continue
            finally:
                with self.lock:
                    if len(self.cover_retry) >= 32:
                        self.cover_retry.pop(next(iter(self.cover_retry)))
                    self.cover_retry[url] = time.monotonic() + 60
                    self.cover_jobs.discard(url)
        threading.Thread(target=fetch, daemon=True, name="spotify-cover").start()

    def snapshot(self):
        with self.lock:
            return {"running": bool(self.process and self.process.poll() is None),
                    "login_open": bool(self.login_process and self.login_process.poll() is None),
                    "connecting": self.connecting, "connection_error": self.connection_error,
                    "active": self.active, "session_saved": (self.profile / "Default").is_dir(),
                    "experimental": True, "hidden": self.hidden,
                    "audio_route": "discord" if self.audio and self.audio.started and not self.audio.closed.is_set() else "local", **self.state}

    def login(self):
        # Google sign-in can reject an app window connected to automation.
        # Authentication happens in an ordinary browser, with no CDP endpoint.
        self.close()
        self._forget_mixes()
        edge = find_edge_browser()
        if not edge:
            raise SpotifyError("Для Spotify требуется Microsoft Edge.")
        self.profile.mkdir(parents=True, exist_ok=True)
        node = shutil.which("node")
        if not node:
            raise SpotifyError("Для входа Spotify требуется Node.js 22 или новее.")
        script = Path(__file__).resolve().parent.parent / "ui" / "spotify-browser.cjs"
        prepared = subprocess.run([node, str(script), "--prepare-login", str(self.profile)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
            timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if prepared.returncode:
            try:
                error = json.loads(prepared.stdout.strip()).get("message", "")
            except (ValueError, TypeError):
                error = ""
            raise SpotifyError(error or "Не удалось закрыть прежнее подключение Spotify. Повторите вход.")
        self.login_process = subprocess.Popen([str(edge), "--user-data-dir=" + str(self.profile),
            "--new-window", "--no-first-run", "--disable-background-mode", "https://open.spotify.com/"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.state = {"message": "Войдите в Spotify в обычном окне Edge, затем закройте это окно и нажмите «Управлять из Astra»."}
        return {"success": True, **self.snapshot()}

    def open(self, url="", query=""):
        target = spotify_url(url, query)
        if self.login_process and self.login_process.poll() is None:
            raise SpotifyError("Сначала завершите вход и закройте обычное окно Spotify, затем подключите управление.")
        with self.lock:
            running = self.process and self.process.poll() is None
            if not running:
                edge, node = find_edge_browser(), shutil.which("node")
                if not edge or not node:
                    raise SpotifyError("Для Spotify нужны Microsoft Edge и Node.js 22 или новее.")
                self.profile.mkdir(parents=True, exist_ok=True)
                script = Path(__file__).resolve().parent.parent / "ui" / "spotify-browser.cjs"
                self.ready.clear()
                self.state = {"message": "Запускаю Spotify в фоне…" if self.hidden else "Открываю окно Spotify…"}
                process = subprocess.Popen([node, str(script), str(edge), str(self.profile), target, "hidden" if self.hidden else "visible"],
                    cwd=str(script.parent), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                self.process = process
                threading.Thread(target=self._read, args=(process,), daemon=True, name="spotify-player").start()
        if running:
            self.request("open", url=target if url or query else "")
        return {"success": True, "playing": False, **self.snapshot()}

    def presentation(self, hidden):
        hidden = bool(hidden)
        if hidden != self.hidden:
            was_active = self.active
            self.close()
            self.hidden = hidden
            settings = self.plugin._read_settings()
            settings["spotify_hidden"] = hidden
            self.plugin._write_settings(settings)
            if was_active:
                return self.activate()
        return {"success": True, **self.snapshot()}

    def _wait_session(self):
        self.open()
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if self.state.get("logged_in"):
                return
            if not self.snapshot()["running"]:
                raise SpotifyError(self.state.get("message") or "Не удалось запустить подключение Spotify.")
            time.sleep(.2)
        raise SpotifyError("Вход Spotify не подтверждён. Войдите в обычном окне Edge и закройте его.")

    def browse(self, kind, query="", playlist_id="", limit=20):
        self._wait_session()
        if kind in {"playlists", "mixes"}:
            target = "https://open.spotify.com/"
        elif kind == "playlist_tracks":
            target = spotify_url("https://open.spotify.com/collection/tracks" if playlist_id == "liked" else "https://open.spotify.com/playlist/" + playlist_id)
        else:
            target = spotify_url(query=query) + "/tracks"
        result = self.request("browse", url=target, kind=kind, limit=limit, _timeout=25).get("catalog", {})
        key = "playlists" if kind in {"playlists", "mixes"} else "tracks"
        rows = result.get(key, [])
        return {"service": "spotify", "query": query, "playlist_id": playlist_id, "count": len(rows), key: rows, "partial": result.get("partial", True)}

    def start(self, url):
        target = spotify_url(url)
        self._wait_session()
        if not self.active:
            self.plugin._stop_playback_sync(release_spotify=False)
            with self.lock, self.plugin._playback_lock:
                self.plugin._playback_state.update(service="spotify", source="browser", url="")
                self.active = True
        started = self.request("start", url=target, _timeout=25)
        expected = started.get("expected_title", "")
        deadline = time.monotonic() + 20
        while (self.state.get("status") != "playing" or expected and self.state.get("title") != expected) and time.monotonic() < deadline:
            time.sleep(.2)
        playing = self.state.get("status") == "playing" and (not expected or self.state.get("title") == expected)
        return {"success": True, "service": "spotify", "playing": playing, "status": self.state.get("status"), "message": "Spotify воспроизводит музыку." if playing else "Spotify не подтвердил воспроизведение. Проверьте доступность песни и ограничения тарифа."}

    def playlist(self, playlist_id="", playlist_name=""):
        if not playlist_id:
            rows = self.browse("playlists").get("playlists", [])
            matches = [r for r in rows if r.get("title", "").casefold() == playlist_name.casefold()]
            if len(matches) != 1:
                raise SpotifyError("Не удалось однозначно найти этот плейлист в медиатеке Spotify. Выберите его из списка.")
            playlist_id = matches[0]["playlist_id"]
        return self.start("https://open.spotify.com/collection/tracks" if playlist_id == "liked" else "https://open.spotify.com/playlist/" + playlist_id)

    def _read(self, process):
        try:
            for line in process.stdout:
                try:
                    event = json.loads(line)
                    if event.get("type") == "ready":
                        self.browser_pid = int(event.get("browser_pid", 0))
                        self.ready.set()
                    elif event.get("type") in {"failure", "closed"}:
                        self._publish({"status": "stopped", "message": event.get("message", "Окно Spotify недоступно.")})
                        self.ready.set()
                    elif event.get("type") == "state":
                        self._publish(event.get("state", {}))
                    elif event.get("type") == "visualizer":
                        self._publish_bands(process, event.get("bands"))
                    elif event.get("type") in {"audio", "audio-failure"}:
                        with self.lock:
                            capture = self.audio if self.process is process else None
                        if capture and capture.lease == event.get("lease"):
                            if event["type"] == "audio-failure":
                                capture.closed.set()
                            else:
                                encoded = event.get("pcm", "")
                                if isinstance(encoded, str) and len(encoded) == 10924:
                                    data = base64.b64decode(encoded, validate=True)
                                    if len(data) == 8192:
                                        capture.feed(data)
                    elif event.get("type") == "reply":
                        if isinstance(event.get("state"), dict):
                            self._publish(event["state"])
                        with self.lock:
                            item = self.pending.get(event.get("id"))
                            if item:
                                item["result"] = event
                                item["event"].set()
                except (ValueError, TypeError):
                    continue
        finally:
            with self.lock:
                if self.process is process:
                    if self.audio:
                        self.audio.closed.set()
                    self.state.update(status="stopped", message=self.state.get("message") or "Окно Spotify закрыто. Вход сохранён.")
                    if self.active and self.plugin._playback_state.get("service") == "spotify":
                        with self.plugin._playback_lock:
                            self.plugin._playback_state.update(status="stopped", playback_error="SpotifyWindowClosed")
                    for item in self.pending.values():
                        item["result"] = {"success": False, "message": "Окно Spotify закрыто."}
                        item["event"].set()
                    self.ready.set()

    def _publish(self, raw):
        if not isinstance(raw, dict):
            return
        with self.lock:
            # Explicit whitelist: browser cookies and access tokens never enter IPC.
            self.state = {key: raw[key] for key in ("logged_in", "title", "artist", "cover_url", "track_id", "status", "position_seconds", "duration_seconds", "volume", "can_next", "can_previous", "can_seek", "message") if key in raw}
            self.updated_at = time.monotonic()
            if not self.active:
                return
            p = self.plugin
            with p._playback_lock:
                if p._playback_state.get("service") != "spotify":
                    self.active = False
                    return
                previous = p._playback_state
                title = str(raw.get("title", ""))[:300]
                track_id = str(raw.get("track_id", ""))[:80]
                if track_id == "spotify-web":
                    track_id += "-" + hashlib.sha256((title + str(raw.get("artist", ""))).encode()).hexdigest()[:16]
                if track_id != previous.get("track_id"):
                    p._playback_revision += 1
                cover = str(raw.get("cover_url", ""))[:1500]
                parsed = urlsplit(cover)
                if (parsed.scheme != "https" or parsed.hostname != "i.scdn.co" or parsed.port not in (None, 443)
                        or parsed.username or parsed.password or not parsed.path.startswith("/image/")):
                    cover = ""
                self._cache_cover(cover)
                status = str(raw.get("status", "ready"))
                if status not in {"ready", "attempting", "playing", "paused", "stopped"}:
                    status = "ready"
                previous.update(revision=p._playback_revision, url="", service="spotify",
                    track_id=track_id, title=title, artist=str(raw.get("artist", ""))[:300], cover_url=self.covers.get(cover, cover),
                    status=status, playback_error="", position_seconds=max(0, finite(raw.get("position_seconds"))),
                    duration_seconds=max(0, finite(raw.get("duration_seconds"))), position_updated_at=time.time(),
                    source="browser", source_title="Spotify · Discord" if self.audio and self.audio.started and not self.audio.closed.is_set() else "Spotify · веб-плеер", command="", liked=False,
                    can_next=bool(raw.get("can_next")), can_previous=bool(raw.get("can_previous")),
                    can_seek=bool(raw.get("can_seek")), queue_index=-1, queue_count=0,
                    playback_message=str(raw.get("message", ""))[:300])
                if raw.get("volume") is not None and p._duck_restore_volume is None and self.audio is None:
                    p._playback_volume = max(0, min(1, finite(raw["volume"], p._playback_volume)))
                previous["volume"] = p._playback_volume

    def peek_next(self):
        """Read Spotify's ordered queue; never infer order from a playlist."""
        from .radio_presenter import track_metadata
        before = self.snapshot()
        key = tuple(before.get(k, "") for k in ("track_id", "title", "artist"))
        if not before.get("running") or not before.get("logged_in") or not key[1]:
            return None
        cached = getattr(self, "_next_cache", None)
        if cached and cached[0] == key and time.monotonic() - cached[1] < 8:
            return cached[2]
        try:
            result = self.request("queue-next", _timeout=5)
            after = self.snapshot()
            if key != tuple(after.get(k, "") for k in ("track_id", "title", "artist")):
                return None
            if result.get("current") != track_metadata(before):
                return None
            upcoming = track_metadata(result.get("next_track"))
            self._next_cache = (key, time.monotonic(), upcoming)
            return upcoming
        except Exception:
            # No readable queue is a valid state (ads, loading, single songs).
            self._next_cache = (key, time.monotonic(), None)
            return None

    def request(self, action, value=0, **params):
        timeout = params.pop("_timeout", 8)
        with self.lock:
            if not self.process or self.process.poll() is not None:
                raise SpotifyError("Откройте Spotify во вкладке «Музыка».")
            self.sequence += 1
            key = self.sequence
            item = {"event": threading.Event()}
            self.pending[key] = item
            try:
                self.process.stdin.write(json.dumps({"id": key, "action": action, "value": value, **params}) + "\n")
                self.process.stdin.flush()
            except OSError as exc:
                self.pending.pop(key, None)
                raise SpotifyError("Окно Spotify недоступно.") from exc
        try:
            if not item["event"].wait(timeout):
                raise SpotifyError("Spotify не ответил. Проверьте его окно и повторите команду.")
            result = item["result"]
            if not result.get("success"):
                raise SpotifyError(result.get("message", "Spotify не выполнил команду."))
            return result
        finally:
            with self.lock:
                self.pending.pop(key, None)

    def activate(self):
        settings = self.plugin._read_settings()
        settings["spotify_auto_connect"] = True
        self.plugin._write_settings(settings)
        if self.active or self.connecting:
            return {"success": True, **self.snapshot()}
        if not self.snapshot()["running"]:
            self.open()
        self.cancel = cancel = threading.Event()
        self.connecting = True
        self.connection_error = ""
        def attach():
            try:
                deadline = time.monotonic() + 60
                while not cancel.is_set() and time.monotonic() < deadline:
                    if self.state.get("logged_in"):
                        self.plugin._stop_playback_sync(release_spotify=False)
                        with self.lock, self.plugin._playback_lock:
                            if cancel.is_set():
                                return
                            self.plugin._playback_state.update(service="spotify", source="browser", url="")
                            self.active = True
                            self._publish(self.state)
                        return
                    if not self.snapshot()["running"]:
                        raise SpotifyError(self.state.get("message") or "Не удалось запустить подключение Spotify.")
                    cancel.wait(.2)
                if not cancel.is_set():
                    self.connection_error = "Вход не подтверждён. Нажмите «Войти в Spotify», войдите в обычном Edge и закройте окно перед подключением управления."
            except Exception as exc:
                self.connection_error = str(exc)[:300]
            finally:
                if self.cancel is cancel:
                    self.connecting = False
        threading.Thread(target=attach, daemon=True, name="spotify-connect").start()
        return {"success": True, "playing": False, "message": "Подключаю управление Spotify…", **self.snapshot()}

    def control(self, action, value=0):
        p = self.plugin
        normal = p._duck_restore_volume if p._duck_restore_volume is not None else p._playback_volume
        if action in {"volume_up", "volume_down", "mute", "unmute"}:
            step = abs(value) if value else 1
            if step > 10:
                step /= 10
            value = min(10, normal*10+step) if action == "volume_up" else max(0, normal*10-step) if action == "volume_down" else 0 if action == "mute" else finite(p._read_settings().get("playback_volume_before_mute"), .8)*10
            action = "volume"
        if action == "like":
            raise SpotifyError("Добавляйте понравившиеся песни в самом окне Spotify.")
        if action == "ended":
            return {"success": True}  # Spotify owns its queue and end-of-track handling.
        if action in {"forward", "backward"}:
            value = finite(p._playback_state.get("position_seconds")) + (abs(value) if action == "forward" else -abs(value))
            action = "seek"
        if action == "volume":
            if value > 10:
                value /= 10
            value = max(0, min(10, finite(value)))
            desired = value/10
            if self.audio is None:
                self.request("volume", min(desired, .1) if p._duck_restore_volume is not None else desired)
            with p._playback_lock:
                if p._duck_restore_volume is not None:
                    p._duck_restore_volume = desired
                p._playback_volume = min(desired, .1) if p._duck_restore_volume is not None else desired
                p._playback_state["volume"] = p._playback_volume
            settings = p._read_settings()
            settings["playback_volume"] = desired
            if desired > 0:
                settings.update(playback_volume_before_mute=desired, playback_volume_before_mute_scale="0-1")
            p._write_settings(settings)
        elif action in {"play", "pause", "stop", "next", "previous", "seek"}:
            self.request("pause" if action == "stop" else action, max(0, finite(value)))
        else:
            raise SpotifyError("Эта команда не поддерживается плеером Spotify.")
        return {"success": True, "action": action, "playing": self.state.get("status") == "playing",
                "status": self.state.get("status", "ready"), "value": value,
                "message": f"Громкость музыки установлена на {value:g} из 10." if action == "volume" else "Команда передана плееру Spotify."}

    def release(self):
        self.cancel.set()
        self.connecting = False
        if self.active and self.snapshot()["running"]:
            self.request("pause")
        self.active = False

    def close(self):
        self.cancel.set()
        self.connecting = False
        self.active = False
        with self.lock:
            process = self.process
            if process and process.poll() is None:
                try:
                    process.stdin.write('{"action":"close"}\n')
                    process.stdin.flush()
                except OSError:
                    pass
        if process and process.poll() is None:
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=3)

    def forget(self):
        if self.login_process and self.login_process.poll() is None:
            raise SpotifyError("Закройте окно входа Spotify перед удалением сохранённой сессии.")
        self.close()
        self._forget_mixes()
        # Only remove this service's dedicated profile, never the user's Edge profile.
        expected = self.plugin.data_dir.resolve() / "spotify-browser-profile"
        if (self.profile.resolve() != expected or self.profile.is_symlink()
                or getattr(self.profile, "is_junction", lambda: False)()):
            raise SpotifyError("Не удалось безопасно очистить профиль Spotify.")
        if expected.exists():
            shutil.rmtree(expected)
        self.state = {"message": "Вход Spotify удалён с этого компьютера."}
        return {"success": True, **self.snapshot()}
