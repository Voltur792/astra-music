"""Independent queue popup; the compact widget never changes size."""
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

WIDTH, HEIGHT = 360, 420


def popup_position(anchor, screens):
    x, y = anchor.get("x", 24), anchor.get("y", 24)
    screen = next((s for s in screens if s.x <= x < s.x + s.width and s.y <= y < s.y + s.height),
                  screens[0] if screens else None)
    if screen is None:
        return x, y, HEIGHT
    height = min(HEIGHT, max(100, screen.height - 48))
    below = y + anchor.get("height", 160) + 6
    top = below if below + height <= screen.y + screen.height - 24 else y - height - 6
    return (max(screen.x, min(x, screen.x + screen.width - WIDTH)),
            max(screen.y, min(top, screen.y + screen.height - height - 24)), height)


class QueueWindow:
    def __init__(self, plugin):
        self.plugin = plugin
        self.process = None
        self.lock = threading.RLock()
        self.log_file = None

    def toggle(self, anchor):
        with self.lock:
            if self.process and self.process.poll() is None:
                self.close()
                return {"opened": False}
            base = self.plugin._audio_host.ensure_server()
            if self.log_file is None:
                self.plugin.data_dir.mkdir(parents=True, exist_ok=True)
                self.log_file = (self.plugin.data_dir / "queue-window.log").open("ab")
            self.process = subprocess.Popen(
                [sys.executable, "-m", "src.queue_window", base, json.dumps(anchor)],
                cwd=str(self.plugin._audio_host.ui.parent), env=dict(os.environ),
                stdin=subprocess.DEVNULL, stdout=self.log_file, stderr=self.log_file,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return {"opened": True}

    def close(self):
        with self.lock:
            process, self.process = self.process, None
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            if self.log_file:
                self.log_file.close()
                self.log_file = None


def main():
    from .platform_support import webview_gui
    from .desktop_widget import apply_window_appearance, WINDOW_BACKGROUND_COLOR
    from .loopback_http import client
    gui = webview_gui()
    import webview
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Voltur.AstraMusic.DesktopWidget")
    base, anchor = sys.argv[1], json.loads(sys.argv[2])
    x, y, height = popup_position(anchor, webview.screens)
    stopped = threading.Event()

    class Api:
        def close_queue(self):
            window.destroy()

    window = webview.create_window(
        "Astra Music — очередь", base + "/queue-window.html", js_api=Api(),
        width=WIDTH, height=height, x=x, y=y, resizable=False,
        frameless=sys.platform == "win32", easy_drag=False,
        on_top=bool(anchor.get("on_top")), background_color=WINDOW_BACKGROUND_COLOR,
    )
    window.events.closed += lambda: stopped.set()
    if sys.platform == "win32":
        def prepare():
            apply_window_appearance(window, 0, round_corners=True, size=(WIDTH, height))
            # A queue is a tool popup, not a second Python application on the taskbar.
            hwnd = int(window.native.Handle.ToInt64())
            user32 = ctypes.windll.user32
            user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
            user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
            user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
            user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
            style = user32.GetWindowLongPtrW(hwnd, -20)
            user32.SetWindowLongPtrW(hwnd, -20, (style | 0x80) & ~0x40000)
        def foreground():
            hwnd = int(window.native.Handle.ToInt64())
            ctypes.windll.user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        window.events.before_show += prepare
        window.events.shown += foreground

    def supervise():
        failures = 0
        while not stopped.wait(2):
            try:
                client().get(base + "/ping", timeout=3).raise_for_status()
                failures = 0
            except Exception:
                failures += 1
                if failures >= 2:
                    window.destroy()
                    return
    icon = Path(__file__).resolve().parent.parent / "ui" / "desktop-widget.ico"
    webview.start(supervise, gui=gui, private_mode=True, icon=str(icon))


if __name__ == "__main__":
    main()
