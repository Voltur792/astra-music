import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import logging
import socket
import struct
import sys
import threading
from types import SimpleNamespace

import pytest

from src.local_http import LocalRequestHandler


@pytest.mark.parametrize("error", [ConnectionAbortedError(10053, "aborted"), ConnectionResetError(10054, "reset")])
def test_idle_abort_logs_phase_and_completed_count_without_traceback(error, caplog):
    def fail(size):
        raise error
    handler = LocalRequestHandler.__new__(LocalRequestHandler)
    handler.rfile = SimpleNamespace(readline=fail)
    handler._completed_requests = 2
    with caplog.at_level(logging.INFO, logger="astra-music.http"):
        handler.handle_one_request()
    assert handler.close_connection
    assert "awaiting request" in caplog.text and "completed_requests=2" in caplog.text
    assert len(caplog.records) == 1 and caplog.records[0].exc_info is None


def test_handler_abort_and_unrelated_read_error_are_not_swallowed(monkeypatch):
    handler = LocalRequestHandler.__new__(LocalRequestHandler)
    def during_request(self):
        self._waiting_for_request_line = False
        raise ConnectionAbortedError(10053, "during handler")
    monkeypatch.setattr(BaseHTTPRequestHandler, "handle_one_request", during_request)
    with pytest.raises(ConnectionAbortedError):
        handler.handle_one_request()
    def unexpected(self):
        raise RuntimeError("real bug")
    monkeypatch.setattr(BaseHTTPRequestHandler, "handle_one_request", unexpected)
    with pytest.raises(RuntimeError, match="real bug"):
        handler.handle_one_request()


def test_real_client_reset_after_success_does_not_break_server(caplog):
    awaiting, finished = threading.Event(), threading.Event()
    failures = []
    class Handler(LocalRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *args):
            pass
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")
        def handle_one_request(self):
            if getattr(self, "_completed_requests", 0):
                awaiting.set()
            super().handle_one_request()
        def finish(self):
            super().finish()
            finished.set()
    class Server(ThreadingHTTPServer):
        daemon_threads = True
        def handle_error(self, *args):
            failures.append(sys.exc_info()[1])
    server = Server(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
    try:
        with caplog.at_level(logging.INFO, logger="astra-music.http"):
            connection.request("GET", "/")
            assert connection.getresponse().read() == b"ok"
            assert awaiting.wait(2)
            linger = struct.pack("HH" if sys.platform == "win32" else "ii", 1, 0)
            connection.sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, linger)
            connection.close()
            assert finished.wait(2)
        assert not failures
        assert "completed_requests=1" in caplog.text
        fresh = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
        try:
            fresh.request("GET", "/")
            assert fresh.getresponse().read() == b"ok"
        finally:
            fresh.close()
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        worker.join(2)
