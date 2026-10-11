"""A bounded PCM lease for the owned Spotify tab, with exclusive local output."""
import queue
import secrets
import threading


class SpotifyCapture:
    def __init__(self, player):
        self.player = player
        self.lease = secrets.token_hex(16)
        self.frames = queue.Queue(maxsize=32)
        self.buffered = threading.Event()
        self.closed = threading.Event()
        self.started = False

    def feed(self, data):
        if self.closed.is_set():
            return
        try:
            self.frames.put_nowait(data)
            if self.frames.qsize() >= 4:
                self.buffered.set()
        except queue.Full:
            # Keep latency bounded when the Discord decoder is slow.
            try:
                self.frames.get_nowait()
            except queue.Empty:
                pass
            try:
                self.frames.put_nowait(data)
            except queue.Full:
                pass

    def read(self):
        if self.closed.is_set():
            return b""
        self.buffered.wait(.25)
        try:
            return self.frames.get(timeout=.1)
        except queue.Empty:
            return bytes(19200)  # Preserve the PCM clock through a short page transition.

    def close(self):
        self.closed.set()
        self.buffered.set()
        with self.player.lock:
            if self.player.audio is self:
                self.player.audio = None
        try:
            self.player.request("capture-stop", lease=self.lease, value=self.player.plugin._playback_volume)
        except RuntimeError:
            pass  # A closed browser cannot keep playing local audio.


def start_capture(plugin):
    player = plugin._spotify
    if not player.active or not player.hidden or not player.snapshot()["running"]:
        raise RuntimeError("Сначала подключите Spotify в Astra и включите режим без окна браузера.")
    capture = SpotifyCapture(player)
    with player.lock:
        previous, player.audio = player.audio, capture
    if previous:
        previous.close()
    try:
        reply = player.request("capture-start", lease=capture.lease)
        if reply.get("local_suppressed") is not True:
            raise RuntimeError("Браузер не подтвердил отключение локального звучания Spotify.")
        capture.started = True
        return capture
    except Exception:
        capture.close()
        raise


def stop_capture(capture):
    capture.close()
