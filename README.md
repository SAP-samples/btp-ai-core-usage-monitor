# AI Core Consumption Monitor

A Python background agent that runs on **SAP BTP Cloud Foundry** and monitors daily AI Core capacity unit consumption. When consumption approaches a configured monthly spending limit, it sends notifications via **SMTP email** and/or **SAP Alert Notification Service (ANS)**.

---

## How It Works

```
┌─────────────────────────────────────────────────────────────┐
│  SAP BTP Cloud Foundry                                      │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  ai-core-monitor (Python / Flask)                    │   │
│  │                                                      │   │
│  │  APScheduler ──► run_monitoring_job() [daily 07:00]  │   │
│  │       │                                              │   │
│  │       ▼                                              │   │
│  │  UAS Reporting API ──► fetch usage (OAuth2)          │   │
│  │       │                                              │   │
│  │       ▼                                              │   │
│  │  Aggregate ai-core capacity_units                    │   │
│  │       │                                              │   │
│  │       ▼                                              │   │
│  │  Compare vs. SPENDING_LIMIT                          │   │
│  │       │                                              │   │
│  │       ├── < 60%  → INFO  (daily email if enabled)    │   │
│  │       ├── ≥ 60%  → WARNING email 🟡                  │   │
│  │       └── ≥ 80%  → ALERT email 🔴                    │   │
│  │                                                      │   │
│  │  Flask endpoints: /health  /status  /trigger         │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

**Metric tracked:** `serviceId=ai-core` + `measureId=capacity_units` — summed across all models (Claude, GPT, embedding models, etc.) for the current calendar month.

---

## Project Structure

```
ai-core-monitor/
├── server.py          # Flask app entry point (health, /status, /trigger, /config)
├── agent.py           # APScheduler + core monitoring logic
├── usage_client.py    # UAS Reporting API client (OAuth2 + aggregation)
├── notifier.py        # SMTP HTML email + SAP Alert Notification Service
├── config.py          # All configuration from environment variables
├── manifest.yml       # CF deployment manifest
├── Procfile           # gunicorn startup command
├── requirements.txt   # Python dependencies
├── runtime.txt        # Python version
├── .env.example       # Template for local development
└── README.md
```

---

## Configuration Reference

All configuration is via environment variables (set in `manifest.yml` or via `cf set-env`).

| Variable | Required | Default | Description |
|---|---|---|---|
| `SUBACCOUNT_ID` | ✅ | — | SAP BTP subaccount UUID to monitor |
| `SPENDING_LIMIT` | ✅ | `100` | Monthly AI Core capacity unit budget |
| `WARNING_THRESHOLD_PERCENT` | | `60` | % of limit that triggers a warning email |
| `ALERT_THRESHOLD_PERCENT` | | `80` | % of limit that triggers an alert email |
| `RECEIVE_DAILY_UPDATE_EMAILS` | | `true` | Send a daily summary even below warning threshold |
| `NOTIFICATION_EMAIL` | ✅ (SMTP) | — | Comma-separated recipient email addresses |
| `SENDER_NAME` | | `AI Core Consumption Monitor` | Display name in the From field |
| `CHECK_TIME_UTC` | | `07:00` | Time of day (UTC) to run the daily check |
| `UAS_CLIENT_ID` | ✅ (Option B) | — | OAuth2 client ID — not needed if using CF service binding |
| `UAS_CLIENT_SECRET` | ✅ (Option B) | — | OAuth2 client secret — not needed if using CF service binding |
| `UAS_TOKEN_URL` | ✅ (Option B) | — | OAuth2 token endpoint URL (append `/oauth/token` to the `url` from the service key) — not needed if using CF service binding |
| `ENABLE_SMTP` | | `true` | Enable SMTP email notifications |
| `SMTP_HOST` | ✅ (SMTP) | — | SMTP server hostname |
| `SMTP_PORT` | | `587` | SMTP port |
| `SMTP_USER` | ✅ (SMTP) | — | SMTP login username |
| `SMTP_FROM` | | `SMTP_USER` | Sender address in the `From` header — set if your SMTP server requires a specific authorized address |
| `SMTP_PASSWORD` | ✅ (SMTP) | — | SMTP password |
| `SMTP_USE_TLS` | | `true` | Use STARTTLS (`true`) or SMTP_SSL (`false`) |
| `ENABLE_ANS` | | `false` | Enable SAP Alert Notification Service |
| `ANS_SERVICE_NAME` | | `ai-core-monitor-ans` | CF service instance name to use. When only one ANS instance is bound, it is found automatically by service label — this variable is only needed if multiple ANS instances are bound and you need to select a specific one |
| `ANS_URL` | ✅ (ANS Option B) | — | ANS API URL — not needed if using CF service binding |
| `ANS_CLIENT_ID` | ✅ (ANS Option B) | — | ANS OAuth2 client ID — not needed if using CF service binding |
| `ANS_CLIENT_SECRET` | ✅ (ANS Option B) | — | ANS OAuth2 client secret — not needed if using CF service binding |
| `ANS_TOKEN_URL` | ✅ (ANS Option B) | — | ANS OAuth2 token URL — not needed if using CF service binding |

---

## Prerequisites

1. **SAP BTP subaccount** with AI Core (Extended plan) enabled
2. **Usage Data Management** service instance with a service key (for UAS API credentials)
3. **CF CLI** installed and logged in to your BTP Cloud Foundry environment
4. An **SMTP server** (e.g. Office 365, Gmail, SendGrid) — OR — an **SAP Alert Notification Service** instance

---

## Step-by-Step Deployment

### 1. Obtain UAS Reporting API Credentials

> **Tip — CF service binding (Option A):** Instead of managing credentials manually, you can bind the `uas` service instance directly to the app. The app reads credentials automatically from `VCAP_SERVICES` — no `UAS_CLIENT_ID` / `UAS_CLIENT_SECRET` / `UAS_TOKEN_URL` env vars needed. See [Step 2](#2-configure-the-manifest) for how to add it to `manifest.yml`.


The Resource Consumption APIs of the **SAP Usage Data Management service** are protected with OAuth 2.0 Client Credentials. Follow the official SAP guide: [Configuration for Using the Resource Consumption APIs](https://help.sap.com/docs/btp/sap-business-technology-platform/configuration-for-using-resource-consumption-apis-in-sap-api-business-hub)

#### Step-by-step

**1.1 — Create a service instance**

The SAP Usage Data Management service (`uas`) has two relevant plans:

| Plan | Use case | Scope granted |
|---|---|---|
| `reporting-ga-admin` | Reports for an entire **global account** (use this for subaccount monitoring) | `Reporting.GA_Admin` |
| `reporting-directory` | Reports scoped to a specific **directory** | `reporting.Directory_Admin` |

For monitoring a subaccount, use **`reporting-ga-admin`**.

In the **BTP Cockpit** (Cloud Foundry environment):
1. Navigate to your subaccount → **Services** → **Service Marketplace**
2. Search for **"Usage Data Management Service (UDM)"** (technical name: `uas`)
3. Select plan **`reporting-ga-admin`** and create a new instance
4. Give the instance a name, e.g. `uas-reporting`

Or via CF CLI:
```bash
cf create-service uas reporting-ga-admin uas-reporting
```

**1.2 — Create a service key**

In the BTP Cockpit:
1. Open the service instance → **Service Keys** tab → **Create Service Key**
2. Name it, e.g. `uas-reporting-key`, and click **Create**

Or via CF CLI:
```bash
cf create-service-key uas-reporting uas-reporting-key
cf service-key uas-reporting uas-reporting-key
```

**1.3 — Extract credentials from the service key**

The service key JSON looks like this:
```json
{
  "clientid":     "sb-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx!bXXXXX|uas-reporting!bXXX",
  "clientsecret": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "identityzone": "your-global-account-id",
  "url":          "https://<global-account-id>.authentication.<region>.hana.ondemand.com",
  "apiurl":       "https://api.authentication.<region>.hana.ondemand.com"
}
```

Map the fields to environment variables:

| Service key field | Environment variable | Notes |
|---|---|---|
| `clientid` | `UAS_CLIENT_ID` | |
| `clientsecret` | `UAS_CLIENT_SECRET` | |
| `url` | `UAS_TOKEN_URL` | Append `/oauth/token` to the base URL from the service key |

**1.4 — Verify the token (optional)**

```bash
curl -L -X POST '<url>/oauth/token' \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -u '<clientid>:<clientsecret>' \
  -d 'grant_type=client_credentials'
