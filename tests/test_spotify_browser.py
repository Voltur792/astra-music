import asyncio
import math
from pathlib import Path

import pytest
from astra_plugin_sdk.testing import Harness

from src.plugin import AstraMusic
from src.spotify_browser import SpotifyError, finite, spotify_url


def test_spotify_search_job_returns_before_work_finishes_and_preserves_error(plugin, monkeypatch):
    import threading
    import time
    waiting, release = threading.Event(), threading.Event()
    def browse(*args, **kwargs):
        waiting.set()
        release.wait(3)
        raise SpotifyError("Spotify недоступен")
    monkeypatch.setattr(plugin._spotify, "browse", browse)
    try:
        job = asyncio.run(plugin.ui_spotify_job_start(operation="search", query="Joseph"))
        assert waiting.wait(1)
        assert asyncio.run(plugin.ui_spotify_job_status(job_id=job["job_id"]))["status"] == "loading"
        assert asyncio.run(plugin.ui_spotify_job_start(operation="search", query="Other"))["error"]
        release.set()
        deadline = time.monotonic() + 2
        while plugin._spotify.job_status(job["job_id"])["status"] == "loading" and time.monotonic() < deadline:
            time.sleep(.01)
        result = asyncio.run(plugin.ui_spotify_job_status(job_id=job["job_id"]))
        assert result["status"] == "done" and result["result"]["error"] == "Spotify недоступен"
        assert asyncio.run(plugin.ui_spotify_job_status(job_id="expired"))["error"]
    finally:
        release.set()


def test_personal_mix_uses_account_playlist_and_never_falls_back_to_current_song(plugin, monkeypatch):
    player = plugin._spotify
    monkeypatch.setattr(player, "browse", lambda kind: {"playlists": [{"playlist_id": "myDaylist", "title": "daylist"}]})
    calls = []
    monkeypatch.setattr(player, "playlist", lambda pid: calls.append(pid) or {"playing": True})
    assert player.personal_mix()["mix_title"] == "daylist"
    assert calls == ["myDaylist"]
    monkeypatch.setattr(player, "browse", lambda kind: {"playlists": []})
    assert player.personal_mix()["mix_title"] == "daylist"
    assert calls == ["myDaylist", "myDaylist"]
    player._forget_mixes()
    with pytest.raises(SpotifyError, match="персональный микс"):
        player.personal_mix()
    assert calls == ["myDaylist", "myDaylist"]


def test_personal_mix_restores_cache_and_rejects_bad_saved_ids(plugin, monkeypatch):
    import json
    # Restored metadata contains no credentials and cannot navigate outside Spotify.
    cache = plugin.data_dir / "spotify-mixes.json"
    cache.write_text(json.dumps([{"playlist_id": "restoredMix", "title": "Daily Mix 1"}]), encoding="utf-8")
    player = plugin._spotify
    monkeypatch.setattr(player, "browse", lambda kind: {"playlists": []})
    calls = []
    monkeypatch.setattr(player, "playlist", lambda pid: calls.append(pid) or {"playing": True})
    assert player.personal_mix()["mix_title"] == "Daily Mix 1"
    cache.write_text(json.dumps([{"playlist_id": "../../account", "title": "Daily Mix"}]), encoding="utf-8")
    with pytest.raises(SpotifyError, match="персональный микс"):
        player.personal_mix()
    assert calls == ["restoredMix"]


def test_discord_spotify_wave_starts_personal_mix(plugin, monkeypatch):
    source, player = plugin._discord_music, plugin._spotify
    source.session = "a" * 32
    calls = []
    monkeypatch.setattr(player, "personal_mix", lambda: calls.append("mix") or {"playing": True})
    monkeypatch.setattr(player, "control", lambda *args: pytest.fail("Must choose personal mix, not resume a song"))
    async def current(session): return {"service": "spotify"}
    monkeypatch.setattr(source, "current", current)
    assert asyncio.run(source._play_spotify(source.session, "", "wave", ""))["service"] == "spotify"
    assert calls == ["mix"] and source.spotify


