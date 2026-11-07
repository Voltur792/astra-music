"""Driver media commands and low-level media keys, without a visible window."""
from collections import deque
import time
import uuid

from .media_keys import WindowsHotkeys, ACTIONS, log
from .hotkeys import CUSTOM_ACTIONS, MODIFIER_KEYS

KEY_IDS = {key: identifier for identifier, (key, _) in ACTIONS.items()}


class MediaInputEvents:
    def __init__(self):
        self.active = False
        self.held = set()
        self.events = deque()
        self.last = None
        self.bindings = {}
        self.capture_id = None
        self.capture_done = False
        self.captured = deque()
        self.standard_enabled = True

    def configure(self, bindings, capture_id, standard_enabled=True):
        self.standard_enabled = standard_enabled
        updated = {(item["vk"], item["modifiers"]): CUSTOM_ACTIONS[action]
                   for action, item in bindings.items() if item}
        if updated != self.bindings or capture_id != self.capture_id:
            self.last = None
        self.bindings = updated
        if capture_id != self.capture_id:
            self.capture_id = capture_id
            self.capture_done = False

    def capture(self, key, modifiers):
        if not self.capture_id or self.capture_done or key in MODIFIER_KEYS:
            return False
        self.capture_done = True
        self.captured.append((self.capture_id, None if key == 27 else {"vk": key, "modifiers": modifiers}))
        return True

    def record(self, identifier, source):
        if not self.active:
            return
        now = time.monotonic()
        if self.last and self.last[0] == identifier and now - self.last[1] < .15:
            return
        self.last = (identifier, now)
        self.events.append((identifier, source))

    def key(self, message, key, modifiers=0):
        if message in (0x100, 0x104) and key in self.held:
            return True
        if message in (0x101, 0x105) and key in self.held:
            self.held.remove(key)
            return True
        if message in (0x100, 0x104) and self.capture(key, modifiers):
            self.held.add(key)
            return True
        if self.capture_id or not self.active:
            return False
        identifier = self.bindings.get((key, modifiers))
        if identifier is None:
            identifier = KEY_IDS.get(key) if modifiers == 0 and self.standard_enabled else None
        if identifier is None:
            return False
        if message in (0x100, 0x104):
            if key not in self.held:
                self.held.add(key)
                self.record(identifier, "keyboard")
            return True
        if message in (0x101, 0x105):
            if key in self.held:
                self.held.remove(key)
                return True
        return False

    def appcommand(self, lparam, source):
        # GET_APPCOMMAND_LPARAM: strip the mouse/OEM device flags.
        command = (int(lparam) >> 16) & 0xFFF
        if self.capture_id:
            key = {11: 0xB0, 12: 0xB1, 13: 0xB2, 14: 0xB3}.get(command)
            return bool(key and self.capture(key, 0))
        key = {11: 0xB0, 12: 0xB1, 13: 0xB2, 14: 0xB3}.get(command)
        identifier = self.bindings.get((key, 0)) if key else None
        if identifier is None and self.standard_enabled:
            identifier = {11: 0x5A10, 12: 0x5A11}.get(command)
        if identifier is None or not self.active:
            return False
        self.record(identifier, source)
        return True


