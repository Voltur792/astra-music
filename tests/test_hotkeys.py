import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from src.hotkeys import normalize, binding, label
from src.media_keys import MediaKeys
from src.windows_media_input import MediaInputEvents
from src.plugin import AstraMusic


def test_normalize_rejects_invalid_keys_and_duplicate_bindings():
    result = normalize({"next": {"vk": 117, "modifiers": 6}, "previous": {"vk": 117, "modifiers": 6},
                        "stop": {"vk": True}, "play_pause": {"vk": 27}})
    assert sum(bool(value) for value in result.values()) == 1
    assert label({"vk": 117, "modifiers": 6}) == "Ctrl + Shift + F6"
    for value in [{"vk": 16}, {"vk": 27}, {"vk": 300}, {"vk": 117, "modifiers": True}]:
        with pytest.raises(ValueError):
            binding(value)


def test_capture_consumes_chord_and_release_without_music_action():
    events = MediaInputEvents()
    events.active = True
    events.configure({}, "record-1")
    assert not events.key(0x100, 0x11)  # Ctrl is not a completed binding.
    assert events.key(0x100, 117, 6)
    assert events.key(0x100, 117, 6)  # Held repeat must be swallowed too.
    assert events.key(0x101, 117, 0)  # Modifiers may already be released.
    assert list(events.captured) == [("record-1", {"vk": 117, "modifiers": 6})]
    assert not events.events
    events.configure({"next": {"vk": 117, "modifiers": 6}}, None)
    assert not events.key(0x100, 117, 0)
    assert events.key(0x100, 117, 6)
    assert list(events.events) == [(0x5A10, "keyboard")]


def test_escape_and_driver_media_capture_preserve_existing_binding():
    events = MediaInputEvents()
    events.configure({}, "cancel")
    assert events.key(0x100, 27)
    assert list(events.captured) == [("cancel", None)]
    events.configure({}, "media")
    assert events.appcommand(14 << 16, "shell")
    assert list(events.captured)[-1] == ("media", {"vk": 0xB3, "modifiers": 0})
    assert not events.events


def test_bindings_save_restart_conflicts_and_reset_preserve_other_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTRA_MUSIC_DATA_DIR", str(tmp_path))
    plugin = AstraMusic()
    plugin._write_settings({"vk": {"token": "fake"}, "widget_theme": {"mode": "custom"}})
    keys = plugin._audio_host.media_keys
    monkeypatch.setattr(keys, "start", lambda: None)
    saved = keys.save("next", {"vk": 117, "modifiers": 6})
    assert saved["labels"]["next"] == "Ctrl + Shift + F6"
    assert AstraMusic()._audio_host.media_keys.bindings == keys.bindings
    with pytest.raises(ValueError, match="уже назначено"):
        keys.save("previous", {"vk": 117, "modifiers": 6})
    settings = json.loads(plugin.settings_file.read_text(encoding="utf-8"))
    assert settings["vk"] == {"token": "fake"} and settings["widget_theme"] == {"mode": "custom"}
    keys.save("next", None)
    assert keys.bindings["next"] is None


def test_capture_session_cancel_timeout_and_auto_save(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTRA_MUSIC_DATA_DIR", str(tmp_path))
    plugin = AstraMusic()
    keys = plugin._audio_host.media_keys
    monkeypatch.setattr(keys, "start", lambda: None)
    monkeypatch.setattr("src.media_keys.sys.platform", "win32")
    started = keys.capture_start("next")
    assert keys.capture_poll("wrong", True) == {"cancelled": True}
    assert keys.capture_poll(started["session"])["pending"]
    keys._capture["result"] = {"vk": 117, "modifiers": 0}
    assert keys.capture_poll(started["session"])["bindings"]["next"] == {"vk": 117, "modifiers": 0}
    started = keys.capture_start("next")
    keys._capture["result"] = None
    assert keys.capture_poll(started["session"]) == {"cancelled": True}
    assert keys.bindings["next"]["vk"] == 117
    started = keys.capture_start("next")
    keys._capture["deadline"] = 0
    assert keys.capture_poll(started["session"]) == {"cancelled": True}


def test_custom_toggle_resumes_paused_track_and_recording_never_leaves_pending():
    calls = []
    plugin = SimpleNamespace(_playback_lock=threading.RLock(), _playback_state={"service": "vk", "track_id": "1", "url": "http://test", "status": "paused"},
                             _playback_control_sync=calls.append)
    keys = MediaKeys(plugin)
    keys.bindings["play_pause"] = {"vk": 117, "modifiers": 0}
    try:
        keys._capture = {"session": "busy"}
        assert not keys.dispatch(0x5A12)
        assert not keys._pending
        keys._capture = None
        assert keys.dispatch(0x5A12)
        keys._worker.join(1)
        assert calls == ["play"]
    finally:
        keys.close()


def test_ui_hotkeys_rejects_unknown_operations(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTRA_MUSIC_DATA_DIR", str(tmp_path))
    plugin = AstraMusic()
    assert "bindings" in asyncio.run(plugin.ui_hotkeys())
    assert "error" in asyncio.run(plugin.ui_hotkeys(operation="unknown"))


def test_spotify_custom_keys_use_its_existing_browser_controls_without_stream_url(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTRA_MUSIC_DATA_DIR", str(tmp_path))
    plugin = AstraMusic()
    plugin._spotify.active = True
    plugin._playback_state.update(service="spotify", track_id="test", url="", status="playing")
    calls = []
    monkeypatch.setattr(plugin._spotify, "request", lambda action, *args, **kwargs: calls.append(action))
    keys = plugin._audio_host.media_keys
    keys.bindings["next"] = {"vk": 117, "modifiers": 0}
    try:
        for identifier in [0x5A10, 0x5A11, 0x5A12, 0x5A13]:
            assert keys.dispatch(identifier)
            keys._worker.join(1)
        plugin._playback_state["status"] = "paused"
        assert keys.dispatch(0x5A12)
        keys._worker.join(1)
        assert calls == ["next", "previous", "pause", "pause", "play"]
        plugin._spotify.active = False
        assert not keys.active()
    finally:
        keys.close()


def test_spotify_leaves_standard_media_controls_to_its_browser():
    events = MediaInputEvents()
    events.active = True
    events.configure({"next": {"vk": 117, "modifiers": 0}}, None, standard_enabled=False)
    assert not events.key(0x100, 0xB0)
    assert not events.appcommand(11 << 16, "shell")
    assert events.key(0x100, 117)
    assert list(events.events) == [(0x5A10, "keyboard")]


def test_spotify_state_restores_listener_for_saved_custom_bindings(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTRA_MUSIC_DATA_DIR", str(tmp_path))
    plugin = AstraMusic()
    keys = plugin._audio_host.media_keys
    keys.bindings["next"] = {"vk": 117, "modifiers": 0}
    calls = []
    monkeypatch.setattr(keys, "start", lambda: calls.append("start"))
    plugin._spotify.active = True
    plugin._playback_state["service"] = "spotify"
    plugin._spotify._publish({"track_id": "test", "title": "Test", "logged_in": True, "status": "playing"})
    assert calls == ["start"]
