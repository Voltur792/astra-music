import asyncio
from pathlib import Path
from types import SimpleNamespace

from src.plugin import AstraMusic
from src.queue_window import QueueWindow, popup_position


def test_popup_opens_below_widget_without_resizing_it():
    screen = SimpleNamespace(x=0, y=0, width=1920, height=1080)
    assert popup_position({"x": 200, "y": 100, "height": 160}, [screen]) == (200, 266, 420)
    assert popup_position({"x": 1800, "y": 850, "height": 160}, [screen]) == (1560, 424, 420)


def test_popup_recovers_negative_and_disconnected_monitors():
    screens = [SimpleNamespace(x=0, y=0, width=1920, height=1080),
               SimpleNamespace(x=-1280, y=0, width=1280, height=720)]
    assert popup_position({"x": -1000, "y": 50, "height": 160}, screens) == (-1000, 216, 420)
    x, y, height = popup_position({"x": -5000, "y": 5000}, screens[:1])
    assert 0 <= x <= 1560 and 0 <= y <= 1080 - height


def test_repeat_click_closes_single_popup_and_shutdown_cleans_up(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTRA_MUSIC_DATA_DIR", str(tmp_path))
    music = AstraMusic()
    monkeypatch.setattr(music._audio_host, "ensure_server", lambda: "http://127.0.0.1:1234/token")
    calls = []
    class Process:
        stopped = False
        def __init__(self, args, **kwargs):
            calls.append(args)
        def poll(self):
            return 0 if self.stopped else None
        def terminate(self):
            self.stopped = True
        def wait(self, timeout):
            return 0
    monkeypatch.setattr("src.queue_window.subprocess.Popen", Process)
    manager = QueueWindow(music)
    assert manager.toggle({"x": 100, "y": 100}) == {"opened": True}
    assert calls[0][1:3] == ["-m", "src.queue_window"]
    assert manager.toggle({}) == {"opened": False}
    assert len(calls) == 1 and manager.process is None
    manager.toggle({})
    manager.close()
    assert manager.process is None and manager.log_file is None


def test_window_api_sanitizes_coordinates(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTRA_MUSIC_DATA_DIR", str(tmp_path))
    music = AstraMusic()
    anchors = []
    music._queue_window = SimpleNamespace(toggle=lambda anchor: anchors.append(anchor) or {"opened": True})
    assert asyncio.run(music.ui_queue_window(x=float("nan"), y="x", height=160, on_top="true"))["opened"]
    assert anchors == [{"x": 24, "y": 24, "height": 160, "on_top": False}]


def test_widget_contains_no_inline_queue_and_popup_has_no_audio():
    root = Path(__file__).resolve().parents[1] / "ui"
    for name in ("desktop-widget.html", "player-remote.html", "player-v12.html"):
        assert 'id="queuePanel"' not in (root / name).read_text(encoding="utf-8")
    assert "<audio" not in (root / "queue-window.html").read_text(encoding="utf-8")
