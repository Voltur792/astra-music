"""Optional movable desktop remote; audio stays in the shared playback host."""
import json
import logging
import os
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger("astra-music.desktop")
WIDTH, HEIGHT = 360, 160
WINDOW_BACKGROUND_COLOR = "#291b42"
DEFAULTS = {"enabled": False, "on_top": False, "transparency": 0, "x": None, "y": None}


def normalize_settings(value):
    result = dict(DEFAULTS)
    if not isinstance(value, dict):
        return result
    for key in ("enabled", "on_top"):
        if isinstance(value.get(key), bool):
            result[key] = value[key]
    transparency = value.get("transparency")
    if type(transparency) in (int, float) and 0 <= transparency <= 80:
        result["transparency"] = round(transparency)
    for key in ("x", "y"):
        coordinate = value.get(key)
        if isinstance(coordinate, int) and not isinstance(coordinate, bool) and -100000 <= coordinate <= 100000:
            result[key] = coordinate
    return result


def apply_window_appearance(window, transparency, round_corners=False, size=None):
    """Use the HWND directly; WinForms' live setters can block off its GUI thread."""
    import ctypes
    from ctypes import wintypes
    hwnd = int(window.native.Handle.ToInt64())
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    user32.GetDpiForWindow.restype = wintypes.UINT
    scale = (user32.GetDpiForWindow(hwnd) or 96) / 96
    if size:
        # WinForms removes its frame after calculating the initial size, losing
        # 16 x 39 px. Restore the requested size before showing the final form.
        user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.SetWindowPos.restype = wintypes.BOOL
        if not user32.SetWindowPos(hwnd, None, 0, 0, round(size[0] * scale),
                                   round(size[1] * scale), 0x16):  # no move/z-order/activation
            raise ctypes.WinError(ctypes.get_last_error())
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.DWORD, wintypes.BYTE, wintypes.DWORD]
    user32.SetLayeredWindowAttributes.restype = wintypes.BOOL
    style = user32.GetWindowLongPtrW(hwnd, -20)  # GWL_EXSTYLE
    if not style & 0x80000:  # WS_EX_LAYERED
        ctypes.set_last_error(0)
        previous = user32.SetWindowLongPtrW(hwnd, -20, style | 0x80000)
        if previous == 0 and ctypes.get_last_error():
            raise ctypes.WinError(ctypes.get_last_error())
    alpha = round(255 * (1 - transparency / 100))
    # WebView2 renders through DirectComposition. Color-keying its parent makes
    # Windows treat the entire background as click-through, including controls.
    if not user32.SetLayeredWindowAttributes(hwnd, 0, alpha, 2):  # LWA_ALPHA only
        raise ctypes.WinError(ctypes.get_last_error())
    if round_corners:
        user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HANDLE, wintypes.BOOL]
        user32.SetWindowRgn.restype = ctypes.c_int
        if sys.getwindowsversion().build >= 22000:
            # HWND regions leave black composited pixels with WebView2 and also
            # prevent DWM from rounding. Let Windows clip the complete visual.
            user32.SetWindowRgn(hwnd, None, True)
            dwmapi = ctypes.WinDLL("dwmapi")
            dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD,
                                                    ctypes.c_void_p, wintypes.DWORD]
            dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
            preference = ctypes.c_int(2)  # DWMWCP_ROUND, native 8px radius.
            result = dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(preference), 4)
            if result != 0:
                raise OSError(f"Could not enable system corners: HRESULT {result:#x}")
            border = wintypes.DWORD(0xfffffffe)  # DWMWA_COLOR_NONE
            dwmapi.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(border), 4)
        else:
            rectangle = wintypes.RECT()
            user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
            if not user32.GetWindowRect(hwnd, ctypes.byref(rectangle)):
                raise ctypes.WinError(ctypes.get_last_error())
            gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
            gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
            gdi32.CreateRoundRectRgn.restype = wintypes.HANDLE
            gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
            radius = round(16 * scale)
            region = gdi32.CreateRoundRectRgn(0, 0, rectangle.right-rectangle.left+1,
                                             rectangle.bottom-rectangle.top+1, radius, radius)
            if not region:
                raise ctypes.WinError(ctypes.get_last_error())
            if not user32.SetWindowRgn(hwnd, region, True):
                gdi32.DeleteObject(region)
                raise ctypes.WinError(ctypes.get_last_error())
            # Windows owns the region after a successful SetWindowRgn.


