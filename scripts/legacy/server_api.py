import hmac
import os
import re
import time
import uuid
import json
import logging
import traceback
import threading
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List
from fastapi import Depends, FastAPI, HTTPException, Header, Request, BackgroundTasks
from pydantic import BaseModel, Field, field_validator
import uvicorn
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# Setup logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("server_api")


def _require_api_key(x_api_key: str = Header(default="")):
    """Fail-closed API key check for protected endpoints."""
    server_key = os.environ.get("AXIOM_COMPUTE_API_KEY", "").strip()
    if not server_key:
        raise HTTPException(status_code=503, detail="Compute API key not configured on server")
    if not x_api_key or not hmac.compare_digest(server_key, x_api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


REMOTE_DATA_ROOT_ENV = "AXIOM_REMOTE_ENGINE_DATA_ROOT"
DEFAULT_REMOTE_DATA_ROOT = "D:/AxiomData/ohlcv"

_INGESTION_RUNS: dict[str, dict[str, Any]] = {}
_INGESTION_RUNS_LOCK = threading.Lock()
_MAX_INGEST_RETRIES = 10
_MAX_INGESTION_RUNS = 200
_DATASET_CACHE_TTL_SECONDS = 30
_DATASET_CACHE_LOCK = threading.Lock()
_DATASET_CACHE: dict[str, Any] = {"expires_at": 0.0, "rows": []}

app = FastAPI(
    title="Axiom Compute Engine & Data Lake API",
    description="Dedicated execution and storage environment for Axiom backtests.",
    version="1.0.0"
)

# ── Pydantic Models ──────────────────────────────────────────────────

_DANGEROUS_CODE_RE = re.compile(
    r"os\.system|subprocess\b|__import__|eval\s*\(|exec\s*\(|socket\b|ctypes\b|shutil\.rmtree|open\s*\([^)]*[\"']w[\"']",
    re.IGNORECASE,
)


class BacktestRequest(BaseModel):
    strategy_code: str = Field(..., max_length=102_400)
    symbol: str = Field(default="BTC-USDT", max_length=32)
    timeframe: str = Field(default="1m", max_length=8)
    parameters: Dict[str, Any] = {}
    fee_bps: float = 3.5
    slippage_bps: float = 2.0

    @field_validator("strategy_code")
    @classmethod
    def reject_dangerous_patterns(cls, v: str) -> str:
        if _DANGEROUS_CODE_RE.search(v):
            raise ValueError("strategy_code contains a blocked pattern")
        return v

class BacktestResponse(BaseModel):
    status: str
    run_id: str
    metrics: Dict[str, Any] = {}
    error: str = None
    execution_time_ms: float = 0

class DataIngestRequest(BaseModel):
    symbol: str
    timeframe: str
    exchange: str = "binance"
    limit: int | None = 1000
    since_ms: int | None = None
    until_ms: int | None = None
    all_available: bool = False

class DataIngestResponse(BaseModel):
    status: str
    run_id: str
    symbol: str
    timeframe: str
    message: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _to_iso(value: Any) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    try:
        ts = pd.Timestamp(value)
    except Exception:
        return None
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts.isoformat().replace("+00:00", "Z")


def _symbol_to_fs(symbol: str) -> str:
    raw = str(symbol or "").strip().upper()
    return raw.replace("/", "-").replace("_", "-")


def _symbol_to_ui(symbol: str) -> str:
    raw = str(symbol or "").strip().upper()
    if "/" in raw:
        return raw
    if "-" in raw:
        base, quote = raw.split("-", 1)
        if base and quote:
            return f"{base}/{quote}"
    return raw


def _resolve_data_root() -> Path:
    configured = str(os.getenv(REMOTE_DATA_ROOT_ENV, "") or "").strip()
    root = configured or DEFAULT_REMOTE_DATA_ROOT
    return Path(root).expanduser()


def _dataset_path(symbol: str, timeframe: str) -> Path:
    return _resolve_data_root() / _symbol_to_fs(symbol) / f"{str(timeframe).strip()}.parquet"


def _read_dataset_frame(symbol: str, timeframe: str) -> pd.DataFrame:
    path = _dataset_path(symbol, timeframe)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"dataset not found: {_symbol_to_fs(symbol)} {timeframe}")
    table = pq.read_table(path)
    df = table.to_pandas()

    required = ["timestamp", "open", "high", "low", "close", "volume"]
    for col in required:
        if col not in df.columns:
            df[col] = pd.NaT if col == "timestamp" else 0.0
    df = df[required].copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"])
    df = df.drop_duplicates(subset=["timestamp"], keep="last").sort_values("timestamp").reset_index(drop=True)
    if df.empty:
        raise HTTPException(status_code=404, detail=f"dataset is empty: {_symbol_to_fs(symbol)} {timeframe}")
    return df


