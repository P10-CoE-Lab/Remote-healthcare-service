from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from notification_service.channels.base import SendOptions
from notification_service.channels.email import EmailChannel
from notification_service.channels.sms import SMSChannel
from notification_service.channels.webhook import WebhookChannel
from notification_service.models import Recipient
from notification_service.providers.base import Provider, ProviderPayload, ProviderResult
from notification_service.registry import ProviderRegistry
from notification_service.templates.registry import RenderedContent


def _mock_provider(name: str = "mock") -> Provider:
    p = MagicMock(spec=Provider)
    p.name = name
    p.send = AsyncMock(return_value=ProviderResult(ok=True, message_id="msg-1"))
    p.classify_error = MagicMock(return_value="permanent")
    return p


def _registry(*providers: Provider) -> ProviderRegistry:
    r = ProviderRegistry()
    for p in providers:
        r.register(p)
    return r


_rendered = RenderedContent(subject="Test subject", body="Test body")
_options = SendOptions()


async def test_email_skips_when_no_email():
    provider = _mock_provider("mailhog")
    registry = _registry(provider)
    channel = EmailChannel(registry, "mailhog")

    result = await channel.send(Recipient(phone="+10000000000"), _rendered, _options)

    assert result.ok is False
    assert result.skipped is True
    assert result.error.kind == "skipped"
    provider.send.assert_not_called()


async def test_email_sends_when_email_present():
    provider = _mock_provider("mailhog")
    registry = _registry(provider)
    channel = EmailChannel(registry, "mailhog")

    result = await channel.send(Recipient(email="a@b.com"), _rendered, _options)

    assert result.ok is True
    assert result.message_id == "msg-1"
    provider.send.assert_called_once()


async def test_sms_skips_when_no_phone():
    provider = _mock_provider("twilio")
    registry = _registry(provider)
    channel = SMSChannel(registry, "twilio")

    result = await channel.send(Recipient(email="a@b.com"), _rendered, _options)

    assert result.ok is False
    assert result.skipped is True
    assert result.error.kind == "skipped"
    provider.send.assert_not_called()


async def test_sms_sends_when_phone_present():
    provider = _mock_provider("twilio")
    registry = _registry(provider)
    channel = SMSChannel(registry, "twilio")

    result = await channel.send(Recipient(phone="+10000000000"), _rendered, _options)

    assert result.ok is True
    provider.send.assert_called_once()


async def test_webhook_calls_provider_regardless_of_recipient_fields():
    provider = _mock_provider("slack")
    registry = _registry(provider)
    channel = WebhookChannel(registry, "slack")

    # Recipient has neither email nor phone
    result = await channel.send(Recipient(), _rendered, _options)

    assert result.ok is True
    provider.send.assert_called_once()


async def test_webhook_generic_missing_url_returns_validation_error():
    provider = _mock_provider("generic")
    registry = _registry(provider)
    channel = WebhookChannel(registry, "generic")

    options = SendOptions(extra={})  # no webhook_url
    result = await channel.send(Recipient(), _rendered, options)

    assert result.ok is False
    assert result.error.kind == "validation"
    assert result.error.code == "missing_webhook_url"
    provider.send.assert_not_called()


async def test_webhook_generic_with_url_sends():
    provider = _mock_provider("generic")
    registry = _registry(provider)
    channel = WebhookChannel(registry, "generic")

    options = SendOptions(extra={"webhook_url": "https://example.com/hook"})
    result = await channel.send(Recipient(), _rendered, options)

    assert result.ok is True
    provider.send.assert_called_once()
    call_payload: ProviderPayload = provider.send.call_args[0][0]
    assert call_payload.extra["url"] == "https://example.com/hook"


async def test_provider_name_override_uses_fallback_provider():
    default_provider = _mock_provider("mailhog")
    fallback_provider = _mock_provider("gmail")
    registry = _registry(default_provider, fallback_provider)
    channel = EmailChannel(registry, "mailhog")

    result = await channel.send(
        Recipient(email="a@b.com"), _rendered, _options, provider_name="gmail"
    )

    assert result.ok is True
    fallback_provider.send.assert_called_once()
    default_provider.send.assert_not_called()


async def test_channel_returns_transient_kind_on_transient_error():
    provider = _mock_provider("mailhog")
    provider.send = AsyncMock(side_effect=Exception("timeout"))
    provider.classify_error = MagicMock(return_value="transient")

    registry = _registry(provider)
    channel = EmailChannel(registry, "mailhog")
    result = await channel.send(Recipient(email="a@b.com"), _rendered, _options)

    assert result.ok is False
    assert result.error.kind == "transient"


async def test_channel_returns_permanent_kind_on_permanent_error():
    provider = _mock_provider("mailhog")
    provider.send = AsyncMock(side_effect=Exception("bad request"))
    provider.classify_error = MagicMock(return_value="permanent")

    registry = _registry(provider)
    channel = EmailChannel(registry, "mailhog")
    result = await channel.send(Recipient(email="a@b.com"), _rendered, _options)

    assert result.ok is False
    assert result.error.kind == "permanent"
