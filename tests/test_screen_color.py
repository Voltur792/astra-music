from types import SimpleNamespace

from src.screen_color import ScreenColorPicker
import src.screen_color as picker_module


class Worker:
    def __init__(self, output=b'{"color":"#AbC123"}', returncode=None):
        self.output = output
        self.returncode = returncode
        self.killed = False

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -1

    def communicate(self):
        return self.output, b""


def test_picker_returns_immediately_and_only_owner_can_poll_or_cancel(monkeypatch):
    worker = Worker()
    monkeypatch.setattr(picker_module, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(picker_module.subprocess, "Popen", lambda *args, **kwargs: worker)
    monkeypatch.setattr(picker_module.subprocess, "CREATE_NO_WINDOW", 0, raising=False)
    picker = ScreenColorPicker()
    started = picker.request()
    assert started["pending"]
    assert picker.request("cancel", "wrong") == {"cancelled": True}
    assert not worker.killed
    assert picker.request("poll", started["session"])["pending"]
    worker.returncode = 0
    assert picker.request("poll", started["session"]) == {"color": "#abc123"}
    assert picker.request("poll", started["session"]) == {"cancelled": True}


def test_timeout_and_shutdown_reap_worker(monkeypatch):
    monkeypatch.setattr(picker_module, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(picker_module.subprocess, "CREATE_NO_WINDOW", 0, raising=False)
    worker = Worker()
    monkeypatch.setattr(picker_module.subprocess, "Popen", lambda *args, **kwargs: worker)
    picker = ScreenColorPicker()
    started = picker.request()
    picker._started -= 66
    assert picker.request("poll", started["session"]) == {"cancelled": True}
    assert worker.killed
    worker = Worker()
    picker.request()
    picker.close()
    assert worker.killed


def test_unsupported_platform_does_not_launch_process(monkeypatch):
    monkeypatch.setattr(picker_module, "sys", SimpleNamespace(platform="linux"))
    assert "error" in ScreenColorPicker().request()


def test_palette_launches_with_current_color_and_rejects_invalid_arguments(monkeypatch):
    calls = []
    worker = Worker()
    monkeypatch.setattr(picker_module, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(picker_module.subprocess, "CREATE_NO_WINDOW", 0, raising=False)
    def launch(args, **kwargs):
        calls.append(args)
        return worker
    monkeypatch.setattr(picker_module.subprocess, "Popen", launch)
    picker = ScreenColorPicker()
    assert "error" in picker.request(mode="unknown")
    assert "error" in picker.request(mode="palette", initial="red;run")
    assert not calls
    assert picker.request(mode="palette", initial="#aAbBcC")["pending"]
    assert calls[0][-4:] == ["-Mode", "palette", "-InitialColor", "#aAbBcC"]
    picker.close()