def visible_position(settings, screens):
    """Recover a window after a monitor is removed; negative coordinates work."""
    if not screens:
        return None, None
    x, y = settings.get("x"), settings.get("y")
    if x is not None and y is not None:
        for screen in screens:
            if screen.x <= x <= screen.x + screen.width - 80 and screen.y <= y <= screen.y + screen.height - 40:
                return x, y
    screen = screens[0]
    return screen.x + max(0, screen.width - WIDTH - 24), screen.y + 24


class DesktopWidget:
    def __init__(self, plugin):
        self.plugin = plugin
        self.settings = normalize_settings(plugin._read_settings().get("desktop_widget"))
        self.available = sys.platform == "win32" and plugin._audio_host.enabled
        self.process = None
        self.session = ""
        self.error = ""
        self.lock = threading.RLock()
        self.log_file = None
        self.closing = False
        self.last_start = 0.0
        self.position_revision = 0

    def snapshot(self):
        with self.lock:
            return {"settings": dict(self.settings), "available": self.available,
                    "running": bool(self.process and self.process.poll() is None),
                    "position_revision": self.position_revision, "message": self.error}

    def _save(self, changes):
        self.settings = normalize_settings({**self.settings, **changes})
        # Merge under the settings lock, preserving service tokens and other UI settings.
        with self.plugin._settings_lock:
            try:
                stored = json.loads(self.plugin.settings_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                stored = {}
            if not isinstance(stored, dict):
                stored = {}
            stored["desktop_widget"] = self.settings
            self.plugin.data_dir.mkdir(parents=True, exist_ok=True)
            temporary = self.plugin.settings_file.with_suffix(".desktop.tmp")
            temporary.write_text(json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.plugin.settings_file)

    def set(self, settings, reset_position=False):
        with self.lock:
            if settings.get("enabled") and not self.available:
                return {"error": "Виджет рабочего стола доступен в Windows с постоянным аудиоплеером."}
            # Screen coordinates are reported by the window, not the settings form.
            previous_on_top = self.settings["on_top"]
            self._save({key: settings[key] for key in ("enabled", "on_top", "transparency") if key in settings})
            if reset_position:
                self._save({"x": None, "y": None})
                self.position_revision += 1
            if self.settings["enabled"]:
                # WinForms' live TopMost setter runs off the GUI thread in
                # pywebview. Recreate only the remote window with the new flag.
                if previous_on_top != self.settings["on_top"]:
                    self._stop_process()
                self.ensure()
            else:
                self._stop_process()
                self.error = ""
            return self.snapshot()

    def ensure(self):
        with self.lock:
            if self.closing or not self.available or not self.settings["enabled"]:
                return
            if self.process and self.process.poll() is None:
                return
            if self.error and time.monotonic() - self.last_start < 5:
                return
            self.last_start = time.monotonic()
            try:
                base = self.plugin._audio_host.ensure_server()
                self.session = secrets.token_urlsafe(24)
                options = {**self.settings, "session": self.session, "position_revision": self.position_revision}
                self.plugin.data_dir.mkdir(parents=True, exist_ok=True)
                if self.log_file is None:
                    self.log_file = (self.plugin.data_dir / "desktop-widget.log").open("ab")
                self.process = subprocess.Popen(
                    [sys.executable, "-m", "src.desktop_widget", base, json.dumps(options)],
                    cwd=str(self.plugin._audio_host.ui.parent), env=dict(os.environ),
                    stdin=subprocess.DEVNULL, stdout=self.log_file, stderr=self.log_file,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                self.error = ""
                process = self.process
                def watch():
                    process.wait()
                    with self.lock:
                        if self.process is process and not self.closing and self.settings["enabled"]:
                            self.error = "Окно виджета недоступно. Выключите и включите настройку для повтора."
                threading.Thread(target=watch, daemon=True, name="music-desktop-watch").start()
            except Exception:
                log.exception("Could not start desktop widget")
                self.error = "Не удалось открыть виджет. Проверьте Microsoft Edge WebView2 и pywebview."

    def report(self, session, x=None, y=None, closed=False):
        with self.lock:
            if not self.session or not secrets.compare_digest(str(session), self.session) or self.closing:
                return {"success": False}
            coordinates = normalize_settings({"x": x, "y": y})
            changes = {key: coordinates[key] for key in ("x", "y") if coordinates[key] is not None}
            if closed:
                changes["enabled"] = False
            if changes:
                self._save(changes)
            return {"success": True}

    def _stop_process(self):
        self.session = ""  # Ignore a late position/closed report from an older window.
        process, self.process = self.process, None
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)

    def close(self):
        with self.lock:
            self.closing = True
            self._stop_process()
            if self.log_file:
                self.log_file.close()


def main():
    import urllib.request
    import webview
    if sys.platform == "win32":
        import ctypes
        shell32 = ctypes.WinDLL("shell32")
        shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = [ctypes.c_wchar_p]
        shell32.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long
        shell32.SetCurrentProcessExplicitAppUserModelID("Voltur.AstraMusic.DesktopWidget")
    base, options = sys.argv[1], json.loads(sys.argv[2])
    stopping = threading.Event()
    moved = threading.Event()
    position_lock = threading.Lock()
    position = [options.get("x"), options.get("y")]

    def call(method, params=None):
        request = urllib.request.Request(base + "/api", json.dumps({"method": method, "params": params or {}}).encode(),
                                         {"Content-Type": "application/json"})
        # Loopback must never go through a system HTTP proxy.
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=3) as response:
            return json.load(response)

    x, y = visible_position(options, webview.screens)
    window = webview.create_window("Astra Music", base + "/desktop-widget.html",
                                   width=WIDTH, height=HEIGHT, x=x, y=y,
                                   resizable=False, frameless=True, easy_drag=False, shadow=False,
                                   on_top=options["on_top"], background_color=WINDOW_BACKGROUND_COLOR, focus=False)

    def on_moved(x, y):
        with position_lock:
            position[:] = [int(x), int(y)]
        moved.set()

    def report_position(closed=False):
        with position_lock:
            x, y = position
        return call("music_desktop_report", {"session": options["session"], "x": x, "y": y, "closed": closed})

    def on_closing(*_):
        stopping.set()
        try:
            report_position(closed=True)
        except Exception:
            pass

    def supervise():
        revision = options["position_revision"]
        transparency = normalize_settings(options)["transparency"]
        failures = 0
        try:
            on_moved(window.x, window.y)
            while not stopping.wait(.5):
                try:
                    if moved.is_set():
                        moved.clear()
                        report_position()
                    status = call("music_desktop_get")
                    failures = 0
                    settings = normalize_settings(status["settings"])
                    if not settings["enabled"]:
                        window.destroy()
                        break
                    if settings["transparency"] != transparency:
                        appearance["transparency"] = settings["transparency"]
                        apply_window_appearance(window, settings["transparency"])
                        transparency = settings["transparency"]
                    if status["position_revision"] != revision:
                        revision = status["position_revision"]
                        x, y = visible_position(settings, webview.screens)
                        window.move(x, y)
                except Exception:
                    failures += 1
                    if failures >= 3:
                        window.destroy()  # Astra is gone; do not leave an orphan window.
                        break
        finally:
            stopping.set()

    window.events.moved += on_moved
    window.events.closing += on_closing
    appearance = {"transparency": normalize_settings(options)["transparency"]}
    def refresh_shape(*_):
        if stopping.is_set() or window.native is None or window.native.IsDisposed:
            return
        apply_window_appearance(window, appearance["transparency"], round_corners=True)
    def prepare_window():
        apply_window_appearance(window, appearance["transparency"],
                                round_corners=True, size=(WIDTH, HEIGHT))
    window.events.before_show += prepare_window
    # Reapply after WinForms has shown/resized the form (including DPI changes).
    window.events.shown += refresh_shape
    window.events.resized += refresh_shape
    # Reapply after Chromium has completed its first navigation.
    window.events.loaded += refresh_shape
    icon = Path(__file__).resolve().parent.parent / "ui" / "desktop-widget.ico"
    webview.start(supervise, gui="edgechromium", private_mode=True, icon=str(icon))


if __name__ == "__main__":
    main()