class WindowsMediaInput(WindowsHotkeys):
    def __init__(self):
        super().__init__()
        import ctypes as c
        from ctypes import wintypes as w
        self.input = MediaInputEvents()
        self.hook = None
        self.shell_registered = False
        self.window = None
        self.atom = 0
        self.class_name = "AstraMusicMedia_" + uuid.uuid4().hex
        self.kernel = c.WinDLL("kernel32", use_last_error=True)
        self.kernel.GetModuleHandleW.argtypes = [w.LPCWSTR]
        self.kernel.GetModuleHandleW.restype = w.HMODULE
        self.module = self.kernel.GetModuleHandleW(None)
        callback_type = c.WINFUNCTYPE(c.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
        hook_type = c.WINFUNCTYPE(c.c_ssize_t, c.c_int, w.WPARAM, w.LPARAM)
        class WindowClass(c.Structure):
            _fields_ = [("style", w.UINT), ("proc", callback_type), ("class_extra", c.c_int),
                        ("window_extra", c.c_int), ("instance", w.HINSTANCE), ("icon", w.HICON),
                        ("cursor", w.HANDLE), ("background", w.HBRUSH),
                        ("menu", w.LPCWSTR), ("name", w.LPCWSTR)]
        def bind(name, result, *args):
            fn = getattr(self.user, name)
            fn.argtypes, fn.restype = list(args), result
            return fn
        bind("DefWindowProcW", c.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
        bind("RegisterClassW", w.ATOM, c.POINTER(WindowClass))
        bind("UnregisterClassW", w.BOOL, w.LPCWSTR, w.HINSTANCE)
        bind("CreateWindowExW", w.HWND, w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
             c.c_int, c.c_int, c.c_int, c.c_int, w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID)
        bind("DestroyWindow", w.BOOL, w.HWND)
        bind("RegisterWindowMessageW", w.UINT, w.LPCWSTR)
        bind("RegisterShellHookWindow", w.BOOL, w.HWND)
        bind("DeregisterShellHookWindow", w.BOOL, w.HWND)
        bind("SetWindowsHookExW", w.HANDLE, c.c_int, hook_type, w.HINSTANCE, w.DWORD)
        bind("UnhookWindowsHookEx", w.BOOL, w.HANDLE)
        bind("CallNextHookEx", c.c_ssize_t, w.HANDLE, c.c_int, w.WPARAM, w.LPARAM)
        bind("TranslateMessage", w.BOOL, c.POINTER(w.MSG))
        bind("DispatchMessageW", c.c_ssize_t, c.POINTER(w.MSG))
        bind("GetAsyncKeyState", c.c_short, c.c_int)
        self.shell_message = self.user.RegisterWindowMessageW("SHELLHOOK")

        @callback_type
        def window_proc(window, message, wp, lp):
            if message == 0x319 and self.input.appcommand(lp, "appcommand"):
                return 1
            if self.shell_message and message == self.shell_message and wp == 12:
                if self.input.appcommand(lp, "shell"):
                    return 1
            return self.user.DefWindowProcW(window, message, wp, lp)

        @hook_type
        def keyboard_proc(code, message, data):
            if code >= 0:
                key = c.cast(data, c.POINTER(w.DWORD)).contents.value
                modifiers = sum(flag for vk, flag in [(0x12, 1), (0x11, 2), (0x10, 4)] if self.user.GetAsyncKeyState(vk) < 0)
                if self.user.GetAsyncKeyState(0x5B) < 0 or self.user.GetAsyncKeyState(0x5C) < 0:
                    modifiers |= 8
                if self.input.key(message, key, modifiers):
                    return 1
            return self.user.CallNextHookEx(self.hook, code, message, data)

        self.window_proc, self.keyboard_proc = window_proc, keyboard_proc
        window_class = WindowClass()
        window_class.proc, window_class.instance, window_class.name = window_proc, self.module, self.class_name
        self.atom = self.user.RegisterClassW(c.byref(window_class))
        if self.atom:
            self.window = self.user.CreateWindowExW(0x80, self.class_name, "", 0,
                                                    0, 0, 0, 0, None, None, self.module, None)
        if not self.window:
            log.warning("Media command window unavailable (Windows error %s)", c.get_last_error())

    def set_active(self, active):
        if self.input.active == active:
            return
        self.input.active = active
        self.input.held.clear()
        self.input.last = None
        if active:
            self.hook = self.user.SetWindowsHookExW(13, self.keyboard_proc, self.module, 0)
            self.shell_registered = bool(self.window and self.user.RegisterShellHookWindow(self.window))
            log.info("Native media input ready: keyboard=%s, appcommand=%s", bool(self.hook), self.shell_registered)
        else:
            if self.hook:
                self.user.UnhookWindowsHookEx(self.hook)
                self.hook = None
            if self.shell_registered:
                self.user.DeregisterShellHookWindow(self.window)
                self.shell_registered = False
            self.input.events.clear()

    def messages(self):
        while self.user.PeekMessageW(self.ctypes.byref(self.message), None, 0, 0, 1):
            if self.message.message == 0x312:
                identifier = int(self.message.wParam)
                if identifier in ACTIONS and self.input.standard_enabled:
                    self.input.record(identifier, "hotkey")
            else:
                self.user.TranslateMessage(self.ctypes.byref(self.message))
                self.user.DispatchMessageW(self.ctypes.byref(self.message))
        while self.input.events:
            identifier, source = self.input.events.popleft()
            names = {**{identifier: value[1] for identifier, value in ACTIONS.items()},
                     **{identifier: action for action, identifier in CUSTOM_ACTIONS.items()}}
            log.info("Native media input: %s via %s", names[identifier], source)
            yield identifier

    def configure(self, bindings, capture_id, standard_enabled=True):
        self.input.configure(bindings, capture_id, standard_enabled)

    def captures(self):
        while self.input.captured:
            yield self.input.captured.popleft()

    def close(self):
        self.set_active(False)
        if self.window:
            self.user.DestroyWindow(self.window)
            self.window = None
        if self.atom:
            self.user.UnregisterClassW(self.class_name, self.module)
            self.atom = 0
