"""Tests for the Astra Music plugin.

Run: `pytest`.

Two layers live here: the SDK harness drives the real gRPC servicer, so a tool
that is declared but not routed fails; and the three service clients are driven
through `httpx.MockTransport`, so every endpoint and error shape they parse is
checked without touching a real account.
"""

import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

# The daemon puts the bundle root on `sys.path` before importing `src.plugin`;
# do the same so `pytest` from the project root finds it.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from astra_plugin_sdk.testing import Harness, fuzz_configs  # noqa: E402

from src import plugin as P  # noqa: E402
from src.plugin import AstraMusic, Track  # noqa: E402

TOKEN = "***"


@pytest.fixture(autouse=True)
def never_really_open_anything(monkeypatch):
    """Никаких реальных браузеров и плееров из тестов."""
    monkeypatch.setattr(P, "open_in_browser", lambda url: False)
    monkeypatch.setattr(P, "open_in_desktop_player", lambda service, link: False)
    monkeypatch.setattr(P.PlaybackHost, "ensure", lambda self: None)
    monkeypatch.setattr(P.SystemAudio, "start", lambda self: None)


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Tokens must never be read from or written to the real %APPDATA%."""
    monkeypatch.setenv("ASTRA_MUSIC_DATA_DIR", str(tmp_path / "music-data"))


def mock_client(client_class: type[P.MusicClient], routes: dict[str, Any], captured: list | None = None):
    """A client whose HTTP goes to canned responses, matched by path substring."""

    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(request)
        for needle, payload in routes.items():
            if needle in request.url.path:
                status, body = payload if isinstance(payload, tuple) else (200, payload)
                return httpx.Response(status, json=body)
        return httpx.Response(500, json={"error": f"unrouted {request.url.path}"})

    client = client_class("t0ken")
    client.http = httpx.Client(transport=httpx.MockTransport(handler), headers=client.http.headers)
    return client


YANDEX_ACCOUNT = {"result": {"account": {"id": 777, "login": "maksim"}, "subdb": {"has_music": True}}}
YANDEX_TRACK = {
    "id": 51,
    "title": "Bohemian Rhapsody",
    "artists": [{"id": 9, "name": "Queen"}],
    "albums": [{"id": 3, "title": "A Night at the Opera"}],
}


# --- registration -----------------------------------------------------------


def test_every_declared_tool_is_routed_and_its_schema_is_an_object():
    with Harness(AstraMusic()) as h:
        assert sorted(h.tool_names()) == [
            "connection_status",
            "control_music_playback",
            "list_playlist_tracks",
            "list_playlists",
            "list_vk_playlists",
            "open_track",
            "play_playlist",
            "play_track",
            "play_vk_my_music",
            "play_vk_playlist",
            "play_yandex_playlist",
            "play_yandex_radio",
            "remove_token",
            "save_token",
            "search_tracks",
            "set_music_volume",
            "test_connection",
        ]
        for name in h.tool_names():
            assert h.schema(name)["type"] == "object"
            # Astra's model answers with a free-form `kwargs` string, so every
            # tool has to be able to read it.
            assert "kwargs" in h.schema(name)["properties"] or name in {
                "connection_status",
                "list_vk_playlists",
                "play_vk_my_music",
            }


def test_the_tab_and_the_visualizer_are_both_contributed():
    with Harness(AstraMusic()) as h:
        contributions = h.ui_contributions()
        by_id = {c.id: c for c in contributions}
        assert by_id["music"].slot == "page.custom"
        # Astra fills an opaque background behind a non-transparent iframe.
        assert by_id["music"].transparent is True
        assert by_id["music-visualizer"].transparent is True
        assert by_id["music-visualizer"].slot == "background.behind"
        assert by_id["music-visualizer"].pointer_events is False
        assert not by_id["music-visualizer"].props.get("audio")


def test_no_config_the_daemon_can_deliver_crashes_this_plugin():
    with Harness(AstraMusic()) as h:
        for payload in fuzz_configs():
            h.set_config(payload)


# --- Yandex Music -----------------------------------------------------------


def test_yandex_authorizes_with_oauth_not_bearer():
    captured: list[httpx.Request] = []
    client = mock_client(P.YandexMusicClient, {"/account/status": YANDEX_ACCOUNT}, captured)
    assert client.test_connection()["account"] == "maksim"
    assert captured[0].headers["Authorization"] == "OAuth t0ken"


def test_yandex_search_returns_parsed_tracks():
    routes = {"/search": {"result": {"tracks": {"results": [YANDEX_TRACK, {"id": 52}]}}}}
    client = mock_client(P.YandexMusicClient, routes)
    tracks = client.search("queen", 10)
    # The second candidate has no title and must be dropped, not half-built.
    assert [t.title for t in tracks] == ["Bohemian Rhapsody"]
    assert tracks[0].artist == "Queen"
    assert tracks[0].album == "A Night at the Opera"
    assert tracks[0].extra["artist_id"] == "9"


def test_yandex_playlists_read_the_list_endpoint_and_track_count():
    # /playlists/list answers with `track_count` and no tracks at all.
    routes = {"/playlists/list": {"result": [{"kind": 111, "title": "Мне нравится", "track_count": 203}]}}
    client = mock_client(P.YandexMusicClient, routes)
    client._user_id = "777"
    playlists = client.playlists()
    assert playlists == [
        {
            "service": "yandex",
            "playlist_id": "111",
            "title": "Мне нравится",
            "track_count": 203,
            "url": "https://music.yandex.ru/users/777/playlists/111",
        }
    ]


def test_yandex_playlist_tracks_reads_both_response_shapes():
    new_shape = {"result": {"list": [{"track": YANDEX_TRACK}, {"track": YANDEX_TRACK}]}}
    old_shape = {"result": {"tracks": [YANDEX_TRACK]}}
    for routes, expected in ((new_shape, 2), (old_shape, 1)):
        client = mock_client(P.YandexMusicClient, {"/playlists/111": routes})
        client._user_id = "777"
        tracks = client.playlist_tracks("111", 50)
        assert len(tracks) == expected
        # Without context_id the UI cannot tell a playlist track from a search hit.
        assert tracks[0].context_id == "111"


def test_yandex_get_track_resolves_a_bare_id():
    client = mock_client(P.YandexMusicClient, {"/tracks": {"result": [YANDEX_TRACK]}})
    track = client.get_track("51")
    assert (track.title, track.artist) == ("Bohemian Rhapsody", "Queen")


def test_yandex_rejected_token_says_so():
    client = mock_client(P.YandexMusicClient, {"/account/status": (401, {"error": "Unauthorized", "errorDescription": "bad token"})})
    with pytest.raises(P.MusicError) as exc:
        client.test_connection()
    assert "OAuth" in str(exc.value)


def test_anonymous_answer_is_not_reported_as_connected():
    # Проверено живым запросом: /account/status без распознанного токена
    # отвечает 200, но без `id`/`login`. Без этой проверки опечатка в токене
    # выглядела бы как «подключено».
    anonymous = {"result": {"account": {"now": "2026-09-23T02:20:21+03:00", "region": 225, "serviceAvailable": True}}}
    client = mock_client(P.YandexMusicClient, {"/account/status": anonymous})
    with pytest.raises(P.MusicError) as exc:
        client.test_connection()
    assert "анонимн" in str(exc.value)


def test_plus_is_read_from_both_subscription_shapes():
    for subdb, permissions, expected in (
        ({"has_music": True}, {}, "plus"),
        ({}, {"products": ["plus"]}, "plus"),
        ({}, {"products": ["mobile"]}, ""),
    ):
        body = {"result": {"account": {"id": 777, "login": "maksim"}, "subdb": subdb, "permissions": permissions}}
        client = mock_client(P.YandexMusicClient, {"/account/status": body})
        assert client.test_connection()["subscription"] == expected


def test_playlists_work_without_a_known_uid():
    # Проверено живым запросом: /users/me/playlists/list — валидный маршрут
    # (403 без токена вместо 400), поэтому отсутствие id в ответе
    # /account/status не должно ломать плейлисты.
    routes = {"/playlists/list": {"result": [{"kind": 111, "title": "Мне нравится", "track_count": 3}]}}
    client = mock_client(P.YandexMusicClient, routes)
    assert client.playlists()[0]["playlist_id"] == "111"
    assert "/users/me/playlists/" in client.playlists()[0]["url"]


def test_pasted_token_prefix_is_stripped():
    # Яндекс отвечает 200 на нераспознанный токен, поэтому `OAuth ` внутри
    # токена (= `Authorization: OAuth OAuth ...`) выглядел бы как «подключено».
    assert P.normalize_token("  OAuth abc123 ") == "abc123"
    assert P.normalize_token("Bearer 'abc123'") == "abc123"
    assert P.normalize_token("abc123") == "abc123"
    assert YandexHeaderCheck().header == "OAuth abc123"


class YandexHeaderCheck:
    def __init__(self):
        client = P.YandexMusicClient("OAuth abc123")
        self.header = client.http.headers["Authorization"]
        client.close()


# --- VK --------------------------------------------------------------------


def test_vk_reports_errors_that_arrive_inside_a_200():
    routes = {"/audio.search": {"error": {"error_code": 5, "error_msg": "user authorization failed"}}}
    client = mock_client(P.VkMusicClient, routes)
    with pytest.raises(P.MusicError) as exc:
        client.search("queen", 10)
    assert "user authorization failed" in str(exc.value)


def test_vk_track_link_carries_the_owner_id():
    client = mock_client(
        P.VkMusicClient,
        {"/audio.search": {"response": {"items": [{"id": 7, "owner_id": 2, "title": "Track", "artist": "Who"}]}}},
    )
    track = client.search("q", 10)[0]
    assert track.url == "https://vk.com/audio2_7"
    assert track.extra["owner_id"] == "2"


# --- argument shapes the model actually sends -------------------------------


class RecordingClient(P.YandexMusicClient):

    def __init__(self, token: str = ""):
        super().__init__(token or "test-token")
        self.calls: list[tuple] = []

    def close(self) -> None:
        super().close()

    def test_connection(self) -> dict:
        return {"service": self.service, "connected": True, "account": "maksim"}

    def search(self, query: str, limit: int) -> list[Track]:
        self.calls.append(("search", query, limit))
        return [Track("yandex", "51", "Bohemian Rhapsody", "Queen", url="https://music.yandex.ru/track/51")]

    def playlists(self) -> list[dict]:
        return [{"service": "yandex", "playlist_id": "111", "title": "Мне нравится", "track_count": 1}]

    def playlist_tracks(self, playlist_id: str, limit: int) -> list[Track]:
        self.calls.append(("playlist_tracks", playlist_id, limit))
        return [Track("yandex", "51", "Bohemian Rhapsody", "Queen", context_id=playlist_id)]

    def play_track(self, track: Track) -> dict:
        self.calls.append(("play", track.track_id, track.title, track.extra))
        return {"service": self.service, "action": "play", "track_id": track.track_id}

    def stream_url(self, track_id: str) -> dict:
        self.calls.append(("stream_url", track_id))
        return {"url": "https://storage.yandex.net/test-audio.mp3"}

    def get_track(self, track_id: str) -> Track:
        self.calls.append(("get_track", track_id))
        return Track("yandex", track_id, "Bohemian Rhapsody", "Queen")

    def open_track(self, track: Track) -> dict:
        # Как настоящий YandexMusicClient: deep link десктоп-плеера вместе с url.
        return {
            "service": self.service,
            "action": "open",
            "url": track.url or "https://example.invalid",
            "deep_link": f"yandexmusic://track/{track.track_id}",
            "track_id": track.track_id,
            "playing": False,
        }


@pytest.fixture
def plugin_with_fake_client(monkeypatch):
    monkeypatch.setitem(P.CLIENTS, "yandex", RecordingClient)
    plugin = AstraMusic()
    plugin.clients["yandex"] = RecordingClient()
    return plugin


def test_bare_kwargs_string_still_searches(plugin_with_fake_client):
    plugin_with_fake_client._search_sync(
        "yandex",
        plugin_with_fake_client._pick(plugin_with_fake_client._args("queen"), "query")
        or plugin_with_fake_client._args("queen")["_raw"],
        10,
    )
    assert plugin_with_fake_client.clients["yandex"].calls == [("search", "queen", 10)]


def test_python_literal_kwargs_is_understood(plugin_with_fake_client):
    args = plugin_with_fake_client._args("{'service': 'yandex', 'query': 'queen', 'limit': '5'}")
    assert plugin_with_fake_client._pick(args, "service") == "yandex"
    assert plugin_with_fake_client._pick(args, "query") == "queen"
    assert plugin_with_fake_client._as_int(plugin_with_fake_client._pick(args, "limit"), 10, 1, 20) == 5


def test_service_names_in_prose_resolve_to_client_ids(plugin_with_fake_client):
    resolve = plugin_with_fake_client._resolve_service
    assert resolve("Яндекс Музыка") == "yandex"
    with pytest.raises(P.MusicError):
        resolve("deezer")
    assert resolve("ВК") == "vk"
    with pytest.raises(P.MusicError):
        resolve("")


def test_play_track_keeps_the_metadata_the_ui_sent(plugin_with_fake_client):
    result = plugin_with_fake_client._play_sync(
        plugin_with_fake_client._args(
            "",
            service="yandex",
            track_id="51",
            title="Bohemian Rhapsody",
            artist="Queen",
            extra={"artist_id": "9"},
        )
    )
    assert result["track"]["title"] == "Bohemian Rhapsody"
    assert result["playback_requested"] is True
    assert result["playing"] is False  # the widget must confirm playback first
    assert plugin_with_fake_client.clients["yandex"].calls[-1] == ("stream_url", "51")


def test_open_track_opens_the_desktop_player_when_available(plugin_with_fake_client, monkeypatch):
    # Deep link Яндекс Музыки: приложение берёт последний аргумент argv и
    # превращает `yandexmusic://x` во внутренний роут `/x`.
    opened: list[str] = []
    monkeypatch.setattr(P, "desktop_player_exe", lambda service: r"C:\app\Яндекс Музыка.exe")
    monkeypatch.setattr(
        P, "open_in_desktop_player", lambda service, link: opened.append(link) or True
    )
    monkeypatch.setattr(P, "open_in_browser", lambda url: pytest.fail("браузер не нужен, есть приложение"))
    result = plugin_with_fake_client._open_sync(
        plugin_with_fake_client._args("", service="yandex", track_id="51", url="https://music.yandex.ru/track/51")
    )
    assert result["opened"] is True
    assert result["opened_via"] == "desktop"
    # `playing` остаётся False: открытое окно — это не начавшееся воспроизведение,
    # и модель повторяет это поле пользователю.
    assert result["playing"] is False
    assert opened and opened[0].startswith("yandexmusic://")


def test_play_track_requests_embedded_widget_playback(plugin_with_fake_client):
    result = plugin_with_fake_client._play_sync(
        plugin_with_fake_client._args("", service="yandex", track_id="51", url="https://music.yandex.ru/track/51")
    )
    assert result["playback_requested"] is True
    assert result["playing"] is False
    assert plugin_with_fake_client._playback_state["command"] == "play"


def test_open_mode_browser_wins_over_desktop(plugin_with_fake_client, monkeypatch):
    monkeypatch.setattr(P, "desktop_player_exe", lambda service: r"C:\app\Яндекс Музыка.exe")
    monkeypatch.setattr(P, "open_in_desktop_player", lambda service, link: pytest.fail("режим — браузер"))
    monkeypatch.setattr(P, "open_in_browser", lambda url: True)
    plugin_with_fake_client._set_open_mode_sync("browser")
    result = plugin_with_fake_client._open_sync(
        plugin_with_fake_client._args("", service="yandex", track_id="51", url="https://music.yandex.ru/track/51")
    )
    assert result["opened_via"] == "browser"


def test_yandex_playback_is_only_reported_after_widget_confirms_it(plugin_with_fake_client):
    result = plugin_with_fake_client._play_sync(plugin_with_fake_client._args("", service="yandex", track_id="51"))
    assert result["playback_requested"] is True
    assert result["playing"] is False


def test_open_mode_rejects_nonsense(plugin_with_fake_client):
    assert "Режим открытия" in plugin_with_fake_client._set_open_mode_sync("floppy")["error"]


def test_status_for_the_tab_carries_account_and_desktop_player(plugin_with_fake_client, monkeypatch):
    monkeypatch.setattr(P, "desktop_player_exe", lambda service: r"C:\app\Яндекс Музыка.exe")
    plugin_with_fake_client._write_settings({"yandex": {"token": "***", "account": "maksim", "subscription": "plus"}})
    full = plugin_with_fake_client._connection_status_sync(full=True)
    brief = plugin_with_fake_client._connection_status_sync()
    assert full["services"]["yandex"]["subscription"] == "plus"
    assert full["desktop_player"].endswith("Яндекс Музыка.exe")
    # Инструмент модели не должен таскать локальные пути и имена аккаунтов.
    assert "desktop_player" not in brief and "account" not in brief["services"]["yandex"]


def test_status_line_survives_a_missing_desktop_player(plugin_with_fake_client, monkeypatch):
    monkeypatch.setattr(P, "desktop_player_exe", lambda service: None)
    assert plugin_with_fake_client._connection_status_sync(full=True)["desktop_player"] == ""


# --- tools and UI calls through the real servicer ---------------------------


def test_search_tool_over_grpc(monkeypatch):
    monkeypatch.setitem(P.CLIENTS, "yandex", RecordingClient)
    plugin = AstraMusic()
    plugin.clients["yandex"] = RecordingClient()
    with Harness(plugin) as h:
        result = h.call_tool("search_tracks", service="yandex", query="queen", limit=5)
        assert result.success, result.error
        body = result.json
        assert body["tracks"][0]["track_id"] == "51"
        assert body["count"] == 1


def test_unknown_service_is_an_error_not_a_crash():
    with Harness(AstraMusic()) as h:
        body = h.call_tool("search_tracks", service="deezer", query="queen").json
        assert "deezer" in body["error"]
        assert set(body["services"]) == {"yandex", "vk"}


def test_search_without_a_configured_service_explains_itself():
    with Harness(AstraMusic()) as h:
        body = h.call_tool("search_tracks", service="yandex", query="queen").json
        assert "Яндекс" in body["error"]


def test_ui_search_and_play_round_trip(monkeypatch):
    monkeypatch.setitem(P.CLIENTS, "yandex", RecordingClient)
    plugin = AstraMusic()
    plugin.clients["yandex"] = RecordingClient()
    with Harness(plugin) as h:
        found = h.ui_call("music_search", service="yandex", query="queen", limit=12)
        assert found.success, found.error
        track = found.json["tracks"][0]
        played = h.ui_call("music_play_track", **track)
        assert played.success, played.error
        assert played.json["track"]["artist"] == "Queen"
        assert played.json["playback_requested"] is True


def test_connection_status_never_leaks_the_token():
    plugin = AstraMusic()
    plugin._write_settings({"yandex": {"token": "***"}})
    with Harness(plugin) as h:
        body = h.call_tool("connection_status").json
        assert body["services"]["yandex"]["configured"] is True
        assert "secret-token" not in json.dumps(body)


def test_save_token_stores_only_after_the_service_accepts_it(monkeypatch):
    class OkClient(RecordingClient):
        def test_connection(self) -> dict:
            return {"service": "yandex", "connected": True, "account": "maksim"}

    monkeypatch.setitem(P.CLIENTS, "yandex", OkClient)
    plugin = AstraMusic()
    result = plugin._save_token_sync("yandex", "  fresh-token  ")
    assert result["success"] is True
    assert plugin._read_settings()["yandex"]["token"] == "fresh-token"

    class DeadClient(RecordingClient):
        def test_connection(self) -> dict:
            raise P.MusicError("Яндекс Музыка: токен отклонён")

    monkeypatch.setitem(P.CLIENTS, "yandex", DeadClient)
    failed = plugin._save_token_sync("yandex", "bad")
    assert "отклонён" in failed["error"]
    assert plugin._read_settings()["yandex"]["token"] == "fresh-token"


def test_remove_token_forgets_the_service():
    plugin = AstraMusic()
    plugin._write_settings({"vk": {"token": "***"}})
    assert plugin._remove_token_sync("vk")["configured"] is False
    assert plugin._read_settings() == {}


def test_vk_library_starts_in_default_order_and_advances_to_distinct_stream(monkeypatch):
    plugin = AstraMusic()
    plugin.data_dir.mkdir(parents=True)
    plugin._vk_cookie_file.write_text("{}")
    calls = []

    def bridge(operation, **params):
        calls.append((operation, params))
        if operation == "my_tracks":
            return {"tracks": [
                {"track_id": "1", "title": "First", "extra": {"owner_id": "9", "reload_id": "9_1_a_b"}},
                {"track_id": "2", "title": "Second", "extra": {"owner_id": "9", "reload_id": "9_2_c_d"}},
            ]}
        return {"stream_url": f"https://audio.vkuseraudio.net/{params['track_id']}/index.m3u8"}

    monkeypatch.setattr(plugin, "_easyvk_call_sync", bridge)
    started = plugin._start_vk_playlist_sync("audio")
    first_url = plugin._playback_state["url"]
    assert started["source_title"] == "Мои треки"
    assert started["queue_count"] == 2
    assert plugin._playback_state["track_id"] == "1"
    plugin._advance_yandex_queue_sync(1)
    assert plugin._playback_state["track_id"] == "2"
    assert plugin._playback_state["url"] != first_url
    assert calls[-1][1]["reload_id"] == "9_2_c_d"


def test_vk_network_failure_preserves_saved_session(monkeypatch):
    plugin = AstraMusic()
    plugin.data_dir.mkdir(parents=True)
    plugin._vk_cookie_file.write_text("{}")
    plugin._write_settings({"vk": {"user_id": "9", "session_valid": True}})

    def failed(*args, **kwargs):
        raise P.MusicError("Не удалось проверить сессию VK. Проверьте интернет.")

    monkeypatch.setattr(plugin, "_easyvk_call_sync", failed)
    assert plugin._test_connection_sync("vk")["connected"] is False
    assert plugin._read_settings()["vk"]["session_valid"] is True

    def expired(*args, **kwargs):
        raise P.MusicError("VK_SESSION_EXPIRED: Войдите снова")

    monkeypatch.setattr(plugin, "_easyvk_call_sync", expired)
    plugin._test_connection_sync("vk")
    assert plugin._read_settings()["vk"]["session_valid"] is False


def test_vk_library_ui_returns_before_slow_network_and_deduplicates_launch(monkeypatch):
    import threading
    import time

    plugin = AstraMusic()
    entered, release = threading.Event(), threading.Event()
    calls = []

    def slow_start():
        calls.append(1)
        entered.set()
        assert release.wait(5)
        return {"success": True, "revision": 7, "queue_count": 1511}

    monkeypatch.setattr(plugin, "_start_vk_my_music_sync", slow_start)
    try:
        with Harness(plugin) as h:
            before = time.monotonic()
            result = h.ui_call("music_play_vk_my_music").json
            assert time.monotonic() - before < 1
            assert result["status"] == "loading"
            assert entered.wait(1)
            repeated = h.ui_call("music_play_vk_my_music").json
            assert repeated["job_id"] == result["job_id"]
            assert calls == [1]
            status = h.ui_call("music_vk_my_music_status", job_id=result["job_id"]).json
            assert status["status"] == "loading"
            release.set()
            for _ in range(100):
                status = h.ui_call("music_vk_my_music_status", job_id=result["job_id"]).json
                if status["status"] == "ready":
                    break
                time.sleep(.01)
            assert status["result"]["queue_count"] == 1511
    finally:
        release.set()


def test_vk_library_preparation_error_is_returned_by_job_status(monkeypatch):
    import time

    plugin = AstraMusic()

    def failed():
        raise P.MusicError("VK не подтвердил сохранённый вход")

    monkeypatch.setattr(plugin, "_start_vk_my_music_sync", failed)
    started = plugin._start_vk_music_job()
    for _ in range(100):
        status = plugin._vk_music_job_status(started["job_id"])
        if status["status"] == "failed":
            break
        time.sleep(.01)
    assert status["status"] == "failed"
    assert "сохранённый вход" in status["error"]
    assert plugin._vk_music_job_status("wrong-job")["status"] == "failed"


def test_vk_library_voice_action_routes_to_same_background_launch(monkeypatch):
    plugin = AstraMusic()
    monkeypatch.setattr(plugin, "_start_vk_music_job", lambda: {"job_id": "voice-job", "status": "loading"})
    with Harness(plugin) as h:
        result = h.execute_action("music_vk_my_music")
        assert result.success, result.error
        assert result.result == "Загружаю ваши треки ВК."


def test_visualizer_defaults_and_preferences_survive_restart_without_changing_accounts():
    plugin = AstraMusic()
    plugin._write_settings({"vk": {"user_id": "9", "session_valid": True}, "playback_volume": .4})
    with Harness(plugin) as h:
        defaults = h.ui_call("music_visualizer_get").json
        assert defaults["mode"] == "music"
        assert defaults["background"] is False
        updated = h.ui_call("music_visualizer_set", settings={
            "mode": "all", "background": True, "widget": False, "style": "liquid", "intensity": .8,
        }).json
        assert updated["widget"] is False
    restarted = AstraMusic()
    assert restarted._visualizer_settings == updated
    stored = restarted._read_settings()
    assert stored["vk"] == {"user_id": "9", "session_valid": True}
    assert stored["playback_volume"] == .4


def test_music_visualizer_rejects_audio_when_paused_or_from_previous_track():
    plugin = AstraMusic()
    plugin._playback_state.update({"revision": 2, "status": "paused", "url": "https://example.invalid/audio"})
    with Harness(plugin) as h:
        assert h.ui_call("music_visualizer_sample", revision=2, bands=[.8] * 48).json["accepted"] is False
        state = h.ui_call("music_visualizer_state").json
        assert state["playing"] is False and state["bands"] == []
        plugin._playback_state["status"] = "playing"
        assert h.ui_call("music_visualizer_sample", revision=1, bands=[.8] * 48).json["accepted"] is False
        assert h.ui_call("music_visualizer_sample", revision=2, bands=[.8] * 48).json["accepted"] is True
        assert h.ui_call("music_visualizer_state").json["bands"] == [.8] * 48
        plugin._playback_state["status"] = "paused"
        assert h.ui_call("music_visualizer_state").json["bands"] == []
        plugin._playback_state.update({"status": "playing", "revision": 3})
        assert h.ui_call("music_visualizer_state").json["bands"] == []


def test_visualizer_off_and_location_settings_do_not_stop_audio():
    plugin = AstraMusic()
    plugin._playback_state.update({"revision": 2, "status": "playing", "url": "https://example.invalid/audio"})
    with Harness(plugin) as h:
        h.ui_call("music_visualizer_set", settings={"mode": "off", "background": False, "widget": False})
        state = h.ui_call("music_visualizer_state").json
        assert state["playing"] is True
        assert state["settings"]["mode"] == "off"
        assert plugin._playback_state["status"] == "playing"


def test_system_visualizer_uses_output_even_without_music(monkeypatch):
    plugin = AstraMusic()
    started = []
    monkeypatch.setattr(plugin._system_audio, "start", lambda: started.append(True))
    monkeypatch.setattr(plugin._system_audio, "snapshot", lambda: [.3] * 48)
    with Harness(plugin) as h:
        h.ui_call("music_visualizer_set", settings={"mode": "all", "background": True})
        result = h.ui_call("music_visualizer_state").json
        assert result["playing"] is False
        assert result["bands"] == [.3] * 48 and started
        h.ui_call("music_visualizer_set", settings={"mode": "music"})
        assert h.ui_call("music_visualizer_state").json["bands"] == []


def test_windows_widget_is_only_a_remote_control():
    plugin = AstraMusic()
    plugin._audio_host.enabled = True
    with Harness(plugin) as h:
        widget = next(c for c in h.ui_contributions() if c.slot == "home.widgets")
        assert widget.url == "player-remote.html"
    root = Path(__file__).resolve().parent.parent
    html = (root / "ui/player-remote.html").read_text(encoding="utf-8-sig")
    script = (root / "ui/player-remote.js").read_text(encoding="utf-8")
    assert "<audio" not in html and "new Audio(" not in script
    assert "music_playback_control" in script and "music_visualizer_state" in script


@pytest.mark.parametrize("style", ["mesh", "binary", "terrain", "silk", "splash", "rain", "contour"])
def test_new_visualizer_style_survives_settings_roundtrip(style):
    plugin = AstraMusic()
    with Harness(plugin) as h:
        assert h.ui_call("music_visualizer_set", settings={"style": style}).json["style"] == style
    assert AstraMusic()._visualizer_settings["style"] == style


def test_edge_lookup_works_without_daemon_environment_paths(monkeypatch):
    from src import playback_host
    for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA", "SystemDrive"):
        monkeypatch.delenv(key, raising=False)
    expected = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
    monkeypatch.setattr(Path, "is_file", lambda self: self == expected)
    monkeypatch.setattr(playback_host.shutil, "which", lambda name: None)
    assert playback_host.find_edge_browser() == expected.resolve()
