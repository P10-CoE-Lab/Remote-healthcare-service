from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from notification_service.channels.base import Channel, SendOptions
from notification_service.models import Message, Recipient
from notification_service.providers.base import Provider, ProviderPayload, ProviderResult
from notification_service.registry import ChannelRegistry, ProviderRegistry
from notification_service.result import ChannelResult, NotificationError
from notification_service.service import NotificationService
from notification_service.templates.registry import RenderedContent, TemplateRegistry
from tests.conftest import TEST_CONFIG_YAML, make_service


def _ok_channel(name: str = "mock_channel") -> Channel:
    ch = MagicMock(spec=Channel)
    ch.send = AsyncMock(
        return_value=ChannelResult(ok=True, provider="mock", message_id="msg-1")
    )
    return ch


def _fail_channel(kind: str = "permanent") -> Channel:
    ch = MagicMock(spec=Channel)
    ch.send = AsyncMock(
        return_value=ChannelResult(
            ok=False,
            provider="mock",
            error=NotificationError(kind=kind, code="ERR", message="failed"),
        )
    )
    return ch


def _make_registry_with_channels(config, **named_channels) -> tuple[ProviderRegistry, ChannelRegistry]:
    pr = ProviderRegistry()
    cr = ChannelRegistry()
    for name, ch in named_channels.items():
        cr.register(name, ch)
    return pr, cr


@pytest.fixture
def config(tmp_path):
    from notification_service.config import load_config
    f = tmp_path / "cfg.yaml"
    f.write_text(TEST_CONFIG_YAML)
    return load_config(str(f))


@pytest.fixture
def service(config, tmp_path):
    pr = ProviderRegistry()
    return make_service(config, pr, str(tmp_path))


def _build_service(config, channels: dict, tmp_path) -> NotificationService:
    pr = ProviderRegistry()
    cr = ChannelRegistry()
    for name, ch in channels.items():
        cr.register(name, ch)
    tr = TemplateRegistry(str(tmp_path))
    return NotificationService(config, pr, cr, tr)


async def test_unknown_profile_returns_ok_false(config, tmp_path):
    svc = _build_service(config, {"email": _ok_channel()}, tmp_path)
    result = await svc.notify("does_not_exist", Message(to=Recipient(email="a@b.com"), body="hi"))
    assert result.ok is False
    assert result.recipients == []


async def test_rule_matching_high_value(config, tmp_path):
    email_ch = _ok_channel()
    sms_ch = _ok_channel()
    svc = _build_service(config, {"email": email_ch, "sms": sms_ch}, tmp_path)
    result = await svc.notify(
        "test_profile",
        Message(
            to=Recipient(email="a@b.com", phone="+10000000000"),
            body="test",
            payload={"value": 95},
        ),
    )
    # value > 90 → channels: [email, sms]
    assert email_ch.send.called
    assert sms_ch.send.called


async def test_rule_matching_default(config, tmp_path):
    email_ch = _ok_channel()
    sms_ch = _ok_channel()
    svc = _build_service(config, {"email": email_ch, "sms": sms_ch}, tmp_path)
    await svc.notify(
        "test_profile",
        Message(
            to=Recipient(email="a@b.com"),
            body="test",
            payload={"value": 50},
        ),
    )
    # value <= 90 → default rule → channels: [email] only
    assert email_ch.send.called
    assert not sms_ch.send.called


async def test_fanout_one_fails_other_succeeds(config, tmp_path):
    email_ch = _ok_channel()
    sms_ch = _fail_channel("permanent")
    svc = _build_service(config, {"email": email_ch, "sms": sms_ch}, tmp_path)
    result = await svc.notify(
        "test_profile",
        Message(
            to=Recipient(email="a@b.com", phone="+10000000000"),
            body="test",
            payload={"value": 95},
        ),
    )
    rr = result.recipients[0]
    assert rr.channels["email"].ok is True
    assert rr.channels["sms"].ok is False
    assert "email" in rr.channels
    assert "sms" in rr.channels


async def test_multi_recipient_isolation(config, tmp_path):
    call_count = 0

    async def send_side_effect(recipient, rendered, options, provider_name=None):
        nonlocal call_count
        call_count += 1
        if recipient.email == "fail@b.com":
            return ChannelResult(
                ok=False,
                provider="mock",
                error=NotificationError(kind="permanent", code="ERR", message="fail"),
            )
        return ChannelResult(ok=True, provider="mock", message_id="ok")

    email_ch = MagicMock(spec=Channel)
    email_ch.send = AsyncMock(side_effect=send_side_effect)

    svc = _build_service(config, {"email": email_ch}, tmp_path)
    result = await svc.notify(
        "test_profile",
        Message(
            to=[
                Recipient(email="ok@b.com"),
                Recipient(email="fail@b.com"),
            ],
            body="test",
            payload={"value": 50},
        ),
    )
    results_by_email = {rr.recipient.email: rr for rr in result.recipients}
    assert results_by_email["ok@b.com"].channels["email"].ok is True
    assert results_by_email["fail@b.com"].channels["email"].ok is False


async def test_ok_aggregation_all_succeed(config, tmp_path):
    svc = _build_service(config, {"email": _ok_channel()}, tmp_path)
    result = await svc.notify(
        "test_profile",
        Message(to=[Recipient(email="a@b.com"), Recipient(email="c@d.com")], body="hi", payload={"value": 50}),
    )
    assert result.ok is True


async def test_ok_aggregation_all_fail(config, tmp_path):
    svc = _build_service(config, {"email": _fail_channel("permanent")}, tmp_path)
    result = await svc.notify(
        "test_profile",
        Message(to=Recipient(email="a@b.com"), body="hi", payload={"value": 50}),
    )
    assert result.ok is False
