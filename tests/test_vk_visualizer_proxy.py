import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from grpc import RpcError

from src.plugin import AstraMusic
from src.playback_host import proxy_audio


@pytest.mark.parametrize("service,url,expected", [
    ("vk", "https://cs1.vkuseraudio.ru/file.mp3?secret=1", True),
    ("vk", "https://cs1.vkuseraudio.ru/index.m3u8", False),
    ("yandex", "https://music.yandex.net/file", True),
    ("spotify", "https://example.com/a.mp3", False),
])
def test_only_supported_file_streams_use_same_origin_proxy(service, url, expected):
    assert proxy_audio({"service": service, "url": url}) is expected


def test_vk_current_and_prepared_tracks_use_same_origin_with_ranges(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTRA_MUSIC_DATA_DIR", str(tmp_path))
    music = AstraMusic()
    upstream = "https://cs1.vkuseraudio.ru/file.mp3?secret=1"
    next_url = "https://cs2.vkuseraudio.ru/next.mp3?secret=2"
    music._playback_state.update(service="vk", revision=4, url=upstream)
    import time
    music._prepared_next = {"service": "vk", "url": next_url, "expires_at": time.time() + 60}
    requests = []
    class Response:
        status_code = 206
        headers = {"Content-Type": "audio/mpeg", "Content-Length": "4", "Content-Range": "bytes 0-3/4"}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_raw(self): yield b"test"
    def stream(method, url, **kwargs):
        requests.append((url, kwargs["headers"]))
        return Response()
    monkeypatch.setattr("src.playback_host.httpx.stream", stream)
    base = music._audio_host.ensure_server()
    try:
        with httpx.Client(trust_env=False) as client:
            state = client.post(base + "/api", json={"method": "music_playback_state"}).json()
            assert state["url"].startswith(base + "/stream/") and "secret" not in state["url"]
            response = client.get(state["url"], headers={"Range": "bytes=0-3"})
            assert response.status_code == 206 and response.content == b"test"
            assert requests[0] == (upstream, {"Accept-Encoding": "identity", "Range": "bytes=0-3"})
            key = hashlib.sha256(next_url.encode()).hexdigest()
            assert client.get(base + "/stream/" + key).content == b"test"
            assert client.get(base + "/stream/not-a-current-track").status_code == 404
    finally:
        music._audio_host.close()


@pytest.mark.parametrize("daemon_error", [None, RpcError, asyncio.TimeoutError])
def test_support_opens_prefilled_issue_form(tmp_path, monkeypatch, daemon_error):
    monkeypatch.setenv("ASTRA_MUSIC_DATA_DIR", str(tmp_path))
    calls = []
    monkeypatch.setattr("src.plugin.open_in_browser", lambda url: calls.append(url) or True)
    monkeypatch.setattr("src.plugin.platform.system", lambda: "Darwin")
    monkeypatch.setattr("src.plugin.platform.mac_ver", lambda: ("15.6", ("", "", ""), "arm64"))
    monkeypatch.setattr("src.plugin.platform.machine", lambda: "arm64")

    async def daemon_info():
        if daemon_error:
            raise daemon_error()
        return SimpleNamespace(version="0.2.7+test", grpc_port=32000)

    music = AstraMusic()
    music.host = SimpleNamespace(get_daemon_info=daemon_info)
    assert asyncio.run(music.ui_support_open(url="https://untrusted.test", os="untrusted"))["success"]
    url, = map(urlsplit, calls)
    assert (url.scheme, url.netloc, url.path) == ("https", "github.com", "/Voltur792/astra-music/issues/new")
    root = Path(__file__).resolve().parents[1]
    manifest = (root / "plugin.toml").read_text(encoding="utf-8")
    version = manifest.split('version = "', 1)[1].split('"', 1)[0]
    expected = {"template": ["bug_report.yml"], "os": ["macOS 15.6 arm64"], "plugin-version": [version]}
    if daemon_error is None:
        expected["astra-version"] = ["0.2.7+test"]
    assert parse_qs(url.query) == expected


@pytest.mark.parametrize("opened", [True, False])
def test_support_response_survives_real_sdk_serialization(tmp_path, monkeypatch, opened):
    import json
    from astra_plugin_sdk.plugin import _CapabilityServicer, plugin_pb2
    monkeypatch.setenv("ASTRA_MUSIC_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("src.plugin.open_in_browser", lambda url: opened)
    service = _CapabilityServicer(AstraMusic())
    response = asyncio.run(service.CallFromUi(
        plugin_pb2.PluginUiCallRequest(method="music_support_open", params_json="{}"), None))
    if opened:
        assert not response.error
        assert json.loads(response.result_json) == {"success": True}
    else:
        assert response.error and not response.result_json
