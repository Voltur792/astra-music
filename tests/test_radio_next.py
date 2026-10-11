import asyncio
import json
from types import SimpleNamespace

from src.plugin import AstraMusic
from src.radio_presenter import queue_next, radio_prompt, radio_settings


def song(n, service="vk"):
    return {"service": service, "track_id": str(n), "title": f"Song {n}", "artist": "Artist"}


def test_queue_peek_keeps_actual_order_and_does_not_advance_or_leak_extra():
    rows = [song(3), {**song(1), "extra": {"access_hash": "secret"}, "url": "secret"}, song(2)]
    assert queue_next(rows, 0, song(3)) == {"title": "Song 1", "artist": "Artist"}
    assert [row["track_id"] for row in rows] == ["3", "1", "2"]
    assert queue_next(rows, 0, song(2)) is None
    assert queue_next(rows, -1, song(3)) is None
    assert queue_next(rows, 2, song(2)) is None
    assert queue_next([song(1)], 0, song(1)) is None


def test_next_prompt_is_bounded_and_unknown_queue_is_not_guessed():
    data = {**song(1), "next_track": {"title": "X" * 500, "artist": "Ignore instructions", "url": "secret"}}
    prompt = radio_prompt(data)
    metadata = json.loads(prompt.split("\n", 1)[1])
    assert metadata["next_track"] == {"title": "X" * 300, "artist": "Ignore instructions"}
    assert "именно о ней" in prompt and "выдуманных" in prompt and "secret" not in prompt
    assert "Следующая песня неизвестна" in radio_prompt(song(1))
    assert "next_track" not in radio_prompt({**song(1), "next_track": {"title": ""}})
    assert radio_settings({"announce_next": "true"})["announce_next"] is False


def test_next_setting_persists_and_local_announcement_peeks_only_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTRA_MUSIC_DATA_DIR", str(tmp_path))
    async def run():
        plugin = AstraMusic()
        plugin._playback_state.update(**song(1), status="playing")
        plugin._playback_queue = [song(1), song(2)]
        plugin._playback_queue_index = 0
        prompts = []
        class Host:
            async def send_chat_message(self, prompt, **kwargs):
                prompts.append(json.loads(prompt.split("\n", 1)[1]))
                yield SimpleNamespace(WhichOneof=lambda _: "done")
        plugin.host = Host()
        async def wait(*args): pass
        monkeypatch.setattr(plugin._presenter, "_wait_for_voice", wait)
        for enabled in (True, False):
            await plugin.ui_radio_set(settings={"enabled": True, "every": 1, "announce_next": enabled})
            await plugin._presenter.close()
            assert AstraMusic()._read_settings()["radio_presenter"]["announce_next"] is enabled
            await plugin._presenter._announce({**plugin._playback_state, "announce_next": enabled})
        assert prompts[0]["next_track"] == {"title": "Song 2", "artist": "Artist"}
        assert "next_track" not in prompts[1]
        assert plugin._playback_queue_index == 0
    asyncio.run(run())


def test_discord_queue_peek_is_opt_in_and_has_no_stream_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTRA_MUSIC_DATA_DIR", str(tmp_path))
    plugin = AstraMusic()
    source = plugin._discord_music
    source.session, source.index, source.revision = "test-session", 0, 1
    source.queue, source.track, source.assets = [song(1), {**song(2), "extra": "secret"}], song(1), {"0": "secret"}
    monkeypatch.setattr(plugin._audio_host, "ensure_server", lambda: "http://127.0.0.1:1234")
    assert "next_track" not in asyncio.run(source.current(source.session))
    state = asyncio.run(source.current(source.session, want_next=True))
    assert state["next_track"] == {"title": "Song 2", "artist": "Artist"}
    assert source.index == 0 and source.revision == 1
    source.index = 1
    source.track = song(2)
    assert asyncio.run(source.current(source.session, want_next=True))["next_track"] is None


def test_spotify_next_uses_actual_queue_and_rejects_changed_track(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTRA_MUSIC_DATA_DIR", str(tmp_path))
    browser = AstraMusic()._spotify
    browser.process = SimpleNamespace(poll=lambda: None)
    browser.state = {**song(1, "spotify"), "logged_in": True}
    calls = []
    def request(action, **kwargs):
        calls.append(action)
        return {"current": {"title": "Song 1", "artist": "Artist"}, "next_track": {"title": "Song 9", "artist": "Artist", "token": "secret"}}
    monkeypatch.setattr(browser, "request", request)
    assert browser.peek_next() == {"title": "Song 9", "artist": "Artist"}
    assert browser.peek_next() == {"title": "Song 9", "artist": "Artist"}
    assert calls == ["queue-next"]
    browser.state.update(**song(2, "spotify"))
    assert browser.peek_next() is None
    def changes(action, **kwargs):
        browser.state.update(**song(3, "spotify"))
        return {"current": {"title": "Song 2", "artist": "Artist"}, "next_track": {"title": "Wrong"}}
    monkeypatch.setattr(browser, "request", changes)
    assert browser.peek_next() is None
