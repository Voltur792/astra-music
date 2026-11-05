import asyncio
import threading

from src import loopback_http
from src.timer_integration import IntegrationServer, integration_call


def test_integration_calls_reuse_one_tcp_connection(tmp_path, monkeypatch):
    monkeypatch.setenv("SPT_INTEGRATION_DIR", str(tmp_path))
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    loop = asyncio.new_event_loop()
    worker = threading.Thread(target=loop.run_forever, daemon=True)
    worker.start()
    server = IntegrationServer("test", {"echo": lambda value: {"value": value}})
    loopback_http.close()
    sockets = set()
    versions = set()
    def record(response):
        sockets.add(response.extensions["network_stream"].get_extra_info("socket").getsockname())
        versions.add(response.http_version)
    loopback_http.client().event_hooks["response"].append(record)
    server.start(loop)
    try:
        for index in range(50):
            assert integration_call("test", "echo", value=index) == {"value": index}
        assert len(sockets) == 1
        assert versions == {"HTTP/1.1"}
    finally:
        loopback_http.close()
        server.close()
        loop.call_soon_threadsafe(loop.stop)
        worker.join(timeout=3)
        loop.close()
