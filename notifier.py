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
from datetime import date
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
        "color": "#155724",
        "bg": "#d4edda",
        "border_left": "#28a745",
        "emoji": "✅",
        "label": "Daily Update",
        "ans_severity": "INFO",
        "ans_category": "NOTIFICATION",
        "bar_color": "#28a745",
    },
    LEVEL_WARNING: {
        "color": "#6a3b00",
        "bg": "#fff3cd",
        "border_left": "#e9730c",
        "emoji": "⚠️",
        "label": "Warning",
        "ans_severity": "WARNING",
        "ans_category": "ALERT",
        "bar_color": "#e9730c",
    },
    LEVEL_ALERT: {
        "color": "#6a0000",
        "bg": "#ffd6d6",
        "border_left": "#bb0000",
        "emoji": "🚨",
        "label": "Alert",
        "ans_severity": "ERROR",
        "ans_category": "ALERT",
        "bar_color": "#bb0000",
    },
}


# ── HTML email builder ────────────────────────────────────────────────────────


def _build_html_email(usage: Dict, level: str) -> str:
    """
    Build a rich HTML email body for the given usage data and alert level.
    Uses SAP '72 Brand' typography and SAP BTP colour palette.
    """
    meta = _LEVEL_META[level]
    total_cu = usage["total_cu"]
    spending_limit = cfg.spending_limit
    pct = (total_cu / spending_limit * 100) if spending_limit > 0 else 0.0
    projected_cu = usage.get("projected_cu", 0.0)
    projected_pct = (projected_cu / spending_limit * 100) if spending_limit > 0 else 0.0
    subaccount_name = usage.get("subaccount_name") or cfg.subaccount_id
    resource_group = usage.get("resource_group", "")
    # Pre-compute resource group HTML snippets (backslashes not allowed in f-string expressions
    # before Python 3.12, so we build these strings outside the f-string)
    rg_code_style = "background:#f4f6f9;padding:2px 6px;border-radius:3px;font-size:12px;color:#354a5f;"
    rg_header_html = (
        f'&nbsp;&#183;&nbsp; Resource Group: <code style="{rg_code_style}">{resource_group}</code>'
        if resource_group else ""
    )
    rg_footer_html = (
        f"&nbsp;&#183;&nbsp; Resource Group: {resource_group}"
        if resource_group else ""
    )
    today = date.today()
    month_start_iso = today.replace(day=1).isoformat()
    fetch_date_iso = usage.get("fetch_date", today.isoformat())
    period_start = usage.get("period_start") or month_start_iso
    period_end = usage.get("period_end") or fetch_date_iso
    # Format dates as "01 May 2026" — unambiguous across locales
    def _fmt_date(iso: str) -> str:
        try:
            return date.fromisoformat(iso).strftime("%-d %b %Y")
        except (ValueError, AttributeError):
            return iso
    period_start_fmt = _fmt_date(period_start)
    period_end_fmt = _fmt_date(period_end)
    days_elapsed = usage.get("days_elapsed", 0)
    days_in_month = usage.get("days_in_month", 0)
    fetch_date = usage.get("fetch_date", "")
    by_application = usage.get("by_application", {})

    bar_color = meta["bar_color"]
    bar_width = min(pct, 100)
    bar_remaining = 100 - bar_width

    # Application breakdown rows (alternating row background)
    app_rows = ""
    for i, (app, cu) in enumerate(by_application.items()):
        app_pct = (cu / total_cu * 100) if total_cu > 0 else 0.0
        row_bg = " style=\"background-color:#f4f6f9;\"" if i % 2 == 1 else ""
        app_rows += f"""
              <tr{row_bg}>
                <td style="padding:9px 14px;border-bottom:1px solid #d5dadd;font-family:'72 Brand',Arial,Helvetica,sans-serif;font-size:13px;color:#223548;">{app}</td>
                <td style="padding:9px 14px;border-bottom:1px solid #d5dadd;text-align:right;font-family:'72 Brand',Arial,Helvetica,sans-serif;font-size:13px;color:#223548;">{cu:.6f}</td>
                <td style="padding:9px 14px;border-bottom:1px solid #d5dadd;text-align:right;font-family:'72 Brand',Arial,Helvetica,sans-serif;font-size:13px;color:#223548;">{app_pct:.1f}%</td>
              </tr>"""

    html = f"""<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
  <meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
  <meta name="viewport" content="width=device-width">
  <meta http-equiv="X-UA-Compatible" content="IE=edge">
  <meta name="x-apple-disable-message-reformatting">
  <title>AI Core Consumption Monitor</title>
  <style type="text/css">
    @font-face {{
      font-family: '72 Brand';
      font-weight: 400;
      src: url("https://client-data.knak.io/production/company_data/642c6c7705cf9/custom-fonts/65d8f652ae55d/72-Regular-full.woff2");
    }}
    @font-face {{
      font-family: '72 Brand';
      font-weight: 700;
      src: url("https://client-data.knak.io/production/company_data/642c6c7705cf9/custom-fonts/65d8f652ae55d/72-Bold-full.woff2");
    }}
    a {{ color: #0057d2; text-decoration: underline; }}
    img {{ border: 0; height: auto; line-height: 100%; outline: 0; text-decoration: none; }}
    p {{ margin: 0; padding: 0; }}
  </style>
</head>
<body style="margin:0;padding:0;background-color:#f4f6f9;font-family:'72 Brand',Arial,Helvetica,sans-serif;">
<table border="0" cellpadding="0" cellspacing="0" width="100%" style="background-color:#f4f6f9;">
  <tr>
    <td align="center" style="padding:24px 0;">
      <table border="0" cellpadding="0" cellspacing="0" style="max-width:640px;width:100%;">
        <tr>
          <td bgcolor="#ffffff" style="background-color:#ffffff;">

            <!-- SAP BTP Logo -->
            <table border="0" cellpadding="0" cellspacing="0" width="100%" bgcolor="#ffffff">
              <tr>
                <td style="padding:24px 48px;background-color:#ffffff;">
                  <img src="https://www.sap.com/dam/application/shared/logos/sap-group/sap-btp-blu.png"
                       alt="SAP BTP" height="32" style="height:32px;width:auto;vertical-align:top;">
                </td>
              </tr>
            </table>

            <!-- Alert banner -->
            <table border="0" cellpadding="0" cellspacing="0" width="100%">
              <tr>
                <td style="padding:0 48px;">
                  <table border="0" cellpadding="0" cellspacing="0" width="100%">
                    <tr>
                      <td style="background-color:{meta['bg']};border-left:4px solid {meta['border_left']};padding:16px 20px;border-radius:4px;">
                        <div style="font-family:'72 Brand',Arial,Helvetica,sans-serif;font-size:16px;font-weight:700;color:{meta['color']};">
                          {meta['emoji']} {meta['label']} — AI Core Capacity Usage
                        </div>
                        <div style="font-family:'72 Brand',Arial,Helvetica,sans-serif;font-size:14px;color:{meta['color']};margin-top:6px;line-height:1.5em;">
                          AI Core capacity unit consumption is at <strong>{pct:.1f}%</strong> of your monthly spending limit.
                        </div>
                      </td>
                    </tr>
                  </table>
                </td>
              </tr>
            </table>

            <!-- Spacer -->
            <table border="0" cellpadding="0" cellspacing="0" width="100%">
              <tr><td height="24"><div style="height:24px;line-height:24px;"> </div></td></tr>
            </table>

            <!-- Subaccount title -->
            <table border="0" cellpadding="0" cellspacing="0" width="100%">
              <tr>
                <td style="padding:0 48px;">
                  <div style="font-family:'72 Brand',Arial,Helvetica,sans-serif;font-size:22px;font-weight:700;color:#223548;line-height:1.2em;">
                    {subaccount_name}
                  </div>
                   <div style="font-family:'72 Brand',Arial,Helvetica,sans-serif;font-size:13px;color:#354a5f;margin-top:4px;">
                     Subaccount ID: <code style="background:#f4f6f9;padding:2px 6px;border-radius:3px;font-size:12px;color:#354a5f;">{cfg.subaccount_id}</code>
                     {rg_header_html}
                     &nbsp;&#183;&nbsp; &#128197; {period_start_fmt} &#8594; {period_end_fmt}
                   </div>
                </td>
              </tr>
            </table>

            <!-- Spacer -->
            <table border="0" cellpadding="0" cellspacing="0" width="100%">
              <tr><td height="24"><div style="height:24px;line-height:24px;"> </div></td></tr>
            </table>

            <!-- Key metrics (3 cards) -->
            <table border="0" cellpadding="0" cellspacing="0" width="100%">
              <tr>
                <td style="padding:0 48px;">
                  <table border="0" cellpadding="0" cellspacing="0" width="100%">
                    <tr>
                      <td width="32%" style="background-color:#f4f6f9;border-radius:6px;padding:16px 12px;text-align:center;vertical-align:top;">
                        <div style="font-family:'72 Brand',Arial,Helvetica,sans-serif;font-size:24px;font-weight:700;color:#0057d2;">{total_cu:.4f}</div>
                        <div style="font-family:'72 Brand',Arial,Helvetica,sans-serif;font-size:12px;color:#354a5f;margin-top:4px;">Capacity Units Used</div>
                      </td>
                      <td width="2%"></td>
                      <td width="32%" style="background-color:#f4f6f9;border-radius:6px;padding:16px 12px;text-align:center;vertical-align:top;">
                        <div style="font-family:'72 Brand',Arial,Helvetica,sans-serif;font-size:24px;font-weight:700;color:#223548;">{spending_limit:.2f}</div>
                        <div style="font-family:'72 Brand',Arial,Helvetica,sans-serif;font-size:12px;color:#354a5f;margin-top:4px;">Monthly Limit (CU)</div>
                      </td>
                      <td width="2%"></td>
                      <td width="32%" style="background-color:#f4f6f9;border-radius:6px;padding:16px 12px;text-align:center;vertical-align:top;">
                        <div style="font-family:'72 Brand',Arial,Helvetica,sans-serif;font-size:24px;font-weight:700;color:{meta['color']};">{projected_cu:.4f}</div>
                        <div style="font-family:'72 Brand',Arial,Helvetica,sans-serif;font-size:12px;color:#354a5f;margin-top:4px;">Projected Month-End</div>
                      </td>
                    </tr>
                  </table>
                </td>
              </tr>
            </table>

            <!-- Spacer -->
            <table border="0" cellpadding="0" cellspacing="0" width="100%">
              <tr><td height="24"><div style="height:24px;line-height:24px;"> </div></td></tr>
            </table>

            <!-- Progress bar (table-based for email client compatibility) -->
            <table border="0" cellpadding="0" cellspacing="0" width="100%">
              <tr>
                <td style="padding:0 48px;">
                  <table border="0" cellpadding="0" cellspacing="0" width="100%">
                    <tr>
                      <td style="font-family:'72 Brand',Arial,Helvetica,sans-serif;font-size:13px;font-weight:700;color:#223548;padding-bottom:6px;">
                        Monthly Usage Progress
                      </td>
                      <td align="right" style="font-family:'72 Brand',Arial,Helvetica,sans-serif;font-size:13px;font-weight:700;color:#223548;padding-bottom:6px;">
                        {pct:.1f}% of {spending_limit:.2f} CU
                      </td>
                    </tr>
                    <tr>
                      <td colspan="2">
                        <table border="0" cellpadding="0" cellspacing="0" width="100%" style="background-color:#d5dadd;border-radius:4px;height:14px;">
                          <tr>
                            <td width="{bar_width:.1f}%" style="background-color:{bar_color};border-radius:4px;height:14px;line-height:14px;font-size:1px;"> </td>
                            <td width="{bar_remaining:.1f}%"></td>
                          </tr>
                        </table>
                      </td>
                    </tr>
                    <tr>
                      <td style="font-family:'72 Brand',Arial,Helvetica,sans-serif;font-size:11px;color:#354a5f;padding-top:6px;">
                        Day {days_elapsed} of {days_in_month}
                      </td>
                      <td align="right" style="font-family:'72 Brand',Arial,Helvetica,sans-serif;font-size:11px;color:#354a5f;padding-top:6px;">
                        Projected: {projected_pct:.1f}% ({projected_cu:.4f} CU)
                      </td>
                    </tr>
                  </table>
                </td>
              </tr>
            </table>

            <!-- Spacer -->
            <table border="0" cellpadding="0" cellspacing="0" width="100%">
              <tr><td height="32"><div style="height:32px;line-height:32px;"> </div></td></tr>
            </table>

            <!-- Divider -->
            <table border="0" cellpadding="0" cellspacing="0" width="100%">
              <tr><td style="padding:0 48px;"><div style="border-top:1px solid #d5dadd;"></div></td></tr>
            </table>

            <!-- Spacer -->
            <table border="0" cellpadding="0" cellspacing="0" width="100%">
              <tr><td height="24"><div style="height:24px;line-height:24px;"> </div></td></tr>
            </table>

            <!-- Consumption breakdown table -->
            <table border="0" cellpadding="0" cellspacing="0" width="100%">
              <tr>
                <td style="padding:0 48px;">
                  <div style="font-family:'72 Brand',Arial,Helvetica,sans-serif;font-size:15px;font-weight:700;color:#223548;margin-bottom:12px;">
                    &#128202; Consumption by Application
                  </div>
                  <table border="0" cellpadding="0" cellspacing="0" width="100%" style="border:1px solid #d5dadd;border-radius:6px;border-collapse:separate;border-spacing:0;font-size:13px;font-family:'72 Brand',Arial,Helvetica,sans-serif;">
                    <thead>
                      <tr style="background-color:#f4f6f9;">
                        <th style="padding:10px 14px;text-align:left;color:#354a5f;font-weight:700;border-bottom:2px solid #d5dadd;">Application / Model</th>
                        <th style="padding:10px 14px;text-align:right;color:#354a5f;font-weight:700;border-bottom:2px solid #d5dadd;">Capacity Units</th>
                        <th style="padding:10px 14px;text-align:right;color:#354a5f;font-weight:700;border-bottom:2px solid #d5dadd;">Share</th>
                      </tr>
                    </thead>
                    <tbody>
                      {app_rows}
                    </tbody>
                    <tfoot>
                      <tr style="background-color:#f4f6f9;">
                        <td style="padding:10px 14px;font-weight:700;color:#223548;">Total</td>
                        <td style="padding:10px 14px;text-align:right;font-weight:700;color:#223548;">{total_cu:.6f}</td>
                        <td style="padding:10px 14px;text-align:right;font-weight:700;color:#223548;">100%</td>
                      </tr>
                    </tfoot>
                  </table>
                </td>
              </tr>
            </table>

            <!-- Spacer -->
            <table border="0" cellpadding="0" cellspacing="0" width="100%">
              <tr><td height="24"><div style="height:24px;line-height:24px;"> </div></td></tr>
            </table>

            <!-- Thresholds reference -->
            <table border="0" cellpadding="0" cellspacing="0" width="100%">
              <tr>
                <td style="padding:0 48px;">
                  <div style="background-color:#f4f6f9;border-radius:4px;padding:12px 16px;font-family:'72 Brand',Arial,Helvetica,sans-serif;font-size:12px;color:#354a5f;">
                    <strong>Configured thresholds:</strong>
                    &nbsp;&#9888;&#65039; Warning at {cfg.warning_threshold_percent:.0f}%
                    &nbsp;|&nbsp;
                    &#128680; Alert at {cfg.alert_threshold_percent:.0f}%
                    &nbsp;|&nbsp;
                    Monthly limit: {spending_limit:.2f} CU
                  </div>
                </td>
              </tr>
            </table>

            <!-- Spacer -->
            <table border="0" cellpadding="0" cellspacing="0" width="100%">
              <tr><td height="40"><div style="height:40px;line-height:40px;"> </div></td></tr>
            </table>

            <!-- Footer -->
            <table border="0" cellpadding="0" cellspacing="0" width="100%" bgcolor="#EAECEE">
              <tr><td bgcolor="#EAECEE" height="24"><div style="height:24px;line-height:24px;"> </div></td></tr>
              <tr>
                <td bgcolor="#EAECEE" style="padding:0 48px;">
                  <img src="https://www.sap.com/dam/application/shared/logos/sap-group/sap-tagline-straight-r-grad-blu.png"
                       alt="SAP | Bring out your best" height="24" style="height:24px;width:auto;vertical-align:top;">
                </td>
              </tr>
              <tr><td bgcolor="#EAECEE" height="12"><div style="height:12px;line-height:12px;"> </div></td></tr>
              <tr>
                <td bgcolor="#EAECEE" style="padding:0 48px 24px;">
                   <div style="font-family:'72 Brand',Arial,Helvetica,sans-serif;font-size:12px;line-height:1.5em;color:#354a5f;">
                     This report was generated automatically by the AI Core Consumption Monitor on {fetch_date}.<br>
                     Running on SAP BTP Cloud Foundry &nbsp;&#183;&nbsp; Subaccount: {subaccount_name}{rg_footer_html}
                   </div>
                </td>
              </tr>
            </table>

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
    subaccount_name = usage.get("subaccount_name") or cfg.subaccount_id
    resource_group = usage.get("resource_group", "")
    scope = f"{subaccount_name} / {resource_group}" if resource_group else subaccount_name
    return (
        f"{meta['emoji']} [{meta['label']}] AI Core Capacity: "
        f"{pct:.1f}% used ({usage['total_cu']:.4f} / {cfg.spending_limit:.2f} CU) "
        f"– {scope}"
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
        creds["token_url"],  # already includes /oauth/token from VCAP_SERVICES oauth_url
        auth=(creds["client_id"], creds["client_secret"]),  # SAP XSUAA requires Basic Auth
        data={"grant_type": "client_credentials"},
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
    # Use 'or' fallback so empty strings also fall back to the subaccount ID
    subaccount_name = usage.get("subaccount_name") or cfg.subaccount_id
    _today = date.today()
    period_start_iso = usage.get("period_start") or _today.replace(day=1).isoformat()
    period_end_iso = usage.get("period_end") or usage.get("fetch_date", _today.isoformat())
    # Human-readable format for tag values used in ANS email templates
    def _fmt(iso: str) -> str:
        try:
            return date.fromisoformat(iso).strftime("%-d %b %Y")
        except (ValueError, AttributeError):
            return iso
    period_start = _fmt(period_start_iso)
    period_end = _fmt(period_end_iso)
    top_consumer = next(iter(usage.get("by_application") or {}), "none")

    # Build the ANS event payload
    event = {
        "eventType": "AI_CORE_CAPACITY_MONITOR",
        "eventTimestamp": int(time.time()),
        "severity": meta["ans_severity"],
        "category": meta["ans_category"],
        "subject": (
            f"AI Core Capacity {meta['label']}: {pct:.1f}% used"
            + (f" (Resource Group: {cfg.resource_group})" if cfg.resource_group else "")
        ),
        "body": (
            f"Subaccount '{subaccount_name}' ({cfg.subaccount_id})"
            + (f", Resource Group '{cfg.resource_group}'" if cfg.resource_group else "")
            + f" has consumed {total_cu:.6f} capacity units ({pct:.1f}% of the "
            f"{cfg.spending_limit:.2f} CU monthly limit). "
            f"Projected month-end: {usage.get('projected_cu', 0.0):.6f} CU.\n\n"
            f"Reporting period: {period_start} → {period_end}.\n"
            f"Top consumer: {top_consumer}."
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
            # Colour tokens for ANS HTML Payload Templates
            "alertColor": meta["color"],
            "alertBg": meta["bg"],
            "alertBorder": meta["border_left"],
            "projectedCu": str(round(usage.get("projected_cu", 0.0), 6)),
            "daysElapsed": str(usage.get("days_elapsed", 0)),
            "daysInMonth": str(usage.get("days_in_month", 0)),
            "periodStart": period_start,
            "periodEnd": period_end,
            "reportingPeriod": f"{period_start} to {period_end}",
            "periodStartIso": period_start_iso,
            "periodEndIso": period_end_iso,
            "warningThreshold": str(cfg.warning_threshold_percent),
            "alertThreshold": str(cfg.alert_threshold_percent),
            "resourceGroup": usage.get("resource_group", ""),
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


