import httpx
import pytest

from src.plugin import AstraMusic, MusicError, YandexMusicClient
from src.yandex_playlist import playlist_reference

PLAYLIST_UUID = "36ee80f9-b411-bcce-9e3a-de5a549fa31a"


def song(number):
    return {"id": str(number), "title": f"Song {number}", "artists": [{"name": "Artist"}], "available": True}


def client_for(handler):
    client = YandexMusicClient("dummy-test-token")
    client.http.close()
    client.http = httpx.Client(transport=httpx.MockTransport(handler), headers=client.http.headers)
    return client


@pytest.mark.parametrize("value", [PLAYLIST_UUID, f"https://music.yandex.ru/playlists/{PLAYLIST_UUID}?utm_source=desktop&utm_medium=copy_link", f"https://music.yandex.com/playlists/{PLAYLIST_UUID.upper()}/"])
def test_uuid_references_ignore_share_tracking(value):
    assert playlist_reference(value) == ("uuid", PLAYLIST_UUID, "")


@pytest.mark.parametrize("value", ["https://music.yandex.ru/users/another.owner/playlists/123?utm_medium=copy_link", "owner:another.owner:123"])
def test_legacy_references_preserve_a_different_owner(value):
    assert playlist_reference(value) == ("owner", "123", "another.owner")


@pytest.mark.parametrize("value", [f"http://music.yandex.ru/playlists/{PLAYLIST_UUID}", f"https://music.yandex.ru.evil.test/playlists/{PLAYLIST_UUID}", f"https://music.yandex.ru@localhost/playlists/{PLAYLIST_UUID}", f"https://music.yandex.ru:8443/playlists/{PLAYLIST_UUID}", f"https://music.yandex.ru/playlists/{PLAYLIST_UUID}/extra", "https://music.yandex.ru/users/%2E%2E/playlists/123", "https://music.yandex.ru/users/a%2Fb/playlists/123", "owner:..:123", "../../account/status"])
def test_untrusted_or_malformed_references_are_rejected(value):
    with pytest.raises(ValueError):
        playlist_reference(value)


def test_uuid_loader_uses_the_verified_singular_playlist_endpoint_without_resolving_current_uid():
    requests = []
    def handle(request):
        requests.append(request)
        assert request.url.host == "api.music.yandex.net"
        assert request.url.path == f"/playlist/{PLAYLIST_UUID}"
        return httpx.Response(200, json={"result": {"tracks": [{"track": song(51)}]}})
    client = client_for(handle)
    try:
        tracks = client.playlist_tracks(PLAYLIST_UUID, 500)
        assert [track.track_id for track in tracks] == ["51"]
        assert tracks[0].context_id == PLAYLIST_UUID
        assert len(requests) == 1
    finally:
        client.close()


def test_foreign_owner_never_falls_back_to_the_connected_account():
    def handle(request):
        assert request.url.path == "/users/another.owner/playlists/123"
        return httpx.Response(200, json={"result": {"list": [{"track": song(52)}]}})
    client = client_for(handle)
    client._user_id = "777"
    try:
        assert client.playlist_tracks("owner:another.owner:123", 500)[0].track_id == "52"
    finally:
        client.close()


def test_short_track_hydration_preserves_playlist_order_duplicates_and_skips_unavailable():
    def handle(request):
        if request.url.path.startswith("/playlist/"):
            return httpx.Response(200, json={"result": {"tracks": [{"id": "2"}, {"id": "1"}, {"id": "2"}, {"id": "3"}]}})
        assert request.url.path == "/tracks"
        assert request.url.params["trackIds"] == "2,1,3"
        return httpx.Response(200, json={"result": [song(1), song(2), {**song(3), "available": False}]})
    client = client_for(handle)
    try:
        assert [t.track_id for t in client.playlist_tracks(PLAYLIST_UUID, 500)] == ["2", "1", "2"]
    finally:
        client.close()


def test_hydration_is_batched_and_obeys_the_requested_limit():
    batches = []
    def handle(request):
        if request.url.path.startswith("/playlist/"):
            return httpx.Response(200, json={"result": {"tracks": [{"id": str(n)} for n in range(1, 101)]}})
        batch = request.url.params["trackIds"].split(",")
        batches.append(batch)
        return httpx.Response(200, json={"result": [song(n) for n in batch]})
    client = client_for(handle)
    try:
        assert len(client.playlist_tracks(PLAYLIST_UUID, 65)) == 65
        assert [len(batch) for batch in batches] == [50, 15]
    finally:
        client.close()


def test_missing_playlist_has_localized_error_without_server_details():
    client = client_for(lambda request: httpx.Response(404, json={"error": "private-server-detail"}))
    try:
        with pytest.raises(MusicError, match="не найден") as error:
            client.playlist_tracks(PLAYLIST_UUID, 500)
        assert "private-server-detail" not in str(error.value)
    finally:
        client.close()


def test_discord_source_loads_a_shared_yandex_playlist_without_using_own_playlist_listing(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTRA_MUSIC_DATA_DIR", str(tmp_path))
    client = client_for(lambda request: httpx.Response(200, json={"result": {"tracks": [{"track": song(51)}, {"track": song(52)}]}}))
    plugin = AstraMusic()
    monkeypatch.setattr(plugin, "_client", lambda service: client)
    try:
        tracks, radio, batch = plugin._discord_music._prepare("yandex", "", "", "", None, "playlist", PLAYLIST_UUID)
        assert [t["track_id"] for t in tracks] == ["51", "52"]
        assert all(t["service"] == "yandex" for t in tracks)
        assert radio is False and batch == ""
    finally:
        client.close()
