"""Registry construction from DataEngineSettings."""

from __future__ import annotations

from axiom.dataeng.ccxt_source import CcxtSource
from axiom.dataeng.settings import DataEngineSettings, load_data_engine_settings
from axiom.dataeng.source import SourceRegistry, Stream


def build_source_registry(settings: DataEngineSettings | None = None) -> SourceRegistry:
    resolved = settings or load_data_engine_settings()
    registry = SourceRegistry()
    for exchange_id in resolved.enabled_exchanges:
        normalized = str(exchange_id or "").strip().lower()
        if not normalized:
            continue
        registry.register(CcxtSource(normalized))
    return registry


def resolve_source_for_stream(
    registry: SourceRegistry,
    settings: DataEngineSettings,
    stream: Stream,
) -> object:
    priority = settings.source_priority.get(stream.value) or settings.enabled_exchanges
    return registry.resolve(stream, priority)
