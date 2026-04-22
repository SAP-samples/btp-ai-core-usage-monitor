"""
usage_client.py
---------------
Client for the SAP BTP Usage and Accounting Service (UAS) Reporting API.

Responsibilities:
  - Obtain and cache an OAuth2 bearer token
  - Fetch subaccount usage for a given date range
  - Filter and aggregate AI Core capacity units
"""

import logging
import time
from datetime import date, datetime
from typing import Dict, List, Optional

import requests

from config import cfg

logger = logging.getLogger(__name__)

# ── OAuth2 token cache ────────────────────────────────────────────────────────

_token_cache: Dict[str, object] = {
    "access_token": None,
    "expires_at": 0.0,
}


def _get_access_token() -> str:
    """
    Fetch (or return cached) OAuth2 access token for the UAS Reporting API.
    Tokens are cached until 60 seconds before expiry.
    """
    now = time.time()
    if _token_cache["access_token"] and now < _token_cache["expires_at"]:
        return _token_cache["access_token"]

    logger.info("Fetching new OAuth2 token from %s", cfg.uas_token_url)
    resp = requests.post(
        cfg.uas_token_url,
        data={
            "grant_type": "client_credentials",
            "client_id": cfg.uas_client_id,
            "client_secret": cfg.uas_client_secret,
        },
        timeout=30,
    )
    resp.raise_for_status()
    token_data = resp.json()

    access_token = token_data["access_token"]
    expires_in = int(token_data.get("expires_in", 3600))

    _token_cache["access_token"] = access_token
    _token_cache["expires_at"] = now + expires_in - 60  # 60-second safety margin

    logger.info("OAuth2 token obtained, expires in %d seconds", expires_in)
    return access_token


# ── API call ──────────────────────────────────────────────────────────────────


def fetch_subaccount_usage(from_date: date, to_date: date) -> List[dict]:
    """
    Call the UAS Reporting API and return the raw list of usage records.

    Args:
        from_date: Start of the reporting period (inclusive).
        to_date:   End of the reporting period (inclusive).

    Returns:
        List of usage record dicts as returned by the API.
    """
    token = _get_access_token()

    reporting_url = cfg._resolved_uas_reporting_base_url()
    if not reporting_url:
        raise ValueError(
            "Cannot determine UAS Reporting URL. "
            "Set UAS_REPORTING_BASE_URL or ensure UAS_TOKEN_URL contains a "
            "recognisable region (e.g. .authentication.eu10.hana.ondemand.com)."
        )
    url = f"{reporting_url}/reports/v1/subaccountUsage"
    params = {
        "fromDate": from_date.strftime("%Y%m%d"),
        "toDate": to_date.strftime("%Y%m%d"),
        "subaccountId": cfg.subaccount_id,
    }

    logger.info(
        "Fetching usage for subaccount %s from %s to %s",
        cfg.subaccount_id,
        params["fromDate"],
        params["toDate"],
    )

    resp = requests.get(
        url,
        params=params,
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    resp.raise_for_status()

    data = resp.json()
    records = data.get("content", [])
    logger.info("Received %d usage records from UAS API", len(records))
    return records


# ── Aggregation ───────────────────────────────────────────────────────────────


def aggregate_ai_core_capacity_units(records: List[dict]) -> Dict:
    """
    Filter records to AI Core capacity units and aggregate them.

    Filtering criteria:
      - serviceId == "ai-core"
      - measureId == "capacity_units"

    Returns a dict with:
      - total_cu:          float  – total capacity units consumed
      - by_application:   dict   – {application_name: cu_value}
      - subaccount_name:  str    – human-readable subaccount name
      - period_start:     str    – ISO start date from the records
      - period_end:       str    – ISO end date from the records
      - raw_records:      list   – the matching raw records
    """
    ai_core_cu_records = [
        r
        for r in records
        if r.get("serviceId") == "ai-core" and r.get("measureId") == "capacity_units"
    ]

    total_cu = sum(r.get("usage", 0.0) for r in ai_core_cu_records)

    by_application: Dict[str, float] = {}
    for r in ai_core_cu_records:
        app = r.get("application") or "unknown"
        by_application[app] = by_application.get(app, 0.0) + r.get("usage", 0.0)

    # Sort applications by consumption descending
    by_application = dict(
        sorted(by_application.items(), key=lambda x: x[1], reverse=True)
    )

    # Extract metadata from the first matching record (if any)
    subaccount_name = ""
    period_start = ""
    period_end = ""
    if ai_core_cu_records:
        first = ai_core_cu_records[0]
        subaccount_name = first.get("subaccountName", "")
        period_start = first.get("startIsoDate", "")
        period_end = first.get("endIsoDate", "")

    logger.info(
        "Aggregated AI Core capacity units: %.6f CU across %d application(s)",
        total_cu,
        len(by_application),
    )

    return {
        "total_cu": total_cu,
        "by_application": by_application,
        "subaccount_name": subaccount_name,
        "period_start": period_start,
        "period_end": period_end,
        "raw_records": ai_core_cu_records,
    }


# ── Convenience: fetch + aggregate for current month ─────────────────────────


def get_current_month_usage() -> Dict:
    """
    Fetch and aggregate AI Core capacity unit usage for the current calendar month
    up to and including today.

    Returns the same dict structure as aggregate_ai_core_capacity_units(), plus:
      - days_elapsed:   int   – number of days from month start to today (inclusive)
      - days_in_month:  int   – total days in the current month
      - projected_cu:   float – linear projection to end of month
      - fetch_date:     str   – ISO date when the data was fetched
    """
    today = date.today()
    month_start = today.replace(day=1)

    # Days in the current month (handle year-end rollover)
    if today.month == 12:
        next_month = today.replace(year=today.year + 1, month=1, day=1)
    else:
        next_month = today.replace(month=today.month + 1, day=1)
    days_in_month = (next_month - month_start).days

    days_elapsed = (today - month_start).days + 1  # inclusive of today

    records = fetch_subaccount_usage(month_start, today)
    result = aggregate_ai_core_capacity_units(records)

    # Linear projection
    if days_elapsed > 0:
        projected_cu = (result["total_cu"] / days_elapsed) * days_in_month
    else:
        projected_cu = 0.0

    result.update(
        {
            "days_elapsed": days_elapsed,
            "days_in_month": days_in_month,
            "projected_cu": projected_cu,
            "fetch_date": today.isoformat(),
        }
    )

    return result