```

Where `<url>` is the `url` field from the service key.

A successful response contains an `access_token` with scope `<xsappname>.Reporting.GA_Admin`.

> **Role requirement:** The service principal must have access to the global account that owns the subaccount being monitored. If you receive a `403 Forbidden`, ensure the service instance was created at the correct account level.

### 2. Configure the manifest

Edit `manifest.yml` and fill in the non-sensitive values.

**Option A — CF service binding for UAS (recommended):**
```yaml
# In the services: section, add your uas instance name:
services:
  - uas-reporting          # your uas service instance name
  - ai-core-monitor-ans

# No UAS_CLIENT_ID / UAS_CLIENT_SECRET / UAS_TOKEN_URL needed in env:
env:
  SUBACCOUNT_ID: "your-subaccount-uuid"
  SPENDING_LIMIT: "50"
```

**Option B — Explicit credentials:**
```yaml
env:
  SUBACCOUNT_ID: "your-subaccount-uuid"
  SPENDING_LIMIT: "50"
  NOTIFICATION_EMAIL: "your-team@company.com"   # required for SMTP
  UAS_CLIENT_ID: "sb-..."
  UAS_TOKEN_URL: "https://<global-account-id>.authentication.<region>.hana.ondemand.com/oauth/token"
  # UAS_REPORTING_BASE_URL is auto-derived from UAS_TOKEN_URL – only set if needed

  # ── SMTP (only required if ENABLE_SMTP=true) ──
  SMTP_HOST: "smtp.office365.com"
  SMTP_PORT: "587"
  SMTP_USER: "monitor@company.com"
  SMTP_USE_TLS: "true"
  # SMTP_FROM: "monitor@company.com"  # only if your server requires a specific sender address
