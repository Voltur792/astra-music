"""Shared handling for peers aborting an idle loopback HTTP connection."""
from http.server import BaseHTTPRequestHandler
import logging

log = logging.getLogger("astra-music.http")


class LocalRequestHandler(BaseHTTPRequestHandler):
    connection_role = "local"

    def parse_request(self):
        # From here on an error belongs to header parsing or request handling,
        # and must not be classified as an idle disconnect.
        self._waiting_for_request_line = False
        parsed = super().parse_request()
        self._request_parsed = parsed
        return parsed

    def handle_one_request(self):
        self._waiting_for_request_line = True
        self._request_parsed = False
        try:
            super().handle_one_request()
        except (ConnectionAbortedError, ConnectionResetError) as error:
            if not self._waiting_for_request_line:
                raise
            self.close_connection = True
            log.info("Local HTTP peer disconnected while awaiting request (server=%s, error=%s, completed_requests=%s)",
                     self.connection_role, getattr(error, "winerror", None) or error.errno,
                     getattr(self, "_completed_requests", 0))
            return
        if self._request_parsed:
            self._completed_requests = getattr(self, "_completed_requests", 0) + 1
