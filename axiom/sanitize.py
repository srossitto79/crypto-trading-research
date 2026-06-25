"""Phase 1 (P1-T05) — operator-input sanitizer.

Strips any ``<brain-context>...</brain-context>`` block that an operator (or
any external input source) might paste into the cycle prompt or chat. The
fence is the runtime's, not the operator's; if it appears in input, it is
either a copy/paste accident or a prompt-injection attempt.

A counter (``brain:fence_strip_count`` in the kv store) records how often a
strip fires so ``/api/diagnostics/snapshot`` can surface unusual activity.

The regex matches any open tag with arbitrary attributes, e.g.
``<brain-context source="ops">...</brain-context>``, with ``re.DOTALL`` so
the block can span newlines.
"""
from __future__ import annotations

import logging
import re

from axiom.db import kv_get, kv_set

log = logging.getLogger("axiom.sanitize")

_FENCE_PATTERN = re.compile(r"<brain-context\b[^>]*>.*?</brain-context>", re.DOTALL | re.IGNORECASE)
_STRIP_COUNT_KEY = "brain:fence_strip_count"


def strip_brain_context_fences(text: str | None) -> tuple[str, int]:
    """Remove every fenced block from `text`. Returns ``(cleaned, strip_count)``.

    The counter increments by the number of blocks removed (not bytes).
    Idempotent: running twice on the same input yields zero further strips.
    """
    if not text:
        return ("" if text is None else text, 0)
    matches = _FENCE_PATTERN.findall(text)
    if not matches:
        return text, 0
    cleaned = _FENCE_PATTERN.sub("", text)
    return cleaned, len(matches)


def sanitize_operator_input(text: str | None, *, source: str = "operator") -> str:
    """Strip fence blocks from operator-supplied text and bump the global counter.

    `source` is recorded only in the warning log. The persistent counter is a
    single integer in kv (no per-source breakdown).
    """
    cleaned, strips = strip_brain_context_fences(text)
    if strips > 0:
        try:
            current = int(kv_get(_STRIP_COUNT_KEY, 0) or 0)
            kv_set(_STRIP_COUNT_KEY, current + strips)
        except Exception:  # noqa: BLE001
            log.warning("brain fence strip counter persist failed", exc_info=True)
        log.warning(
            "stripped %d <brain-context> block(s) from %s input", strips, source
        )
    return cleaned


def fence_strip_count() -> int:
    """Return the cumulative number of fence blocks stripped from operator input."""
    try:
        return int(kv_get(_STRIP_COUNT_KEY, 0) or 0)
    except Exception:  # noqa: BLE001
        return 0


__all__ = [
    "fence_strip_count",
    "sanitize_operator_input",
    "strip_brain_context_fences",
]
