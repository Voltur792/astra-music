import asyncio
import io
import json
from types import SimpleNamespace

import httpx
import pytest

from src.plugin import AstraMusic, Track, YandexMusicClient
from src.music_extensions import identifiers
from src.internet_radio import RadioCatalogue, RadioRelay, public_url
import src.internet_radio as radio_module


@pytest.fixture
def music(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTRA_MUSIC_DATA_DIR", str(tmp_path))
    plugin = AstraMusic()
    yield plugin
    plugin._internet_radio.close()
    plugin._radio_relay.close()


@pytest.mark.parametrize("value", ['[]', '[true]', '["https://bad"]', '["1",{}]', '["1;delete"]'])
def test_selection_rejects_invalid_ids(value):
    with pytest.raises(ValueError):
        identifiers(value)


def test_selection_deduplicates_preserving_order():
    assert identifiers('["3:55", 2, "3", "1"]') == ["3", "2", "1"]


def setup_yandex(music, handler):
    client = YandexMusicClient("test-token")
    client._user_id = "77"
    client.http.close()
    client.http = httpx.Client(transport=httpx.MockTransport(handler))
    music.clients["yandex"] = client
    return client


def tracks_response(ids):
    return [{"id": i, "title": "Track " + i, "artists": [{"id": "8", "name": "Band"}], "albums": [{"id": "55"}]} for i in ids]


def test_playlist_is_private_ordered_and_retry_does_not_duplicate(music):
    writes = []
    def handler(request):
        if request.method == "GET":
            if request.url.path.endswith("/playlists/100"):
                return httpx.Response(200, json={"result": {"tracks": [{"id": "3"}, {"id": "2"}]}})
            return httpx.Response(200, json={"result": tracks_response([request.url.params["trackIds"]])})
        writes.append(request)
        if request.url.path.endswith("/create"):
            assert b'visibility=private' in request.content
            return httpx.Response(200, json={"result": {"kind": 100, "revision": 4}})
        from urllib.parse import parse_qs
        data = parse_qs(request.content.decode())
        assert data["revision"] == ["4"]
        diff = json.loads(data["diff"][0])
        assert [t["id"] for t in diff[0]["tracks"]] == ["3", "2"]
        assert diff[0]["tracks"][0]["albumId"] == "55"
        return httpx.Response(200, json={"result": {"kind": 100, "trackCount": 2}})
    client = setup_yandex(music, handler)
    result = music._create_selection_playlist("My selection", '["3", "2", "3"]')
    assert result["success"] and result["track_count"] == 2
    assert music._create_selection_playlist("My selection", '["3", "2"]')["repeated_request"]
    assert len(writes) == 2
    client._user_id = "88"
    assert music._create_selection_playlist("My selection", '["3", "2"]')["success"]
    assert len(writes) == 4  # A different account must not reuse the first account's operation.
    client.close()


def test_failed_fill_keeps_created_playlist_and_does_not_recreate(music):
    writes = []
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"result": tracks_response(["1"])})
        writes.append(request)
        if request.url.path.endswith("/create"):
            return httpx.Response(200, json={"result": {"kind": 100}})
        raise httpx.ReadTimeout("network", request=request)
    client = setup_yandex(music, handler)
    result = music._create_selection_playlist("test", '["1"]')
    assert result["created"] and not result["success"] and result["status"] == "partial"
    assert "url" in result and "error" not in result
    assert music._create_selection_playlist("test", '["1"]')["repeated_request"]
    assert len(writes) == 2
    client.close()


def test_tracks_are_validated_before_playlist_write(music):
    writes = []
    def handler(request):
        writes.append(request.method)
        return httpx.Response(200, json={"result": []})
    client = setup_yandex(music, handler)
    with pytest.raises(ValueError):
        music._create_selection_playlist("test", '["1"]')
    assert writes == ["GET"]
    client.close()


