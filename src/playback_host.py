"""A private persistent audio window, independent of Astra's page/widget lifetime."""
import asyncio
import json
import os
import secrets
import subprocess
import sys
import threading
import shutil
import re
import time
import logging
import httpx
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger("astra-music.audio-host")


def find_edge_browser():
    """Astra's daemon omits ProgramFiles from its child environment."""
    candidates = []
    for base in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES"),
                 os.environ.get("LOCALAPPDATA")):
        if base:
            candidates.append(Path(base) / "Microsoft/Edge/Application/msedge.exe")
    # Standard machine installs must work even with the daemon's allowlisted env.
    drive = os.environ.get("SystemDrive", "C:")
    for directory in ("Program Files (x86)", "Program Files"):
        candidates.append(Path(drive + "/") / directory / "Microsoft/Edge/Application/msedge.exe")
    if sys.platform == "win32":
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe", 0, winreg.KEY_READ | view) as key:
                        candidates.append(Path(winreg.QueryValue(key, None).strip('"')))
                except OSError:
                    pass
    found = shutil.which("msedge.exe")
    if found:
        candidates.append(Path(found))
    return next((path.resolve() for path in candidates if path.is_file()), None)


class PlaybackHost:
    def __init__(self, plugin):
        self.plugin = plugin
        self.enabled = sys.platform == "win32" and os.environ.get("ASTRA_MUSIC_NATIVE_PLAYER") != "0"
        self.server = None
        self.process = None
        self.lock = threading.Lock()
        self.token = secrets.token_urlsafe(32)
        self.ui = Path(__file__).resolve().parent.parent / "ui"
        self.log_file = None
        self.closing = False
        self.last_page_ping = 0.0
        self.recovery_attempts = 0

    def ensure_server(self):
        """Serve UI/control calls without starting or changing audio playback."""
        with self.lock:
            self._ensure_server_locked()
            return f"http://127.0.0.1:{self.server.server_port}/{self.token}"

    def _ensure_server_locked(self):
        if self.server is None:
            host = self
            class Handler(BaseHTTPRequestHandler):
                protocol_version = "HTTP/1.1"
                def log_message(self, *args):
                    pass

                def do_GET(self):
                    parts = urlsplit(self.path).path.split("/")
                    if len(parts) < 3 or not secrets.compare_digest(parts[1], host.token):
                        self.send_error(403); return
                    name = "/".join(parts[2:])
                    if name.startswith("discord-stream/"):
                        match = re.fullmatch(r"discord-stream/(\d+)/(\d+)(?:\.(?:m3u8|ts|aac|mp4|m4s|key|bin))?", name)
                        if not match:
                            self.send_error(404); return
                        host.plugin._discord_music.serve(self, int(match[1]), match[2])
                        return
                    if name.startswith("stream/"):
                        state = asyncio.run(host.plugin.ui_playback_state())
                        expected = f"stream/{state.get('revision')}"
                        if name != expected or state.get("service") != "yandex" or not state.get("url"):
                            self.send_error(404); return
                        headers = {"Accept-Encoding": "identity"}
                        requested_range = self.headers.get("Range", "")
                        if re.fullmatch(r"bytes=\d+-\d*", requested_range):
                            headers["Range"] = requested_range
                        try:
                            with httpx.stream("GET", state["url"], headers=headers, follow_redirects=True, timeout=20) as response:
                                if response.status_code not in (200, 206):
                                    self.send_error(502); return
                                self.send_response(response.status_code)
                                for key in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
                                    if key in response.headers:
                                        self.send_header(key, response.headers[key])
                                if "Content-Length" not in response.headers:
                                    self.send_header("Connection", "close")
                                    self.close_connection = True
                                self.send_header("Cache-Control", "no-store")
                                self.end_headers()
                                for chunk in response.iter_raw():
                                    self.wfile.write(chunk)
                        except (BrokenPipeError, ConnectionResetError):
                            pass
                        except httpx.HTTPError:
                            self.close_connection = True
                        return
                    if name == "ping":
                        data, mime = b"ok", "text/plain"
                    else:
                        path = (host.ui / name).resolve()
                        if not path.is_relative_to(host.ui.resolve()) or not path.is_file() or path.suffix not in {".html", ".js", ".css"}:
                            self.send_error(404); return
                        data = path.read_bytes()
                        mime = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}[path.suffix]
                        if name in {"player-v12.html", "desktop-widget.html"}:
                            data = data.replace(b'<script src="http://astra-plugin.localhost/bridge/astra-bridge.js"></script>', b'<script src="native-bridge.js"></script>')
                            data = data.replace(b'preload="none"', b'preload="auto"')
                    self.send_response(200)
                    self.send_header("Content-Type", mime + "; charset=utf-8")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers(); self.wfile.write(data)

                def do_POST(self):
                    if self.path != f"/{host.token}/api":
                        self.send_error(403); return
                    size = int(self.headers.get("Content-Length", "0"))
                    if size > 65536:
                        self.send_error(413); return
                    methods = {
                        "music_playback_state": host.plugin.ui_playback_state,
                        "music_playback_status": host.plugin.ui_playback_status,
                        "music_visualizer_state": host.plugin.ui_visualizer_state,
                        "music_desktop_get": host.plugin.ui_desktop_get,
                        "music_desktop_report": host.plugin.ui_desktop_report,
                        "music_playback_restore": host.plugin.ui_playback_restore,
                        "music_playback_control": host.plugin.ui_playback_control,
                        "music_playback_stop": host.plugin.ui_playback_stop,
                        "music_playback_report": host.plugin.ui_playback_report,
                        "music_visualizer_get": host.plugin.ui_visualizer_get,
                        "music_visualizer_sample": host.plugin.ui_visualizer_sample,
                    }
                    try:
                        request = json.loads(self.rfile.read(size))
                        if request.get("method") == "music_audio_host_ping":
                            host.last_page_ping = time.monotonic()
                            host.recovery_attempts = 0
                            result = {"ok": True}
                        else:
                            method = methods.get(request.get("method"))
                            if method is None:
                                self.send_error(404); return
                            result = asyncio.run(method(**(request.get("params") or {})))
                        if request.get("method") == "music_playback_state" and result.get("service") == "yandex" and result.get("url"):
                            # A same-origin stream allows captureStream() to
                            # measure Yandex audio instead of returning silence.
                            result = dict(result)
                            result["url"] = f"http://127.0.0.1:{host.server.server_port}/{host.token}/stream/{result['revision']}"
                            if "stream_url" in result:
                                result["stream_url"] = result["url"]
                        data = json.dumps(result, ensure_ascii=False).encode()
                    except Exception:
                        self.send_error(500); return
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers(); self.wfile.write(data)
            class PlayerServer(ThreadingHTTPServer):
                # Both remotes poll while Chromium loads several assets at once.
                # The standard backlog of five can drop the player script.
                request_queue_size = 128
            self.server = PlayerServer(("127.0.0.1", 0), Handler)
            self.server.daemon_threads = True
            threading.Thread(target=self.server.serve_forever, daemon=True, name="music-player-api").start()

    def ensure(self):
        if not self.enabled:
            return
        with self.lock:
            if self.process and self.process.poll() is None:
                return
            browser = find_edge_browser()
            if browser is None:
                with self.plugin._playback_lock:
                    self.plugin._playback_state.update(status="failed", playback_error="EdgeNotFound")
                raise RuntimeError("Не найден Microsoft Edge для постоянного аудиоплеера")
            self._ensure_server_locked()
            url = f"http://127.0.0.1:{self.server.server_port}/{self.token}/player-v12.html"
            if self.log_file is None:
                self.plugin.data_dir.mkdir(parents=True, exist_ok=True)
                self.log_file = (self.plugin.data_dir / "audio-host.log").open("ab")
            env = dict(os.environ)
            env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = "--autoplay-policy=no-user-gesture-required --disable-background-timer-throttling --disable-renderer-backgrounding --disable-backgrounding-occluded-windows --disable-background-media-suspend --disable-features=CalculateNativeWinOcclusion"
            self.process = subprocess.Popen(
                [sys.executable, "-m", "src.playback_host", url, str(browser)],
                cwd=str(self.ui.parent), env=env, stdin=subprocess.DEVNULL,
                stdout=self.log_file, stderr=self.log_file,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            process = self.process
            started_at = time.monotonic()
            self.last_page_ping = 0.0
            def watch_process():
                while process.poll() is None:
                    if self.closing or self.process is not process:
                        return
                    if time.monotonic() - max(started_at, self.last_page_ping) > 10:
                        log.warning("Audio page stopped responding; restarting its private browser")
                        with self.lock:
                            if self.closing or self.process is not process:
                                return
                            self.process = None
                            process.terminate()
                            try:
                                process.wait(timeout=2)
                            except subprocess.TimeoutExpired:
                                process.kill()
                            retry = self.recovery_attempts < 1
                            self.recovery_attempts += 1
                        if retry:
                            try:
                                self.ensure()
                                return
                            except Exception:
                                log.exception("Audio page recovery failed")
                        with self.plugin._playback_lock:
                            self.plugin._playback_state.update(status="failed", playback_error="AudioHostUnresponsive")
                        return
                    threading.Event().wait(.5)
                process.wait()
                if not self.closing and self.process is process:
                    with self.plugin._playback_lock:
                        if self.plugin._playback_state.get("track_id"):
                            self.plugin._playback_state.update(status="failed", playback_error="AudioHostUnavailable")
            threading.Thread(target=watch_process, daemon=True, name="music-player-watch").start()

    def close(self):
        self.closing = True
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
        if self.server:
            self.server.shutdown(); self.server.server_close()
        if self.log_file:
            self.log_file.close()


def main():
    import urllib.request
    url = sys.argv[1]
    transport = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    # The parent resolves the executable before spawning: do not rediscover it
    # using a different or further restricted subprocess environment.
    edge = Path(sys.argv[2]) if len(sys.argv) > 2 else find_edge_browser()
    if edge:
        import ctypes
        from ctypes import wintypes
        import tempfile
        # Closing this supervisor also kills the complete private browser tree.
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        job = kernel.CreateJobObjectW(None, None)
        # JOBOBJECT_EXTENDED_LIMIT_INFORMATION is 144 bytes on x64; flags at 16.
        limits = ctypes.create_string_buffer(144)
        ctypes.c_uint32.from_buffer(limits, 16).value = 0x2000  # KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(job, 9, limits, len(limits)):
            raise ctypes.WinError(ctypes.get_last_error())
        with tempfile.TemporaryDirectory(prefix="astra-music-audio-") as profile:
            process = subprocess.Popen([
                str(edge), "--headless=new", "--disable-extensions", "--disable-sync", "--no-first-run", "--no-default-browser-check",
                "--autoplay-policy=no-user-gesture-required", "--disable-background-timer-throttling",
                "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows",
                "--disable-background-media-suspend", "--disable-features=CalculateNativeWinOcclusion",
                "--user-data-dir=" + profile, url,
            ], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if not kernel.AssignProcessToJobObject(job, int(process._handle)):
                process.terminate(); raise ctypes.WinError(ctypes.get_last_error())
            try:
                while process.poll() is None:
                    try:
                        transport.open(url.rsplit("/", 1)[0] + "/ping", timeout=3).close()
                    except Exception:
                        break
                    threading.Event().wait(2)
            finally:
                kernel.CloseHandle(job)
                process.wait(timeout=5)
        return
    raise RuntimeError("Для постоянного аудиоплеера требуется Microsoft Edge")


if __name__ == "__main__":
    main()
