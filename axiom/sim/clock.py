"""Virtual Clock for Simulation Mode.

Provides a centralized time authority that returns 'virtual' time when
a simulation is active, and real-world UTC time otherwise.
"""

from datetime import date, datetime, timezone
from contextvars import ContextVar

_is_sim_active = ContextVar("is_sim_active", default=False)
_sim_time = ContextVar("sim_time", default=None)
_sim_exec_mode = ContextVar("sim_exec_mode", default="direct")

def set_sim_active(active: bool):
    _is_sim_active.set(active)

def set_sim_time(time_iso: str):
    _sim_time.set(time_iso)

def set_sim_exec_mode(mode: str):
    _sim_exec_mode.set(mode)

def get_sim_exec_mode() -> str:
    return _sim_exec_mode.get()

def get_now() -> datetime:
    """Return virtual time when sim active, else real UTC time."""
    if _is_sim_active.get():
        raw = _sim_time.get()
        if raw:
            try:
                dt = datetime.fromisoformat(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                pass
    return datetime.now(timezone.utc)

def get_today() -> date:
    """Return virtual date when sim active, else real UTC date."""
    return get_now().date()

def is_sim_active() -> bool:
    """Return True if simulation mode is active in the current task context."""
    return _is_sim_active.get()

def sim_kv_key(key: str) -> str:
    """Prefix KV keys with 'sim:' during simulation to isolate state."""
    if is_sim_active():
        return f"sim:{key}"
    return key