def test_artist_lookup_filters_other_artists_instead_of_name_search_covers(music):
    def handler(request):
        if request.url.path == "/search":
            return httpx.Response(200, json={"result": {"artists": {"results": [{"id": 8, "name": "Band"}]}}})
        raw = tracks_response(["1", "2", "1"])
        raw[1]["artists"] = [{"id": "9", "name": "Cover Band"}]
        return httpx.Response(200, json={"result": {"popularTracks": raw}})
    client = setup_yandex(music, handler)
    result = music._artist_selection("Band", 15)
    assert result["count"] == 1 and result["tracks"][0]["track_id"] == "1"
    client.close()


def test_temporary_selection_uses_the_whole_queue(music, monkeypatch):
    tracks = [Track("yandex", "1", "A", "B"), Track("yandex", "2", "C", "D")]
    monkeypatch.setattr(music, "_selection_tracks", lambda ids: tracks)
    captured = []
    monkeypatch.setattr(music, "_start_yandex_queue_sync", lambda values, **kwargs: captured.append((values, kwargs)) or {"playing": False})
    result = music._play_selection('["1", "2"]', "test")
    assert captured[0][0] == tracks and captured[0][1]["source"] == "selection"
    assert not result["playing"]


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://localhost/a", "http://127.0.0.1/a", "http://[::1]/a", "http://10.0.0.1/a", "https://user:pass@example.com/a"])
def test_radio_rejects_private_or_credential_urls(url):
    with pytest.raises(ValueError):
        public_url(url)


def test_catalogue_filters_deduplicates_and_caches(music):
    catalogue = music._internet_radio
    catalogue.servers = ["test.api.radio-browser.info"]
    calls = []
    def handler(request):
        calls.append(request)
        assert request.url.params["hidebroken"] == "true"
        assert request.url.params["countrycode"] == "RU"
        return httpx.Response(200, json=[{"stationuuid": "a", "name": "Radio", "url_resolved": "https://radio.example/live.m3u8", "hls": 1},
                                       {"stationuuid": "b", "url": "http://127.0.0.1"},
                                       {"stationuuid": "c", "url": "https://radio.example/live.m3u8"}])
    catalogue.http.close(); catalogue.http = httpx.Client(transport=httpx.MockTransport(handler))
    result = catalogue.search(country="ru")
    assert len(result["stations"]) == 1 and result["stations"][0]["hls"]
    assert catalogue.search(country="ru")["cached"] and len(calls) == 1


def test_favorites_preserve_ids_and_survive_reopen(music):
    station = {"station_id": "real-uuid", "name": "Radio", "url": "https://radio.example/live", "country": "DE"}
    music._internet_radio.save(station)
    result = music._internet_radio.favorites()
    assert result[0]["station_id"] == "real-uuid" and result[0]["country"] == "DE"
    music._internet_radio.save(result[0], remove=True)
    assert not music._internet_radio.favorites()


def test_live_radio_has_no_song_commands_and_exposes_live_status(music):
    music._playback_state.update(service="internet_radio", track_id="station", live=True)
    for action in ("next", "previous", "like", "seek", "ended"):
        with pytest.raises(Exception, match="прямой эфир"):
            music._playback_control_sync(action)
    assert asyncio.run(music.ui_playback_status())["live"]


def test_relay_rewrites_nested_hls_and_keys_without_leaking_remote_urls(music, monkeypatch):
    monkeypatch.setattr(radio_module.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("8.8.8.8", 443))])
    real_client = httpx.Client
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "application/vnd.apple.mpegurl"},
            content=b'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n#EXT-X-STREAM-INF:BANDWIDTH=128000\nchild\n')
    monkeypatch.setattr(radio_module, "httpx", SimpleNamespace(Client=lambda **kw: real_client(transport=httpx.MockTransport(handler)), HTTPError=httpx.HTTPError))
    relay = music._radio_relay
    relay.http.close()
    relay.http = real_client(transport=httpx.MockTransport(handler))
    path = relay.start("https://radio.example/root.m3u8", 3, True)
    music._playback_state.update(service="internet_radio", revision=3)
    fake = SimpleNamespace(path=path, headers={}, wfile=io.BytesIO(), send_response=lambda status: None,
                           send_header=lambda *a: None, end_headers=lambda: None, send_error=lambda code: pytest.fail(str(code)))
    relay.serve(fake, 3, path.split('/')[-1].split('.')[0])
    body = fake.wfile.getvalue()
    assert b'URI="/' in body and b'https://radio.example' not in body
    assert body.decode().splitlines()[-1].endswith('.m3u8')
    assert len(relay.assets) == 3


