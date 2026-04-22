"""
notifier.py
-----------
Notification channels for the AI Core Consumption Monitor.

Supported channels:
  1. SMTP – sends an HTML email via any SMTP server
  2. SAP Alert Notification Service (ANS) – posts a structured event to the
     ANS REST API, which can then route to email, Slack, MS Teams, etc.
"""

import json
import logging
import os
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, List, Optional

import requests

from config import cfg

logger = logging.getLogger(__name__)

# ── Alert levels ──────────────────────────────────────────────────────────────

LEVEL_INFO = "INFO"
LEVEL_WARNING = "WARNING"
LEVEL_ALERT = "ALERT"

_LEVEL_META = {
    LEVEL_INFO: {
        "color": "#28a745",
        "bg": "#d4edda",
        "border": "#c3e6cb",
        "emoji": "✅",
        "label": "Daily Update",
        "ans_severity": "INFO",
        "ans_category": "NOTIFICATION",
    },
    LEVEL_WARNING: {
        "color": "#856404",
        "bg": "#fff3cd",
        "border": "#ffeeba",
        "emoji": "⚠️",
        "label": "Warning",
        "ans_severity": "WARNING",
        "ans_category": "ALERT",
    },
    LEVEL_ALERT: {
        "color": "#721c24",
        "bg": "#f8d7da",
        "border": "#f5c6cb",
        "emoji": "🚨",
        "label": "Alert",
        "ans_severity": "ERROR",
        "ans_category": "ALERT",
    },
}


# ── HTML email builder ────────────────────────────────────────────────────────