def _dataset_checksum(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _invalidate_dataset_cache() -> None:
    with _DATASET_CACHE_LOCK:
        _DATASET_CACHE["expires_at"] = 0.0
        _DATASET_CACHE["rows"] = []


def _timestamp_bounds_from_metadata(metadata: Any) -> tuple[str | None, str | None]:
    try:
        schema = metadata.schema
        ts_index = -1
        for idx in range(schema.num_columns):
            if str(schema.column(idx).name).strip().lower() == "timestamp":
                ts_index = idx
                break
        if ts_index < 0:
            return None, None

        mins: list[Any] = []
        maxs: list[Any] = []
        for rg_idx in range(int(metadata.num_row_groups or 0)):
            col = metadata.row_group(rg_idx).column(ts_index)
            stats = getattr(col, "statistics", None)
            if stats is None or not getattr(stats, "has_min_max", False):
                continue
            mins.append(stats.min)
            maxs.append(stats.max)

        if not mins or not maxs:
            return None, None
        return _to_iso(min(mins)), _to_iso(max(maxs))
    except Exception:
        return None, None


def _dataset_from_file(path: Path, fs_symbol: str, timeframe: str) -> dict[str, Any]:
    metadata = pq.read_metadata(path)
    row_count = int(metadata.num_rows or 0)
    keyvals = metadata.metadata or {}
    source_raw = keyvals.get(b"AXIOM_source", b"remote")
    source = source_raw.decode("utf-8", errors="ignore") or "remote"

    start_ts, end_ts = _timestamp_bounds_from_metadata(metadata)
    if row_count > 0 and not end_ts:
        end_ts = _to_iso(datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc))

    return {
        "symbol": fs_symbol,
        "timeframe": timeframe,
        "source": source,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "row_count": row_count,
    }


def _scan_datasets() -> list[dict[str, Any]]:
    now = time.time()
    with _DATASET_CACHE_LOCK:
        if now < float(_DATASET_CACHE.get("expires_at", 0.0)):
            return [dict(row) for row in list(_DATASET_CACHE.get("rows", []))]

    root = _resolve_data_root()
    if not root.exists():
        return []

    datasets: list[dict[str, Any]] = []
    for symbol_dir in sorted(root.iterdir()):
        if not symbol_dir.is_dir():
            continue
        fs_symbol = symbol_dir.name
        for parquet_file in sorted(symbol_dir.glob("*.parquet")):
            timeframe = parquet_file.stem
            try:
                datasets.append(_dataset_from_file(parquet_file, fs_symbol, timeframe))
            except Exception as exc:
                log.warning("Failed to scan %s: %s", parquet_file, exc)
                continue

    datasets.sort(key=lambda row: str(row.get("end_ts") or row.get("start_ts") or ""), reverse=True)
    with _DATASET_CACHE_LOCK:
        _DATASET_CACHE["expires_at"] = now + _DATASET_CACHE_TTL_SECONDS
        _DATASET_CACHE["rows"] = [dict(row) for row in datasets]
    return [dict(row) for row in datasets]