```

> **Never put secrets in `manifest.yml`** — set them via `cf set-env` after the first deployment (see Step 5).
> SMTP secret: `SMTP_PASSWORD`. UAS secret (Option B only): `UAS_CLIENT_SECRET`.

### 3. (Optional) Set up SAP Alert Notification Service

> See the official tutorial: [Getting Started with SAP Alert Notification Service for SAP BTP](https://developers.sap.com/tutorials/alert-notification-service..html)

**3.1 — Create a service instance and service key**

```bash
cf create-service alert-notification standard ai-core-monitor-ans
cf create-service-key ai-core-monitor-ans ai-core-monitor-ans-key
cf service-key ai-core-monitor-ans ai-core-monitor-ans-key
```

**3.2 — Configure `manifest.yml`**

Uncomment the `services` section and set `ENABLE_ANS`:
```yaml
services:
  - ai-core-monitor-ans

env:
  ENABLE_ANS: "true"
  ENABLE_SMTP: "false"   # set to false if ANS is your primary channel
```

**3.3 — Create a condition and subscription**

In the ANS dashboard (BTP cockpit → your ANS service instance → **Manage**):

1. Go to **Actions** → **Create** and configure your delivery channel.

   ANS supports multiple action types — choose whichever fits your setup (or any other channel available in your ANS instance):

   | Type | Notes |
   |---|---|
   | `EMAIL` | Plain-text email (default, no extra config needed) |
   | `EMAIL` + HTML | Rich HTML email — requires a custom Payload Template (see below) |
   | `SLACK` | Post to a Slack channel via webhook |
   | `MS_TEAMS` | Post to a Microsoft Teams channel via webhook |
   | … | Any other channel configured in your ANS instance |

   For a **plain-text email**, just fill in the recipient address and leave Subject/Payload Templates empty.

   For a **rich HTML email**, additionally:
   - Check **Use HTML** ✅
   - Fill in **Subject Template** and **Payload Template** as shown below

   > ⚠️ Two requirements for HTML emails in ANS:
   > 1. A custom **Payload Template** must be provided (leaving it empty gives *"Using HTML is not allowed for the default email payload template"*)
   > 2. The template **must** contain the placeholder `{ans-disclaimer}` (ANS appends its mandatory legal footer there)

<details>
<summary>📋 Optional: HTML Subject & Payload Templates (click to expand)</summary>

**Subject Template:**
```
{subject}
```

**Payload Template:**

> ANS template variables use single-brace syntax: `{subject}`, `{body}`, `{tags.<key>}`.
> The template must be compact — ANS enforces a payload length limit.

```html
<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f4f6f9;font-family:Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="padding:20px 0;"><tr><td align="center">
<table width="580" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:8px;overflow:hidden;">

