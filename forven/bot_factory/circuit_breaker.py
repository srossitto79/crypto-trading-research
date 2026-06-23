"""Circuit breaker and LLM cost control for Bot Factory."""

from __future__ import annotations

import logging

from forven.db import (
    get_bot,
    get_bot_status,
    increment_bot_errors,
    increment_bot_llm_calls,
    reset_bot_errors,
    set_bot_status,
    log_activity,
)

logger = logging.getLogger(__name__)


def check_circuit_breaker(bot_id: str) -> bool:
    """Check if bot should continue running.

    Returns True if OK, False if bot should be paused.
    """
    bot = get_bot(bot_id)
    if not bot:
        return False
    status = get_bot_status(bot_id)
    if not status:
        return False
    max_errors = bot.get("max_consecutive_errors", 5)
    current_errors = status.get("consecutive_errors", 0) or 0
    if current_errors >= max_errors:
        logger.warning(
            "Circuit breaker tripped for bot %s: %d consecutive errors (max %d)",
            bot_id, current_errors, max_errors,
        )
        return False
    return True


def check_llm_daily_cap(bot_id: str) -> bool:
    """Check if bot has exceeded daily LLM call cap.

    Returns True if OK, False if cap reached.
    """
    bot = get_bot(bot_id)
    if not bot:
        return False
    status = get_bot_status(bot_id)
    if not status:
        return False
    max_calls = bot.get("max_llm_calls_per_day", 200)
    # The counter only resets lazily on the next increment; if the stored reset
    # date isn't today, today's effective count is 0. This lets a daily-cap-paused
    # bot resume after UTC midnight without a real LLM call to trigger the reset.
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if status.get("llm_calls_reset_date") != today:
        return True
    current_calls = status.get("llm_calls_today", 0) or 0
    if current_calls >= max_calls:
        logger.warning(
            "Daily LLM cap reached for bot %s: %d / %d calls",
            bot_id, current_calls, max_calls,
        )
        return False
    return True


def record_success(bot_id: str) -> None:
    """Record a successful decision cycle — reset consecutive errors."""
    reset_bot_errors(bot_id)


def record_failure(bot_id: str) -> None:
    """Record a failed decision cycle. Auto-pause if threshold reached."""
    new_count = increment_bot_errors(bot_id)
    bot = get_bot(bot_id)
    max_errors = (bot or {}).get("max_consecutive_errors", 5)
    if new_count >= max_errors:
        bot_name = (bot or {}).get("name", bot_id)
        set_bot_status(bot_id, "paused", error_message=f"Circuit breaker: {new_count} consecutive errors")
        log_activity(
            "warning",
            "bot_factory",
            f"Bot '{bot_name}' auto-paused: {new_count} consecutive errors",
            {"bot_id": bot_id, "consecutive_errors": new_count},
        )
        logger.warning("Bot %s auto-paused after %d consecutive errors", bot_id, new_count)


def record_llm_call(bot_id: str) -> None:
    """Record an LLM API call. Pause bot if daily cap reached."""
    new_count = increment_bot_llm_calls(bot_id)
    bot = get_bot(bot_id)
    max_calls = (bot or {}).get("max_llm_calls_per_day", 200)
    if new_count >= max_calls:
        bot_name = (bot or {}).get("name", bot_id)
        set_bot_status(bot_id, "paused", error_message=f"Daily LLM cap reached: {new_count} calls")
        log_activity(
            "info",
            "bot_factory",
            f"Bot '{bot_name}' paused: daily LLM cap reached ({new_count}/{max_calls})",
            {"bot_id": bot_id, "llm_calls_today": new_count, "max_calls": max_calls},
        )