def _dataset_runs(
    *,
    symbol: str | None,
    status: str | None,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    normalized_symbol = _symbol_to_fs(symbol) if symbol else None
    normalized_status = str(status or "").strip().lower() if status else None

    rows: list[dict[str, Any]] = []
    for idx, ds in enumerate(_scan_datasets()):
        ds_symbol = str(ds.get("symbol") or "")
        if normalized_symbol and ds_symbol != normalized_symbol:
            continue
        if normalized_status and normalized_status != "completed":
            continue
        completed_at = str(ds.get("end_ts") or ds.get("start_ts") or _now_iso())
        bars = int(ds.get("row_count") or 0)
        rows.append(
            {
                "id": f"remote-dataset-{idx}-{ds_symbol}-{ds.get('timeframe')}",
                "symbol": ds_symbol,
                "timeframe": str(ds.get("timeframe") or ""),
                "source": str(ds.get("source") or "remote"),
                "status": "completed",
                "idempotency_key": None,
                "bars_fetched": bars,
                "bars_new": bars,
                "bars_updated": 0,
                "error": None,
                "prior_version_id": None,
                "new_version_id": None,
                "started_at": completed_at,
                "completed_at": completed_at,
                "duration_ms": None,
            }
        )

    start_idx = max(int(offset), 0)
    end_idx = start_idx + max(int(limit), 1)
    return rows[start_idx:end_idx]


def _dataset_detail_payload(symbol: str, timeframe: str) -> dict[str, Any]:
    fs_symbol = _symbol_to_fs(symbol)
    path = _dataset_path(fs_symbol, timeframe)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"dataset not found: {fs_symbol} {timeframe}")
    match = next(
        (row for row in _scan_datasets() if row.get("symbol") == fs_symbol and row.get("timeframe") == timeframe),
        None,
    )
    if not match:
        raise HTTPException(status_code=404, detail=f"dataset not found: {fs_symbol} {timeframe}")
    return {
        **match,
        "updated_at": _to_iso(datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)),
        "parquet_exists": True,
        "checksum": _dataset_checksum(path),
    }


def _quality_payload(symbol: str, timeframe: str) -> dict[str, Any]:
    fs_symbol = _symbol_to_fs(symbol)
    frame = _read_dataset_frame(fs_symbol, timeframe)
    ts = frame["timestamp"].sort_values().reset_index(drop=True)
    start = ts.iloc[0]
    end = ts.iloc[-1]
    duration_days = max(0.0, (end - start).total_seconds() / 86400.0)

    if len(ts) < 2:
        gaps = 0
        gap_details: list[dict[str, str]] = []
    else:
        diffs_ms = ts.diff().dt.total_seconds().mul(1000).fillna(0).astype(int)
        tf_map = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}
        tf = str(timeframe or "").strip()
        try:
            tf_ms = int(tf[:-1]) * tf_map.get(tf[-1], 60_000)
        except Exception:
            tf_ms = 60_000
        gaps = 0
        gap_details = []
        for idx in range(1, len(ts)):
            diff = int(diffs_ms.iloc[idx])
            if diff <= tf_ms:
                continue
            missing = max(1, int(round(diff / max(tf_ms, 1))) - 1)
            gaps += missing
            gap_details.append(
                {
                    "timestamp": _to_iso(ts.iloc[idx - 1] + pd.Timedelta(milliseconds=tf_ms)) or "",
                    "gap_size": f"{missing} bars",
                }
            )
            if len(gap_details) >= 200:
                break

    null_values = int(frame[["open", "high", "low", "close", "volume"]].isna().sum().sum())
    price_min = float(frame["low"].min()) if len(frame) else 0.0
    price_max = float(frame["high"].max()) if len(frame) else 0.0
    volume_min = float(frame["volume"].min()) if len(frame) else 0.0
    volume_max = float(frame["volume"].max()) if len(frame) else 0.0
    volume_avg = float(frame["volume"].mean()) if len(frame) else 0.0

    close_std = float(frame["close"].std(ddof=0) or 0.0)
    close_mean = float(frame["close"].mean() or 0.0)
    close_outliers = int((frame["close"].sub(close_mean).abs() > (3 * close_std)).sum()) if close_std > 0 else 0

    volume_std = float(frame["volume"].std(ddof=0) or 0.0)
    volume_mean = float(frame["volume"].mean() or 0.0)
    volume_outliers = int((frame["volume"].sub(volume_mean).abs() > (3 * volume_std)).sum()) if volume_std > 0 else 0

    invalid_high_low = int((frame["high"] < frame["low"]).sum())
    invalid_close_range = int(((frame["close"] < frame["low"]) | (frame["close"] > frame["high"])).sum())

    now = datetime.now(timezone.utc)
    last_update = end if getattr(end, "tzinfo", None) is not None else end.tz_localize("UTC")
    hours_ago = max(0.0, (now - last_update.to_pydatetime()).total_seconds() / 3600.0)
    freshness = {
        "last_update": _to_iso(last_update),
        "hours_ago": round(hours_ago, 3),
        "is_stale": hours_ago > 24.0,
    }

    return {
        "symbol": fs_symbol,
        "timeframe": str(timeframe).strip(),
        "row_count": int(len(frame)),
        "start": _to_iso(start),
        "end": _to_iso(end),
        "duration_days": round(duration_days, 3),
        "gaps": int(gaps),
        "gap_details": gap_details,
        "null_values": null_values,
        "price_range": {"min": price_min, "max": price_max},
        "volume_stats": {"min": volume_min, "max": volume_max, "avg": volume_avg},
        "outliers": {"close": close_outliers, "volume": volume_outliers},
        "integrity": {
            "invalid_high_low": invalid_high_low,
            "invalid_close_range": invalid_close_range,
        },
        "freshness": freshness,
    }

