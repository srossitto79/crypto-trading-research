"""Typed data-engine errors and read status records."""

from __future__ import annotations

from dataclasses import dataclass, field


class DataEngineError(Exception):
    """Base class for data-engine failures."""


class NoData(DataEngineError):
    """A source returned no data for the requested window."""


class SourceError(DataEngineError):
    """A source failed unexpectedly."""


class StaleData(DataEngineError):
    """Available data is older than the configured freshness threshold."""


@dataclass
class PartialData(DataEngineError):
    """A request succeeded with known gaps."""

    gaps: list[dict[str, object]] = field(default_factory=list)
    message: str = "partial data"

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class ReadStatus:
    ok: bool
    source: str
    stream: str
    status: str = "ok"
    message: str = ""
    gaps: tuple[dict[str, object], ...] = ()
