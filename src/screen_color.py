"""User-initiated screen picker, isolated from the daemon's UI call deadline."""
import json
import logging
from pathlib import Path
import secrets
import subprocess
import sys
import time
import threading

logger = logging.getLogger("astra-music.colors")


class ScreenColorPicker:
    def __init__(self):
        self._lock = threading.Lock()
        self._process = None
        self._session = ""
        self._started = 0.0
        self._mode = "screen"

    def close(self):
        with self._lock:
            self._close()

    def _close(self):
        if self._process:
            if self._process.poll() is None:
                self._process.kill()
            self._process.communicate()
        self._process = None
        self._session = ""

    def request(self, action="start", session="", mode="screen", initial="#000000"):
        with self._lock:
            if action == "start":
                if mode not in ("screen", "palette"):
                    return {"error": "Неизвестный способ выбора цвета."}
                if not isinstance(initial, str) or len(initial) != 7 or initial[0] != "#" or any(c not in "0123456789abcdefABCDEF" for c in initial[1:]):
                    return {"error": "Некорректный исходный цвет."}
                if sys.platform != "win32":
                    return {"error": "Выбор с экрана доступен на Windows. Используйте палитру цвета."}
                self._close()
                self._session = secrets.token_urlsafe(24)
                self._started = time.monotonic()
                self._mode = mode
                try:
                    self._process = subprocess.Popen(
                        [sys.executable, str(Path(__file__).with_name("screen_color_native.py")),
                         "--mode", mode, "--initial", initial],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                except OSError as error:
                    logger.warning("Color picker launch failed: mode=%s code=%s", mode, error.winerror if hasattr(error, "winerror") else error.errno)
                    self._close()
                    return self._failure()
            elif not session or session != self._session:
                return {"cancelled": True}
            if action == "cancel" or time.monotonic() - self._started > 65:
                self._close()
                return {"cancelled": True}
            if self._process.poll() is None:
                return {"pending": True, "session": self._session}
            output, stderr = self._process.communicate()
            code = self._process.returncode
            self._process = None
            self._session = ""
            try:
                result = json.loads(output.decode("utf-8-sig"))
                if result.get("error"):
                    stages = {"unsupported_platform", "pixel_read", "monitor", "cursor", "desktop_dc", "window", "window_proc", "color_dialog", "mouse_hook", "timer", "message_loop", "native_runtime"}
                    stage = result.get("stage")
                    native_code = result.get("code")
                    logger.warning("Color picker failed: mode=%s stage=%s code=%s exit=%s", self._mode,
                                   stage if isinstance(stage, str) and stage in stages else "unknown",
                                   native_code if type(native_code) is int else 0, code)
                    return self._failure()
                color = result.get("color", "")
                if isinstance(color, str) and len(color) == 7 and color[0] == "#" and all(c in "0123456789abcdefABCDEF" for c in color[1:]):
                    return {"color": color.lower()}
                if result.get("cancelled"):
                    return {"cancelled": True}
            except (ValueError, AttributeError):
                pass
            logger.warning("Color picker invalid response: mode=%s exit=%s stdout_bytes=%s stderr_bytes=%s", self._mode, code, len(output), len(stderr))
            return self._failure()

    def _failure(self):
        label = "палитру" if self._mode == "palette" else "пипетку"
        return {"error": f"Не удалось открыть {label}. Подробности записаны в журнал Astra."}
