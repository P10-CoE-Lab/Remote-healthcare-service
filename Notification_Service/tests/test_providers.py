from __future__ import annotations

import httpx
import pytest
import respx

from notification_service.models import Recipient
from notification_service.providers.base import ProviderPayload
from notification_service.providers.email.gmail import GmailProvider
from notification_service.providers.email.mailhog import MailhogProvider
from notification_service.providers.email.outlook import OutlookProvider
from notification_service.providers.sms.twilio import TwilioProvider
from notification_service.providers.webhook.generic import GenericWebhookProvider
from notification_service.providers.webhook.slack import SlackProvider

_RECIPIENT_EMAIL = Recipient(email="test@example.com", name="Tester")
_RECIPIENT_PHONE = Recipient(phone="+10000000000", name="Tester")
_PAYLOAD_EMAIL = ProviderPayload(recipient=_RECIPIENT_EMAIL, subject="Test", body="Hello")
_PAYLOAD_SMS = ProviderPayload(recipient=_RECIPIENT_PHONE, body="Hello")
_PAYLOAD_WEBHOOK = ProviderPayload(
    recipient=_RECIPIENT_EMAIL, body='{"test": true}', extra={"url": "https://hook.example.com/notify"}
)


# ── Gmail ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def gmail_provider(monkeypatch):
    monkeypatch.setenv("GMAIL_ADDRESS", "sender@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "abcd efgh ijkl mnop")
    return GmailProvider()


async def test_gmail_success(gmail_provider, monkeypatch):
    import smtplib
    mock_smtp = type(
        "MockSMTP",
        (),
        {
            "__init__": lambda self, *a, **kw: None,
            "__enter__": lambda self: self,
            "__exit__": lambda self, *a: None,
            "ehlo": lambda self: None,
            "starttls": lambda self: None,
            "login": lambda self, *a: None,
            "sendmail": lambda self, *a: {},
        },
    )
    monkeypatch.setattr(smtplib, "SMTP", mock_smtp)
    result = await gmail_provider.send(_PAYLOAD_EMAIL)
    assert result.ok is True


async def test_gmail_permanent_auth_error(gmail_provider, monkeypatch):
    import smtplib
    def raise_auth(*a, **kw):
        raise smtplib.SMTPAuthenticationError(535, b"Bad credentials")
    monkeypatch.setattr(smtplib, "SMTP", raise_auth)
    with pytest.raises(smtplib.SMTPAuthenticationError) as exc_info:
        await gmail_provider.send(_PAYLOAD_EMAIL)
    assert gmail_provider.classify_error(exc_info.value) == "permanent"


async def test_gmail_transient_connection_error(gmail_provider, monkeypatch):
    import smtplib
    def raise_conn(*a, **kw):
        raise ConnectionRefusedError("connection refused")
    monkeypatch.setattr(smtplib, "SMTP", raise_conn)
    with pytest.raises(ConnectionRefusedError) as exc_info:
        await gmail_provider.send(_PAYLOAD_EMAIL)
    assert gmail_provider.classify_error(exc_info.value) == "transient"


def test_gmail_classify_timeout(gmail_provider):
    assert gmail_provider.classify_error(TimeoutError("t")) == "transient"


# ── Outlook ────────────────────────────────────────────────────────────────────

@pytest.fixture
def outlook_provider(monkeypatch):
    monkeypatch.setenv("OUTLOOK_CLIENT_ID", "cid")
    monkeypatch.setenv("OUTLOOK_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("OUTLOOK_TENANT_ID", "tenant")
    monkeypatch.setenv("OUTLOOK_SENDER", "sender@company.com")
    return OutlookProvider()


@respx.mock
async def test_outlook_success(outlook_provider):
    respx.post(
        "https://login.microsoftonline.com/tenant/oauth2/v2.0/token"
    ).mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    respx.post(
        "https://graph.microsoft.com/v1.0/users/sender@company.com/sendMail"
    ).mock(return_value=httpx.Response(202))
    result = await outlook_provider.send(_PAYLOAD_EMAIL)
    assert result.ok is True


@respx.mock
async def test_outlook_transient_429(outlook_provider):
    respx.post(
        "https://login.microsoftonline.com/tenant/oauth2/v2.0/token"
    ).mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    respx.post(
        "https://graph.microsoft.com/v1.0/users/sender@company.com/sendMail"
    ).mock(return_value=httpx.Response(429))
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await outlook_provider.send(_PAYLOAD_EMAIL)
    assert outlook_provider.classify_error(exc_info.value) == "transient"


# ── Mailhog ────────────────────────────────────────────────────────────────────

@pytest.fixture
def mailhog_provider(monkeypatch):
    monkeypatch.setenv("MAILHOG_HOST", "localhost")
    monkeypatch.setenv("MAILHOG_PORT", "1025")
    return MailhogProvider()


async def test_mailhog_success(mailhog_provider, monkeypatch):
    import smtplib
    mock_smtp = type(
        "MockSMTP",
        (),
        {
            "__init__": lambda self, *a, **kw: None,
            "__enter__": lambda self: self,
            "__exit__": lambda self, *a: None,
            "sendmail": lambda self, *a: {},
        },
    )
    monkeypatch.setattr(smtplib, "SMTP", mock_smtp)
    result = await mailhog_provider.send(_PAYLOAD_EMAIL)
    assert result.ok is True


def test_mailhog_classify_connection_refused(mailhog_provider):
    assert mailhog_provider.classify_error(ConnectionRefusedError()) == "transient"


def test_mailhog_classify_smtp_error(mailhog_provider):
    import smtplib
    assert mailhog_provider.classify_error(smtplib.SMTPException()) == "permanent"


# ── Twilio ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def twilio_provider(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "authtoken")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15005550006")
    return TwilioProvider()


@respx.mock
async def test_twilio_success(twilio_provider):
    respx.post(
        "https://api.twilio.com/2010-04-01/Accounts/ACtest/Messages.json"
    ).mock(return_value=httpx.Response(201, json={"sid": "SM123"}))
    result = await twilio_provider.send(_PAYLOAD_SMS)
    assert result.ok is True
    assert result.message_id == "SM123"


@respx.mock
async def test_twilio_transient_429(twilio_provider):
    respx.post(
        "https://api.twilio.com/2010-04-01/Accounts/ACtest/Messages.json"
    ).mock(return_value=httpx.Response(429))
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await twilio_provider.send(_PAYLOAD_SMS)
    assert twilio_provider.classify_error(exc_info.value) == "transient"


@respx.mock
async def test_twilio_permanent_invalid_number(twilio_provider):
    respx.post(
        "https://api.twilio.com/2010-04-01/Accounts/ACtest/Messages.json"
    ).mock(
        return_value=httpx.Response(
            400, json={"code": 21211, "message": "invalid number"}
        )
    )
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await twilio_provider.send(_PAYLOAD_SMS)
    assert twilio_provider.classify_error(exc_info.value) == "permanent"


def test_twilio_classify_timeout(twilio_provider):
    assert twilio_provider.classify_error(httpx.TimeoutException("t")) == "transient"


# ── Slack ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def slack_provider(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/secret")
    return SlackProvider()


@respx.mock
async def test_slack_success(slack_provider):
    respx.post("https://hooks.slack.com/services/T/B/secret").mock(
        return_value=httpx.Response(200, text="ok")
    )
    result = await slack_provider.send(
        ProviderPayload(recipient=_RECIPIENT_EMAIL, body="hello")
    )
    assert result.ok is True


@respx.mock
async def test_slack_transient_429(slack_provider):
    respx.post("https://hooks.slack.com/services/T/B/secret").mock(
        return_value=httpx.Response(429)
    )
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await slack_provider.send(ProviderPayload(recipient=_RECIPIENT_EMAIL, body="hello"))
    assert slack_provider.classify_error(exc_info.value) == "transient"


@respx.mock
async def test_slack_permanent_404(slack_provider):
    respx.post("https://hooks.slack.com/services/T/B/secret").mock(
        return_value=httpx.Response(404)
    )
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await slack_provider.send(ProviderPayload(recipient=_RECIPIENT_EMAIL, body="hello"))
    assert slack_provider.classify_error(exc_info.value) == "permanent"


# ── Generic Webhook ────────────────────────────────────────────────────────────

@pytest.fixture
def generic_provider():
    return GenericWebhookProvider()


@respx.mock
async def test_generic_success(generic_provider):
    respx.post("https://hook.example.com/notify").mock(
        return_value=httpx.Response(200)
    )
    result = await generic_provider.send(_PAYLOAD_WEBHOOK)
    assert result.ok is True


@respx.mock
async def test_generic_transient_500(generic_provider):
    respx.post("https://hook.example.com/notify").mock(
        return_value=httpx.Response(500)
    )
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await generic_provider.send(_PAYLOAD_WEBHOOK)
    assert generic_provider.classify_error(exc_info.value) == "transient"


@respx.mock
async def test_generic_permanent_400(generic_provider):
    respx.post("https://hook.example.com/notify").mock(
        return_value=httpx.Response(400)
    )
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await generic_provider.send(_PAYLOAD_WEBHOOK)
    assert generic_provider.classify_error(exc_info.value) == "permanent"


async def test_generic_missing_url_raises_value_error(generic_provider):
    payload = ProviderPayload(recipient=_RECIPIENT_EMAIL, body="test", extra={})
    with pytest.raises(ValueError, match="webhook url missing"):
        await generic_provider.send(payload)


def test_generic_classify_missing_url(generic_provider):
    assert generic_provider.classify_error(ValueError("webhook url missing")) == "permanent"
