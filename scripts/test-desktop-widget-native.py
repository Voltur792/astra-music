"""Windows integration probe: visible corners, hit testing and WebView input.

Run with the project's venv. Uses an isolated fixture; never reads account tokens
or controls the user's music. Browser input is dispatched inside the test WebView.
"""
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def wait_until(check, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(.05)
    raise AssertionError("Native widget probe timed out")


def check_window(window):
    from System import Action
    from src.desktop_widget import apply_window_appearance

    wait_until(lambda: window.evaluate_js("document.getElementById('title').textContent") == "Native input test")
    user32, gdi32 = ctypes.WinDLL("user32"), ctypes.WinDLL("gdi32")
    hwnd = int(window.native.Handle.ToInt64())
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRgn.argtypes = [wintypes.HWND, wintypes.HANDLE]
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetLayeredWindowAttributes.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.BYTE), ctypes.POINTER(wintypes.DWORD)]
    gdi32.CreateRectRgn.restype = wintypes.HANDLE
    gdi32.PtInRegion.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_int]
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
    bounds = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(bounds))
    points = window.evaluate_js("Array.from(document.querySelectorAll('button,input')).map(e=>{const r=e.getBoundingClientRect();return {id:e.id,x:r.x+r.width/2,y:r.y+r.height/2,enabled:!e.disabled}})")
    assert all(point["enabled"] for point in points), points
    dpr = window.evaluate_js("devicePixelRatio")
    for transparency in (0, 40, 80):
        apply_window_appearance(window, transparency, round_corners=True)
        time.sleep(.1)
        key, alpha, flags = wintypes.DWORD(), wintypes.BYTE(), wintypes.DWORD()
        assert user32.GetLayeredWindowAttributes(hwnd, ctypes.byref(key), ctypes.byref(alpha), ctypes.byref(flags))
        assert flags.value == 2 and alpha.value == round(255 * (1 - transparency / 100))
        for point in points:
            recipient = user32.WindowFromPoint(wintypes.POINT(bounds.left + round(point["x"] * dpr), bounds.top + round(point["y"] * dpr)))
            assert user32.GetAncestor(recipient, 2) == hwnd, (transparency, point["id"], "click-through")
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    surfaces = [hwnd]
    @callback_type
    def collect(handle, _):
        rectangle = wintypes.RECT()
        user32.GetWindowRect(handle, ctypes.byref(rectangle))
        if (rectangle.left, rectangle.top, rectangle.right, rectangle.bottom) == (bounds.left, bounds.top, bounds.right, bounds.bottom):
            surfaces.append(handle)
        return True
    user32.EnumChildWindows.argtypes = [wintypes.HWND, callback_type, wintypes.LPARAM]
    user32.EnumChildWindows(hwnd, collect, 0)
    assert len(surfaces) >= 4
    if sys.getwindowsversion().build >= 22000:
        dwmapi = ctypes.WinDLL("dwmapi")
        dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        preference = ctypes.c_int()
        assert dwmapi.DwmGetWindowAttribute(hwnd, 33, ctypes.byref(preference), 4) == 0
        assert preference.value == 2
        # Regions disable system rounding. No child region is needed either.
        for surface in surfaces:
            region = gdi32.CreateRectRgn(0, 0, 0, 0)
            assert user32.GetWindowRgn(surface, region) == 0
            gdi32.DeleteObject(region)
        surfaces = []
    width, height = bounds.right - bounds.left, bounds.bottom - bounds.top
    for surface in surfaces:
        region = gdi32.CreateRectRgn(0, 0, 0, 0)
        try:
            assert user32.GetWindowRgn(surface, region) > 0
            assert all(not gdi32.PtInRegion(region, x, y) for x, y in ((0, 0), (width-1, 0), (0, height-1), (width-1, height-1)))
            assert gdi32.PtInRegion(region, width//2, height//2)
        finally:
            gdi32.DeleteObject(region)
    # Check the actual visible pixels over a known green test background.
    # Native window shadows may slightly darken green; an opaque black/purple
    # corner must fail even if all window-region API checks report success.
    apply_window_appearance(window, 40, round_corners=True)
    time.sleep(.3)
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    gdi32.GetPixel.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.GetPixel.restype = wintypes.DWORD
    dc = user32.GetDC(None)
    try:
        for x, y in ((0, 0), (width-1, 0), (0, height-1), (width-1, height-1)):
            color = int(gdi32.GetPixel(dc, bounds.left+x, bounds.top+y))
            red, green, blue = color & 255, (color >> 8) & 255, (color >> 16) & 255
            assert red < 10 and blue < 10 and green >= 190, (x, y, hex(color), "opaque corner remains")
    finally:
        user32.ReleaseDC(None, dc)
    from System.Drawing import Bitmap, Graphics
    preview = Bitmap(width, height)
    graphics = Graphics.FromImage(preview)
    destination = graphics.GetHdc()
    dc = user32.GetDC(None)
    gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
    gdi32.BitBlt.restype = wintypes.BOOL
    try:
        assert gdi32.BitBlt(int(destination.ToInt64()), 0, 0, width, height, dc, bounds.left, bounds.top, 0x40cc0020)
    finally:
        graphics.ReleaseHdc(destination)
        user32.ReleaseDC(None, dc)
    preview.Save(str(Path(os.environ["TEMP"]) / "astra-widget-rounded-fixture.png"))
    graphics.Dispose()
    preview.Dispose()
    print("Actual visible corners show the test background; all controls receive Windows hit tests at 0/40/80% transparency.", flush=True)

    def browser_input(method, params):
        tasks = []
        window.native.Invoke(Action(lambda: tasks.append(window.native.webview.CoreWebView2.CallDevToolsProtocolMethodAsync(method, json.dumps(params)))))
        wait_until(lambda: tasks[0].IsCompleted)
        tasks[0].GetAwaiter().GetResult()

    # Trusted browser mouse events, after confirming Windows delivers each
    # corresponding coordinate to this native window instead of the desktop.
    for button in ("play", "play", "previous", "next", "like", "volume", "seek", "stop"):
        point = next(point for point in points if point["id"] == button)
        if button == "volume":
            point = {**point, "y": point["y"] + 10}
        browser_input("Input.dispatchMouseEvent", {"type": "mousePressed", "x": point["x"], "y": point["y"], "button": "left", "clickCount": 1})
        browser_input("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": point["x"], "y": point["y"], "button": "left", "clickCount": 1})
        time.sleep(.2)