@pytest.mark.parametrize("mode", ["playlist", "wave"])
def test_discord_spotify_playlist_and_mix_prepare_a_shared_pcm_stream(plugin, monkeypatch, mode):
    source, player = plugin._discord_music, plugin._spotify
    playlist_id = "5KafKeLCCXVTEywG5NhPPq"
    mix_id = "37i9dQZF1E35gT7J7SjLQL"
    calls = []
    player.hidden = True
    def browse(kind):
        assert mode == "wave", "A linked playlist must not be looked up in the owner's library"
        assert kind == "mixes"
        return {"playlists": [{"playlist_id": mix_id, "title": "Daily Mix 1"}]}
    monkeypatch.setattr(player, "browse", browse)
    monkeypatch.setattr(player, "start", lambda url: calls.append(url) or {"playing": True})
    monkeypatch.setattr(player, "control", lambda *_: pytest.fail("A new playlist/mix must not just resume the current song"))
    monkeypatch.setattr(player, "snapshot", lambda: {"active": True, "running": True, "hidden": True, "status": "playing", "title": "Song", "artist": "Artist"})
    monkeypatch.setattr(plugin._audio_host, "ensure_server", lambda: "http://127.0.0.1:12345/" + "b" * 40)
    result = asyncio.run(source.play("a" * 32, service="spotify", mode=mode, playlist_id=playlist_id if mode == "playlist" else ""))
    assert calls == ["https://open.spotify.com/playlist/" + (playlist_id if mode == "playlist" else mix_id)]
    assert result["status"] == "ready" and result["service"] == "spotify"
    assert result["codec"] == "pcm_s16le" and result["shared_player"] is True
    assert result["stream_url"].endswith(f"/discord-stream/{source.revision}/0")
    assert result["title"] == "Song" and source.spotify and source.assets == {"0": "spotify-tab"}


@pytest.mark.parametrize("mode", ["playlist", "wave"])
def test_discord_spotify_requires_confirmed_playback_before_exposing_a_stream(plugin, monkeypatch, mode):
    source, player = plugin._discord_music, plugin._spotify
    monkeypatch.setattr(player, "playlist", lambda *_: {"playing": False, "message": "Плейлист недоступен"})
    monkeypatch.setattr(player, "personal_mix", lambda: {"playing": False, "message": "Микс недоступен"})
    monkeypatch.setattr(player, "snapshot", lambda: pytest.fail("Failed playback must not expose a Discord stream"))
    result = asyncio.run(source.play("a" * 32, service="spotify", mode=mode, playlist_id="5KafKeLCCXVTEywG5NhPPq"))
    assert "недоступен" in result["error"] and not source.spotify and not source.assets


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTRA_MUSIC_DATA_DIR", str(tmp_path))
    value = AstraMusic()
    monkeypatch.setattr(value._spotify, "_cache_cover", lambda url: None)
    return value


def test_spotify_bands_are_scoped_to_current_music_session(plugin):
    player=plugin._spotify
    process=object()
    player.process=process
    player.active=True
    plugin._visualizer_settings.update(mode="music", widget=True)
    plugin._playback_state.update(service="spotify", status="playing", revision=5)
    player._publish_bands(process, [.3]*48)
    assert plugin._music_audio_bands == [.3]*48
    assert plugin._music_audio_sample_revision == 5
    for other_process, bands in ((object(), [.9]*48), (process, [float("nan")]*48), (process,[.9]*47)):
        player._publish_bands(other_process,bands)
        assert plugin._music_audio_bands == [.3]*48
    for mode,status,service in (("all","playing","spotify"),("music","paused","spotify"),("music","playing","yandex")):
        plugin._visualizer_settings["mode"]=mode
        plugin._playback_state.update(status=status,service=service)
        player._publish_bands(process,[.9]*48)
        assert plugin._music_audio_bands == [.3]*48


