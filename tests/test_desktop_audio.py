import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.request import Request, build_opener, ProxyHandler

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.desktop_widget import normalize_settings, visible_position, DesktopWidget
from src.plugin import AstraMusic
from src.system_audio import SystemAudio


def make_plugin(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTRA_MUSIC_DATA_DIR", str(tmp_path))
    return AstraMusic()


def test_desktop_settings_and_position_preserve_accounts_and_playback(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    plugin._write_settings({"vk": {"token": "test-account"}, "visualizer": {"style": "mesh"}})
    widget = plugin._desktop_widget
    widget.available = True
    monkeypatch.setattr(widget, "ensure", lambda: None)
    plugin._playback_state.update(status="playing", revision=9)
    result = asyncio.run(plugin.ui_desktop_set(settings={"enabled": True, "on_top": True}))
    assert result["settings"]["enabled"] and result["settings"]["on_top"]
    widget.session = "current"
    assert widget.report("old", x=99, y=99, closed=True)["success"] is False
    widget.report("current", x=-900, y=120)
    restarted = AstraMusic()
    assert restarted._desktop_widget.settings == {"enabled": True, "on_top": True, "transparency": 0, "x": -900, "y": 120}
    stored = plugin._read_settings()
    assert stored["vk"]["token"] == "test-account"
    assert stored["visualizer"]["style"] == "mesh"
    widget.report("current", x=-900, y=120, closed=True)
    assert not widget.snapshot()["settings"]["enabled"]
    assert plugin._playback_state["status"] == "playing"
    assert plugin._playback_state["revision"] == 9


def test_desktop_reset_recovers_removed_monitor_and_allows_negative_positions(tmp_path, monkeypatch):
    primary = SimpleNamespace(x=0, y=0, width=1920, height=1080)
    secondary = SimpleNamespace(x=-1920, y=0, width=1920, height=1080)
    assert visible_position({"x": -900, "y": 100}, [primary, secondary]) == (-900, 100)
    assert visible_position({"x": -900, "y": 100}, [primary]) == (1536, 24)
    assert normalize_settings({"enabled": "true", "x": True, "y": 1e20}) == {"enabled": False, "on_top": False, "transparency": 0, "x": None, "y": None}
    plugin = make_plugin(tmp_path, monkeypatch)
    widget = plugin._desktop_widget
    widget.available = True
    widget._save({"x": -900, "y": 100})
    result = widget.set({}, reset_position=True)
    assert result["settings"]["x"] is None and result["position_revision"] == 1


def test_desktop_server_uses_shared_state_without_spawning_audio(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    plugin._playback_state.update(title="Shared track", artist="Artist", track_id="7", status="paused")
    plugin._visualizer_settings["mode"] = "off"
    base = plugin._audio_host.ensure_server()
    transport = build_opener(ProxyHandler({}))
    try:
        with transport.open(base + "/desktop-widget.html") as response:
            html = response.read().decode()
        assert 'src="native-bridge.js"' in html and "pywebview-drag-region" in html
        assert plugin._audio_host.process is None
        for method in ("music_playback_status", "music_visualizer_state", "music_desktop_get"):
            request = Request(base + "/api", json.dumps({"method": method}).encode(), {"Content-Type": "application/json"})
            with transport.open(request) as response:
                result = json.load(response)
            if method == "music_playback_status":
                assert result["title"] == "Shared track"
        assert plugin._audio_host.process is None
    finally:
        plugin._audio_host.close()


def test_audio_page_heartbeat_reaches_host_without_starting_another_player(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    host = plugin._audio_host
    host.recovery_attempts = 1
    base = host.ensure_server()
    request = Request(base + "/api", json.dumps({"method": "music_audio_host_ping"}).encode(),
                      {"Content-Type": "application/json"})
    try:
        with build_opener(ProxyHandler({})).open(request) as response:
            assert json.load(response) == {"ok": True}
        assert host.last_page_ping > 0 and host.recovery_attempts == 0
        assert host.process is None
    finally:
        host.close()


def test_player_assets_survive_concurrent_remote_requests(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    plugin = make_plugin(tmp_path, monkeypatch)
    host = plugin._audio_host
    base = host.ensure_server()
    barrier = threading.Barrier(32)

    def read_asset(_):
        barrier.wait(timeout=5)
        with build_opener(ProxyHandler({})).open(base + "/player-v12.js", timeout=5) as response:
            body = response.read()
            assert int(response.headers["Content-Length"]) == len(body)
            assert b"MUSIC_PLAYER_READY = true" in body
            return response.status

    try:
        with ThreadPoolExecutor(max_workers=32) as pool:
            assert list(pool.map(read_asset, range(32))) == [200] * 32
        assert host.process is None
    finally:
        host.close()


def test_on_top_change_reopens_only_remote_window(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    plugin._playback_state.update(status="playing", revision=12)
    widget = plugin._desktop_widget
    widget.available = True
    widget._save({"enabled": True, "on_top": False, "x": 100, "y": 200})
    calls = []
    monkeypatch.setattr(widget, "_stop_process", lambda: calls.append("close remote"))
    monkeypatch.setattr(widget, "ensure", lambda: calls.append("open remote"))
    widget.set({"on_top": True})
    assert calls == ["close remote", "open remote"]
    assert widget.settings == {"enabled": True, "on_top": True, "transparency": 0, "x": 100, "y": 200}
    assert plugin._playback_state["status"] == "playing" and plugin._playback_state["revision"] == 12


def test_transparency_is_saved_without_restarting_or_changing_playback(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    widget = plugin._desktop_widget
    widget.available = True
    widget._save({"enabled": True})
    monkeypatch.setattr(widget, "ensure", lambda: None)
    def must_not_restart():
        raise AssertionError("Transparency must not recreate the remote window")
    monkeypatch.setattr(widget, "_stop_process", must_not_restart)
    plugin._playback_state.update(status="playing", revision=12)
    widget.set({"transparency": 55})
    assert AstraMusic()._desktop_widget.settings["transparency"] == 55
    assert plugin._playback_state["status"] == "playing" and plugin._playback_state["revision"] == 12
    for invalid in (-1, 81, True, "50", float("nan"), float("inf")):
        assert normalize_settings({"transparency": invalid})["transparency"] == 0
    assert normalize_settings({"transparency": 80})["transparency"] == 80


def test_loopback_reconnects_in_same_worker_and_clears_transient_error(monkeypatch):
    capture = SystemAudio()
    capture.active.set()
    attempts = []
    waits = []
    def once(sc, np):
        attempts.append(True)
        if len(attempts) <= 2:
            raise RuntimeError("Error 0x88890004")
        capture.bands = [.2] * 48
        capture.sample_at = time.monotonic()
        capture.error = ""
        capture.stop.set()
    monkeypatch.setattr(capture, "_capture_once", once)
    monkeypatch.setattr(capture.stop, "wait", lambda delay: waits.append(delay) or False)
    capture._run(None, None)
    assert len(attempts) == 3 and waits == [.5, 1]
    assert capture.snapshot() == [.2] * 48 and capture.error == ""
    capture.pause()
    assert capture.snapshot() == [] and capture.error == ""


def test_each_replacement_worker_initializes_windows_com_even_when_soundcard_cached(monkeypatch):
    calls = []
    ole = SimpleNamespace(CoInitializeEx=lambda *_: calls.append("init"), CoUninitialize=lambda: calls.append("uninit"))
    import ctypes
    monkeypatch.setattr(ctypes, "OleDLL", lambda _: ole, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "soundcard", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "numpy", SimpleNamespace())
    for _ in range(2):
        capture = SystemAudio()
        monkeypatch.setattr(capture, "_run", lambda *_: calls.append("capture"))
        capture._capture()
    assert calls == ["init", "capture", "uninit"] * 2


def test_soundcard_first_import_precedes_worker_com_initialization(monkeypatch):
    import builtins
    import ctypes
    original_import = builtins.__import__
    calls = []
    initialized = False

    def initialize(*_):
        nonlocal initialized
        initialized = True
        calls.append("init")

    def uninitialize():
        nonlocal initialized
        initialized = False
        calls.append("uninit")

    def importing(name, *args, **kwargs):
        if name == "soundcard":
            # SoundCard 0.4.x rejects S_FALSE when its import initializes COM
            # after the caller has already initialized the same worker.
            if initialized:
                raise RuntimeError("Error 0x100000001")
            calls.append("import")
            return SimpleNamespace()
        return original_import(name, *args, **kwargs)

    ole = SimpleNamespace(CoInitializeEx=initialize, CoUninitialize=uninitialize)
    monkeypatch.setattr(ctypes, "OleDLL", lambda _: ole, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "numpy", SimpleNamespace())
    monkeypatch.setattr(builtins, "__import__", importing)
    capture = SystemAudio()
    monkeypatch.setattr(capture, "_run", lambda *_: calls.append("capture"))
    capture._capture()
    assert calls == ["import", "init", "capture", "uninit"]
    assert capture.error == ""