def run_child():
    import webview
    from src.desktop_widget import main
    create = webview.create_window
    passed = []
    def create_test_window(*args, **kwargs):
        window = create(*args, **kwargs)
        def check():
            try:
                check_window(window)
                passed.append(True)
            except Exception:
                import traceback
                traceback.print_exc()
            finally:
                window.destroy()
        window.events.loaded += check
        return window
    webview.create_window = create_test_window
    # Production entry point receives only its base URL and window settings.
    sys.argv = [sys.argv[0], sys.argv[2], sys.argv[3]]
    main()
    assert passed, "Native window checks failed"


def run_parent():
    from src.plugin import AstraMusic
    with tempfile.TemporaryDirectory(prefix="music-native-") as temporary:
        os.environ["ASTRA_MUSIC_DATA_DIR"] = temporary
        plugin = AstraMusic()
        widget = plugin._desktop_widget
        widget.session = "native-input-test"
        widget._save({"enabled": True, "on_top": True, "transparency": 40, "x": 80, "y": 80})
        plugin._visualizer_settings["mode"] = "off"
        plugin._playback_state.update(track_id="7", service="yandex", title="Native input test", status="paused", duration_seconds=180, queue_index=1, queue_count=3, source="playlist", volume=.5)
        received = []
        def control(action, value):
            received.append(action)
            if action in ("play", "pause"):
                plugin._playback_state["status"] = "playing" if action == "play" else "paused"
            elif action == "volume":
                plugin._playback_state["volume"] = value / 10
            elif action == "seek":
                plugin._playback_state["position_seconds"] = value
            return {"success": True}
        plugin._playback_control_sync = control
        base = plugin._audio_host.ensure_server()
        options = {**widget.settings, "session": widget.session, "position_revision": 0}
        ready = Path(temporary) / "backdrop-ready"
        backdrop = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--backdrop", str(ready)], cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT)}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            wait_until(ready.exists, 8)
            result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--child", base, json.dumps(options)], cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8"}, capture_output=True, encoding="utf-8", timeout=25)
            print(result.stdout, end="")
            if result.returncode:
                raise AssertionError(result.stderr)
            assert received == ["play", "pause", "previous", "next", "like", "volume", "seek", "stop"], received
            assert plugin._audio_host.process is None
            print("All buttons and both sliders delivered commands through the real WebView and loopback bridge; user music was untouched.")
        finally:
            ready.with_suffix(".stop").touch()
            try:
                backdrop.wait(timeout=3)
            except subprocess.TimeoutExpired:
                backdrop.terminate()
            widget.close()
            plugin._audio_host.close()


def run_backdrop():
    import webview
    ready = Path(sys.argv[2])
    window = webview.create_window("Astra Music — test backdrop", html="<html><body style='margin:0;background:#00ff00'></body></html>", width=440, height=240, x=60, y=60, frameless=True, resizable=False, shadow=False, focus=False, on_top=True, background_color="#00ff00")
    def supervise():
        ready.touch()
        while not ready.with_suffix(".stop").exists():
            time.sleep(.1)
        window.destroy()
    webview.start(supervise, gui="edgechromium", private_mode=True)


if __name__ == "__main__":
    if sys.platform != "win32":
        raise SystemExit("This probe requires Windows and the project's venv.")
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        run_child()
    elif len(sys.argv) > 1 and sys.argv[1] == "--backdrop":
        run_backdrop()
    else:
        run_parent()
