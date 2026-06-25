"""On-chain reference source adapter."""

from __future__ import annotations

from typing import AsyncIterator

import pandas as pd

from axiom.dataeng.errors import NoData, SourceError
from axiom.dataeng.source import SourceHealth, Stream


class OnChainSource:
    def __init__(self, provider: str = "", api_key: str = "", session: object | None = None) -> None:
        self.id = f"onchain:{str(provider or 'disabled').strip().lower()}"
        self.provider = str(provider or "").strip().lower()
        self.api_key = str(api_key or "").strip()
        self.session = session
        self.capabilities = {Stream.ONCHAIN, Stream.MACRO}

    @property
    def enabled(self) -> bool:
        return bool(self.provider and self.api_key)

    def fetch(self, ref: object, stream: Stream, since: object | None = None, until: object | None = None) -> pd.DataFrame:
        if not self.enabled:
            raise SourceError("on-chain source disabled: provider and api key are required")
        if self.provider == "coingecko-pro":
            return self._fetch_coingecko_market_chart(ref)
        raise SourceError(f"unsupported on-chain provider: {self.provider}")

    async def stream(self, ref: object, stream: Stream) -> AsyncIterator[pd.DataFrame]:
        raise SourceError("on-chain streaming is not supported")
        yield  # pragma: no cover

    def health(self) -> SourceHealth:
        return SourceHealth(
            source=self.id,
            status="closed" if self.enabled else "disabled",
            message="" if self.enabled else "provider/api key not configured",
        )

    def _fetch_coingecko_market_chart(self, ref: object) -> pd.DataFrame:
        if self.session is None:
            raise SourceError("session is required for coingecko-pro fetches in this adapter slice")
        response = self.session.get(
            "https://pro-api.coingecko.com/api/v3/coins/bitcoin/market_chart",
            params={"vs_currency": "usd", "days": "max"},
            headers={"x-cg-pro-api-key": self.api_key},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        prices = payload.get("prices") if isinstance(payload, dict) else None
        if not prices:
            raise NoData("coingecko-pro returned no prices")
        return pd.DataFrame(
            [
                {
                    "timestamp": pd.Timestamp(int(item[0]), unit="ms", tz="UTC"),
                    "btc_price_usd": float(item[1]),
                }
                for item in prices
                if isinstance(item, list) and len(item) >= 2
            ]
        )


def source_from_settings() -> OnChainSource:
    from axiom.dataeng.settings import load_data_engine_settings

    settings = load_data_engine_settings()
    return OnChainSource(settings.onchain_provider, settings.onchain_api_key)
