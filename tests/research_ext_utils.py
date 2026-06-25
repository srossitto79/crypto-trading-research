"""Shared helpers for the external research-source tool tests.

Successful external-fetched tool results are wrapped in an
``<untrusted_content source="external_fetch">`` prompt-injection envelope
before being handed to the model (see ``axiom.agents.tools_research``).
Error/blocked results are returned as raw JSON. ``parse_tool_result`` strips
the envelope when present so tests can assert on the underlying payload
regardless of which path produced it.
"""

from __future__ import annotations

import json
from typing import Any

from axiom.agents.tools_research import _UNTRUSTED_PREFIX, _UNTRUSTED_SUFFIX


def parse_tool_result(raw: str) -> Any:
    """JSON-decode a research tool result, unwrapping the untrusted envelope."""
    if raw.startswith(_UNTRUSTED_PREFIX) and raw.endswith(_UNTRUSTED_SUFFIX):
        raw = raw[len(_UNTRUSTED_PREFIX) : -len(_UNTRUSTED_SUFFIX)]
    return json.loads(raw)
