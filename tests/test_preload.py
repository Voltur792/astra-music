import asyncio
import threading
from src.plugin import AstraMusic, YandexMusicClient


def player(tmp_path, monkeypatch, service="yandex"):
    monkeypatch.setenv("ASTRA_MUSIC_DATA_DIR", str(tmp_path))
    p = AstraMusic()
    p._playback_state.update(service=service, revision=4, status="playing")
    p._playback_queue = [{"service": service, "track_id": "1"}, {"service": service, "track_id": "2"}]
    p._playback_queue_index = 0
    return p


def test_prepare_does_not_move_queue_and_yandex_activation_reuses_result(tmp_path, monkeypatch):
    p = player(tmp_path, monkeypatch)
    calls = []
    c = YandexMusicClient("test-only")
    monkeypatch.setattr(c, "stream_url", lambda track: calls.append(track) or {"url":"https://cdn.yandex.net/song.mp3"})
    p.clients["yandex"] = c
    result = asyncio.run(p.ui_playback_prepare_next(revision=4))
    assert result["track_id"] == "2" and result["revision"] == 4
    assert p._playback_queue_index == 0 and p._playback_state["revision"] == 4
    assert p._yandex_stream_sync("2")["url"] == result["url"]
    assert calls == ["2"]
    c.close()


def test_stale_resolution_cannot_prepare_another_queue(tmp_path, monkeypatch):
    p = player(tmp_path, monkeypatch, "vk")
    def resolve(item):
        p._playback_state["revision"] = 5
        return {"url":"https://example.test/track.m3u8"}
    monkeypatch.setattr(p, "_resolve_vk_queue_stream", resolve)
    assert p._prepare_next_sync(4) == {}
    assert p._prepared_next == {}


def test_preload_rejects_unsafe_yandex_address_and_missing_next(tmp_path, monkeypatch):
    p = player(tmp_path, monkeypatch)
    monkeypatch.setattr(p, "_yandex_stream_sync", lambda _: {"url":"http://127.0.0.1/private"})
    assert p._prepare_next_sync(4) == {}
    p._playback_queue_index = 1
    assert p._prepare_next_sync(4) == {}


def test_parallel_preparations_do_not_duplicate_resolve(tmp_path, monkeypatch):
    p = player(tmp_path, monkeypatch, "vk")
    started, release = threading.Event(), threading.Event()
    calls = []
    def resolve(item):
        calls.append(item["track_id"]); started.set(); release.wait(2)
        return {"url":"https://example.test/song.m3u8"}
    monkeypatch.setattr(p, "_resolve_vk_queue_stream", resolve)
    worker = threading.Thread(target=lambda:p._prepare_next_sync(4))
    worker.start()
    try:
        assert started.wait(1)
        assert p._prepare_next_sync(4) == {}
    finally:
        release.set(); worker.join(3)
    assert calls == ["2"]
