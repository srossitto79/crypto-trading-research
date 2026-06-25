from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from axiom.correlation import (
    REQUEST_ID_HEADER,
    CorrelationIdMiddleware,
    RequestIdLogFilter,
    get_request_id,
    new_request_id,
)


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)

    @app.get("/echo")
    def echo():
        return {"request_id": get_request_id()}

    return app


def test_correlation_middleware_mints_id_when_none_supplied():
    client = TestClient(_make_app())
    response = client.get("/echo")
    assert response.status_code == 200
    body = response.json()
    rid = body["request_id"]
    assert rid and len(rid) == 16
    assert response.headers[REQUEST_ID_HEADER] == rid


def test_correlation_middleware_honors_incoming_id():
    client = TestClient(_make_app())
    response = client.get("/echo", headers={REQUEST_ID_HEADER: "client-supplied-id"})
    assert response.status_code == 200
    assert response.json()["request_id"] == "client-supplied-id"
    assert response.headers[REQUEST_ID_HEADER] == "client-supplied-id"


def test_correlation_contextvar_resets_between_requests():
    client = TestClient(_make_app())
    a = client.get("/echo").json()["request_id"]
    b = client.get("/echo").json()["request_id"]
    assert a != b
    # Outside any request, the contextvar reverts to None.
    assert get_request_id() is None


def test_log_filter_injects_request_id_attribute(capsys):
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(request_id)s] %(message)s"))
    logger = logging.getLogger("axiom.test.correlation")
    logger.handlers = [handler]
    logger.addFilter(RequestIdLogFilter())
    logger.setLevel(logging.INFO)

    logger.info("no request bound")  # should render with "-"

    captured = capsys.readouterr()
    assert "[-]" in captured.err or "[-]" in captured.out


def test_new_request_id_is_short_hex():
    rid = new_request_id()
    assert len(rid) == 16
    int(rid, 16)
