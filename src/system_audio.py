"""Read the default output's loopback spectrum; never open a microphone."""
import sys
import threading
import time


class SystemAudio:
    def __init__(self):
        self.bands = []
        self.sample_at = 0.0
        self.error = ""
        self.stop = threading.Event()
        self.active = threading.Event()
        self.thread = None
        self.last_start = 0.0

    def start(self):
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
        try:
            import numpy as np
            import soundcard as sc
            while not self.stop.is_set():
                if not self.active.wait(.2):
                    continue
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
                        mono = np.mean(data, axis=1)
                        spectrum = np.abs(np.fft.rfft(mono * np.hanning(len(mono)))) / len(mono)
                        edges = np.geomspace(1, len(spectrum) - 1, 49).astype(int)
                        self.bands = [float(np.clip(np.max(spectrum[lo:max(lo+1,hi)]) * 12, 0, 1)) for lo, hi in zip(edges[:-1], edges[1:])]
                        self.sample_at = time.monotonic()
                        self.error = ""
                        if time.monotonic() >= check_at:
                            if sc.default_speaker().id != speaker.id:
                                break
                            check_at = time.monotonic() + 2
        except Exception as exc:
            self.error = f"Системный звук недоступен: {type(exc).__name__}"
            self.bands = []

    def close(self):
        self.stop.set()

    def pause(self):
        self.active.clear()
        self.bands = []
