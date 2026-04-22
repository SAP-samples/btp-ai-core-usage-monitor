"""
agent.py
--------
Core monitoring logic and APScheduler setup for the AI Core Capacity Monitor.

The daily job:
  1. Fetches AI Core capacity unit usage for the current month via the UAS API
  2. Determines the alert level (INFO / WARNING / ALERT) based on thresholds
  3. Sends notifications via configured channels (SMTP and/or ANS)
  4. Stores the last result in memory for the /status endpoint
"""

import logging
import threading
from datetime import datetime, timezone
from typing import Dict, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import notifier
import usage_client
from config import cfg
from notifier import LEVEL_ALERT, LEVEL_INFO, LEVEL_WARNING

logger = logging.getLogger(__name__)

# ── Shared state (thread-safe via lock) ───────────────────────────────────────

_state_lock = threading.Lock()
_last_result: Dict = {
    "status": "never_run",
    "last_run_utc": None,
    "usage": None,
    "alert_level": None,
    "notification_results": None,
    "error": None,
}


def get_last_result() -> Dict:
    """Return a copy of the last monitoring result (safe to call from any thread)."""
    with _state_lock:
        return dict(_last_result)


def _set_last_result(**kwargs) -> None:
    with _state_lock:
        _last_result.update(kwargs)


# ── Alert level determination ─────────────────────────────────────────────────


def determine_alert_level(total_cu: float) -> str:
    """
    Determine the alert level based on current consumption vs. spending limit.

    Returns one of: LEVEL_INFO, LEVEL_WARNING, LEVEL_ALERT
    """
    if cfg.spending_limit <= 0:
        return LEVEL_INFO

    pct = (total_cu / cfg.spending_limit) * 100

    if pct >= cfg.alert_threshold_percent:
        return LEVEL_ALERT
    elif pct >= cfg.warning_threshold_percent:
        return LEVEL_WARNING
    else:
        return LEVEL_INFO


# ── Core monitoring job ───────────────────────────────────────────────────────


def run_monitoring_job() -> Dict:
    """
    Execute one monitoring cycle:
      1. Fetch current month's AI Core capacity unit usage
      2. Determine alert level
      3. Send notifications if warranted
      4. Update shared state

    Returns the result dict (same structure as _last_result).
    """
    run_time = datetime.now(timezone.utc).isoformat()
    logger.info("=== AI Core Capacity Monitor: starting daily check (%s) ===", run_time)

    try:
        # ── Step 1: Fetch usage ───────────────────────────────────────────────
        usage = usage_client.get_current_month_usage()

        total_cu = usage["total_cu"]
        pct = (total_cu / cfg.spending_limit * 100) if cfg.spending_limit > 0 else 0.0

        logger.info(
            "Usage this month: %.6f CU / %.2f CU limit (%.1f%%) | "
            "Projected: %.6f CU | Day %d of %d",
            total_cu,
            cfg.spending_limit,
            pct,
            usage.get("projected_cu", 0.0),
            usage.get("days_elapsed", 0),
            usage.get("days_in_month", 0),
        )

        # ── Step 2: Determine alert level ─────────────────────────────────────
        level = determine_alert_level(total_cu)
        logger.info("Alert level: %s", level)

        # ── Step 3: Decide whether to send a notification ─────────────────────
        should_notify = (
            level in (LEVEL_WARNING, LEVEL_ALERT)  # always notify on threshold breach
            or cfg.receive_daily_update_emails       # or if daily updates are enabled
        )

        notification_results = {}
        if should_notify:
            logger.info("Sending %s notification...", level)
            notification_results = notifier.send_notification(usage, level)
            logger.info("Notification results: %s", notification_results)
        else:
            logger.info(
                "No notification sent (level=INFO, daily updates disabled)"
            )

        # ── Step 4: Update state ──────────────────────────────────────────────
        result = {
            "status": "ok",
            "last_run_utc": run_time,
            "usage": {
                "total_cu": total_cu,
                "spending_limit": cfg.spending_limit,
                "percentage_used": round(pct, 2),
                "projected_cu": usage.get("projected_cu", 0.0),
                "days_elapsed": usage.get("days_elapsed", 0),
                "days_in_month": usage.get("days_in_month", 0),
                "period_start": usage.get("period_start", ""),
                "period_end": usage.get("period_end", ""),
                "subaccount_name": usage.get("subaccount_name", ""),
                "by_application": usage.get("by_application", {}),
            },
            "alert_level": level,
            "notification_results": notification_results,
            "error": None,
        }
        _set_last_result(**result)
        logger.info("=== Monitoring check complete ===")
        return result

    except Exception as exc:
        error_msg = str(exc)
        logger.error("Monitoring job failed: %s", error_msg, exc_info=True)
        result = {
            "status": "error",
            "last_run_utc": run_time,
            "usage": None,
            "alert_level": None,
            "notification_results": None,
            "error": error_msg,
        }
        _set_last_result(**result)
        return result


# ── Scheduler setup ───────────────────────────────────────────────────────────


def _parse_check_time(time_str: str):
    """
    Parse CHECK_TIME_UTC (e.g. "07:00" or "07:30") into (hour, minute).
    Falls back to 07:00 on parse error.
    """
    try:
        parts = time_str.strip().split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        return hour, minute
    except Exception:
        logger.warning(
            "Could not parse CHECK_TIME_UTC='%s', defaulting to 07:00", time_str
        )
        return 7, 0


_scheduler: Optional[BackgroundScheduler] = None


def start_scheduler() -> BackgroundScheduler:
    """
    Create and start the APScheduler background scheduler.
    Schedules the daily monitoring job at the configured UTC time.

    Returns the scheduler instance.
    """
    global _scheduler

    hour, minute = _parse_check_time(cfg.check_time_utc)

    logger.info(
        "Starting scheduler: daily check at %02d:%02d UTC", hour, minute
    )

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        func=run_monitoring_job,
        trigger=CronTrigger(hour=hour, minute=minute, timezone="UTC"),
        id="daily_capacity_check",
        name="AI Core Daily Capacity Check",
        replace_existing=True,
        misfire_grace_time=3600,  # allow up to 1 hour late start
    )
    _scheduler.start()

    logger.info(
        "Scheduler started. Next run: %s",
        _scheduler.get_job("daily_capacity_check").next_run_time,
    )

    return _scheduler


def stop_scheduler() -> None:
    """Gracefully shut down the scheduler."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")