def _build_html_email(usage: Dict, level: str) -> str:
    """
    Build a rich HTML email body for the given usage data and alert level.
    """
    meta = _LEVEL_META[level]
    total_cu = usage["total_cu"]
    spending_limit = cfg.spending_limit
    pct = (total_cu / spending_limit * 100) if spending_limit > 0 else 0.0
    projected_cu = usage.get("projected_cu", 0.0)
    projected_pct = (projected_cu / spending_limit * 100) if spending_limit > 0 else 0.0
    subaccount_name = usage.get("subaccount_name", cfg.subaccount_id)
    period_start = usage.get("period_start", "")
    period_end = usage.get("period_end", "")
    days_elapsed = usage.get("days_elapsed", 0)
    days_in_month = usage.get("days_in_month", 0)
    fetch_date = usage.get("fetch_date", "")
    by_application = usage.get("by_application", {})

    # Progress bar colour
    if pct >= cfg.alert_threshold_percent:
        bar_color = "#dc3545"
    elif pct >= cfg.warning_threshold_percent:
        bar_color = "#ffc107"
    else:
        bar_color = "#28a745"

    bar_width = min(pct, 100)

    # Application breakdown rows
    app_rows = ""
    for app, cu in by_application.items():
        app_pct = (cu / total_cu * 100) if total_cu > 0 else 0.0
        app_rows += f"""
        <tr>
          <td style="padding:8px 12px;border-bottom:1px solid #dee2e6;">{app}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #dee2e6;text-align:right;">{cu:.6f}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #dee2e6;text-align:right;">{app_pct:.1f}%</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AI Core Consumption Monitor</title>
</head>
<body style="margin:0;padding:0;background-color:#f4f6f9;font-family:Arial,Helvetica,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f4f6f9;padding:30px 0;">
    <tr>
      <td align="center">
        <table width="620" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.1);">

          <!-- Header -->
          <tr>
            <td style="background-color:#0070f3;padding:24px 32px;">
              <h1 style="margin:0;color:#ffffff;font-size:20px;font-weight:600;">
                🤖 AI Core Consumption Monitor
              </h1>
              <p style="margin:4px 0 0;color:#cce4ff;font-size:13px;">
                SAP BTP · {subaccount_name}
              </p>
            </td>
          </tr>

          <!-- Alert banner -->
          <tr>
            <td style="padding:0 32px;padding-top:24px;">
              <div style="background:{meta['bg']};border:1px solid {meta['border']};border-radius:6px;padding:14px 18px;">
                <span style="color:{meta['color']};font-size:16px;font-weight:700;">
                  {meta['emoji']} {meta['label']}
                </span>
                <p style="margin:6px 0 0;color:{meta['color']};font-size:14px;">
                  AI Core capacity unit consumption is at
                  <strong>{pct:.1f}%</strong> of your monthly spending limit.
                </p>
              </div>
            </td>
          </tr>

          <!-- Key metrics -->
          <tr>
            <td style="padding:24px 32px 0;">
              <table width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td width="33%" style="text-align:center;padding:12px;background:#f8f9fa;border-radius:6px;margin-right:8px;">
                    <div style="font-size:22px;font-weight:700;color:#0070f3;">{total_cu:.4f}</div>
                    <div style="font-size:12px;color:#6c757d;margin-top:4px;">Capacity Units Used</div>
                  </td>
                  <td width="4%"></td>
                  <td width="33%" style="text-align:center;padding:12px;background:#f8f9fa;border-radius:6px;">
                    <div style="font-size:22px;font-weight:700;color:#495057;">{spending_limit:.2f}</div>
                    <div style="font-size:12px;color:#6c757d;margin-top:4px;">Monthly Limit</div>
                  </td>
                  <td width="4%"></td>
                  <td width="33%" style="text-align:center;padding:12px;background:#f8f9fa;border-radius:6px;">
                    <div style="font-size:22px;font-weight:700;color:#6f42c1;">{projected_cu:.4f}</div>
                    <div style="font-size:12px;color:#6c757d;margin-top:4px;">Projected Month-End</div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Progress bar -->
          <tr>
            <td style="padding:20px 32px 0;">
              <div style="display:flex;justify-content:space-between;margin-bottom:6px;">
                <span style="font-size:13px;color:#495057;font-weight:600;">Monthly Usage Progress</span>
                <span style="font-size:13px;color:#495057;font-weight:600;">{pct:.1f}% of {spending_limit:.2f} CU</span>
              </div>
              <div style="background:#e9ecef;border-radius:4px;height:14px;overflow:hidden;">
                <div style="background:{bar_color};width:{bar_width:.1f}%;height:100%;border-radius:4px;"></div>
              </div>
              <div style="display:flex;justify-content:space-between;margin-top:6px;">
                <span style="font-size:11px;color:#6c757d;">Day {days_elapsed} of {days_in_month}</span>
                <span style="font-size:11px;color:#6c757d;">Projected: {projected_pct:.1f}% ({projected_cu:.4f} CU)</span>
              </div>
            </td>
          </tr>

          <!-- Period info -->
          <tr>
            <td style="padding:16px 32px 0;">
              <p style="margin:0;font-size:13px;color:#6c757d;">
                📅 Reporting period: <strong>{period_start}</strong> → <strong>{period_end}</strong>
                &nbsp;|&nbsp; Subaccount ID: <code style="background:#f8f9fa;padding:2px 5px;border-radius:3px;font-size:12px;">{cfg.subaccount_id}</code>
              </p>
            </td>
          </tr>

          <!-- Application breakdown -->
          <tr>
            <td style="padding:24px 32px 0;">
              <h3 style="margin:0 0 12px;font-size:15px;color:#343a40;">
                📊 Consumption by Application
              </h3>
              <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #dee2e6;border-radius:6px;overflow:hidden;font-size:13px;">
                <thead>
                  <tr style="background:#f8f9fa;">
                    <th style="padding:10px 12px;text-align:left;color:#495057;font-weight:600;border-bottom:2px solid #dee2e6;">Application / Model</th>
                    <th style="padding:10px 12px;text-align:right;color:#495057;font-weight:600;border-bottom:2px solid #dee2e6;">Capacity Units</th>
                    <th style="padding:10px 12px;text-align:right;color:#495057;font-weight:600;border-bottom:2px solid #dee2e6;">Share</th>
                  </tr>
                </thead>
                <tbody>
                  {app_rows}
                </tbody>
                <tfoot>
                  <tr style="background:#f8f9fa;">
                    <td style="padding:10px 12px;font-weight:700;color:#343a40;">Total</td>
                    <td style="padding:10px 12px;text-align:right;font-weight:700;color:#343a40;">{total_cu:.6f}</td>
                    <td style="padding:10px 12px;text-align:right;font-weight:700;color:#343a40;">100%</td>
                  </tr>
                </tfoot>
              </table>
            </td>
          </tr>

          <!-- Thresholds reference -->
          <tr>
            <td style="padding:20px 32px 0;">
              <div style="background:#f8f9fa;border-radius:6px;padding:12px 16px;font-size:12px;color:#6c757d;">
                <strong>Configured thresholds:</strong>
                &nbsp;⚠️ Warning at {cfg.warning_threshold_percent:.0f}%
                &nbsp;|&nbsp;
                🚨 Alert at {cfg.alert_threshold_percent:.0f}%
                &nbsp;|&nbsp;
                Monthly limit: {spending_limit:.2f} CU
              </div>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="padding:24px 32px;border-top:1px solid #dee2e6;margin-top:24px;">
              <p style="margin:0;font-size:12px;color:#adb5bd;text-align:center;">
                This report was generated automatically by the AI Core Consumption Monitor on {fetch_date}.<br>
                Running on SAP BTP Cloud Foundry · Subaccount: {subaccount_name}
              </p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

    return html


def _build_subject(usage: Dict, level: str) -> str:
    meta = _LEVEL_META[level]
    pct = (usage["total_cu"] / cfg.spending_limit * 100) if cfg.spending_limit > 0 else 0
    return (
        f"{meta['emoji']} [{meta['label']}] AI Core Capacity: "
        f"{pct:.1f}% used ({usage['total_cu']:.4f} / {cfg.spending_limit:.2f} CU) "
        f"– {usage.get('subaccount_name', cfg.subaccount_id)}"
    )


# ── SMTP channel ──────────────────────────────────────────────────────────────


def send_smtp_notification(usage: Dict, level: str) -> bool:
    """
    Send an HTML email notification via SMTP.

    Returns True on success, False on failure.
    """
    if not cfg.enable_smtp:
        logger.debug("SMTP notifications disabled, skipping")
        return False

    if not cfg.notification_emails:
        logger.warning("No NOTIFICATION_EMAIL configured, skipping SMTP send")
        return False

    subject = _build_subject(usage, level)
    html_body = _build_html_email(usage, level)

    sender_address = cfg.smtp_from or cfg.smtp_user
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{cfg.sender_name} <{sender_address}>"
    msg["To"] = ", ".join(cfg.notification_emails)

    # Plain-text fallback
    plain_text = (
        f"AI Core Consumption Monitor – {_LEVEL_META[level]['label']}\n\n"
        f"Subaccount: {usage.get('subaccount_name', cfg.subaccount_id)}\n"
        f"Total Capacity Units Used: {usage['total_cu']:.6f}\n"
        f"Monthly Spending Limit:    {cfg.spending_limit:.2f}\n"
        f"Usage:                     "
        f"{(usage['total_cu'] / cfg.spending_limit * 100) if cfg.spending_limit else 0:.1f}%\n"
        f"Projected Month-End:       {usage.get('projected_cu', 0.0):.6f} CU\n\n"
        f"Reporting period: {usage.get('period_start', '')} → {usage.get('period_end', '')}\n"
    )

    msg.attach(MIMEText(plain_text, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        if cfg.smtp_use_tls:
            server = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30)
            server.ehlo()
            server.starttls()
            server.ehlo()
        else:
            server = smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, timeout=30)

        if cfg.smtp_password:
            server.login(cfg.smtp_user, cfg.smtp_password)

        server.sendmail(
            sender_address,
            cfg.notification_emails,
            msg.as_string(),
        )
        server.quit()

        logger.info(
            "SMTP email sent to %s (level=%s)", cfg.notification_emails, level
        )
        return True

    except Exception as exc:
        logger.error("Failed to send SMTP email: %s", exc, exc_info=True)
        return False


# ── SAP Alert Notification Service channel ────────────────────────────────────


def _get_ans_credentials() -> Optional[Dict]:
    """
    Resolve ANS credentials from either:
      1. CF service binding (VCAP_SERVICES) – preferred when running on BTP
      2. Explicit environment variables (ANS_URL, ANS_CLIENT_ID, etc.)
    """
    # Try CF service binding first
    vcap_raw = os.environ.get("VCAP_SERVICES", "{}")
    try:
        vcap = json.loads(vcap_raw)
    except json.JSONDecodeError:
        vcap = {}

    for service_list in vcap.values():
        for svc in service_list:
            if svc.get("name") == cfg.ans_service_name or svc.get(
                "label"
            ) == "alert-notification":
                creds = svc.get("credentials", {})
                return {
                    "url": creds.get("url", ""),
                    "client_id": creds.get("client_id", ""),
                    "client_secret": creds.get("client_secret", ""),
                    "token_url": creds.get("oauth_url", ""),
                }

    # Fall back to explicit env vars
    if cfg.ans_url and cfg.ans_client_id:
        return {
            "url": cfg.ans_url,
            "client_id": cfg.ans_client_id,
            "client_secret": cfg.ans_client_secret,
            "token_url": cfg.ans_token_url,
        }

    return None


_ans_token_cache: Dict = {"access_token": None, "expires_at": 0.0}


def _get_ans_token(creds: Dict) -> str:
    """Fetch (or return cached) OAuth2 token for ANS."""
    now = time.time()
    if _ans_token_cache["access_token"] and now < _ans_token_cache["expires_at"]:
        return _ans_token_cache["access_token"]

    resp = requests.post(
        creds["token_url"] + "/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
        },
        timeout=30,
    )
    resp.raise_for_status()
    token_data = resp.json()
    _ans_token_cache["access_token"] = token_data["access_token"]
    _ans_token_cache["expires_at"] = now + int(token_data.get("expires_in", 3600)) - 60
    return _ans_token_cache["access_token"]


def send_ans_notification(usage: Dict, level: str) -> bool:
    """
    Post a structured event to the SAP Alert Notification Service.

    The ANS event follows the CloudEvents-inspired ANS schema.
    ANS can then route the event to email, Slack, MS Teams, PagerDuty, etc.
    based on subscriptions configured in the ANS dashboard.

    Returns True on success, False on failure.
    """
    if not cfg.enable_ans:
        logger.debug("ANS notifications disabled, skipping")
        return False

    creds = _get_ans_credentials()
    if not creds or not creds.get("url"):
        logger.error("ANS credentials not found – check ANS_SERVICE_NAME or ANS_URL")
        return False

    meta = _LEVEL_META[level]
    total_cu = usage["total_cu"]
    pct = (total_cu / cfg.spending_limit * 100) if cfg.spending_limit > 0 else 0.0
    subaccount_name = usage.get("subaccount_name", cfg.subaccount_id)

    # Build the ANS event payload
    event = {
        "eventType": "AI_CORE_CAPACITY_MONITOR",
        "eventTimestamp": int(time.time()),
        "severity": meta["ans_severity"],
        "category": meta["ans_category"],
        "subject": f"AI Core Capacity {meta['label']}: {pct:.1f}% used",
        "body": (
            f"Subaccount '{subaccount_name}' ({cfg.subaccount_id}) has consumed "
            f"{total_cu:.6f} capacity units ({pct:.1f}% of the {cfg.spending_limit:.2f} CU "
            f"monthly limit). Projected month-end: {usage.get('projected_cu', 0.0):.6f} CU.\n\n"
            f"Reporting period: {usage.get('period_start', '')} → {usage.get('period_end', '')}.\n"
            f"Top consumer: {next(iter(usage.get('by_application', {'N/A': 0})), 'N/A')}."
        ),
        "tags": {
            "subaccountId": cfg.subaccount_id,
            "subaccountName": subaccount_name,
            "totalCapacityUnits": str(round(total_cu, 6)),
            "percentageUsed": str(round(pct, 2)),
            "spendingLimit": str(cfg.spending_limit),
            "alertLevel": level,
            "alertLabel": meta["label"],
            "alertEmoji": meta["emoji"],
            "projectedCu": str(round(usage.get("projected_cu", 0.0), 6)),
            "daysElapsed": str(usage.get("days_elapsed", 0)),
            "daysInMonth": str(usage.get("days_in_month", 0)),
            "periodStart": usage.get("period_start", ""),
            "periodEnd": usage.get("period_end", ""),
            "reportingPeriod": f"{usage.get('period_start', '')} to {usage.get('period_end', '')}",
            "warningThreshold": str(cfg.warning_threshold_percent),
            "alertThreshold": str(cfg.alert_threshold_percent),
        },
        "resource": {
            "resourceName": subaccount_name,
            "resourceType": "SAP BTP Subaccount",
            "resourceInstance": cfg.subaccount_id,
            "tags": {"serviceId": "ai-core"},
        },
    }

    try:
        token = _get_ans_token(creds)
        ans_events_url = f"{creds['url'].rstrip('/')}/cf/producer/v1/resource-events"

        resp = requests.post(
            ans_events_url,
            json=event,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        resp.raise_for_status()
        logger.info("ANS event posted successfully (level=%s, status=%d)", level, resp.status_code)
        return True

    except Exception as exc:
        logger.error("Failed to post ANS event: %s", exc, exc_info=True)
        return False


# ── Public interface ──────────────────────────────────────────────────────────


def send_notification(usage: Dict, level: str) -> Dict[str, bool]:
    """
    Send notifications via all configured channels.

    Args:
        usage: Usage data dict from usage_client.get_current_month_usage()
        level: One of LEVEL_INFO, LEVEL_WARNING, LEVEL_ALERT

    Returns:
        Dict with channel names as keys and success booleans as values.
        e.g. {"smtp": True, "ans": False}
    """
    results = {}

    if cfg.enable_smtp:
        results["smtp"] = send_smtp_notification(usage, level)

    if cfg.enable_ans:
        results["ans"] = send_ans_notification(usage, level)

    if not results:
        logger.warning(
            "No notification channels are enabled. "
            "Set ENABLE_SMTP=true and/or ENABLE_ANS=true."
        )

    return results


