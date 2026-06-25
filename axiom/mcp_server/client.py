"""Thin auth-aware HTTP client for hitting the running Axiom API.

The MCP server is an external process and must cross the wire rather than
reach into Axiom internals — the user might be running the backend with a
different Python env, under uvicorn workers, or on another machine. Going
through HTTP also means auth/permissions match exactly what a browser
operator would get.
"""

from __future__ import annotations

import os
from typing import Any

import httpx


class AxiomClient:
    """Blocking httpx client bound to a single Axiom backend."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        operator_key: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("AXIOM_API_URL") or "http://127.0.0.1:8003").rstrip("/")
        self.api_key = api_key or os.environ.get("AXIOM_API_KEY") or ""
        self.operator_key = operator_key or os.environ.get("AXIOM_OPERATOR_KEY") or ""
        try:
            self.timeout = float(timeout if timeout is not None else os.environ.get("AXIOM_MCP_TIMEOUT") or 60.0)
        except (TypeError, ValueError):
            self.timeout = 60.0
        self._client: httpx.Client | None = None

    def _headers(self) -> dict[str, str]:
        headers = {"accept": "application/json"}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        if self.operator_key:
            headers["x-operator-key"] = self.operator_key
        return headers

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.base_url,
                headers=self._headers(),
                timeout=self.timeout,
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}
        response = self._get_client().get(path, params=clean_params or None)
        self._raise_for_status(response)
        if not response.content:
            return None
        return response.json()

    def post(self, path: str, json_body: dict[str, Any] | None = None) -> Any:
        response = self._get_client().post(path, json=json_body or {})
        self._raise_for_status(response)
        if not response.content:
            return None
        return response.json()

    def _raise_for_status(self, response: httpx.Response) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = response.text.strip()
            if len(body) > 1000:
                body = body[:1000] + "..."
            message = f"{exc} | response body: {body}" if body else str(exc)
            raise httpx.HTTPStatusError(
                message,
                request=exc.request,
                response=exc.response,
            ) from exc
