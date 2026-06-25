from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar
from typing import Awaitable, Callable, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_REQUEST_ID: ContextVar[Optional[str]] = ContextVar("AXIOM_request_id", default=None)

REQUEST_ID_HEADER = "x-request-id"


def get_request_id() -> Optional[str]:
    return _REQUEST_ID.get()


def set_request_id(request_id: Optional[str]) -> None:
    _REQUEST_ID.set(request_id)


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


class RequestIdLogFilter(logging.Filter):
    """Inject the active request_id (or '-') into every log record so that
    formatters can use %(request_id)s without crashing on records emitted
    outside an HTTP request."""

    def filter(self, record: logging.LogRecord) -> bool:
        rid = _REQUEST_ID.get()
        record.request_id = rid or "-"
        return True


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Honor incoming X-Request-ID or mint a new one. Echoes it in the
    response so clients can quote it when reporting issues."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER, "").strip()
        request_id = incoming or new_request_id()
        token = _REQUEST_ID.set(request_id)
        try:
            response = await call_next(request)
        finally:
            _REQUEST_ID.reset(token)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
