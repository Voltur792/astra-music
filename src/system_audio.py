"""Read the default output's loopback spectrum; never open a microphone."""
import logging
import sys
import threading
import time

log = logging.getLogger("astra-music.system-audio")


class SystemAudio:
    def __init__(self):
        self.bands = []
        self.sample_at = 0.0
        self.error = ""
        self.stop = threading.Event()
        self.active = threading.Event()
        self.thread = None
        self._start_lock = threading.Lock()
        self.last_start = 0.0

    def start(self):
        with self._start_lock:
            self.active.set()
            if self.thread and self.thread.is_alive():
                return
            if self.error and time.monotonic() - self.last_start < 5:
                return
            self.last_start = time.monotonic()
            self.stop.clear()
            self.thread = threading.Thread(target=self._capture, daemon=True, name="music-output-loopback")
            self.thread.start()

    def snapshot(self):
        return list(self.bands) if self.active.is_set() and time.monotonic() - self.sample_at < .8 else []

    def _capture(self):
        ole32 = None
        com_initialized = False
        try:
            import numpy as np
            # SoundCard initializes COM on its first importing thread and treats
            # S_FALSE (already initialized) as an error. Let that import happen
            # first; cached imports still need COM initialized on each worker.
            import soundcard as sc
            if sys.platform == "win32":
                import ctypes
                # SoundCard's import initializes only the first importing thread.
                # Each capture worker needs its own balanced COM initialization.
                ole32 = ctypes.OleDLL("ole32")
                ole32.CoInitializeEx(None, 0)
                com_initialized = True
            self._run(sc, np)
        except Exception as exc:
            log.exception("Output loopback worker unavailable")
            self.error = "Не удалось подключить захват звука. Проверьте устройство вывода и зависимости плагина."
            self.bands = []
        finally:
            if com_initialized:
                ole32.CoUninitialize()

    def _run(self, sc, np):
        failed_at = None
        delay = .5
        while not self.stop.is_set():
            if not self.active.wait(.2):
                failed_at = None
                delay = .5
                continue
            try:
                self._capture_once(sc, np)
                failed_at = None
                delay = .5
            except Exception as exc:
                self.bands = []
                now = time.monotonic()
                if failed_at is None or self.sample_at > failed_at:
                    failed_at = now
                    log.warning("Output loopback interrupted; reconnecting: %s: %s", type(exc).__name__, exc)
                if self.active.is_set() and now - failed_at >= 3:
                    self.error = "Захват звука временно недоступен — переподключаю устройство…"
                if self.stop.wait(delay):
                    break
                delay = min(5, delay * 2)

    def _capture_once(self, sc, np):
        speaker = sc.default_speaker()
        if speaker is None:
            raise RuntimeError("Устройство вывода звука не найдено")
        loopback = sc.get_microphone(id=speaker.id, include_loopback=True)
        # WASAPI single-channel mode produces invalid samples on some devices.
        channels = min(2, loopback.channels) if sys.platform == "win32" else None
        with loopback.recorder(samplerate=48000, channels=channels, blocksize=2048) as recorder:
            check_at = time.monotonic() + 2
            while not self.stop.is_set() and self.active.is_set():
                data = recorder.record(numframes=1024)
                if self.stop.is_set() or not self.active.is_set():
                    break
                mono = np.mean(data, axis=1)
                spectrum = np.abs(np.fft.rfft(mono * np.hanning(len(mono)))) / len(mono)
                edges = np.geomspace(1, len(spectrum) - 1, 49).astype(int)
                self.bands = [float(np.clip(np.max(spectrum[lo:max(lo+1, hi)]) * 12, 0, 1)) for lo, hi in zip(edges[:-1], edges[1:])]
                self.sample_at = time.monotonic()
                self.error = ""
                if time.monotonic() >= check_at:
                    current = sc.default_speaker()
                    if current is None or current.id != speaker.id:
                        break
                    check_at = time.monotonic() + 2

    def close(self):
        self.stop.set()
        self.pause()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=1)

    def pause(self):
        self.active.clear()
        self.bands = []
        self.error = ""