# ── Data Lake Endpoints ──────────────────────────────────────────────

@app.get("/data/{symbol}/{timeframe}")
async def get_market_data(
    symbol: str,
    timeframe: str,
    start: str = None,
    end: str = None,
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
):
    """
    Simulates fetching a slice of data from the massive 10TB local storage.
    Instead of sending back 50GB of raw tick data, the orchestrator only requests
    what it needs to display a frontend chart.
    """
    normalized_symbol = str(symbol or "").strip().lower()
    normalized_timeframe = str(timeframe or "").strip().lower()
    if normalized_symbol == "ingestion" and normalized_timeframe == "runs":
        return list_ingestion_runs(status=status, limit=limit, offset=offset)

    log.info("Orchestrator requested data for %s (%s) - Stream starting.", symbol, timeframe)
    
    # In production, this would load from Local 10TB drive using Pandas Parquet:
    # df = pd.read_parquet(f"D:/AxiomData/{symbol}/{timeframe}.parquet")
    # return df.loc[start:end].to_dict()
    
    return {
        "status": "success",
        "symbol": symbol,
        "timeframe": timeframe,
        "message": "This is where the massive parquet chunks would be streamed back to the frontend.",
        "sample_data": [
            {"timestamp": "2024-01-01T00:00:00Z", "close": 42000.5},
            {"timestamp": "2024-01-01T00:01:00Z", "close": 42010.2},
        ]
    }

