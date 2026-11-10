"""Windows next/previous keys for the private headless audio player."""
import logging
import sys
import threading
import time
import json
import secrets
from .hotkeys import CUSTOM_ACTIONS, binding, normalize, label

log = logging.getLogger("astra-music.media-keys")
ACTIONS = {0x5A10: (0xB0, "next"), 0x5A11: (0xB1, "previous")}


class WindowsHotkeys:
    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ctypes = ctypes
        self.message = wintypes.MSG()
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.user.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        self.user.RegisterHotKey.restype = wintypes.BOOL
        self.user.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user.UnregisterHotKey.restype = wintypes.BOOL
        self.user.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
        self.user.PeekMessageW.restype = wintypes.BOOL

    def register(self, identifier, key):
        # MOD_NOREPEAT: holding a key must not skip the whole playlist.
        if self.user.RegisterHotKey(None, identifier, 0x4000, key):
            return True
        log.warning("Cannot register media key %s (Windows error %s)", ACTIONS[identifier][1], self.ctypes.get_last_error())
        return False

    def unregister(self, identifier):
        self.user.UnregisterHotKey(None, identifier)

    def messages(self):
        while self.user.PeekMessageW(self.ctypes.byref(self.message), None, 0, 0, 1):
            if self.message.message == 0x312:  # WM_HOTKEY
                yield int(self.message.wParam)


class MediaKeys:
    def __init__(self, plugin, api_factory=None):
        self.plugin = plugin
        self._api_factory = api_factory
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._worker = None
        self._pending = False
        self.bindings = normalize(plugin._read_settings().get("hotkeys")) if hasattr(plugin, "_read_settings") else normalize(None)
        self._capture = None
        self._listener_error = ""

    def snapshot(self):
        with self._lock:
            values = dict(self.bindings)
        return {"supported": sys.platform == "win32", "bindings": values,
                "labels": {action: label(item) for action, item in values.items()}}

    def save(self, action, value):
        if not isinstance(action, str) or action not in CUSTOM_ACTIONS:
            raise ValueError("Неизвестное действие.")
        item = binding(value) if value is not None else None
        with self._lock:
            if item and any(other != action and current == item for other, current in self.bindings.items()):
                raise ValueError("Это сочетание уже назначено другому действию.")
            updated = {**self.bindings, action: item}
            with self.plugin._settings_lock:
                try:
                    settings = json.loads(self.plugin.settings_file.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    settings = {}
                if not isinstance(settings, dict):
                    settings = {}
                settings["hotkeys"] = updated
                self.plugin.data_dir.mkdir(parents=True, exist_ok=True)
                temporary = self.plugin.settings_file.with_suffix(".hotkeys.tmp")
                temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
                temporary.replace(self.plugin.settings_file)
            self.bindings = updated
        self.start()
        return self.snapshot()

    def capture_start(self, action):
        if sys.platform != "win32":
            raise ValueError("Запись глобальных клавиш доступна на Windows.")
        if not isinstance(action, str) or action not in CUSTOM_ACTIONS:
            raise ValueError("Неизвестное действие.")
        with self._lock:
            session = secrets.token_urlsafe(24)
            self._capture = {"session": session, "action": action, "deadline": time.monotonic() + 20}
        self.start()
        return {"pending": True, "session": session}

    def capture_poll(self, session, cancel=False):
        with self._lock:
            capture = self._capture
            if not capture or capture["session"] != session:
                return {"cancelled": True}
            if cancel or time.monotonic() >= capture["deadline"]:
                self._capture = None
                return {"cancelled": True}
            if self._listener_error:
                self._capture = None
                raise ValueError("Не удалось запустить запись клавиш. Перезапустите плагин.")
            if "result" not in capture:
                return {"pending": True, "session": session}
            self._capture = None
        if capture["result"] is None:
            return {"cancelled": True}
        return self.save(capture["action"], capture["result"])

    def active(self):
        with self.plugin._playback_lock:
            state = self.plugin._playback_state
            if state.get("service") == "spotify":
                return bool(any(self.bindings.values()) and state.get("track_id")
                            and getattr(getattr(self.plugin, "_spotify", None), "active", False)
                            and state.get("status") in ("playing", "paused"))
            return bool(state.get("track_id") and state.get("url")
                        and state.get("service") in ("yandex", "vk")
                        and (state.get("status") in ("playing", "play_requested", "attempting")
                             or state.get("status") == "paused" and any(self.bindings.values())))

    def start(self):
        if sys.platform != "win32":
            return
        with self._lock:
            if self._stop.is_set() or self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._run, name="music-media-keys", daemon=True)
            self._thread.start()

    def dispatch(self, identifier):
        names = {**{identifier: value[1] for identifier, value in ACTIONS.items()},
                 **{identifier: action for action, identifier in CUSTOM_ACTIONS.items()}}
        if identifier not in names or self._stop.is_set() or not self.active():
            return False
        with self._lock:
            if self._pending or self._capture:
                return False
            self._pending = True
            action = names[identifier]
            if action == "play_pause":
                with self.plugin._playback_lock:
                    action = "play" if self.plugin._playback_state.get("status") == "paused" else "pause"
            def execute():
                try:
                    if not self._stop.is_set() and self.active():
                        log.info("Native media key: %s", action)
                        self.plugin._playback_control_sync(action)
                except Exception:
                    if not self._stop.is_set():
                        log.exception("Native media key command failed: %s", action)
                finally:
                    with self._lock:
                        self._pending = False
            self._worker = threading.Thread(target=execute, name="music-media-command", daemon=True)
            self._worker.start()
        return True

    def _run(self):
        registered = set()
        retry_at = 0.0
        api = None
        try:
            factory = self._api_factory
            if factory is None:
                from .windows_media_input import WindowsMediaInput
                factory = WindowsMediaInput
            api = factory()
            while not self._stop.is_set():
                active = self.active()
                with self.plugin._playback_lock:
                    standard = active and self.plugin._playback_state.get("service") != "spotify"
                with self._lock:
                    capture = self._capture
                    if capture and time.monotonic() >= capture["deadline"]:
                        self._capture = None
                        capture = None
                    token = capture["session"] if capture and "result" not in capture else None
                    bindings = dict(self.bindings)
                if hasattr(api, "configure"):
                    api.configure(bindings, token, standard)
                if hasattr(api, "set_active"):
                    api.set_active(active or bool(token))
                if not standard:
                    for identifier in registered:
                        api.unregister(identifier)
                    registered.clear()
                    retry_at = 0.0
                elif time.monotonic() >= retry_at:
                    for identifier, (key, _) in ACTIONS.items():
                        if identifier not in registered and api.register(identifier, key):
                            registered.add(identifier)
                            log.info("Native media key registered: %s", ACTIONS[identifier][1])
                    retry_at = time.monotonic() + 5
                for identifier in api.messages():
                    if active:
                        self.dispatch(identifier)
                if hasattr(api, "captures"):
                    for session, value in api.captures():
                        with self._lock:
                            if self._capture and self._capture["session"] == session and "result" not in self._capture:
                                self._capture["result"] = value
                if hasattr(api, "wait_for_messages"):
                    api.wait_for_messages(50)
                else:
                    self._stop.wait(.05)
        except Exception:
            self._listener_error = "failed"
            log.exception("Native media key listener failed")
        finally:
            if api:
                for identifier in registered:
                    api.unregister(identifier)
                if hasattr(api, "close"):
                    api.close()

    def close(self):
        self._stop.set()
        with self._lock:
            self._capture = None
        if self._thread:
            self._thread.join(timeout=1)
        if self._worker:
            self._worker.join(timeout=.5)