def test_spotify_meter_notifications_do_not_wait_and_restart_with_helper(plugin):
    import io
    from types import SimpleNamespace
    player=plugin._spotify
    player.process=SimpleNamespace(stdin=io.StringIO(),poll=lambda:None)
    player.set_visualizer(True);player.set_visualizer(True);player.set_visualizer(False)
    import json
    rows=[json.loads(line) for line in player.process.stdin.getvalue().splitlines()]
    assert [r["enabled"] for r in rows] == [True,False]
    assert not player.pending
    player.process=SimpleNamespace(stdin=io.StringIO(),poll=lambda:None)
    player.set_visualizer(False)
    assert player.process.stdin.getvalue()


@pytest.mark.parametrize("url", ["http://open.spotify.com/", "https://evil.test/track/abc", "https://open.spotify.com@evil.test/", "https://open.spotify.com:444/", "https://open.spotify.com/../../login", "https://open.spotify.com.evil.test/", "https://open.spotify.com/track/a/b"])
def test_navigation_is_limited_to_spotify_player(url):
    with pytest.raises(SpotifyError):
        spotify_url(url)


def test_search_and_links_do_not_carry_tokens():
    assert spotify_url(query="Кино / <script>") == "https://open.spotify.com/search/%D0%9A%D0%B8%D0%BD%D0%BE%20%2F%20%3Cscript%3E"
    assert spotify_url("https://open.spotify.com/playlist/abc?si=private#token") == "https://open.spotify.com/playlist/abc"
    assert finite(math.nan, .5) == .5


def test_inactive_browser_does_not_replace_current_music(plugin):
    plugin._playback_state.update(service="vk", title="VK song", status="playing")
    plugin._spotify._publish({"title": "Spotify song", "status": "playing", "track_id": "7"})
    assert plugin._playback_state["title"] == "VK song"
    assert plugin._playback_state["service"] == "vk"


def test_metadata_and_real_browser_status_feed_both_widgets_without_stream(plugin):
    plugin._spotify.active = True
    plugin._playback_state["service"] = "spotify"
    plugin._spotify._publish({"logged_in": True, "track_id": "7", "title": "Song", "artist": "Artist", "status": "paused", "duration_seconds": 120, "position_seconds": 8, "volume": .4, "can_next": True, "can_previous": False, "can_seek": True, "cookie": "must-not-cross", "token": "must-not-cross", "cover_url": "https://i.scdn.co/image/abc"})
    state = asyncio.run(plugin.ui_playback_status())
    assert state["service"] == "spotify" and state["track_id"] == "7"
    assert state["status"] == "paused" and state["can_next"]
    assert state["duration_seconds"] == 120 and state["volume"] == .4
    assert plugin._playback_state["url"] == ""
    assert "cookie" not in plugin._spotify.snapshot() and "token" not in plugin._spotify.snapshot()
    revision = state["revision"]
    assert asyncio.run(plugin.ui_playback_report(revision=revision, status="playing")) == {"ok": False}
    assert plugin._playback_state["status"] == "paused"
    plugin._spotify._publish({"track_id": "8", "title": "Next", "status": "playing"})
    assert plugin._playback_state["revision"] > revision


def test_controls_use_spotify_and_keep_voice_response_readable(plugin, monkeypatch):
    plugin._spotify.active = True
    plugin._playback_state.update(service="spotify", track_id="7", status="playing", volume=.5)
    plugin._playback_volume = .5
    plugin._spotify.state.update(status="playing")
    calls = []
    monkeypatch.setattr(plugin._spotify, "request", lambda action, value=0: calls.append((action, value)) or {"success": True})
    with Harness(plugin) as h:
        assert h.execute_action("music_volume_5").result == "Громкость музыки установлена на 5 из 10."
        h.execute_action("music_pause")
        assert h.call_tool("control_music_playback", action="next").success
    assert calls == [("volume", .5), ("pause", 0), ("next", 0)]
    assert plugin._read_settings()["playback_volume"] == .5


def test_failed_control_does_not_claim_success_or_change_volume(plugin, monkeypatch):
    plugin._spotify.active = True
    plugin._playback_state.update(service="spotify", track_id="7")
    original = plugin._playback_volume
    def fail(*args, **kwargs):
        raise SpotifyError("Команда недоступна")
    monkeypatch.setattr(plugin._spotify, "request", fail)
    result = asyncio.run(plugin.ui_playback_control(action="volume", value=2))
    assert result["error"] == "Команда недоступна"
    assert plugin._playback_volume == original


