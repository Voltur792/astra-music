"""Bounded, proxy-free connection pool shared by local Python callers."""
import atexit
import threading

import httpx

_client = None
_lock = threading.Lock()


def client():
    global _client
    with _lock:
        if _client is None:
            _client = httpx.Client(
                trust_env=False, follow_redirects=False,
                limits=httpx.Limits(max_connections=8, max_keepalive_connections=8, keepalive_expiry=30),
            )
        return _client


def close():
    global _client
    with _lock:
        if _client is not None:
            _client.close()
            _client = None


atexit.register(close)