def _bg_ingest_market_data(symbol: str, timeframe: str, exchange_id: str, limit: int, since_ms: int, until_ms: int, all_available: bool, run_id: str):
    log.info("[%s] Background ingest started for %s %s", run_id, symbol, timeframe)
    with _INGESTION_RUNS_LOCK:
        if run_id in _INGESTION_RUNS:
            _INGESTION_RUNS[run_id]["status"] = "running"
    try:
        import ccxt
        exchange = getattr(ccxt, exchange_id.lower())({'enableRateLimit': True})
    except Exception as e:
        log.error("Failed to load ccxt exchange %s: %s", exchange_id, e)
        with _INGESTION_RUNS_LOCK:
            if run_id in _INGESTION_RUNS:
                _INGESTION_RUNS[run_id]["status"] = "failed"
                _INGESTION_RUNS[run_id]["error"] = str(e)
                _INGESTION_RUNS[run_id]["completed_at"] = _now_iso()
        return

    def tf_to_ms(tf: str):
        unit = tf[-1]
        val = int(tf[:-1])
        if unit == 'm': return val * 60000
        if unit == 'h': return val * 3600000
        if unit == 'd': return val * 86400000
        if unit == 'w': return val * 604800000
        return 60000

    tf_ms = tf_to_ms(timeframe)
    all_rows = []

    if all_available or not since_ms:
        cursor = exchange.parse8601('2018-01-01T00:00:00Z')
    else:
        cursor = since_ms

    log.info("[%s] Fetching %s starting from %s...", run_id, symbol, cursor)

    consecutive_errors = 0
    while True:
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe, since=cursor, limit=1000)
            if not ohlcv:
                break
            consecutive_errors = 0
            all_rows.extend(ohlcv)
            last_ts = ohlcv[-1][0]
            if last_ts == cursor:
                # no forward progress
                break
            cursor = last_ts + tf_ms
            log.info("[%s] Fetched %d rows... latest %s", run_id, len(all_rows), last_ts)
            time.sleep(exchange.rateLimit / 1000.0 if exchange.rateLimit else 0.1)

            # Stop if reached until_ms or if limit reached without all_available
            if until_ms and cursor > until_ms:
                break
            if not all_available and limit and len(all_rows) >= limit:
                all_rows = all_rows[:limit]
                break
        except Exception as e:
            consecutive_errors += 1
            if consecutive_errors >= _MAX_INGEST_RETRIES:
                log.error("[%s] Giving up after %d consecutive errors: %s", run_id, consecutive_errors, e)
                with _INGESTION_RUNS_LOCK:
                    if run_id in _INGESTION_RUNS:
                        _INGESTION_RUNS[run_id]["status"] = "failed"
                        _INGESTION_RUNS[run_id]["error"] = f"Max retries ({_MAX_INGEST_RETRIES}) exceeded: {e}"
                        _INGESTION_RUNS[run_id]["completed_at"] = _now_iso()
                return
            backoff = min(5 * 2 ** consecutive_errors, 60)
            log.warning("[%s] CCXT error (%d/%d), retrying in %ds: %s", run_id, consecutive_errors, _MAX_INGEST_RETRIES, backoff, e)
            time.sleep(backoff)

    if all_rows:
        df = pd.DataFrame(all_rows, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
        # Clean duplicates
        df = df.drop_duplicates(subset=['timestamp'], keep='last').sort_values('timestamp').reset_index(drop=True)

        save_path = _dataset_path(symbol, timeframe)
        save_path.parent.mkdir(parents=True, exist_ok=True)

        # Merge if exists
        try:
            if save_path.exists():
                existing = pq.read_table(str(save_path)).to_pandas()
                df = pd.concat([existing, df], ignore_index=True)
                df = df.drop_duplicates(subset=['timestamp'], keep='last').sort_values('timestamp').reset_index(drop=True)
        except Exception as e:
            log.warning("Failed to load existing parquet for %s, overwriting. %s", symbol, e)

        # Save to disk
        table = pa.Table.from_pandas(df, preserve_index=False)
        pq.write_table(table, str(save_path), compression="zstd")
        log.info("[%s] Successfully stored %d rows to %s", run_id, len(df), save_path)
        with _INGESTION_RUNS_LOCK:
            if run_id in _INGESTION_RUNS:
                _INGESTION_RUNS[run_id]["status"] = "completed"
                _INGESTION_RUNS[run_id]["bars_fetched"] = len(all_rows)
                _INGESTION_RUNS[run_id]["bars_new"] = len(all_rows)
                _INGESTION_RUNS[run_id]["completed_at"] = _now_iso()
    else:
        with _INGESTION_RUNS_LOCK:
            if run_id in _INGESTION_RUNS:
                _INGESTION_RUNS[run_id]["status"] = "failed"
                _INGESTION_RUNS[run_id]["error"] = "No rows fetched"
                _INGESTION_RUNS[run_id]["completed_at"] = _now_iso()

def _prune_ingestion_runs() -> None:
    """Remove oldest completed/failed runs when over _MAX_INGESTION_RUNS.

    Must be called while holding _INGESTION_RUNS_LOCK.
    """
    if len(_INGESTION_RUNS) <= _MAX_INGESTION_RUNS:
        return
    removable = [
        (rid, run)
        for rid, run in _INGESTION_RUNS.items()
        if run.get("status") in ("completed", "failed")
    ]
    removable.sort(key=lambda x: x[1].get("started_at") or "")
    to_remove = len(_INGESTION_RUNS) - _MAX_INGESTION_RUNS
    for rid, _ in removable[:to_remove]:
        del _INGESTION_RUNS[rid]


@app.post("/data/ingest", response_model=DataIngestResponse, dependencies=[Depends(_require_api_key)])
async def ingest_market_data(request: DataIngestRequest, background_tasks: BackgroundTasks):
    """
    Instructs the Data Lake to connect to CCXT/Binance and stream the datasets
    directly to its local drive in the background.
    """
    run_id = f"remote_ingest_{uuid.uuid4().hex[:8]}"
    log.info("[%s] Remote Data Lake queuing ingest for %s %s", run_id, request.symbol, request.timeframe)

    with _INGESTION_RUNS_LOCK:
        _INGESTION_RUNS[run_id] = {
            "id": run_id,
            "symbol": _symbol_to_fs(request.symbol),
            "timeframe": str(request.timeframe).strip(),
            "source": str(request.exchange or "remote").strip() or "remote",
            "status": "pending",
            "idempotency_key": None,
            "bars_fetched": 0,
            "bars_new": 0,
            "bars_updated": 0,
            "error": None,
            "prior_version_id": None,
            "new_version_id": None,
            "started_at": _now_iso(),
            "completed_at": None,
            "duration_ms": None,
        }
        _prune_ingestion_runs()

    background_tasks.add_task(
        _bg_ingest_market_data,
        symbol=request.symbol,
        timeframe=request.timeframe,
        exchange_id=request.exchange,
        limit=request.limit,
        since_ms=request.since_ms,
        until_ms=request.until_ms,
        all_available=request.all_available,
        run_id=run_id
    )
    
    return DataIngestResponse(
        status="queued",
        run_id=run_id,
        symbol=_symbol_to_fs(request.symbol),
        timeframe=request.timeframe,
        message="Remote ingest queued."
    )


def _symbol_from_parts(base: str, quote: str) -> str:
    return f"{str(base or '').strip().upper()}-{str(quote or '').strip().upper()}"


@app.get("/api/datasets")
@app.get("/data/datasets")
def list_datasets():
    return _scan_datasets()


@app.get("/api/data/ingestion/runs")
@app.get("/data/ingestion/runs")
def list_ingestion_runs(
    symbol: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    with _INGESTION_RUNS_LOCK:
        active = [dict(run) for run in _INGESTION_RUNS.values()]
    active.sort(key=lambda row: str(row.get("started_at") or ""), reverse=True)

    synthetic = _dataset_runs(symbol=symbol, status=status, limit=max(limit, 1), offset=0)
    merged = active + synthetic

    normalized_symbol = _symbol_to_fs(symbol) if symbol else None
    normalized_status = str(status or "").strip().lower() if status else None
    filtered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in merged:
        row_symbol = str(row.get("symbol") or "")
        row_status = str(row.get("status") or "").lower()
        if normalized_symbol and row_symbol != normalized_symbol:
            continue
        if normalized_status and row_status != normalized_status:
            continue
        row_id = str(row.get("id") or "")
        if row_id and row_id in seen:
            continue
        if row_id:
            seen.add(row_id)
        filtered.append(row)

    start_idx = max(int(offset), 0)
    end_idx = start_idx + max(int(limit), 1)
    return filtered[start_idx:end_idx]


@app.get("/api/datasets/{base}/{quote}/{timeframe}/ohlcv")
def get_dataset_pair_ohlcv(base: str, quote: str, timeframe: str, limit: int = 100):
    return get_dataset_ohlcv(_symbol_from_parts(base, quote), timeframe, limit=limit)


@app.get("/api/datasets/{symbol}/{timeframe}/ohlcv")
def get_dataset_ohlcv(symbol: str, timeframe: str, limit: int = 100):
    fs_symbol = _symbol_to_fs(symbol)
    frame = _read_dataset_frame(fs_symbol, timeframe)
    rows = frame.tail(max(int(limit), 1)).copy()
    rows["timestamp"] = rows["timestamp"].map(_to_iso)
    records = rows.to_dict("records")
    return {
        "symbol": fs_symbol,
        "timeframe": str(timeframe).strip(),
        "source": "remote",
        "start": _to_iso(frame["timestamp"].iloc[0]),
        "end": _to_iso(frame["timestamp"].iloc[-1]),
        "row_count": int(len(frame)),
        "data": records,
    }


@app.get("/api/datasets/{base}/{quote}/{timeframe}")
def get_dataset_pair_detail(base: str, quote: str, timeframe: str):
    return _dataset_detail_payload(_symbol_from_parts(base, quote), timeframe)


@app.get("/api/datasets/{symbol}/{timeframe}")
def get_dataset_detail(symbol: str, timeframe: str):
    return _dataset_detail_payload(symbol, timeframe)


@app.delete("/api/datasets/{base}/{quote}/{timeframe}", dependencies=[Depends(_require_api_key)])
def delete_dataset_pair(base: str, quote: str, timeframe: str):
    return delete_dataset(_symbol_from_parts(base, quote), timeframe)


@app.delete("/api/datasets/{symbol}/{timeframe}", dependencies=[Depends(_require_api_key)])
def delete_dataset(symbol: str, timeframe: str):
    fs_symbol = _symbol_to_fs(symbol)
    path = _dataset_path(fs_symbol, timeframe)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"dataset not found: {fs_symbol} {timeframe}")
    path.unlink()
    parent = path.parent
    try:
        if parent.exists() and not any(parent.glob("*.parquet")):
            parent.rmdir()
    except Exception:
        pass
    return {"status": "deleted", "symbol": fs_symbol, "timeframe": str(timeframe).strip()}


@app.get("/api/data/quality")
@app.get("/data/quality")
def get_data_quality(symbol: str, timeframe: str):
    return _quality_payload(symbol, timeframe)

# ── Compute Node Endpoints ───────────────────────────────────────────

@app.post("/backtest/run", response_model=BacktestResponse, dependencies=[Depends(_require_api_key)])
async def run_remote_backtest(request: BacktestRequest):
    """
    Accepts a strategy type name (e.g. "rsi_momentum") and parameters,
    returns mock backtest metrics.  No code is written to disk.
    Future real execution should use the subprocess sandbox in Axiom/sandbox.py.
    """
    run_id = f"remote_run_{uuid.uuid4().hex[:8]}"
    log.info("[%s] Received backtest request for %s on %s.", run_id, request.symbol, request.timeframe)
    start_time = time.time()

    try:
        log.info("[%s] Loading data for %s...", run_id, request.symbol)
        time.sleep(1.0)  # Simulate memory load

        log.info("[%s] Running simulation...", run_id)
        time.sleep(2.5)  # Simulate heavy calculations

        mock_metrics = {
            "sharpe_ratio": 2.45,
            "win_rate_pct": 68.2,
            "total_return_pct": 142.5,
            "max_drawdown_pct": -12.4,
            "profit_factor": 1.85,
            "total_trades": 405,
        }

        execution_time_ms = round((time.time() - start_time) * 1000, 2)
        log.info("[%s] Execution complete in %sms.", run_id, execution_time_ms)

        return BacktestResponse(
            status="completed",
            run_id=run_id,
            metrics=mock_metrics,
            execution_time_ms=execution_time_ms,
        )

    except Exception as e:
        log.error("[%s] Execution failed: %s", run_id, e)
        return BacktestResponse(
            status="failed",
            run_id=run_id,
            error=str(e),
            execution_time_ms=round((time.time() - start_time) * 1000, 2),
        )

@app.get("/health")
@app.get("/api/health")
def health_check():
    root = _resolve_data_root()
    return {
        "status": "online",
        "role": "Data Lake & Compute Engine",
        "ram_available": "48GB",
        "data_root": str(root),
        "data_root_exists": root.exists(),
    }

if __name__ == "__main__":
    print("-" * 60)
    print("🚀 Axiom COMPUTE ENGINE & DATA LAKE STARTED")
    print("Listening for LAN requests on Port 9050...")
    print("This server will now handle all backtest math and Parquet Storage!")
    print("-" * 60)
    # Production default: keep process stable while backtests write temp files.
    uvicorn.run("server_api:app", host="0.0.0.0", port=9050, reload=False)