def test_forget_deletes_only_dedicated_profile(plugin):
    sibling = plugin.data_dir / "vk-browser-profile"
    sibling.mkdir()
    (sibling / "session").write_text("preserve")
    plugin._spotify.profile.mkdir()
    (plugin._spotify.profile / "local-data").write_text("session")
    plugin._spotify.forget()
    assert not plugin._spotify.profile.exists()
    assert (sibling / "session").read_text() == "preserve"


def test_switching_service_releases_spotify_before_installing_new_queue(plugin, monkeypatch):
    from src.plugin import Track
    calls = []
    monkeypatch.setattr(plugin._spotify, "release", lambda: calls.append("pause spotify"))
    monkeypatch.setattr(plugin, "_activate_vk_queue_item_sync", lambda **kwargs: calls.append("start vk") or {})
    plugin._start_vk_queue_sync([Track("vk", "7", "Song", "Artist")])
    assert calls == ["pause spotify", "start vk"]


def test_switching_music_cancels_pending_spotify_attachment(plugin):
    plugin._spotify.connecting = True
    plugin._spotify.release()
    assert plugin._spotify.cancel.is_set()
    assert not plugin._spotify.connecting


def test_spotify_search_and_playlists_never_fall_back_to_other_services(plugin, monkeypatch):
    calls = []
    monkeypatch.setattr(plugin._spotify, "browse", lambda kind, **params: calls.append((kind, params)) or {"service": "spotify", "tracks": [], "playlists": []})
    monkeypatch.setattr(plugin, "_client", lambda service: pytest.fail("Spotify must not use another service's API"))
    assert plugin._resolve_service("Спотифай") == "spotify"
    asyncio.run(plugin.ui_search(service="spotify", query="Artist"))
    asyncio.run(plugin.ui_list_playlists(service="spotify"))
    asyncio.run(plugin.ui_list_playlist_tracks(service="spotify", playlist_id="abc"))
    assert [item[0] for item in calls] == ["search", "playlists", "playlist_tracks"]


def test_spotify_cover_cache_reaches_widgets_but_never_exports_secrets(plugin):
    player = plugin._spotify
    player.active = True
    plugin._playback_state["service"] = "spotify"
    url = "https://i.scdn.co/image/abc"
    player.covers[url] = "data:image/jpeg;base64,YWJj"
    player._publish({"title": "Song", "status": "playing", "cover_url": url})
    assert asyncio.run(plugin.ui_playback_status())["cover_url"] == player.covers[url]
    assert player.snapshot()["cover_url"] == url
    player._publish({"title": "Song", "cover_url": "https://i.scdn.co.evil.test/image/abc"})
    assert not asyncio.run(plugin.ui_playback_status())["cover_url"]


def test_spotify_discord_stream_is_scoped_to_a_hidden_owned_player(plugin):
    source = plugin._discord_music
    source.session = "a" * 32
    source.revision = 7
    source.assets = {"0": "spotify-tab"}
    source.spotify = True
    plugin._spotify.active = True
    plugin._spotify.process = type("Process", (), {"poll": lambda self: None})()
    plugin._spotify.state.update(title="Song", status="playing")
    state = asyncio.run(source.current(source.session))
    assert state["service"] == "spotify" and state["codec"] == "pcm_s16le"
    assert state["shared_player"] and state["spotify_status"] == "playing"
    assert "token" not in state and "browser_pid" not in state
    plugin._spotify.hidden = False
    assert asyncio.run(source.current(source.session)) == {"status": "stopped"}


def test_spotify_capture_requires_confirmed_suppression_and_restores_on_failure(plugin, monkeypatch):
    from src.spotify_audio import start_capture
    player = plugin._spotify
    player.active = True
    player.process = type("Process", (), {"poll": lambda self: None})()
    calls = []
    monkeypatch.setattr(player, "request", lambda action, **params: calls.append(action) or {"success": True})
    with pytest.raises(RuntimeError, match="локального"):
        start_capture(plugin)
    assert calls == ["capture-start", "capture-stop"]
    assert player.audio is None and player.snapshot()["audio_route"] == "local"


