"""Tests for Polygon.io REST client (mocked — no real API calls)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from axiom.polygon_client import PolygonClient, PolygonAuthError


@pytest.fixture
def mock_client():
    """Create a PolygonClient with a fake key and mocked httpx."""
    with patch("axiom.polygon_client.get_polygon_api_key", return_value="test_key_1234"):
        client = PolygonClient(api_key="test_key_1234", calls_per_minute=6000)
    yield client
    client.close()


def test_init_requires_api_key():
    with patch("axiom.polygon_client.get_polygon_api_key", return_value=None):
        with pytest.raises(PolygonAuthError):
            PolygonClient(api_key=None)


def test_results_to_dataframe(mock_client: PolygonClient):
    results = [
        {"t": 1710000000000, "o": 170.0, "h": 172.0, "l": 169.0, "c": 171.0, "v": 1000},
        {"t": 1710003600000, "o": 171.0, "h": 173.0, "l": 170.0, "c": 172.0, "v": 1200},
    ]
    df = mock_client._results_to_dataframe(results, "AAPL")
    assert len(df) == 2
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert df["open"].iloc[0] == 170.0
    assert df["volume"].iloc[1] == 1200.0


def test_results_to_dataframe_skips_invalid(mock_client: PolygonClient):
    results = [
        {"t": 1710000000000, "o": 170.0, "h": 172.0, "l": 169.0, "c": 171.0, "v": 1000},
        {"t": 1710003600000, "o": None, "h": 173.0, "l": 170.0, "c": 172.0, "v": 1200},  # Missing open
        {"t": 1710007200000, "o": -1.0, "h": 173.0, "l": 170.0, "c": 172.0, "v": 1200},  # Negative price
    ]
    df = mock_client._results_to_dataframe(results, "AAPL")
    assert len(df) == 1  # Only the first valid row


def test_results_to_dataframe_empty(mock_client: PolygonClient):
    df = mock_client._results_to_dataframe([], "AAPL")
    assert df.empty
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]


def test_results_to_dataframe_deduplicates(mock_client: PolygonClient):
    results = [
        {"t": 1710000000000, "o": 170.0, "h": 172.0, "l": 169.0, "c": 171.0, "v": 1000},
        {"t": 1710000000000, "o": 170.5, "h": 172.5, "l": 169.5, "c": 171.5, "v": 1100},  # Duplicate timestamp
    ]
    df = mock_client._results_to_dataframe(results, "AAPL")
    assert len(df) == 1
    # Should keep the last one
    assert df["open"].iloc[0] == 170.5


def test_fetch_aggs_mock(mock_client: PolygonClient):
    """Test fetch_aggs with mocked HTTP response."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "results": [
            {"t": 1710000000000, "o": 170.0, "h": 172.0, "l": 169.0, "c": 171.0, "v": 1000},
            {"t": 1710086400000, "o": 171.0, "h": 173.0, "l": 170.0, "c": 172.0, "v": 1200},
        ],
        "resultsCount": 2,
        "next_url": None,
    }
    mock_response.raise_for_status = MagicMock()

    mock_client._client = MagicMock()
    mock_client._client.request.return_value = mock_response

    df = mock_client.fetch_aggs("AAPL", "1d", "2024-03-01", "2024-03-15")
    assert len(df) == 2
    assert df["close"].iloc[0] == 171.0


def test_validate_key_success(mock_client: PolygonClient):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"market": "open"}
    mock_response.raise_for_status = MagicMock()

    mock_client._client = MagicMock()
    mock_client._client.request.return_value = mock_response

    assert mock_client.validate_key() is True


def test_validate_key_failure(mock_client: PolygonClient):
    mock_client._client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 403
    mock_client._client.request.return_value = mock_resp

    assert mock_client.validate_key() is False


def test_context_manager():
    with patch("axiom.polygon_client.get_polygon_api_key", return_value="test_key_1234"):
        with PolygonClient(api_key="test_key_1234") as client:
            assert client._api_key == "test_key_1234"
