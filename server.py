"""
server.py
---------
Flask web server for the AI Core Consumption Monitor.

Cloud Foundry requires a web process that binds to $PORT.
This server provides:
  - GET  /health   – liveness probe (always returns 200)
  - GET  /status   – last monitoring result as JSON
  - POST /trigger  – manually trigger a monitoring check immediately
  - GET  /config   – show active (non-sensitive) configuration

The APScheduler daily job is started when the app boots.
"""

# Load .env file for local development (no-op when running on CF)
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

import logging
import os
import signal
import sys

from flask import Flask, jsonify, request

import agent
from config import cfg

# ── Logging setup ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# ── Flask app ─────────────────────────────────────────────────────────────────

app = Flask(__name__)


# ── Routes ────────────────────────────────────────────────────────────────────


@app.route("/health", methods=["GET"])
def health():
    """
    Liveness probe endpoint.
    Returns 200 OK so CF / load balancers know the app is alive.
    """
    return jsonify({"status": "ok", "service": "ai-core-monitor"}), 200


@app.route("/status", methods=["GET"])
def status():
    """
    Return the result of the last monitoring run as JSON.

    Response fields:
      - status:               "ok" | "error" | "never_run"
      - last_run_utc:         ISO-8601 timestamp of last run (or null)
      - alert_level:          "INFO" | "WARNING" | "ALERT" (or null)
      - usage.total_cu:       Total AI Core capacity units this month
      - usage.percentage_used: % of spending limit consumed
      - usage.projected_cu:   Linear projection to end of month
      - usage.by_application: Per-model breakdown
      - notification_results: {"smtp": bool, "ans": bool}
      - error:                Error message if last run failed (or null)
    """
    result = agent.get_last_result()
    http_status = 200 if result.get("status") != "error" else 500
    return jsonify(result), http_status


@app.route("/trigger", methods=["POST"])
def trigger():
    """
    Manually trigger a monitoring check immediately (runs synchronously).

    Useful for testing configuration or forcing an immediate check.
    Returns the same JSON structure as /status.

    Optional JSON body:
      { "dry_run": true }  – fetch usage and compute level but skip notifications
    """
    body = request.get_json(silent=True) or {}
    dry_run = bool(body.get("dry_run", False))

    logger.info("Manual trigger received (dry_run=%s)", dry_run)

    if dry_run:
        # Run the job but temporarily disable notifications
        original_smtp = cfg.enable_smtp
        original_ans = cfg.enable_ans
        cfg.enable_smtp = False
        cfg.enable_ans = False
        try:
            result = agent.run_monitoring_job()
        finally:
            cfg.enable_smtp = original_smtp
            cfg.enable_ans = original_ans
        result["dry_run"] = True
    else:
        result = agent.run_monitoring_job()

    http_status = 200 if result.get("status") != "error" else 500
    return jsonify(result), http_status


@app.route("/config", methods=["GET"])
def show_config():
    """
    Return the active (non-sensitive) configuration as JSON.
    Credentials and passwords are masked.
    """
    return jsonify(
        {
            "subaccount_id": cfg.subaccount_id,
            "spending_limit": cfg.spending_limit,
            "warning_threshold_percent": cfg.warning_threshold_percent,
            "alert_threshold_percent": cfg.alert_threshold_percent,
            "receive_daily_update_emails": cfg.receive_daily_update_emails,
            "notification_emails": cfg.notification_emails,
            "check_time_utc": cfg.check_time_utc,
            "uas_reporting_base_url": cfg.uas_reporting_base_url,
            "uas_client_id": cfg.uas_client_id[:6] + "***" if cfg.uas_client_id else "",
            "enable_smtp": cfg.enable_smtp,
            "smtp_host": cfg.smtp_host,
            "smtp_port": cfg.smtp_port,
            "smtp_user": cfg.smtp_user,
            "smtp_use_tls": cfg.smtp_use_tls,
            "sender_name": cfg.sender_name,
            "enable_ans": cfg.enable_ans,
            "ans_service_name": cfg.ans_service_name,
        }
    ), 200


# ── Startup ───────────────────────────────────────────────────────────────────


def _validate_and_warn():
    """Log configuration validation errors as warnings (don't crash on startup)."""
    errors = cfg.validate()
    if errors:
        logger.warning("Configuration issues detected:")
        for err in errors:
            logger.warning("  ✗ %s", err)
    else:
        logger.info("Configuration validated successfully ✓")


def _graceful_shutdown(signum, frame):
    logger.info("Received signal %d, shutting down...", signum)
    agent.stop_scheduler()
    sys.exit(0)


# ── Module-level initialisation (runs under both `python server.py` and gunicorn)
# gunicorn imports this module, so anything at module level executes in the worker.
# We use a single gunicorn worker (--workers 1 in Procfile) to avoid duplicate jobs.

_validate_and_warn()

signal.signal(signal.SIGTERM, _graceful_shutdown)
signal.signal(signal.SIGINT, _graceful_shutdown)

agent.start_scheduler()

# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Bind to the port CF assigns via $PORT (default 8080)
    port = int(os.environ.get("PORT", 8080))
    logger.info("Starting Flask server on port %d", port)

    # Use threaded=True so the scheduler thread and request threads coexist
    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)
