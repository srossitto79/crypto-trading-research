"""Single-shot loopback HTTP listener for OAuth redirect capture."""

from __future__ import annotations

import http.server
import logging
import threading
import time
import urllib.parse
from collections.abc import Callable
from typing import Optional

log = logging.getLogger("axiom.auth.callback_listener")


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 — http.server convention
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        listener: "LoopbackCallbackListener" = self.server.listener  # type: ignore[attr-defined]

        code_values = params.get("code")
        state_values = params.get("state")
        if parsed.path == "/auth/callback" and code_values:
            listener.capture(code_values[0], state_values[0] if state_values else None)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body style='font-family:sans-serif;padding:2rem'>"
                b"<h2>Signed in.</h2><p>You can close this tab.</p>"
                b"</body></html>"
            )
        else:
            self.send_response(400)
            self.end_headers()

    def log_message(self, format, *args):
        pass


class LoopbackCallbackListener:
    """Bind 127.0.0.1:<port> for one OAuth callback, then release."""

    def __init__(
        self,
        port: int = 1455,
        ttl_seconds: int = 300,
        on_callback: Callable[[str, str | None], None] | None = None,
    ):
        self.port = port
        self.ttl_seconds = ttl_seconds
        self.on_callback = on_callback
        self.code: Optional[str] = None
        self.state: Optional[str] = None
        self.bind_error: Optional[str] = None
        self._server: Optional[http.server.HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._started_at: float = 0.0

    def start(self) -> bool:
        try:
            self._server = http.server.HTTPServer(("127.0.0.1", self.port), _Handler)
        except OSError as exc:
            self.bind_error = str(exc)
            log.warning("loopback callback bind failed on port %d: %s", self.port, exc)
            return False
        self._server.listener = self  # type: ignore[attr-defined]
        self._started_at = time.time()
        # Must set self._server.listener BEFORE starting the thread —
        # _Handler.do_GET reads it via self.server.listener.
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return True

    def _serve(self):
        assert self._server is not None
        try:
            self._server.serve_forever(poll_interval=0.25)
        except Exception as exc:
            log.debug("loopback listener exited: %s", exc)

    def expired(self) -> bool:
        return (time.time() - self._started_at) > self.ttl_seconds

    def capture(self, code: str, state: str | None) -> None:
        self.code = code
        self.state = state
        if self.on_callback is None:
            return
        try:
            self.on_callback(code, state)
        except Exception as exc:
            log.warning("loopback callback side-effect failed: %s", exc)

    def shutdown(self):
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            self._server = None
