import queue
import threading
import time
from types import SimpleNamespace

from src.media_keys import MediaKeys


def wait_for(predicate):
    end = time.monotonic() + 2
    while not predicate():
        assert time.monotonic() < end, "media key worker timed out"
        time.sleep(.01)


def fake_plugin(control):
    return SimpleNamespace(_playback_lock=threading.RLock(), _playback_control_sync=control,
                           _playback_state={"service": "vk", "url": "http://local/song", "track_id": "2", "status": "playing"})


def test_listener_releases_keys_on_pause_and_shutdown_and_dispatches_once():
    calls = []
    plugin = fake_plugin(calls.append)
    class Api:
        def __init__(self):
            self.registered = set()
            self.events = queue.Queue()
        def register(self, identifier, key):
            self.registered.add(identifier)
            return True
        def unregister(self, identifier):
            self.registered.remove(identifier)
        def messages(self):
            while not self.events.empty():
                yield self.events.get_nowait()
    api = Api()
    listener = MediaKeys(plugin, lambda: api)
    thread = threading.Thread(target=listener._run)
    thread.start()
    try:
        wait_for(lambda: len(api.registered) == 2)
        api.events.put(0x5A10)
        wait_for(lambda: calls == ["next"])
        plugin._playback_state["status"] = "paused"
        wait_for(lambda: not api.registered)
        api.events.put(0x5A11)
        time.sleep(.1)
        assert calls == ["next"]
        plugin._playback_state["status"] = "playing"
        wait_for(lambda: len(api.registered) == 2)
    finally:
        listener.close()
        thread.join(1)
    assert not thread.is_alive() and not api.registered


def test_slow_command_does_not_allow_duplicate_switches_or_capture_other_players():
    entered, release = threading.Event(), threading.Event()
    calls = []
    def control(action):
        calls.append(action)
        entered.set()
        release.wait(2)
    plugin = fake_plugin(control)
    listener = MediaKeys(plugin)
    try:
        assert listener.dispatch(0x5A10)
        assert entered.wait(1)
        assert not listener.dispatch(0x5A11)
        release.set()
        wait_for(lambda: not listener._pending)
        assert calls == ["next"]
        for service, status in [("spotify", "playing"), ("vk", "paused"), ("yandex", "stopped"), ("vk", "failed")]:
            plugin._playback_state.update(service=service, status=status)
            assert not listener.dispatch(0x5A11)
        assert not listener.dispatch(123)
    finally:
        release.set()
        listener.close()


def test_native_actions_switch_the_real_vk_queue(tmp_path, monkeypatch):
    from src.plugin import AstraMusic
    monkeypatch.setenv("ASTRA_MUSIC_DATA_DIR", str(tmp_path))
    plugin = AstraMusic()
    plugin._playback_queue = [{"track_id": str(i)} for i in range(3)]
    plugin._playback_queue_index = 1
    plugin._playback_source = "playlist"
    plugin._playback_state.update(service="vk", track_id="1", url="http://local/song", status="playing")
    monkeypatch.setattr(plugin, "_activate_vk_queue_item_sync", lambda **kw: {"success": True})
    listener = MediaKeys(plugin)
    try:
        assert listener.dispatch(0x5A10)
        wait_for(lambda: not listener._pending)
        assert plugin._playback_queue_index == 2
        assert listener.dispatch(0x5A11)
        wait_for(lambda: not listener._pending)
        assert plugin._playback_queue_index == 1
    finally:
        listener.close()


def test_keyboard_decoder_consumes_only_track_keys_and_ignores_held_repeat():
    from src.windows_media_input import MediaInputEvents
    events = MediaInputEvents()
    assert not events.key(0x100, 0xB0)
    events.active = True
    for key in [0x41, 0xB2, 0xB3, 0xAF]:
        assert not events.key(0x100, key)
    assert events.key(0x100, 0xB0)
    assert events.key(0x100, 0xB0)
    assert events.key(0x101, 0xB0)
    assert not events.key(0x101, 0xB0)
    assert list(events.events) == [(0x5A10, "keyboard")]
    assert events.key(0x104, 0xB1)
    assert events.key(0x105, 0xB1)
    assert list(events.events)[-1] == (0x5A11, "keyboard")


def test_driver_commands_strip_device_flags_and_deduplicate_routes(monkeypatch):
    from src.windows_media_input import MediaInputEvents
    import src.windows_media_input as module
    now = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    events = MediaInputEvents()
    assert not events.appcommand(11 << 16, "shell")
    events.active = True
    assert events.appcommand((11 | 0x8000) << 16, "shell")
    assert events.appcommand((11 | 0x1000) << 16, "appcommand")
    assert list(events.events) == [(0x5A10, "shell")]
    now[0] += .2
    assert events.appcommand(11 << 16, "shell")
    assert events.appcommand(12 << 16, "appcommand")
    assert not events.appcommand(14 << 16, "appcommand")
    assert list(events.events)[1:] == [(0x5A10, "shell"), (0x5A11, "appcommand")]


def test_driver_route_works_without_hotkey_registration_and_closes_on_shutdown():
    calls = []
    class Api:
        active = False
        closed = False
        sent = False
        def set_active(self, active):
            self.active = active
        def register(self, *args):
            return False
        def unregister(self, *args):
            raise AssertionError("A failed registration must not be unregistered")
        def messages(self):
            if not self.sent:
                self.sent = True
                yield 0x5A10
        def close(self):
            self.active = False
            self.closed = True
    api = Api()
    listener = MediaKeys(fake_plugin(calls.append), lambda: api)
    # Abandoned UI recording must expire without a final poll and unblock music.
    listener._capture = {"session": "lost-ui", "deadline": 0, "action": "next"}
    thread = threading.Thread(target=listener._run)
    thread.start()
    try:
        wait_for(lambda: calls == ["next"])
    finally:
        listener.close()
        thread.join(1)
    assert api.closed and not api.active and listener._capture is None
