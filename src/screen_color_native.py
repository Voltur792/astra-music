"""Windows palette and one-pixel picker using only Python's standard library."""
import argparse
import ctypes as c
import json
import sys
import time
from ctypes import wintypes as w


class PickerError(Exception):
    def __init__(self, stage, code=0):
        self.stage, self.code = stage, int(code or 0)
        super().__init__(stage)


def colorref_to_hex(value):
    if value == 0xFFFFFFFF:
        raise PickerError("pixel_read")
    return "#{:02x}{:02x}{:02x}".format(value & 255, value >> 8 & 255, value >> 16 & 255)


def hex_to_colorref(value):
    if not isinstance(value, str) or len(value) != 7 or not value.startswith("#"):
        raise ValueError("Invalid color")
    red, green, blue = (int(value[index:index + 2], 16) for index in (1, 3, 5))
    return red | green << 8 | blue << 16


class PixelSelection:
    def __init__(self):
        self.release = None
        self.complete = False
        self.result = {"cancelled": True}

    def mouse(self, message, sample):
        if self.release:
            if message == self.release:
                self.complete = True
                return True
            return message in (0x201, 0x204)
        if message not in (0x201, 0x204):
            return False
        if message == 0x201:
            self.result = {"color": colorref_to_hex(sample())}
        self.release = 0x202 if message == 0x201 else 0x205
        return True


