"""User-initiated screen picker, isolated from the daemon's UI call deadline."""
import json
from pathlib import Path
import secrets
import subprocess
import sys
import time
import threading


class ScreenColorPicker:
    def __init__(self):
        self._lock = threading.Lock()
        self._process = None
        self._session = ""
        self._started = 0.0

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
                try:
                    self._process = subprocess.Popen(
                        ["powershell.exe", "-NoProfile", "-NonInteractive", "-STA", "-WindowStyle", "Hidden",
                         "-File", str(Path(__file__).with_name("screen-color.ps1")), "-Mode", mode, "-InitialColor", initial],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                except OSError:
                    self._close()
                    return {"error": "Не удалось запустить пипетку. Используйте палитру цвета."}
            elif not session or session != self._session:
                return {"cancelled": True}
            if action == "cancel" or time.monotonic() - self._started > 65:
                self._close()
                return {"cancelled": True}
            if self._process.poll() is None:
                return {"pending": True, "session": self._session}
            output, _ = self._process.communicate()
            code = self._process.returncode
            self._process = None
            self._session = ""
            try:
                result = json.loads(output.decode("utf-8-sig"))
                color = result.get("color", "")
                if isinstance(color, str) and len(color) == 7 and color[0] == "#" and all(c in "0123456789abcdefABCDEF" for c in color[1:]):
                    return {"color": color.lower()}
                if result.get("cancelled"):
                    return {"cancelled": True}
            except (ValueError, AttributeError):
                pass
            return {"error": "Пипетка недоступна. Используйте палитру цвета."} if code or output else {"cancelled": True}