<tr><td style="background:#0070f3;padding:20px 24px;">
  <b style="color:#fff;font-size:18px;">🤖 AI Core Consumption Monitor</b>
  <p style="margin:4px 0 0;color:#cce4ff;font-size:12px;">SAP BTP · {tags.subaccountName}</p>
</td></tr>

<tr><td style="padding:16px 24px 0;">
  <div style="background:#f8f9fa;border:1px solid #dee2e6;border-radius:6px;padding:12px 16px;">
    <b style="color:#495057;font-size:15px;">{tags.alertEmoji} {tags.alertLabel}</b>
    <p style="margin:4px 0 0;color:#495057;font-size:13px;">Consumption is at <b>{tags.percentageUsed}%</b> of the monthly limit.</p>
  </div>
</td></tr>

<tr><td style="padding:16px 24px 0;">
  <table width="100%" cellpadding="0" cellspacing="0"><tr>
    <td width="32%" style="text-align:center;padding:10px;background:#f8f9fa;border-radius:6px;">
      <div style="font-size:20px;font-weight:700;color:#0070f3;">{tags.totalCapacityUnits}</div>
      <div style="font-size:11px;color:#6c757d;">CU Used</div>
    </td>
    <td width="4%"></td>
    <td width="32%" style="text-align:center;padding:10px;background:#f8f9fa;border-radius:6px;">
      <div style="font-size:20px;font-weight:700;color:#495057;">{tags.spendingLimit}</div>
      <div style="font-size:11px;color:#6c757d;">Monthly Limit</div>
    </td>
    <td width="4%"></td>
    <td width="32%" style="text-align:center;padding:10px;background:#f8f9fa;border-radius:6px;">
      <div style="font-size:20px;font-weight:700;color:#6f42c1;">{tags.projectedCu}</div>
      <div style="font-size:11px;color:#6c757d;">Projected</div>
    </td>
  </tr></table>
</td></tr>

<tr><td style="padding:12px 24px 0;font-size:12px;color:#6c757d;">
  📅 {tags.periodStart} → {tags.periodEnd} &nbsp;|&nbsp; Day {tags.daysElapsed}/{tags.daysInMonth}
  &nbsp;|&nbsp; ⚠️ {tags.warningThreshold}% / 🚨 {tags.alertThreshold}%
</td></tr>

<tr><td style="padding:16px 24px;">
  <div style="background:#f8f9fa;border-radius:6px;padding:10px 14px;font-size:12px;color:#495057;">
    <pre style="margin:0;white-space:pre-wrap;font-family:Arial,sans-serif;">{body}</pre>
  </div>
</td></tr>

<tr><td style="padding:12px 24px;border-top:1px solid #dee2e6;font-size:11px;color:#adb5bd;text-align:center;">
  AI Core Consumption Monitor · SAP BTP · {tags.subaccountName}<br>{ans-disclaimer}
</td></tr>