def test_spotify_capture_queue_is_bounded_and_old_lease_does_not_stop_replacement(plugin, monkeypatch):
    from src.spotify_audio import start_capture, stop_capture
    player = plugin._spotify
    player.active = True
    player.process = type("Process", (), {"poll": lambda self: None})()
    monkeypatch.setattr(player, "request", lambda action, **params: {"success": True, "local_suppressed": True})
    first = start_capture(plugin)
    second = start_capture(plugin)
    assert first.closed.is_set() and player.audio is second
    stop_capture(first)
    assert player.audio is second and player.snapshot()["audio_route"] == "discord"
    for index in range(100):
        second.feed(bytes([index]))
    assert second.frames.qsize() == 32 and second.read() == bytes([68])
    source = plugin._discord_music
    source.session, source.capture = "a" * 32, second
    source.stop("b" * 32)
    assert player.audio is second
    source.stop("a" * 32)
    assert second.closed.is_set() and player.audio is None


def test_public_cover_fallback_uses_the_same_image_id_and_bounded_cache(plugin, monkeypatch):
    import time
    import httpx
    from src.spotify_browser import cover_sources
    assert not cover_sources("https://i.scdn.co.evil.test/image/abc")
    assert not cover_sources("https://private@i.scdn.co/image/abc")
    assert not cover_sources("https://i.scdn.co/image/abc?secret=x")
    calls = []
    class Image:
        status_code = 200
        headers = {"content-type": "image/jpeg"}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_bytes(self): return iter([b"image bytes"])
    def request(method, url, **kwargs):
        calls.append(url)
        assert kwargs["follow_redirects"] is False
        if url.startswith("https://i.scdn.co/"):
            raise httpx.ConnectError("image edge unavailable")
        return Image()
    monkeypatch.setattr(httpx, "stream", request)
    player = plugin._spotify
    monkeypatch.setattr(player, "_cache_cover", type(player)._cache_cover.__get__(player))
    player._cache_cover("https://i.scdn.co/image/abc")
    deadline = time.monotonic() + 2
    while player.cover_jobs and time.monotonic() < deadline:
        time.sleep(.01)
    assert calls == ["https://i.scdn.co/image/abc", "https://image-cdn-ak.spotifycdn.com/image/abc"]
    assert player.covers["https://i.scdn.co/image/abc"].startswith("data:image/jpeg;base64,")


def test_discord_volume_does_not_overwrite_local_spotify_volume(plugin, monkeypatch):
    from types import SimpleNamespace
    player = plugin._spotify
    player.active = True
    plugin._playback_state["service"] = "spotify"
    plugin._playback_volume = .3
    player.audio = SimpleNamespace(started=True, closed=__import__('threading').Event())
    calls = []
    monkeypatch.setattr(player, "request", lambda *args, **kw: calls.append((args, kw)))
    player._publish({"title": "Song", "status": "playing", "volume": 1})
    assert plugin._playback_volume == .3
    player.control("volume", 5)
    assert plugin._playback_volume == .5 and not calls


def test_discord_stop_pauses_spotify_before_restoring_local_output(plugin, monkeypatch):
    source = plugin._discord_music
    source.session = "a" * 32
    source.spotify = True
    calls = []
    monkeypatch.setattr(plugin._spotify, "request", lambda action, **kw: calls.append(action))
    from types import SimpleNamespace
    source.capture = SimpleNamespace(close=lambda: calls.append("restore output"))
    source.stop("stale")
    assert not calls
    source.stop(source.session)
    assert calls == ["pause", "restore output"]


def test_saved_spotify_connection_restores_once_without_play_command(plugin, monkeypatch):
    player = plugin._spotify
    (player.profile / "Default").mkdir(parents=True)
    plugin._write_settings({"spotify_auto_connect": True})
    calls = []
    monkeypatch.setattr(player, "activate", lambda: calls.append("attach"))
    player.restore_saved()
    player.restore_saved()
    assert calls == ["attach"]
