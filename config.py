"""
config.py
---------
Centralised configuration for the AI Core Consumption Monitor.
All values are read from environment variables so the app can be
configured via `cf set-env` or the manifest.yml without code changes.
"""

import os
from dataclasses import dataclass, field
from typing import List


def _bool(key: str, default: bool = False) -> bool:
    """Parse a boolean environment variable."""
    val = os.environ.get(key, str(default)).strip().lower()
    return val in ("1", "true", "yes", "on")


def _float(key: str, default: float = 0.0) -> float:
    try:
        return float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _int(key: str, default: int = 0) -> int:
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _list(key: str, default: str = "") -> List[str]:
    """Parse a comma-separated list of strings."""
    raw = os.environ.get(key, default).strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass
class Config:
    # ── BTP Subaccount ────────────────────────────────────────────────────────
    subaccount_id: str = field(
        default_factory=lambda: os.environ.get("SUBACCOUNT_ID", "")
    )

    # ── Spending limit (capacity units / month) ───────────────────────────────
    spending_limit: float = field(
        default_factory=lambda: _float("SPENDING_LIMIT", 100.0)
    )

    # ── Alert thresholds (% of spending_limit) ────────────────────────────────
    warning_threshold_percent: float = field(
        default_factory=lambda: _float("WARNING_THRESHOLD_PERCENT", 60.0)
    )
    alert_threshold_percent: float = field(
        default_factory=lambda: _float("ALERT_THRESHOLD_PERCENT", 80.0)
    )

    # ── Daily update emails ───────────────────────────────────────────────────
    receive_daily_update_emails: bool = field(
        default_factory=lambda: _bool("RECEIVE_DAILY_UPDATE_EMAILS", True)
    )

    # ── Notification recipients ───────────────────────────────────────────────
    # Comma-separated list of email addresses
    notification_emails: List[str] = field(
        default_factory=lambda: _list("NOTIFICATION_EMAIL")
    )

    # ── Scheduler ─────────────────────────────────────────────────────────────
    # Time of day (UTC) to run the daily check, e.g. "07:00"
    check_time_utc: str = field(
        default_factory=lambda: os.environ.get("CHECK_TIME_UTC", "07:00")
    )

    # ── UAS Reporting API ─────────────────────────────────────────────────────
    # Credentials can come from a CF service binding (preferred) or explicit env vars.
    # Option A – CF service binding: bind a 'uas' service instance in manifest.yml.
    #   UAS_SERVICE_NAME selects the instance by name (optional if only one is bound).
    # Option B – Explicit env vars: set UAS_CLIENT_ID, UAS_CLIENT_SECRET, UAS_TOKEN_URL.
    #
    # UAS_REPORTING_BASE_URL is always optional – derived from the token URL region.
    uas_service_name: str = field(
        default_factory=lambda: os.environ.get("UAS_SERVICE_NAME", "")
    )
    uas_reporting_base_url: str = field(
        default_factory=lambda: os.environ.get("UAS_REPORTING_BASE_URL", "")
    )
    uas_client_id: str = field(
        default_factory=lambda: os.environ.get("UAS_CLIENT_ID", "")
    )
    uas_client_secret: str = field(
        default_factory=lambda: os.environ.get("UAS_CLIENT_SECRET", "")
    )
    uas_token_url: str = field(
        default_factory=lambda: os.environ.get("UAS_TOKEN_URL", "")
    )

    # ── SMTP email ────────────────────────────────────────────────────────────
    enable_smtp: bool = field(
        default_factory=lambda: _bool("ENABLE_SMTP", True)
    )
    smtp_host: str = field(
        default_factory=lambda: os.environ.get("SMTP_HOST", "")
    )
    smtp_port: int = field(
        default_factory=lambda: _int("SMTP_PORT", 587)
    )
    smtp_user: str = field(
        default_factory=lambda: os.environ.get("SMTP_USER", "")
    )
    smtp_password: str = field(
        default_factory=lambda: os.environ.get("SMTP_PASSWORD", "")
    )
    # Sender address used in the From header.
    # Defaults to SMTP_USER if not set.
    smtp_from: str = field(
        default_factory=lambda: os.environ.get("SMTP_FROM", "")
    )
    smtp_use_tls: bool = field(
        default_factory=lambda: _bool("SMTP_USE_TLS", True)
    )
    sender_name: str = field(
        default_factory=lambda: os.environ.get("SENDER_NAME", "AI Core Consumption Monitor")
    )

    # ── SAP Alert Notification Service ────────────────────────────────────────
    enable_ans: bool = field(
        default_factory=lambda: _bool("ENABLE_ANS", False)
    )
    # Name of the bound CF service instance for ANS
    ans_service_name: str = field(
        default_factory=lambda: os.environ.get(
            "ANS_SERVICE_NAME", "ai-core-monitor-ans"
        )
    )
    # If not using CF service binding, provide credentials directly
    ans_url: str = field(
        default_factory=lambda: os.environ.get("ANS_URL", "")
    )
    ans_client_id: str = field(
        default_factory=lambda: os.environ.get("ANS_CLIENT_ID", "")
    )
    ans_client_secret: str = field(
        default_factory=lambda: os.environ.get("ANS_CLIENT_SECRET", "")
    )
    ans_token_url: str = field(
        default_factory=lambda: os.environ.get("ANS_TOKEN_URL", "")
    )

    def validate(self) -> List[str]:
        """Return a list of validation error messages (empty = valid)."""
        errors = []
        if not self.subaccount_id:
            errors.append("SUBACCOUNT_ID is required")
        if self.spending_limit <= 0:
            errors.append("SPENDING_LIMIT must be a positive number")
        # UAS credentials: either a CF service binding OR explicit env vars must be present.
        # The actual binding check happens at runtime in usage_client._get_uas_credentials().
        # Here we only validate explicit env vars when no binding name is configured.
        has_explicit_creds = self.uas_client_id and self.uas_client_secret and self.uas_token_url
        has_binding_hint = bool(os.environ.get("VCAP_SERVICES", "{}").strip() not in ("{}", ""))
        if not has_explicit_creds and not has_binding_hint:
            errors.append(
                "UAS credentials not configured. Either bind a 'uas' service instance "
                "or set UAS_CLIENT_ID, UAS_CLIENT_SECRET, and UAS_TOKEN_URL."
            )
        if self.enable_smtp:
            if not self.smtp_host:
                errors.append("SMTP_HOST is required when ENABLE_SMTP=true")
            if not self.smtp_user:
                errors.append("SMTP_USER is required when ENABLE_SMTP=true")
            if not self.notification_emails:
                errors.append(
                    "NOTIFICATION_EMAIL is required when ENABLE_SMTP=true"
                )
        if self.enable_ans:
            # ANS credentials can come from CF service binding or env vars
            if not self.ans_url and not self.ans_service_name:
                errors.append(
                    "ANS_URL or ANS_SERVICE_NAME is required when ENABLE_ANS=true"
                )
        return errors


# Singleton instance used across the application
cfg = Config()