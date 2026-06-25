"""Regression: every bot tasks.loop that drives a queue must survive a Discord
gateway-induced asyncio.CancelledError and must auto-restart on crash.

The 2026-04-26 overnight stall happened because ``agent_runner_loop`` was
missing both primitives. A CancelledError from a gateway reconnect killed the
loop permanently while the bot process kept holding ``bot.lock``, which made
the API headless fallback (``run_headless_agent_loop``) refuse to take over.
Tasks accumulated as ``pending`` and the scheduler reaper expired them after
two hours.

This test inspects the source of Axiom.bot to assert the invariant. It is
intentionally an AST-level check rather than a behavioural one because
reproducing a Discord gateway reconnect in unit tests is not worth the
fixture cost — the structural guarantee is what we care about.
"""

from __future__ import annotations

import ast
from pathlib import Path

import axiom.bot as bot_module


# Loops that pull work off a shared queue. If any of these dies silently the
# system reaches the "alive but idle" state that the 04-26 incident exposed.
_QUEUE_DRAINING_LOOPS = {"scheduler_loop", "task_processor_loop", "agent_runner_loop"}


def _load_bot_ast() -> ast.AST:
    return ast.parse(Path(bot_module.__file__).read_text(encoding="utf-8"))


def _collect_loops_and_error_handlers(tree: ast.AST) -> tuple[dict[str, ast.AST], set[str]]:
    loops: dict[str, ast.AST] = {}
    error_handlers: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        for dec in node.decorator_list:
            # @tasks.loop(seconds=...)
            if isinstance(dec, ast.Call) and getattr(dec.func, "attr", None) == "loop":
                loops[node.name] = node
            # @some_loop.error
            if isinstance(dec, ast.Attribute) and dec.attr == "error":
                target = getattr(dec.value, "id", None) or getattr(dec.value, "attr", None)
                if target:
                    error_handlers.add(target)
    return loops, error_handlers


def _body_handles_cancelled_error(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if not isinstance(child, ast.ExceptHandler):
            continue
        exc_type = child.type
        if exc_type is None:
            continue
        names: list[str] = []
        if isinstance(exc_type, ast.Tuple):
            names = [getattr(elt, "attr", None) or getattr(elt, "id", None) for elt in exc_type.elts]
        else:
            names = [getattr(exc_type, "attr", None) or getattr(exc_type, "id", None)]
        if "CancelledError" in names:
            # Re-raising would still kill the loop, so the handler must
            # NOT contain a bare ``raise`` of CancelledError.
            for stmt in ast.walk(child):
                if isinstance(stmt, ast.Raise) and stmt.exc is None:
                    return False
            return True
    return False


def test_queue_draining_loops_have_error_handlers():
    tree = _load_bot_ast()
    loops, error_handlers = _collect_loops_and_error_handlers(tree)
    missing = sorted(name for name in _QUEUE_DRAINING_LOOPS if name not in loops)
    assert not missing, f"expected tasks.loop methods missing from axiom.bot: {missing}"
    no_handler = sorted(name for name in _QUEUE_DRAINING_LOOPS if name not in error_handlers)
    assert not no_handler, (
        "queue-draining loops must register an `@<loop>.error` handler so the "
        f"loop auto-restarts on crash. Missing: {no_handler}"
    )


def test_queue_draining_loops_swallow_cancelled_error():
    tree = _load_bot_ast()
    loops, _ = _collect_loops_and_error_handlers(tree)
    offenders = []
    for name in _QUEUE_DRAINING_LOOPS:
        node = loops.get(name)
        if node is None:
            offenders.append(f"{name} (loop not found)")
            continue
        if not _body_handles_cancelled_error(node):
            offenders.append(name)
    assert not offenders, (
        "queue-draining loops must catch asyncio.CancelledError without "
        "re-raising it; otherwise a gateway reconnect kills the loop "
        f"permanently. Offenders: {offenders}"
    )


def test_bot_runtime_loops_are_api_owned_by_default(monkeypatch):
    monkeypatch.delenv("AXIOM_BOT_OWNS_RUNTIME", raising=False)
    assert bot_module._bot_owns_runtime_loops() is False

    monkeypatch.setenv("AXIOM_BOT_OWNS_RUNTIME", "1")
    assert bot_module._bot_owns_runtime_loops() is True