def test_radio_search_ui_returns_job_before_slow_network_finishes(music, monkeypatch):
    import threading
    started, release = threading.Event(), threading.Event()
    def slow(*args):
        started.set(); release.wait(2); return {"stations": []}
    monkeypatch.setattr(music._internet_radio, "search", slow)
    result = asyncio.run(music.ui_stations_search(query="radio"))
    assert result["pending"] and started.wait(1)
    release.set()


def test_new_voice_tools_accept_astra_kwargs_envelope(music, monkeypatch):
    captured = []
    monkeypatch.setattr(music._internet_radio, "search", lambda *args: captured.append(args) or {"stations": []})
    asyncio.run(music.search_radio_stations(kwargs='{"query":"Jazz","country":"DE","offset":50}'))
    assert captured == [("Jazz", "DE", "", 50)]
    monkeypatch.setattr(music, "_create_selection_playlist", lambda title, ids: {"title": title, "ids": ids})
    assert asyncio.run(music.create_yandex_playlist(kwargs='{"title":"Test","track_ids_json":"[1,2]"}')) == {"title": "Test", "ids": "[1,2]"}


def favorite_stations(music):
    for station_id, name in (("a", "Радио Джаз"), ("b", "Rock FM"), ("c", "Rock Hits")):
        music._internet_radio.save({"station_id": station_id, "name": name, "url": f"https://radio.example/{station_id}"})


def test_voice_reads_local_favorites_without_catalogue(music, monkeypatch):
    favorite_stations(music)
    def no_network(*args):
        raise AssertionError("Favorites must not require the public catalogue")
    monkeypatch.setattr(music._internet_radio, "search", no_network)
    result = asyncio.run(music.list_favorite_radio_stations(kwargs='{"query":"rock"}'))
    assert [station["station_id"] for station in result["stations"]] == ["b", "c"]
    assert result["count"] == 2 and result["source"] == "favorites"


def test_voice_plays_unique_saved_name_and_explicit_id(music, monkeypatch):
    favorite_stations(music)
    calls = []
    monkeypatch.setattr(music, "_start_internet_radio", lambda station: calls.append(station) or {"playing": True})
    assert asyncio.run(music.play_favorite_radio_station(kwargs='{"name":"  РАДИО   джаз "}'))["playing"]
    assert calls[-1]["station_id"] == "a"
    assert music._play_favorite_radio("", "c")["playing"]
    assert calls[-1]["name"] == "Rock Hits"
    assert music._play_favorite_radio("Hits", "")["playing"]
    assert calls[-1]["station_id"] == "c"


@pytest.mark.parametrize("name,station_id", [("Rock", ""), ("", ""), ("Missing", ""), ("Радио Джаз", "unknown")])
def test_favorite_miss_or_ambiguity_does_not_change_playback(music, monkeypatch, name, station_id):
    favorite_stations(music)
    def must_not_play(*args):
        raise AssertionError("Must resolve the user's choice first")
    monkeypatch.setattr(music, "_start_internet_radio", must_not_play)
    result = asyncio.run(music.play_favorite_radio_station(name=name, station_id=station_id))
    assert not result["playing"] and not result["success"]
    if name == "Rock" or not name:
        assert result["needs_station_choice"]