class Windows:
    def __init__(self):
        if sys.platform != "win32":
            raise PickerError("unsupported_platform")
        self.user = c.WinDLL("user32", use_last_error=True)
        self.gdi = c.WinDLL("gdi32", use_last_error=True)
        self.dialog = c.WinDLL("comdlg32", use_last_error=True)
        self.window_proc = c.WINFUNCTYPE(c.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
        self.mouse_proc = c.WINFUNCTYPE(c.c_ssize_t, c.c_int, w.WPARAM, w.LPARAM)
        self.color_hook = c.WINFUNCTYPE(c.c_size_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)

        def bind(dll, name, result, *args):
            fn = getattr(dll, name)
            fn.argtypes, fn.restype = list(args), result
            return fn
        bind(self.user, "CreateWindowExW", w.HWND, w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
             c.c_int, c.c_int, c.c_int, c.c_int, w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID)
        bind(self.user, "DestroyWindow", w.BOOL, w.HWND)
        bind(self.user, "IsWindow", w.BOOL, w.HWND)
        bind(self.user, "ShowWindow", w.BOOL, w.HWND, c.c_int)
        bind(self.user, "UpdateWindow", w.BOOL, w.HWND)
        bind(self.user, "SetWindowLongPtrW", c.c_void_p, w.HWND, c.c_int, c.c_void_p)
        bind(self.user, "CallWindowProcW", c.c_ssize_t, c.c_void_p, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
        bind(self.user, "SetWindowPos", w.BOOL, w.HWND, w.HWND, c.c_int, c.c_int, c.c_int, c.c_int, w.UINT)
        bind(self.user, "SetTimer", c.c_size_t, w.HWND, c.c_size_t, w.UINT, c.c_void_p)
        bind(self.user, "PostMessageW", w.BOOL, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
        bind(self.user, "PostQuitMessage", None, c.c_int)
        bind(self.user, "GetMessageW", c.c_int, c.POINTER(w.MSG), w.HWND, w.UINT, w.UINT)
        bind(self.user, "TranslateMessage", w.BOOL, c.POINTER(w.MSG))
        bind(self.user, "DispatchMessageW", c.c_ssize_t, c.POINTER(w.MSG))
        bind(self.user, "GetCursorPos", w.BOOL, c.POINTER(w.POINT))
        bind(self.user, "GetDC", w.HDC, w.HWND)
        bind(self.user, "ReleaseDC", c.c_int, w.HWND, w.HDC)
        bind(self.user, "GetAsyncKeyState", c.c_short, c.c_int)
        bind(self.user, "SetWindowsHookExW", w.HANDLE, c.c_int, self.mouse_proc, w.HINSTANCE, w.DWORD)
        bind(self.user, "UnhookWindowsHookEx", w.BOOL, w.HANDLE)
        bind(self.user, "CallNextHookEx", c.c_ssize_t, w.HANDLE, c.c_int, w.WPARAM, w.LPARAM)
        bind(self.gdi, "GetPixel", w.DWORD, w.HDC, c.c_int, c.c_int)
        bind(self.dialog, "CommDlgExtendedError", w.DWORD)
        try:
            bind(self.user, "SetThreadDpiAwarenessContext", c.c_void_p, c.c_void_p)(c.c_void_p(-4))
        except AttributeError:
            pass

    def work_area(self):
        class Monitor(c.Structure):
            _fields_ = [("size", w.DWORD), ("monitor", w.RECT), ("work", w.RECT), ("flags", w.DWORD)]
        self.user.MonitorFromPoint.argtypes = [w.POINT, w.DWORD]
        self.user.MonitorFromPoint.restype = w.HANDLE
        self.user.GetMonitorInfoW.argtypes = [w.HANDLE, c.POINTER(Monitor)]
        self.user.GetMonitorInfoW.restype = w.BOOL
        point, info = w.POINT(), Monitor()
        self.user.GetCursorPos(c.byref(point))
        info.size = c.sizeof(info)
        if not self.user.GetMonitorInfoW(self.user.MonitorFromPoint(point, 2), c.byref(info)):
            raise PickerError("monitor", c.get_last_error())
        return info.work

    def pixel(self):
        point = w.POINT()
        if not self.user.GetCursorPos(c.byref(point)):
            raise PickerError("cursor", c.get_last_error())
        dc = self.user.GetDC(None)
        if not dc:
            raise PickerError("desktop_dc", c.get_last_error())
        try:
            return self.gdi.GetPixel(dc, point.x, point.y)
        finally:
            self.user.ReleaseDC(None, dc)


class PickerWindow:
    def __init__(self, api, visible=False):
        self.api = api
        self.timer_action = None
        area = api.work_area()
        left, top = (area.left + 16, area.top + 16) if visible else ((area.left + area.right) // 2, (area.top + area.bottom) // 2)
        # A small no-activate tool window, never a desktop-sized overlay.
        self.handle = api.user.CreateWindowExW(0x08000088, "STATIC",
            "Выберите точку на экране.\nEsc / правая кнопка — отмена.", 0x80800001,
            left, top, 360 if visible else 1, 64 if visible else 1, None, None, None, None)
        if not self.handle:
            raise PickerError("window", c.get_last_error())
        self.previous_proc = None

        @api.window_proc
        def window_proc(window, message, wp, lp):
            if message == 0x10:  # WM_CLOSE
                api.user.DestroyWindow(window)
                return 0
            if message == 2:  # WM_DESTROY
                api.user.PostQuitMessage(0)
                return 0
            if message == 0x113 and self.timer_action:
                self.timer_action()
                return 0
            return api.user.CallWindowProcW(self.previous_proc, window, message, wp, lp)
        self.callback = window_proc
        self.previous_proc = api.user.SetWindowLongPtrW(self.handle, -4, c.cast(window_proc, c.c_void_p))
        if not self.previous_proc:
            self.close()
            raise PickerError("window_proc", c.get_last_error())
        if visible:
            api.user.ShowWindow(self.handle, 4)
            api.user.UpdateWindow(self.handle)

    def close(self):
        if self.handle and self.api.user.IsWindow(self.handle):
            if self.previous_proc:
                self.api.user.SetWindowLongPtrW(self.handle, -4, self.previous_proc)
            self.api.user.DestroyWindow(self.handle)
        self.handle = None


def choose_palette(initial, api=None, cancel_immediately=False):
    initial_color = hex_to_colorref(initial)
    api = api or Windows()
    class ChooseColor(c.Structure):
        _fields_ = [("size", w.DWORD), ("owner", w.HWND), ("instance", w.HWND),
                    ("color", w.DWORD), ("custom", c.POINTER(w.DWORD)), ("flags", w.DWORD),
                    ("data", w.LPARAM), ("hook", api.color_hook), ("template", w.LPCWSTR)]
    owner = PickerWindow(api)
    custom = (w.DWORD * 16)()
    aborted = False
    @api.color_hook
    def dialog_hook(window, message, wp, lp):
        nonlocal aborted
        if message == 0x110:  # WM_INITDIALOG
            api.user.SetWindowPos(window, w.HWND(-1), 0, 0, 0, 0, 0x13)
            api.user.SetTimer(window, 1, 60000, None)
            if cancel_immediately:
                aborted = True
                api.user.PostMessageW(window, 0x111, 3, 0)  # documented IDABORT
        elif message == 0x113:
            aborted = True
            api.user.PostMessageW(window, 0x111, 3, 0)
            return 1
        return 0
    settings = ChooseColor()
    settings.size, settings.owner, settings.color = c.sizeof(settings), owner.handle, initial_color
    settings.custom, settings.flags, settings.hook = custom, 0x113, dialog_hook
    api.dialog.ChooseColorW.argtypes = [c.POINTER(ChooseColor)]
    api.dialog.ChooseColorW.restype = w.BOOL
    try:
        if api.dialog.ChooseColorW(c.byref(settings)):
            return {"color": colorref_to_hex(settings.color)}
        error = api.dialog.CommDlgExtendedError()
        if error and not (aborted and error == 0xFFFF):
            raise PickerError("color_dialog", error)
        return {"cancelled": True}
    finally:
        owner.close()


def choose_pixel(api=None):
    api = api or Windows()
    window = PickerWindow(api, visible=True)
    selection = PixelSelection()
    deadline = time.monotonic() + 60
    failure = []
    hook = None
    def cancel_if_needed():
        if time.monotonic() >= deadline or api.user.GetAsyncKeyState(27) < 0:
            api.user.PostMessageW(window.handle, 0x10, 0, 0)
    window.timer_action = cancel_if_needed
    @api.mouse_proc
    def mouse_proc(code, message, data):
        if code >= 0:
            try:
                if selection.mouse(message, api.pixel):
                    if selection.complete:
                        api.user.PostMessageW(window.handle, 0x10, 0, 0)
                    return 1
            except PickerError as error:
                failure.append(error)
                api.user.PostMessageW(window.handle, 0x10, 0, 0)
                return 1
        return api.user.CallNextHookEx(hook, code, message, data)
    try:
        hook = api.user.SetWindowsHookExW(14, mouse_proc, None, 0)
        if not hook:
            raise PickerError("mouse_hook", c.get_last_error())
        if not api.user.SetTimer(window.handle, 1, 40, None):
            raise PickerError("timer", c.get_last_error())
        message = w.MSG()
        while True:
            received = api.user.GetMessageW(c.byref(message), None, 0, 0)
            if received == 0:
                break
            if received == -1:
                raise PickerError("message_loop", c.get_last_error())
            api.user.TranslateMessage(c.byref(message))
            api.user.DispatchMessageW(c.byref(message))
        if failure:
            raise failure[0]
        return selection.result if selection.complete else {"cancelled": True}
    finally:
        if hook:
            api.user.UnhookWindowsHookEx(hook)
        window.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("screen", "palette"), required=True)
    parser.add_argument("--initial", default="#000000")
    args = parser.parse_args()
    try:
        hex_to_colorref(args.initial)
        result = choose_palette(args.initial) if args.mode == "palette" else choose_pixel()
    except PickerError as error:
        result = {"error": "native_picker_failed", "stage": error.stage, "code": error.code}
    except Exception as error:
        result = {"error": "native_picker_failed", "stage": "native_runtime", "kind": type(error).__name__,
                  "code": int(getattr(error, "winerror", 0) or 0)}
    print(json.dumps(result, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
