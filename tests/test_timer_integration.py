import asyncio
import pytest
from src.plugin import AstraMusic


@pytest.fixture
def music(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTRA_MUSIC_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ASTRA_MUSIC_NATIVE_PLAYER", "0")
    return AstraMusic()


def test_timer_current_does_not_return_stream_or_credentials(music, monkeypatch):
    async def state():
        return {"revision": 3, "track_id": "42", "service": "yandex", "title": "Песня", "url": "private-stream", "token": "secret"}
    monkeypatch.setattr(music, "ui_playback_state", state)
    result = asyncio.run(music._timer_music_current())
    assert result["track_id"] == "42"
    assert "url" not in result
    assert "token" not in result


def test_timer_play_uses_native_player_and_requires_confirmation(music, monkeypatch):
    calls = []
    async def start(**params):
        calls.append(params)
        return {"playback_requested": True, "revision": 4}
    async def confirm(result):
        calls.append("confirmed")
        return {"playing": True}
    async def current():
        return {"track_id": "42", "revision": 4, "status": "playing"}
    monkeypatch.setattr(music, "ui_yandex_start", start)
    monkeypatch.setattr(music, "_wait_for_playback_start", confirm)
    monkeypatch.setattr(music, "_timer_music_current", current)
    result = asyncio.run(music._timer_music_play("yandex", "42"))
    assert result["status"] == "playing"
    assert calls[0]["auto_play"] is True
    assert calls[1] == "confirmed"
    async def blocked(result):
        return {"error": "Автозапуск заблокирован"}
    monkeypatch.setattr(music, "_wait_for_playback_start", blocked)
    assert "заблокирован" in asyncio.run(music._timer_music_play("yandex", "42"))["error"]


def test_alarm_stop_does_not_stop_user_replacement_track(music, monkeypatch):
    calls = []
    async def state():
        return {"revision": 9}
    async def stop():
        calls.append("stop")
        return {"ok": True}
    monkeypatch.setattr(music, "ui_playback_state", state)
    monkeypatch.setattr(music, "ui_playback_stop", stop)
    assert asyncio.run(music._timer_music_stop(8))["changed"]
    assert not calls
    asyncio.run(music._timer_music_stop(9))
    assert calls == ["stop"]


def test_vk_current_retains_restore_metadata_for_alarm(music, monkeypatch):
    async def state():
        return {"service": "vk", "track_id": "42", "revision": 3, "status": "playing"}
    monkeypatch.setattr(music, "ui_playback_state", state)
    monkeypatch.setattr(music, "_read_playback_selection", lambda: {
        "service": "vk", "track_id": "42", "extra": {"owner_id": "5", "reload_id": "5_42"}})
    result = asyncio.run(music._timer_music_current())
    assert result["extra"] == {"owner_id": "5", "reload_id": "5_42"}
    monkeypatch.setattr(music, "_read_playback_selection", lambda: {
        "service": "vk", "track_id": "77", "extra": {"owner_id": "other"}})
    assert not asyncio.run(music._timer_music_current()).get("extra")


def test_vk_alarm_uses_metadata_recovery_path(music, monkeypatch):
    calls = []
    async def guarded(fn, args):
        assert fn == music._play_sync
        calls.append(args)
        return {"revision": 4}
    async def confirm(result):
        return result
    async def current():
        return {"revision": 4, "status": "playing"}
    monkeypatch.setattr(music, "_guarded", guarded)
    monkeypatch.setattr(music, "_wait_for_playback_start", confirm)
    monkeypatch.setattr(music, "_timer_music_current", current)
    assert asyncio.run(music._timer_music_play("vk", "42", "Песня", "Исполнитель"))["status"] == "playing"
    assert calls == [{"service": "vk", "track_id": "42", "title": "Песня", "artist": "Исполнитель", "extra": {}}]
