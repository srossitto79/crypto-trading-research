"""Deterministic Simulation Engine — Bar-stepping playback of history.

Iterates over historical candles, providing a 'live' environment to the
scanner and agents without wall-clock drift or missed signals.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from axiom.db import get_db, kv_get as kv_get, kv_set
from axiom.trade_state import close_trade_record
from axiom.market_cache import publish_price_snapshot
from axiom.market_data import fetch_hyperliquid_candles
from axiom.exchange.risk import update_equity

log = logging.getLogger("axiom.simulation")

class SimulationConfig:
    def __init__(self, start_date: str, end_date: str, interval: str = "1h",
                 initial_equity: float = 10000.0, exec_mode: str = "direct"):
        self.start_time = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
        self.end_time = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
        if self.start_time.tzinfo is None:
            self.start_time = self.start_time.replace(tzinfo=timezone.utc)
        if self.end_time.tzinfo is None:
            self.end_time = self.end_time.replace(tzinfo=timezone.utc)

        self.interval = interval.lower()
        self.initial_equity = initial_equity
        self.exec_mode = exec_mode  # "direct" or "agent"

class SimulationExchange:
    """Tracks virtual equity and mark-to-market performance."""
    def __init__(self, initial_equity: float):
        self.initial_equity = initial_equity
        self.equity_curve = []  # List of (timestamp, equity)

    def get_current_equity(self) -> float:
        """Calculate equity from initial balance + closed PnL."""
        with get_db() as conn:
            row = conn.execute(
                "SELECT SUM(pnl_usd) as total_pnl FROM trades WHERE execution_type='simulation' AND status='CLOSED'"
            ).fetchone()
            closed_pnl = float(row["total_pnl"] or 0.0)
            return self.initial_equity + closed_pnl

class SimulationRunner:
    def __init__(self, config: SimulationConfig):
        self.config = config
        self.exchange = SimulationExchange(config.initial_equity)
        self.active = False
        self.current_bar_index = 0
        self.total_bars = 0
        self.assets = ["BTC", "ETH", "SOL"]

    def _generate_bar_timestamps(self) -> List[datetime]:
        """Create a list of discrete timestamps to iterate over."""
        delta_map = {
            "1m": timedelta(minutes=1),
            "5m": timedelta(minutes=5),
            "15m": timedelta(minutes=15),
            "1h": timedelta(hours=1),
            "4h": timedelta(hours=4),
            "1d": timedelta(days=1),
        }
        delta = delta_map.get(self.config.interval, timedelta(hours=1))

        timestamps = []
        curr = self.config.start_time
        while curr <= self.config.end_time:
            timestamps.append(curr)
            curr += delta
        return timestamps

    def _cleanup_orphaned_trades(self):
        """Close any orphaned OPEN simulation trades from previous runs."""
        with get_db() as conn:
            orphaned_ids = conn.execute(
                "SELECT id FROM trades WHERE execution_type='simulation' AND status='OPEN'"
            ).fetchall()
            closed_count = 0
            for row in orphaned_ids:
                trade_id = str(row["id"]).strip()
                # Use close_trade_record to properly set exit_price and PnL
                closed = close_trade_record(
                    trade_id,
                    close_reason="simulation_cleanup",
                    close_price_source="cleanup_orphaned",
                    only_if_open=True,
                )
                if closed and closed.get("updated"):
                    closed_count += 1
            if closed_count:
                log.info("Cleaned up %d orphaned sim trades", closed_count)
            conn.execute("DELETE FROM portfolio_positions WHERE trade_id IN "
                         "(SELECT id FROM trades WHERE execution_type='simulation' AND status='CLOSED')")

    async def run(self):
        self.active = True
        bar_timestamps = self._generate_bar_timestamps()
        self.total_bars = len(bar_timestamps)

        log.info("Starting Simulation: %d bars (%s) | mode=%s",
                 self.total_bars, self.config.interval, self.config.exec_mode)

        kv_set("axiom:simulation:active", True)
        kv_set("axiom:simulation:exec_mode", self.config.exec_mode)

        from axiom.sim.clock import set_sim_active
        set_sim_active(True)

        # Cleanup orphaned sim trades from previous runs
        self._cleanup_orphaned_trades()

        try:
            # Pre-fetch data
            kv_set("simulation_state", {
                "active": True, "phase": "prefetching", "progress": 0,
                "total_bars": self.total_bars, "exec_mode": self.config.exec_mode,
                "interval": self.config.interval,
                "initial_equity": self.config.initial_equity,
            })
            from axiom.sim.data_pump import prefetch_candles
            await prefetch_candles(self.assets, self.config.start_time, self.config.end_time, self.config.interval)

            # Initialize sim risk state (must happen WHILE sim is active so sim_kv_key prefixes with 'sim:')
            from axiom.sim.clock import sim_kv_key
            kv_set(sim_kv_key("risk_state"), {
                "high_water_mark": self.config.initial_equity,
                "kill_switch_active": False,
                "kill_switch_triggered_at": None,
                "daily_loss_halt": False,
                "daily_loss_halt_date": None,
                "last_equity": self.config.initial_equity,
                "drawdown_pct": 0.0,
            })
            if bar_timestamps:
                kv_set(sim_kv_key("daily_risk"), {
                    "date": bar_timestamps[0].date().isoformat(),
                    "start_equity": self.config.initial_equity,
                    "current_equity": self.config.initial_equity,
                    "pnl_pct": 0.0,
                })

            for i, bar_time in enumerate(bar_timestamps):
                if not self.active:
                    break
                self.current_bar_index = i

                # 1. Update Virtual Time
                from axiom.sim.clock import set_sim_time
                set_sim_time(bar_time.isoformat())

                # 2. Inject Virtual Prices
                prices = await self._publish_virtual_prices(bar_time)
                if not prices:
                    log.warning("No prices for bar %s, skipping", bar_time)
                    continue

                # 3. Clear Regime Cache so each bar gets fresh detection
                from axiom.regime import invalidate_cache
                invalidate_cache()

                # 4. Run Scanner (mock exchange intercepts all HL calls)
                from axiom.scanner import run_scan
                await asyncio.to_thread(run_scan, execute_positions=True)

                # 5. Update Equity and Risk State
                current_equity = self.exchange.get_current_equity()
                await asyncio.to_thread(update_equity, current_equity)

                # 6. Record History
                self.exchange.equity_curve.append((bar_time.isoformat(), current_equity))

                # 7. Update UI
                self._update_ui_state(bar_time, current_equity, prices)

                # Yield for potential cancellation
                await asyncio.sleep(0)

        except Exception as e:
            log.error("Simulation crashed: %s", e, exc_info=True)
        finally:
            self._finalize()

    async def _publish_virtual_prices(self, bar_time: datetime) -> dict:
        """Fetch close prices for the bar and publish to cache."""
        prices = {}
        end_ms = int(bar_time.timestamp() * 1000)

        from axiom.sim.data_pump import get_cached_candles
        for asset in self.assets:
            try:
                # Try prefetch cache first
                df = get_cached_candles(asset, self.config.interval, end_ms, 1)

                # Fallback to API if not cached
                if df is None or df.empty:
                    df = await asyncio.to_thread(
                        fetch_hyperliquid_candles, asset, bars=1,
                        interval=self.config.interval, end_time=end_ms
                    )

                if df is not None and not df.empty:
                    prices[asset] = float(df["close"].iloc[-1])
            except Exception as e:
                log.debug("Sim price fetch failed for %s: %s", asset, e)

        if prices:
            publish_price_snapshot(prices, "simulation")
        return prices

    def _update_ui_state(self, bar_time: datetime, equity: float, prices: dict | None = None):
        progress = (self.current_bar_index + 1) / self.total_bars if self.total_bars else 0
        kv_set("simulation_state", {
            "active": self.active,
            "phase": "running",
            "current_time": bar_time.isoformat(),
            "progress": progress,
            "bar": self.current_bar_index + 1,
            "total_bars": self.total_bars,
            "equity": equity,
            "exec_mode": self.config.exec_mode,
            "interval": self.config.interval,
            "prices": prices or {},
        })

    def _compute_analytics(self) -> dict:
        """Compute comprehensive post-simulation analytics."""
        final_equity = self.exchange.get_current_equity()
        total_return = (final_equity - self.config.initial_equity) / self.config.initial_equity

        # Trade statistics
        with get_db() as conn:
            trades = conn.execute(
                "SELECT * FROM trades WHERE execution_type='simulation'"
            ).fetchall()
            trades = [dict(t) for t in trades]

        total_trades = len(trades)
        closed_trades = [t for t in trades if t["status"] == "CLOSED"]
        open_trades = [t for t in trades if t["status"] == "OPEN"]
        wins = [t for t in closed_trades if (t.get("pnl_pct") or 0) > 0]
        losses = [t for t in closed_trades if (t.get("pnl_pct") or 0) <= 0]
        win_rate = len(wins) / len(closed_trades) if closed_trades else 0.0

        avg_win = 0.0
        avg_loss = 0.0
        if wins:
            avg_win = sum(float(t.get("pnl_pct") or 0) for t in wins) / len(wins)
        if losses:
            avg_loss = sum(float(t.get("pnl_pct") or 0) for t in losses) / len(losses)

        # Max drawdown from equity curve
        max_drawdown = 0.0
        peak = self.config.initial_equity
        for _, equity in self.exchange.equity_curve:
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak if peak > 0 else 0.0
            if dd > max_drawdown:
                max_drawdown = dd

        # Profit factor
        gross_profit = sum(float(t.get("pnl_usd") or 0) for t in wins)
        gross_loss = abs(sum(float(t.get("pnl_usd") or 0) for t in losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0

        return {
            "final_equity": round(final_equity, 2),
            "initial_equity": self.config.initial_equity,
            "total_return_pct": round(total_return * 100, 2),
            "max_drawdown_pct": round(max_drawdown * 100, 2),
            "total_trades": total_trades,
            "closed_trades": len(closed_trades),
            "open_trades": len(open_trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": round(win_rate * 100, 1),
            "avg_win_pct": round(avg_win * 100, 2),
            "avg_loss_pct": round(avg_loss * 100, 2),
            "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else "inf",
            "gross_profit_usd": round(gross_profit, 2),
            "gross_loss_usd": round(gross_loss, 2),
            "bars_processed": self.current_bar_index + 1,
            "total_bars": self.total_bars,
            "interval": self.config.interval,
            "exec_mode": self.config.exec_mode,
            "equity_curve": self.exchange.equity_curve,
        }

    def _finalize(self):
        # IMPORTANT: Cleanup sim-namespaced keys BEFORE deactivating sim flag,
        # because sim_kv_key() only prefixes with 'sim:' when sim is active.
        from axiom.sim.clock import sim_kv_key
        kv_set(sim_kv_key("risk_state"), None)
        kv_set(sim_kv_key("daily_risk"), None)

        # Now deactivate simulation
        self.active = False
        kv_set("axiom:simulation:active", False)
        kv_set("axiom:simulation:exec_mode", None)
        kv_set("axiom:simulation:time", None)

        # Clear data pump cache
        try:
            from axiom.sim.data_pump import clear_cache
            clear_cache()
        except ImportError:
            pass

        # Compute and store analytics
        analytics = self._compute_analytics()
        kv_set("simulation_analytics", analytics)
        kv_set("simulation_state", {"active": False, "phase": "complete", "analytics": analytics})

        log.info("Simulation Finalized: Return %+.2f%% | %d trades | Win rate %.0f%% | Max DD %.1f%%",
                 analytics["total_return_pct"], analytics["total_trades"],
                 analytics["win_rate_pct"], analytics["max_drawdown_pct"])

_runner: Optional[SimulationRunner] = None

async def start_simulation(start_date: str, end_date: str, interval: str = "1h",
                           initial_equity: float = 10000.0, exec_mode: str = "direct"):
    # ── ARCHIVED: Simulation engine is disabled ──
    raise ValueError("Simulation engine is archived and disabled.")

async def stop_simulation():
    global _runner
    if _runner:
        _runner.active = False
    return {"status": "stopped"}
