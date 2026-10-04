import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from astra_plugin_sdk.testing import Harness

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.plugin import AstraMusic, MusicError, Track, YandexMusicClient


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTRA_MUSIC_DATA_DIR", str(tmp_path))
    music = AstraMusic()
    monkeypatch.setattr(music._audio_host, "ensure", lambda: None)
    return music


def test_voice_actions_return_readable_text_through_real_sdk(plugin):
    with Harness(plugin) as h:
        result = h.execute_action("music_volume_5")
        assert result.success
        assert result.result == "Громкость музыки установлена на 5 из 10."
        plugin._playback_state.update(track_id="51", title="Песня", status="playing")
        assert h.execute_action("music_pause").result == "Музыка на паузе."
        # Structured results are still available to the UI and model tools.
        assert h.call_tool("set_music_volume", level=4).json["value"] == 4


def test_vk_voice_action_reports_preparation_without_claiming_playback(plugin, monkeypatch):
    monkeypatch.setattr(plugin, "_start_vk_music_job", lambda: {"status": "loading", "message": "Загружаю ваши треки ВК."})
    with Harness(plugin) as h:
        assert h.execute_action("music_vk_my_music").result == "Загружаю ваши треки ВК."


def test_wave_continues_with_previous_track_not_batch_id(plugin, monkeypatch):
    client = YandexMusicClient("test-token")
    calls = []
    monkeypatch.setattr(client, "radio_feedback", lambda *a, **k: None)
    monkeypatch.setattr(client, "radio_batch", lambda station, queue: (calls.append((station, queue)) or [Track("yandex", "52", "Next", "Artist")], "batch-new"))
    monkeypatch.setattr(plugin, "_client", lambda service: client)
    plugin._radio_station = "user:onyourwave"
    plugin._radio_batch_id = "batch-old"
    plugin._playback_source = "radio"
    plugin._playback_queue = [{"track_id": "51", "radio_batch_id": "batch-old"}]
    plugin._playback_queue_index = 0
    plugin._playback_state.update(track_id="51", service="yandex")
    monkeypatch.setattr(plugin, "_activate_yandex_queue_item_sync", lambda **kw: {"success": True})
    assert plugin._advance_yandex_queue_sync(1)["success"]
    assert calls == [("user:onyourwave", "51")]
    assert plugin._playback_queue_index == 1
    assert plugin._playback_queue[1]["radio_batch_id"] == "batch-new"


@pytest.mark.parametrize("event", ["skip", "track_finished"])
def test_immediate_radio_skip_includes_required_seconds(event):
    client = YandexMusicClient("test-token")
    calls = []
    client._stream_client = SimpleNamespace(**{
        f"rotor_station_feedback_{event}": lambda **kw: calls.append(kw)
    })
    client.radio_feedback("user:onyourwave", event, track_id="51", played_seconds=0)
    assert calls[0]["total_played_seconds"] > 0


def test_failed_stream_resolution_keeps_current_track_index(plugin, monkeypatch):
    plugin._playback_state.update(track_id="51", service="yandex")
    plugin._playback_queue = [{"track_id": "51"}, {"track_id": "52"}]
    plugin._playback_queue_index = 0
    def failing(**kwargs):
        raise MusicError("Повторите попытку")
    monkeypatch.setattr(plugin, "_activate_yandex_queue_item_sync", failing)
    with pytest.raises(MusicError):
        plugin._advance_yandex_queue_sync(1)
    assert plugin._playback_queue_index == 0
