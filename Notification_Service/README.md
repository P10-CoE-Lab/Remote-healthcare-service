# Notification Service

A reusable, generic notification service built for internal POC projects. Supports
multiple channels (email, SMS, webhook), multiple providers per channel, rule-based
routing, automatic retry with fallback, and three usage modes — HTTP, MCP, and direct
Python import — all from the same codebase with zero code changes between modes.

---

## Table of Contents

- [Quick Start](#quick-start)
- [Architecture Overview](#architecture-overview)
- [Supported Channels & Providers](#supported-channels--providers)
- [Running the Service](#running-the-service)
  - [Mode 1 — Docker Compose (recommended)](#mode-1--docker-compose-recommended)
  - [Mode 2 — Direct Python (HTTP server)](#mode-2--direct-python-http-server)
  - [Mode 3 — MCP Server](#mode-3--mcp-server)
  - [Mode 4 — In-process Python import](#mode-4--in-process-python-import)
- [Configuration](#configuration)
  - [Important: You Only Need Credentials for Providers You Use](#important-you-only-need-credentials-for-providers-you-use)
  - [Environment Variables](#environment-variables)
  - [notification-config.yaml](#notification-configyaml)
  - [Notification Profiles & Rules](#notification-profiles--rules)
- [Sending a Notification](#sending-a-notification)
  - [HTTP API](#http-api)
  - [Python SDK](#python-sdk)
- [Templates](#templates)
- [Extending the Service](#extending-the-service)
  - [Add a New Provider](#add-a-new-provider)
  - [Add a New Channel](#add-a-new-channel)
  - [Add a New Notification Profile](#add-a-new-notification-profile)
- [Project Structure](#project-structure)
- [Running Tests](#running-tests)
- [Dead-Letter Log](#dead-letter-log)
- [Troubleshooting](#troubleshooting)

---

## Quick Start

Get from zero to a real email in under 5 minutes using Gmail.

**Prerequisites:** Python 3.11+, Git

```bash
# 1. Clone and enter the project
git clone <repo-url>
cd Notification_Service

# 2. Create virtual environment and install dependencies
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 3. Create your .env file
cp .env.example .env
```

Open `.env` and fill in **only** these two lines (you can leave everything else blank for now):

```
GMAIL_ADDRESS=yourname@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
```

> **Gmail App Password setup:** Go to [myaccount.google.com](https://myaccount.google.com) →
> Security → 2-Step Verification (must be enabled first) → App passwords → create one for
> "Mail". You get a 16-character password. Use that here, spaces included.

Also set `gmail` as the email default and remove the mailhog fallback in `notification-config.yaml`:

```yaml
providers:
  email:
    default: gmail
    fallback: null   # mailhog won't be running locally unless you start it
```

```bash
# 4. Load env and start the server
set -a && source .env && set +a
uvicorn http_server:app --reload
```

```bash
# 5. Send a test notification
curl -X POST http://localhost:8000/notify \
  -H "Content-Type: application/json" \
  -d '{
    "profile": "maintenance_report",
    "to": {"email": "recipient@example.com", "name": "Your Name"},
    "subject": "Hello from Notification Service",
    "body": "It works!",
    "payload": {"report_id": "TEST-1", "system": "Test", "status": "ok"}
  }'
```

If you see `"ok": true` in the response, the email has been sent.

---

## Architecture Overview

```
                 ┌─────────────────────────────────────────────┐
                 │            NotificationService               │
                 │                  (facade)                    │
                 │                                              │
  HTTP POST ───► │  1. Resolve profile from config              │
  MCP tool  ───► │  2. Evaluate rules against payload           │
  import    ───► │  3. Fan-out to channels (asyncio.gather)     │
                 │  4. Retry with backoff + fallback            │
                 │  5. Dead-letter on exhaustion                │
                 └───────────┬──────────┬──────────┬───────────┘
                             │          │          │
                       EmailChannel  SMSChannel  WebhookChannel
                             │          │          │
                      GmailProvider  Twilio    Slack / Generic
                      Outlook        AWS SNS
                      Mailhog
```

**Key design decisions:**

| Decision | Choice |
|---|---|
| Entry points | Three thin wrappers (HTTP, MCP, import) over the same core class |
| Routing | YAML config — no routing logic in code |
| Fan-out | `asyncio.gather` — all channels attempt regardless of others |
| Retry | Exponential backoff + jitter, max 3 attempts, then fallback provider |
| Error handling | Typed result objects — no exceptions for expected failures |
| Templates | Jinja2 — HTML auto-escaped, plain text/JSON not |
| Secrets | Environment variables only — never in YAML or code |

---

## Supported Channels & Providers

| Channel | Providers | Skip condition |
|---|---|---|
| **email** | Gmail, Outlook, Mailhog (local dev) | `recipient.email` is absent |
| **sms** | Twilio, AWS SNS | `recipient.phone` is absent |
| **webhook** | Slack, Generic (any URL) | — |

Each provider knows how to classify its own errors as `transient` (retry) or
`permanent` (do not retry).

---

## Running the Service

### Prerequisites

- **Python 3.11 or higher** — the codebase uses `X | Y` union syntax and other 3.10+ features
- Docker + Docker Compose (only needed for Mode 1)

```bash
python3 --version   # must be 3.11+

python3 -m venv .venv
source .venv/bin/activate   # macOS/Linux
# .venv\Scripts\activate    # Windows CMD
# .venv/Scripts/Activate.ps1  # Windows PowerShell

pip install -r requirements.txt
```

---

### Mode 1 — Docker Compose (recommended)

Runs the service alongside Mailhog — a local SMTP catcher that captures all outgoing
email and shows it in a web UI. No real email credentials needed for this mode.

> **Note:** Docker Compose reads from `.env` via `env_file: .env`. The file must
> exist before running `docker compose up`, even if it is mostly empty.

```bash
# Create .env if you haven't already (can be empty for Mailhog-only testing)
cp .env.example .env

docker compose up --build
```

| Service | URL |
|---|---|
| Notification API | http://localhost:8000 |
| Mailhog web UI (view captured emails) | http://localhost:8025 |

Templates and config are mounted as read-only volumes — edit them without rebuilding
the image. Logs persist in `./logs/` on the host.

```bash
docker compose down   # stop everything
```

---

### Mode 2 — Direct Python (HTTP server)

**macOS / Linux:**

```bash
set -a && source .env && set +a
uvicorn http_server:app --reload --host 0.0.0.0 --port 8000
```

**Windows CMD:**

```cmd
for /f "tokens=*" %i in (.env) do set %i
uvicorn http_server:app --reload --host 0.0.0.0 --port 8000
```

**Windows PowerShell:**

```powershell
Get-Content .env | ForEach-Object { $name, $value = $_ -split '=', 2; [System.Environment]::SetEnvironmentVariable($name, $value) }
uvicorn http_server:app --reload --host 0.0.0.0 --port 8000
```

`--reload` enables hot-reload on code changes. Remove it in production.

Health check:

```bash
curl http://localhost:8000/health
# {"status":"ok","version":"0.1.0"}
```

---

### Mode 3 — MCP Server

The MCP entry point exposes a single `send_notification` tool over stdio. Claude
Desktop or any MCP-compatible agent can spawn it as a subprocess.

```bash
set -a && source .env && set +a
python mcp_server.py
```

Add to your Claude Desktop `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "notification-service": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["/absolute/path/to/mcp_server.py"],
      "env": {
        "NOTIFICATION_CONFIG_PATH": "/absolute/path/to/notification-config.yaml",
        "TEMPLATE_DIR": "/absolute/path/to/templates",
        "DEAD_LETTER_PATH": "/absolute/path/to/logs/dead_letter.jsonl",
        "GMAIL_ADDRESS": "yourname@gmail.com",
        "GMAIL_APP_PASSWORD": "xxxx xxxx xxxx xxxx"
      }
    }
  }
}
```

> Add any other provider env vars here that your config references.

The tool accepts the same fields as the HTTP `/notify` endpoint and returns the
`NotificationResult` as a JSON string.

---

### Mode 4 — In-process Python import

Use this in scripts, notebooks, or other services that share the same Python
environment — no HTTP, no network.

```python
import asyncio
from notification_service import NotificationService
from notification_service.factory import create_service
from notification_service.models import Message, Recipient

service: NotificationService = create_service()

result = asyncio.run(service.notify(
    "sensor_alert",
    Message(
        to=Recipient(email="ops@example.com", phone="+910000000000"),
        subject="Sensor threshold exceeded",
        body="Sensor S1 exceeded threshold",
        payload={"sensor_id": "S1", "value": 95, "unit": "°C", "threshold": 90},
    ),
))

print(result.model_dump_json(indent=2))
```

---

## Configuration

### Important: You Only Need Credentials for Providers You Use

> **This is the most common setup mistake.** The service registers all providers at
> startup and each provider reads its env vars in `__init__`. If a required env var
> is missing, the service will fail to start with a `KeyError` — even for providers
> you are not using.
>
> **Solution:** Set dummy placeholder values for providers you are not using.

Example `.env` for a project that only uses Gmail:

```
# Active — fill in real values
GMAIL_ADDRESS=yourname@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx

# Not used — set dummies to prevent startup errors
OUTLOOK_CLIENT_ID=unused
OUTLOOK_CLIENT_SECRET=unused
OUTLOOK_TENANT_ID=unused
OUTLOOK_SENDER=unused
TWILIO_ACCOUNT_SID=unused
TWILIO_AUTH_TOKEN=unused
TWILIO_FROM_NUMBER=+10000000000
AWS_ACCESS_KEY_ID=unused
AWS_SECRET_ACCESS_KEY=unused
SLACK_WEBHOOK_URL=https://hooks.slack.com/unused

# Service defaults — leave as-is
NOTIFICATION_CONFIG_PATH=./notification-config.yaml
TEMPLATE_DIR=./templates
LOG_LEVEL=INFO
DEAD_LETTER_PATH=./logs/dead_letter.jsonl
MAILHOG_HOST=mailhog
MAILHOG_PORT=1025
```

Also make sure `notification-config.yaml` only references providers you have real
credentials for in the `default` and `fallback` fields.

---

### Environment Variables

Full reference for all supported variables:

```
# Email — Gmail (App Password, active implementation)
GMAIL_ADDRESS            Your full Gmail address e.g. yourname@gmail.com
GMAIL_APP_PASSWORD       16-character app password — Google Account → Security →
                         2-Step Verification → App passwords
                         Note: 2-Step Verification must be enabled first.
                         The OAuth2 REST API implementation is preserved (commented
                         out) in providers/email/gmail.py if you need to switch.

# Email — Outlook / Microsoft 365
OUTLOOK_CLIENT_ID        Azure AD app registration client ID
OUTLOOK_CLIENT_SECRET    Client secret
OUTLOOK_TENANT_ID        Azure AD tenant ID (Azure Portal → Azure Active Directory)
OUTLOOK_SENDER           Sending mailbox address

# Email — Mailhog (local dev SMTP catcher, no credentials needed)
MAILHOG_HOST             Hostname of Mailhog container (default: mailhog)
MAILHOG_PORT             SMTP port (default: 1025)

# SMS — Twilio
TWILIO_ACCOUNT_SID       From Twilio Console → Dashboard
TWILIO_AUTH_TOKEN        From Twilio Console → Dashboard
TWILIO_FROM_NUMBER       Sending number in E.164 format e.g. +15005550006

# SMS — AWS SNS
AWS_ACCESS_KEY_ID        IAM credentials with sns:Publish permission
AWS_SECRET_ACCESS_KEY
AWS_REGION               e.g. us-east-1

# Webhook — Slack
SLACK_WEBHOOK_URL        Incoming Webhook URL from your Slack App configuration

# Service
NOTIFICATION_CONFIG_PATH Path to notification-config.yaml (default: ./notification-config.yaml)
TEMPLATE_DIR             Path to templates directory (default: ./templates)
LOG_LEVEL                DEBUG | INFO | WARNING | ERROR (default: INFO)
DEAD_LETTER_PATH         Path to dead-letter JSONL file (default: ./logs/dead_letter.jsonl)
```

---

### notification-config.yaml

This file controls everything about routing. **No code changes are needed to change
routing behaviour** — only edit this file.

```yaml
providers:
  email:
    default: gmail        # provider key used for all email sends
    fallback: mailhog     # tried if 'gmail' exhausts retries with transient errors
  sms:
    default: twilio
    fallback: null
  webhook:
    default: generic
    fallback: null

profiles:
  sensor_alert:
    rules:
      - condition: "payload.value > 90"
        channels: [email, sms, webhook]
        priority: critical
      - condition: "payload.value > 70"
        channels: [email, sms]
        priority: high
      - default: true       # catch-all — every profile must have exactly one
        channels: [email]
        priority: medium
```

---

### Notification Profiles & Rules

A **profile** is a named routing configuration. When you call `notify("sensor_alert", ...)`,
the service finds the `sensor_alert` profile and evaluates its rules.

**Rule evaluation:**

- Rules are checked top-down; the **first matching rule wins**
- `condition` supports: `==`, `!=`, `>`, `<`, `>=`, `<=`, `and`, `or`
- All conditions reference `payload.*` fields (e.g. `payload.value > 90`)
- Every profile **must** end with a `default: true` catch-all rule
- When a rule matches, all listed channels are fired **concurrently**

**Priority** is passed to channels and templates as context; it does not affect
retry behaviour.

---

## Sending a Notification

### HTTP API

**`POST /notify`**

```bash
curl -X POST http://localhost:8000/notify \
  -H "Content-Type: application/json" \
  -d '{
    "profile": "sensor_alert",
    "to": {"email": "ops@example.com", "phone": "+910000000000"},
    "subject": "Sensor threshold exceeded",
    "body": "Fallback body if no template matches",
    "payload": {
      "sensor_id": "S1",
      "value": 95,
      "unit": "°C",
      "threshold": 90
    }
  }'
```

Multiple recipients:

```bash
curl -X POST http://localhost:8000/notify \
  -H "Content-Type: application/json" \
  -d '{
    "profile": "maintenance_report",
    "to": [
      {"email": "alice@example.com", "name": "Alice"},
      {"email": "bob@example.com",   "name": "Bob"}
    ],
    "subject": "Weekly maintenance report",
    "body": "See attached report.",
    "payload": {"report_id": "RPT-42", "system": "HVAC", "status": "completed"}
  }'
```

Using a generic webhook (supply the destination URL in payload):

```bash
curl -X POST http://localhost:8000/notify \
  -H "Content-Type: application/json" \
  -d '{
    "profile": "system_health",
    "to": {},
    "body": "",
    "payload": {
      "status": "down",
      "service": "api-gateway",
      "region": "ap-south-1",
      "webhook_url": "https://your-system.example.com/hooks/alerts"
    }
  }'
```

**Response format:**

```json
{
  "ok": true,
  "recipients": [
    {
      "recipient": {"email": "ops@example.com", "phone": "+910000000000"},
      "channels": {
        "email":   {"ok": true,  "provider": "gmail",  "message_id": null,  "attempts": 1},
        "sms":     {"ok": true,  "provider": "twilio", "message_id": "SM123", "attempts": 1},
        "webhook": {"ok": false, "provider": "generic", "attempts": 3,
                    "error": {"kind": "transient_exhausted", "code": "...", "message": "..."}}
      }
    }
  ]
}
```

`ok` at the top level is `true` only when every recipient had at least one
successful channel delivery.

**`GET /health`**

```bash
curl http://localhost:8000/health
# {"status":"ok","version":"0.1.0"}
```

---

### Python SDK

```python
from notification_service.factory import create_service
from notification_service.models import Message, Recipient

service = create_service()

result = await service.notify(
    "sensor_alert",
    Message(
        to=Recipient(email="ops@example.com", phone="+910000000000"),
        subject="High temperature",
        body="Sensor exceeded threshold",
        payload={"sensor_id": "S1", "value": 95, "unit": "°C", "threshold": 90},
    ),
)

# The result is always a typed object — never raises for expected failures
if not result.ok:
    for rr in result.recipients:
        for channel, cr in rr.channels.items():
            if not cr.ok:
                print(f"{channel}: {cr.error.kind} — {cr.error.message}")
```

---

## Templates

Templates are Jinja2 files organised by profile and channel:

```
templates/
  {profile_name}/
    email.html        HTML email body (auto-escaped)
    email.txt         Plain text email body
    sms.txt           SMS body (keep under 160 chars)
    webhook.json      JSON webhook body (not auto-escaped)
    default.txt       Fallback for any channel with no specific template
```

**Resolution order** (first found wins):

1. `templates/{profile}/{channel}.{ext}`
2. `templates/{profile}/default.txt`
3. `message.body` raw string

**Template context variables:**

```
{{ recipient.name }}       Recipient's name
{{ recipient.email }}      Recipient's email
{{ recipient.phone }}      Recipient's phone
{{ payload.* }}            Any field from message.payload
{{ subject }}              message.subject
{{ body }}                 message.body (plain text fallback)
```

**Adding templates never requires a code change.** Create or edit files in
`templates/` and the service picks them up on the next request.

---

## Extending the Service

### Add a New Provider

Example: adding SendGrid for email.

**1.** Create `notification_service/providers/email/sendgrid.py`:

```python
import os, httpx
from notification_service.providers.base import Provider, ProviderPayload, ProviderResult, classify_httpx_error

class SendGridProvider(Provider):
    name = "sendgrid"

    def __init__(self) -> None:
        self._api_key = os.environ["SENDGRID_API_KEY"]
        self._sender  = os.environ["SENDGRID_SENDER"]

    async def send(self, payload: ProviderPayload) -> ProviderResult:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "personalizations": [{"to": [{"email": payload.recipient.email}]}],
                    "from": {"email": self._sender},
                    "subject": payload.subject,
                    "content": [{"type": "text/html", "value": payload.body}],
                },
            )
            resp.raise_for_status()
            return ProviderResult(ok=True, message_id=resp.headers.get("X-Message-Id"))

    def classify_error(self, error):
        return classify_httpx_error(error)
```

**2.** Register it in `notification_service/factory.py`:

```python
from notification_service.providers.email.sendgrid import SendGridProvider
# Inside build_registries():
pr.register(SendGridProvider())
```

**3.** Add credentials to `.env.example` and `.env`.

**4.** Switch the default in `notification-config.yaml`:

```yaml
providers:
  email:
    default: sendgrid
    fallback: mailhog
```

**No other files change.**

---

### Add a New Channel

Example: push notifications.

**1.** Create `notification_service/channels/push.py`:

```python
from notification_service.channels.base import Channel, SendOptions
from notification_service.models import Recipient
from notification_service.providers.base import ProviderPayload
from notification_service.registry import ProviderRegistry
from notification_service.result import ChannelResult, NotificationError
from notification_service.templates.registry import RenderedContent

class PushChannel(Channel):
    def __init__(self, registry: ProviderRegistry, default_provider: str) -> None:
        self._registry = registry
        self._default_provider = default_provider

    async def send(self, recipient, rendered, options, provider_name=None):
        if not recipient.device_token:
            return ChannelResult(
                ok=False, skipped=True,
                error=NotificationError(kind="skipped", code="no_device_token",
                                        message="Recipient has no device token"),
            )
        provider = self._registry.get(provider_name or self._default_provider)
        payload = ProviderPayload(recipient=recipient, body=rendered.body)
        try:
            result = await provider.send(payload)
            if result.ok:
                return ChannelResult(ok=True, provider=provider.name, message_id=result.message_id)
            return ChannelResult(ok=False, provider=provider.name,
                                 error=NotificationError(kind="permanent", code="send_failed",
                                                         message=result.error_message or ""))
        except Exception as e:
            return ChannelResult(ok=False, provider=provider.name,
                                 error=NotificationError(kind=provider.classify_error(e),
                                                         code=type(e).__name__, message=str(e)))
```

**2.** Create at least one push provider under `notification_service/providers/push/`.

**3.** Register in `factory.py`:

```python
from notification_service.channels.push import PushChannel
from notification_service.providers.push.fcm import FCMProvider

# Inside build_registries():
pr.register(FCMProvider())
cr.register("push", PushChannel(pr, config.providers.push.default))
```

**4.** Add a `push:` section to `notification-config.yaml`:

```yaml
providers:
  push:
    default: fcm
    fallback: null
```

**5.** Add templates: `templates/{profile}/push.txt`

**No existing channel, provider, or service files change.**

---

### Add a New Notification Profile

No code changes at all — only config and templates.

**1.** Add the profile to `notification-config.yaml`:

```yaml
profiles:
  payment_failed:
    rules:
      - condition: "payload.amount > 10000"
        channels: [email, sms]
        priority: critical
      - default: true
        channels: [email]
        priority: high
```

**2.** Create template files:

```
templates/
  payment_failed/
    email.html
    email.txt
    sms.txt
```

Done. The profile is immediately available to all three entry points.

---

## Project Structure

```
Notification_Service/
│
├── notification_service/          Core package — no HTTP or MCP here
│   ├── __init__.py                Exports NotificationService
│   ├── models.py                  Recipient, Message domain models
│   ├── result.py                  Typed result chain + NotificationError
│   ├── config.py                  AppConfig Pydantic models + YAML loader
│   ├── service.py                 NotificationService facade (routing, retry, fan-out)
│   ├── factory.py                 Wires providers, channels, registries together
│   ├── registry.py                ProviderRegistry + ChannelRegistry
│   ├── rule_evaluator.py          Safe condition evaluator (no eval())
│   ├── dead_letter.py             Async append-only JSONL writer
│   │
│   ├── channels/
│   │   ├── base.py                Channel ABC + SendOptions
│   │   ├── email.py               EmailChannel — skips if no email address
│   │   ├── sms.py                 SMSChannel — skips if no phone number
│   │   └── webhook.py             WebhookChannel — needs webhook_url for generic provider
│   │
│   ├── providers/
│   │   ├── base.py                Provider ABC + ProviderPayload/Result + httpx helper
│   │   ├── email/
│   │   │   ├── gmail.py           App Password + SMTP (OAuth2 REST API preserved, commented out)
│   │   │   ├── outlook.py         Client-credentials + Microsoft Graph API
│   │   │   └── mailhog.py         SMTP via smtplib (local dev only)
│   │   ├── sms/
│   │   │   ├── twilio.py          Twilio Messages REST API
│   │   │   └── aws_sns.py         boto3 SNS publish (run_in_executor)
│   │   └── webhook/
│   │       ├── slack.py           Slack Incoming Webhooks
│   │       └── generic.py         POST JSON to any URL (from payload.webhook_url)
│   │
│   └── templates/
│       └── registry.py            TemplateRegistry — two Jinja2 envs, 3-level fallback
│
├── templates/                     Jinja2 template files — no Python here
│   ├── sensor_alert/              email.html, email.txt, sms.txt, webhook.json
│   ├── maintenance_report/        email.html, email.txt
│   └── system_health/             email.html, email.txt, sms.txt, webhook.json
│
├── tests/
│   ├── conftest.py                Shared fixtures and test config
│   ├── test_service.py            Routing, fan-out, multi-recipient, ok aggregation
│   ├── test_channels.py           Skip logic, provider override, error classification
│   ├── test_providers.py          Per-provider HTTP mock tests (respx)
│   ├── test_templates.py          Rendering, fallback, XSS escape, JSON no-escape
│   └── test_retry.py              Retry, backoff, fallback, dead-letter, rule eval
│
├── http_server.py                 FastAPI app — thin wrapper, no business logic
├── mcp_server.py                  FastMCP stdio entry point — thin wrapper
├── notification-config.yaml       Runtime routing config
├── .env.example                   All environment variables documented
├── requirements.txt               Python dependencies
├── pytest.ini                     asyncio_mode = auto
├── Dockerfile                     python:3.11-slim, non-root user
└── docker-compose.yml             notification-service + mailhog
```

---

## Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run a specific test file
pytest tests/test_retry.py -v

# Run a specific test
pytest tests/test_providers.py::test_gmail_success -v

# Run with coverage
pip install pytest-cov
pytest tests/ --cov=notification_service --cov-report=term-missing
```

All tests run with no real external API calls. HTTP providers are mocked with
`respx`; `smtplib` and `boto3` calls are patched with `unittest.mock`.

---

## Dead-Letter Log

When all retry attempts for a channel are exhausted (including the fallback provider),
the notification is written to a structured JSONL file at `DEAD_LETTER_PATH`
(default: `./logs/dead_letter.jsonl`).

Each line is a self-contained JSON record:

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "timestamp": "2026-05-18T10:23:45.123456+00:00",
  "profile": "sensor_alert",
  "channel": "sms",
  "recipient": {"phone": "+910000000000"},
  "attempts": 3,
  "last_error": "503 Service Unavailable",
  "payload": {"sensor_id": "S1", "value": 95, "unit": "°C", "threshold": 90}
}
```

The full original payload is preserved so any entry can be replayed by posting it
back to `/notify`. The file is append-only and safe to tail in real time:

```bash
tail -f logs/dead_letter.jsonl | python3 -m json.tool
```

---

## Troubleshooting

**`KeyError: 'TWILIO_ACCOUNT_SID'` (or any other provider var) on startup**

The service registers all providers at startup. You must set env vars for every
provider — even ones you are not using. Set dummy values for unused providers.
See [Important: You Only Need Credentials for Providers You Use](#important-you-only-need-credentials-for-providers-you-use).

---

**Gmail: "App passwords" option not visible in Google Account**

2-Step Verification must be enabled on your Google Account before the App passwords
option appears. Go to Google Account → Security → 2-Step Verification and enable it
first, then return to Security → App passwords.

---

**Gmail: `SMTPAuthenticationError` on send**

- Double-check the app password — copy it directly from Google, spaces included
- Make sure `GMAIL_ADDRESS` is the full Gmail address (`yourname@gmail.com`), not
  just the username
- Confirm the app password was generated for "Mail", not another app type
- If it worked before and stopped, the app password may have been revoked — generate
  a new one

---

**`docker compose up` fails with `env_file .env not found`**

The `docker-compose.yml` expects a `.env` file. Run `cp .env.example .env` and fill
in at minimum the provider vars you plan to use (set dummies for the rest).

---

**`TemplateNotFound` in logs but notifications still send**

This is expected behaviour. The template registry falls back to `message.body` when
no matching template file exists. Create the template file under
`templates/{profile}/{channel}.{ext}` to use a rendered template instead.

---

**Service starts but `"ok": false` with `kind: "no_provider"`**

The provider key in `notification-config.yaml` does not match any registered
provider name. Valid keys are: `gmail`, `outlook`, `mailhog`, `twilio`, `aws_sns`,
`slack`, `generic`. Check spelling in the `providers:` section of your config.

---

**Windows: env vars not loading correctly**

The `set -a && source .env && set +a` command is bash-only. Use the Windows CMD or
PowerShell alternatives shown in [Mode 2](#mode-2--direct-python-http-server), or
set env vars manually in System Properties → Environment Variables before running
the server.