</table></td></tr></table>
</body></html>
```

</details>

2. Go to **Conditions** → **Create**:
   - **Property:** `eventType`
   - **Predicate:** `EQUALS`
   - **Value:** `AI_CORE_CAPACITY_MONITOR`

3. Go to **Subscriptions** → **Create**, then:
   - Select the condition created in step 2
   - Select the action created in step 1

### 4. Deploy

```bash
cf push
```

### 5. Set secrets

`cf set-env` requires the app to exist, so run these after the first `cf push`:

```bash
# Only required if using Option B (explicit UAS credentials, not CF service binding):
cf set-env ai-core-monitor UAS_CLIENT_SECRET 'your-uas-client-secret'

# Only required if ENABLE_SMTP=true:
cf set-env ai-core-monitor SMTP_PASSWORD 'your-smtp-password'

cf restage ai-core-monitor
```

### 6. Verify

```bash
# Check app is running – the output shows the assigned random route
cf app ai-core-monitor

# Get just the URL
APP_URL="https://$(cf app ai-core-monitor | grep -E '^routes:' | awk '{print $2}')"

# View logs
cf logs ai-core-monitor --recent

# Check health endpoint
curl "$APP_URL/health"

# View last monitoring result
curl "$APP_URL/status"

# Manually trigger a check (dry run – no emails sent)
curl -X POST "$APP_URL/trigger" \
  -H "Content-Type: application/json" \
  -d '{"dry_run": true}'

# Manually trigger a real check (sends notifications if thresholds are met)
curl -X POST "$APP_URL/trigger"
```

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness probe — returns `{"status": "ok"}` |
| `GET` | `/status` | Last monitoring result as JSON |
| `GET` | `/config` | Active configuration (credentials masked) |
| `POST` | `/trigger` | Run a monitoring check immediately |

### `/trigger` request body (optional)

```json
{ "dry_run": true }
```
When `dry_run=true`, usage is fetched and the alert level is computed, but **no notifications are sent**. Useful for testing.

### `/status` response example

```json
{
  "status": "ok",
  "last_run_utc": "2026-04-22T07:00:01.234567+00:00",
  "alert_level": "INFO",
  "usage": {
    "total_cu": 17.6328,
    "spending_limit": 100.0,
    "percentage_used": 17.63,
    "projected_cu": 24.12,
    "days_elapsed": 22,
    "days_in_month": 30,
    "period_start": "2026-04-01",
    "period_end": "2026-04-22",
    "subaccount_name": "Building Custom AI Solutions",
    "by_application": {
      "anthropic--claude-4.6-sonnet-1": 15.0546,
      "anthropic--claude-4.5-sonnet-1": 2.4712,
      "gpt-4.1-2025-04-14": 0.1070,
      "text-embedding-3-large-1": 0.0001
    }
  },
  "notification_results": {
    "smtp": true
  },
  "error": null
}
```

---

## Notification Email Preview

The HTML email includes:
- 🟢/🟡/🔴 colour-coded alert banner
- Key metrics: CU used, monthly limit, projected month-end
- Visual progress bar
- Per-model consumption breakdown table
- Configured threshold reference

---

## Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Copy and fill in environment variables
cp .env.example .env
# Edit .env with your actual values

# Run locally (loads .env automatically via python-dotenv)
python server.py

# Test endpoints
curl http://localhost:8080/health
curl -X POST http://localhost:8080/trigger -H "Content-Type: application/json" -d '{"dry_run": true}'
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `SUBACCOUNT_ID is required` in logs | Env var not set | `cf set-env ai-core-monitor SUBACCOUNT_ID "..."` then `cf restage` |
| `401 Unauthorized` from UAS API | Wrong credentials | Verify `UAS_CLIENT_ID`, `UAS_CLIENT_SECRET`, `UAS_TOKEN_URL` |
| `0.0 CU` reported | No AI Core usage this month, or wrong subaccount ID | Check subaccount ID and date range |
| Emails not arriving | SMTP misconfiguration | Check `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` |
| ANS events not routing | No ANS subscription configured | Create a subscription in the ANS dashboard for event type `AI_CORE_CAPACITY_MONITOR` |
| Scheduler fires twice | Multiple CF instances | Keep `instances: 1` in `manifest.yml` |