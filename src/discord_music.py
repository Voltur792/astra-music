"""Independent Discord queue and a capability-scoped, loopback media proxy.

Never starts the desktop player, changes its selection, or exposes account keys.
"""
import asyncio
import re
import threading
import time
from urllib.parse import urljoin, urlsplit

import httpx


class DiscordSource:
    def __init__(self, plugin):
        self.plugin = plugin
        self.lock = threading.RLock()
        self.operations = asyncio.Lock()
        self.session = ""
        self.revision = 0
        self.queue = []
        self.index = -1
        self.radio = False
        self.batch_id = ""
        self.assets = {}
        self.track = {}
        self.last_used = time.monotonic()
        self.cancelled = set()

    @staticmethod
    def valid_url(url):
        parsed = urlsplit(url)
        domains = ("yandex.ru", "yandex.net", "userapi.com", "vkuseraudio.net",
                   "vkuseraudio.com", "vkuseraudio.ru", "vk.com")
        return (parsed.scheme == "https" and not parsed.username and not parsed.password
                and parsed.port in (None, 443) and any(
                    parsed.hostname == d or (parsed.hostname or "").endswith("." + d)
                    for d in domains))

    async def play(self, session, service="yandex", track_id="", title="", artist="",
                   extra=None, mode="track", playlist_id=""):
        if not re.fullmatch(r"[a-f0-9]{32}", str(session)):
            return {"error": "Invalid Discord session"}
        async with self.operations:
            with self.lock:
                if session in self.cancelled:
                    return {"cancelled": True}
                self.session = session
                self.last_used = time.monotonic()
            try:
                tracks, radio, batch = await asyncio.to_thread(
                    self._prepare, service, track_id, title, artist, extra, mode, playlist_id)
                with self.lock:
                    if self.session != session:
                        return {"cancelled": True}
                    self.queue, self.index, self.radio, self.batch_id = tracks, 0, radio, batch
                return await self._activate(session)
            except Exception:
                self.stop(session)
                return {"error": "Не удалось подготовить музыку. Проверьте подключение сервиса в Astra Music."}

    def _prepare(self, service, track_id, title, artist, extra, mode, playlist_id):
        if service not in {"yandex", "vk"}:
            raise ValueError("Unknown music service")
        batch, radio = "", mode == "wave"
        if radio:
            tracks, batch = self.plugin._client("yandex").radio_batch("user:onyourwave")
            try:
                self.plugin._client("yandex").radio_feedback("user:onyourwave", "started", batch_id=batch)
            except Exception:
                pass
            rows = [t.as_dict() for t in tracks]
        elif mode == "library":
            result = self.plugin._easyvk_call_sync("my_tracks")
            rows = [self.plugin._vk_track_from_easyvk(t).as_dict()
                    for t in result.get("tracks", []) if isinstance(t, dict)]
        elif mode == "playlist":
            rows = self.plugin._playlist_tracks_sync(service, str(playlist_id), 500).get("tracks", [])
        else:
            if not track_id:
                saved = self.plugin._read_playback_selection() or {}
                with self.plugin._playback_lock:
                    state = dict(self.plugin._playback_state)
                selected = state if state.get("track_id") else saved
                if not selected.get("track_id"):
                    raise ValueError("No selected track")
                extra = selected.get("extra") or {}
                if selected.get("track_id") == saved.get("track_id") and selected.get("service") == saved.get("service"):
                    extra = {**(saved.get("extra") or {}), **extra}
                rows = [{**selected, "extra": extra}]
            else:
                rows = [{"service": service, "track_id": str(track_id), "title": str(title),
                         "artist": str(artist), "extra": extra if isinstance(extra, dict) else {}}]
        if not rows:
            raise ValueError("Empty queue")
        return rows[:500], radio, batch

    def _resolve(self, item):
        service, track_id = item.get("service"), str(item.get("track_id", ""))
        if service == "yandex":
            url = self.plugin._yandex_stream_sync(track_id).get("url", "")
        elif service == "vk":
            extra = item.get("extra") or {}
            result = self.plugin._easyvk_call_sync("resolve", track_id=track_id,
                owner_id=extra.get("owner_id", ""), title=item.get("title", ""),
                artist=item.get("artist", ""), search_query=extra.get("search_query", ""),
                reload_id=extra.get("reload_id", ""), playlist_owner_id=extra.get("playlist_owner_id", ""),
                playlist_id=extra.get("playlist_id", ""), playlist_access_hash=extra.get("playlist_access_hash", ""))
            url = self.plugin._easyvk_stream_url(result.get("stream_url"))
        else:
            raise ValueError("Unknown service")
        if not self.valid_url(url):
            raise ValueError("Invalid media URL")
        if self.radio and service == "yandex":
            try:
                self.plugin._client("yandex").radio_feedback("user:onyourwave", "track_started",
                    track_id=track_id, batch_id=self.batch_id)
            except Exception:
                pass
        return url

    async def _activate(self, session):
        with self.lock:
            if self.session != session:
                return {"cancelled": True}
            item = dict(self.queue[self.index])
        url = await asyncio.to_thread(self._resolve, item)
        with self.lock:
            if self.session != session:
                return {"cancelled": True}
            self.revision += 1
            self.track = {k: item.get(k, "") for k in ("service", "track_id", "title", "artist")}
            self.assets = {"0": url}
        return await self.current(session)

    async def current(self, session=""):
        with self.lock:
            if session != self.session or not self.assets:
                return {"status": "stopped"}
            self.last_used = time.monotonic()
            result = {**self.track, "bridge_version": 2, "revision": self.revision, "queue_count": len(self.queue),
                      "queue_index": self.index, "status": "ready"}
            revision = self.revision
        base = await asyncio.to_thread(self.plugin._audio_host.ensure_server)
        result["stream_url"] = f"{base}/discord-stream/{revision}/0"
        return result

    async def advance(self, session, revision, direction=1, finished=False, played_seconds=0):
        async with self.operations:
            with self.lock:
                if session != self.session or revision != self.revision or not self.assets:
                    return {"cancelled": True}
                self.index += 1 if int(direction) >= 0 else -1
                self.index = max(0, self.index)
                exhausted, radio = self.index >= len(self.queue), self.radio
                previous = str(self.track.get("track_id", ""))
            try:
                if radio:
                    client = self.plugin._client("yandex")
                    try:
                        await asyncio.to_thread(client.radio_feedback, "user:onyourwave",
                            "track_finished" if finished else "skip", track_id=previous, batch_id=self.batch_id,
                            played_seconds=max(0, min(86400, float(played_seconds))))
                    except Exception:
                        pass  # Feedback availability must not stop audio.
                if exhausted and radio:
                    tracks, batch = await asyncio.to_thread(client.radio_batch, "user:onyourwave", previous)
                    with self.lock:
                        if self.session != session:
                            return {"cancelled": True}
                        self.queue, self.index, self.batch_id = [t.as_dict() for t in tracks], 0, batch
                        if not self.queue:
                            exhausted = True
                        else:
                            exhausted = False
                if exhausted:
                    self.stop(session)
                    return {"status": "stopped"}
                return await self._activate(session)
            except Exception:
                self.stop(session)
                return {"error": "Не удалось получить следующий трек."}

    def stop(self, session):
        with self.lock:
            if len(self.cancelled) >= 1024:
                self.cancelled.clear()
            self.cancelled.add(session)
            if session == self.session:
                self.session, self.assets, self.queue, self.track = "", {}, [], {}
        return {"ok": True}

    def _asset(self, revision, asset):
        with self.lock:
            if (revision != self.revision or not self.session
                    or time.monotonic() - self.last_used > 120):
                raise ValueError("Expired stream")
            return self.assets[asset]

    def _local_asset(self, revision, source, base):
        url = urljoin(base, source)
        if not self.valid_url(url):
            raise ValueError("Unsupported HLS reference")
        with self.lock:
            if revision != self.revision or not self.session or len(self.assets) >= 12000:
                raise ValueError("Expired stream")
            for key, value in self.assets.items():
                if value == url:
                    break
            else:
                key = str(len(self.assets))
                self.assets[key] = url
        extension = urlsplit(url).path.rsplit(".", 1)[-1].lower()
        suffix = extension if extension in {"m3u8", "ts", "aac", "mp4", "m4s", "key"} else "bin"
        return f"/{self.plugin._audio_host.token}/discord-stream/{revision}/{key}.{suffix}"

    def serve(self, handler, revision, asset):
        """Rewrite all HLS URLs to this authenticated server, including AES keys."""
        sent = False
        try:
            url = self._asset(revision, asset)
            with httpx.Client(follow_redirects=False, timeout=25) as client:
                for attempt in range(5):
                    if not self.valid_url(url):
                        raise ValueError("Invalid redirect")
                    headers = {"Accept-Encoding": "identity"}
                    requested = handler.headers.get("Range", "")
                    if re.fullmatch(r"bytes=\d+-\d*", requested):
                        headers["Range"] = requested
                    with client.stream("GET", url, headers=headers) as response:
                        if response.status_code in (301, 302, 303, 307, 308):
                            url = urljoin(url, response.headers.get("location", ""))
                            continue
                        if response.status_code not in (200, 206):
                            raise ValueError("Upstream unavailable")
                        is_manifest = urlsplit(url).path.lower().endswith(".m3u8") or "mpegurl" in response.headers.get("content-type", "").lower()
                        if is_manifest:
                            data = bytearray()
                            for chunk in response.iter_bytes():
                                data.extend(chunk)
                                if len(data) > 256000:
                                    raise ValueError("Manifest too large")
                            text = data.decode("utf-8-sig")
                            if not text.startswith("#EXTM3U") or "#EXT-X-DEFINE" in text:
                                raise ValueError("Unsupported manifest")
                            lines = []
                            for line in text.splitlines():
                                if line.startswith("#"):
                                    if re.search(r'(?:[:,])(?:[A-Z-]*URI)=(?!")', line):
                                        raise ValueError("Unquoted HLS reference")
                                    line = re.sub(r'URI="([^"]+)"', lambda m: 'URI="' + self._local_asset(revision, m[1], url) + '"', line)
                                elif line.strip():
                                    line = self._local_asset(revision, line.strip(), url)
                                lines.append(line)
                            payload = ("\n".join(lines) + "\n").encode()
                            handler.send_response(200)
                            handler.send_header("Content-Type", "application/vnd.apple.mpegurl")
                            handler.send_header("Content-Length", str(len(payload)))
                            handler.send_header("Cache-Control", "no-store")
                            handler.end_headers()
                            sent = True
                            handler.wfile.write(payload)
                        else:
                            handler.send_response(response.status_code)
                            for name in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
                                if name in response.headers:
                                    handler.send_header(name, response.headers[name])
                            handler.send_header("Connection", "close")
                            handler.close_connection = True
                            handler.send_header("Cache-Control", "no-store")
                            handler.end_headers()
                            sent = True
                            for chunk in response.iter_raw():
                                self._asset(revision, asset)
                                handler.wfile.write(chunk)
                        return
                raise ValueError("Too many redirects")
        except (BrokenPipeError, ConnectionResetError):
            handler.close_connection = True
        except Exception:
            if not sent:
                handler.send_error(502)
            handler.close_connection = True
