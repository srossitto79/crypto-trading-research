from __future__ import annotations

import asyncio
import logging
from typing import Any, Coroutine

log = logging.getLogger("axiom.async")


def _log_task_exception(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is None:
        return
    log.error(
        "Background task %r failed: %s",
        task.get_name(),
        exc,
        exc_info=(type(exc), exc, exc.__traceback__),
    )


def spawn(coro: Coroutine[Any, Any, Any], *, name: str) -> asyncio.Task:
    """Create a tracked asyncio task that logs unhandled exceptions.

    Always prefer this helper over a bare ``asyncio.create_task`` for any
    long-running background loop or fire-and-forget work. A bare create_task
    drops exceptions into the event loop's default handler, which routes them
    to stderr without an application logger context — making silent task
    deaths a recurring incident pattern.
    """
    task = asyncio.create_task(coro, name=name)
    task.add_done_callback(_log_task_exception)
    return task
