import asyncio

import pytest

from src.plugin import AstraMusic, MusicError


@pytest.fixture
def music(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTRA_MUSIC_DATA_DIR", str(tmp_path))
    plugin = AstraMusic()
    plugin._playback_state.update(service="yandex", revision=9, track_id="a", queue_index=0)
    plugin._playback_queue = [
        {"track_id": "a", "title": "Первый", "url": "private", "extra": {"token": "secret"}},
        {"track_id": "b", "title": "Следующий", "artist": "Исполнитель"},
    ]
    plugin._playback_queue_index = 0
    return plugin


def test_queue_returns_ordered_metadata_without_credentials(music):
    result = asyncio.run(music.ui_queue())
    assert [item["track_id"] for item in result["items"]] == ["a", "b"]
    assert result["current_index"] == 0 and result["revision"] == 9
    assert "url" not in result["items"][0] and "extra" not in result["items"][0]


@pytest.mark.parametrize("service", ["yandex", "vk"])
def test_queue_selects_requested_track_in_existing_playlist(music, monkeypatch, service):
    music._playback_state["service"] = service
    calls = []
    def activate(**kwargs):
        calls.append((music._playback_queue_index, kwargs))
        return {"success": True}
    monkeypatch.setattr(music, f"_activate_{service}_queue_item_sync", activate)
    assert asyncio.run(music.ui_queue_play(index=1, revision=9))["success"]
    assert calls == [(1, {"auto_play": True})]
    assert len(music._playback_queue) == 2


@pytest.mark.parametrize("index,revision", [(1, 8), (-1, 9), (3, 9), (True, 9), ("1", 9)])
def test_queue_rejects_stale_or_invalid_selection(music, index, revision):
    result = asyncio.run(music.ui_queue_play(index=index, revision=revision))
    assert result["error"]
    assert music._playback_queue_index == 0


def test_queue_failure_preserves_current_index(music, monkeypatch):
    def fail(**kwargs):
        raise MusicError("Нет потока")
    monkeypatch.setattr(music, "_activate_yandex_queue_item_sync", fail)
    assert asyncio.run(music.ui_queue_play(index=1, revision=9))["error"] == "Нет потока"
    assert music._playback_queue_index == 0


def test_spotify_does_not_expose_previous_service_queue(music):
    music._playback_state["service"] = "spotify"
    result = asyncio.run(music.ui_queue())
    assert result["items"] == [] and "Spotify" in result["message"]


def test_large_queue_is_paginated(music):
    music._playback_queue = [{"track_id": str(i)} for i in range(250)]
    result = asyncio.run(music.ui_queue(offset=100))
    assert result["total"] == 250 and len(result["items"]) == 100
    assert result["items"][0]["index"] == 100
