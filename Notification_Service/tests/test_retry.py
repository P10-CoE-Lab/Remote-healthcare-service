from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from notification_service.channels.base import Channel, SendOptions
from notification_service.config import load_config
from notification_service.models import Message, Recipient
from notification_service.registry import ChannelRegistry, ProviderRegistry
from notification_service.result import ChannelResult, NotificationError
from notification_service.rule_evaluator import evaluate_condition
from notification_service.service import NotificationService
from notification_service.templates.registry import TemplateRegistry

TEST_CONFIG = """\
providers:
  email:
    default: mailhog
    fallback: gmail
  sms:
    default: twilio
    fallback: null
  webhook:
    default: generic
    fallback: null
profiles:
  test_profile:
    rules:
      - default: true
        channels: [email]
        priority: low
"""


@pytest.fixture
def config(tmp_path):
    f = tmp_path / "cfg.yaml"
    f.write_text(TEST_CONFIG)
    return load_config(str(f))


def _build_service(config, channels: dict, tmp_path) -> NotificationService:
    pr = ProviderRegistry()
    cr = ChannelRegistry()
    for name, ch in channels.items():
        cr.register(name, ch)
    tr = TemplateRegistry(str(tmp_path))
    return NotificationService(config, pr, cr, tr)


def _transient_result():
    return ChannelResult(
        ok=False,
        provider="mailhog",
        error=NotificationError(kind="transient", code="TimeoutException", message="timeout"),
    )


def _ok_result():
    return ChannelResult(ok=True, provider="mailhog", message_id="msg-ok")


# ── Rule Evaluator ─────────────────────────────────────────────────────────────

def test_evaluate_greater_than_true():
    assert evaluate_condition("payload.value > 90", {"value": 95}) is True


def test_evaluate_greater_than_false():
    assert evaluate_condition("payload.value > 90", {"value": 80}) is False


def test_evaluate_equals_string():
    assert evaluate_condition("payload.status == 'down'", {"status": "down"}) is True


def test_evaluate_not_equals():
    assert evaluate_condition("payload.status != 'up'", {"status": "down"}) is True


def test_evaluate_missing_key_returns_false():
    assert evaluate_condition("payload.missing_key > 10", {}) is False


def test_evaluate_and_combinator():
    assert evaluate_condition(
        "payload.value > 50 and payload.value < 100", {"value": 75}
    ) is True


def test_evaluate_or_combinator():
    assert evaluate_condition(
        "payload.value < 10 or payload.value > 90", {"value": 95}
    ) is True


# ── Retry behaviour ────────────────────────────────────────────────────────────

async def test_retry_on_transient(config, tmp_path):
    call_count = 0

    async def send_side_effect(recipient, rendered, options, provider_name=None):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return _transient_result()
        return _ok_result()

    channel = MagicMock(spec=Channel)
    channel.send = AsyncMock(side_effect=send_side_effect)

    with patch("notification_service.service.asyncio.sleep", new_callable=AsyncMock):
        svc = _build_service(config, {"email": channel}, tmp_path)
        result = await svc.notify(
            "test_profile",
            Message(to=Recipient(email="a@b.com"), body="test"),
        )

    rr = result.recipients[0]
    assert rr.channels["email"].ok is True
    assert rr.channels["email"].attempts == 3
    assert call_count == 3


async def test_no_retry_on_permanent(config, tmp_path):
    channel = MagicMock(spec=Channel)
    channel.send = AsyncMock(
        return_value=ChannelResult(
            ok=False,
            provider="mailhog",
            error=NotificationError(kind="permanent", code="ERR", message="bad"),
        )
    )

    with patch("notification_service.service.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        svc = _build_service(config, {"email": channel}, tmp_path)
        result = await svc.notify(
            "test_profile",
            Message(to=Recipient(email="a@b.com"), body="test"),
        )

    rr = result.recipients[0]
    assert rr.channels["email"].ok is False
    assert rr.channels["email"].attempts == 1
    channel.send.assert_called_once()
    mock_sleep.assert_not_called()


async def test_backoff_delay_bounded(config, tmp_path):
    delays = []

    async def fake_sleep(delay):
        delays.append(delay)

    channel = MagicMock(spec=Channel)
    channel.send = AsyncMock(return_value=_transient_result())

    with patch("notification_service.service.asyncio.sleep", side_effect=fake_sleep):
        svc = _build_service(config, {"email": channel}, tmp_path)
        await svc.notify(
            "test_profile",
            Message(to=Recipient(email="a@b.com"), body="test"),
        )

    assert all(d <= config.retry.max_delay for d in delays)


async def test_fallback_provider_called_after_exhaustion(config, tmp_path):
    primary_count = 0
    fallback_count = 0

    async def send_side_effect(recipient, rendered, options, provider_name=None):
        nonlocal primary_count, fallback_count
        if provider_name == "gmail":
            fallback_count += 1
            return _ok_result()
        primary_count += 1
        return _transient_result()

    channel = MagicMock(spec=Channel)
    channel.send = AsyncMock(side_effect=send_side_effect)

    with patch("notification_service.service.asyncio.sleep", new_callable=AsyncMock):
        svc = _build_service(config, {"email": channel}, tmp_path)
        result = await svc.notify(
            "test_profile",
            Message(to=Recipient(email="a@b.com"), body="test"),
        )

    assert primary_count == config.retry.max_attempts
    assert fallback_count == 1
    assert result.recipients[0].channels["email"].ok is True


async def test_dead_letter_written_after_exhaustion(config, tmp_path):
    channel = MagicMock(spec=Channel)
    channel.send = AsyncMock(return_value=_transient_result())

    dead_letter_path = str(tmp_path / "dead_letter.jsonl")
    os.environ["DEAD_LETTER_PATH"] = dead_letter_path

    try:
        with patch("notification_service.service.asyncio.sleep", new_callable=AsyncMock):
            svc = _build_service(config, {"email": channel}, tmp_path)
            # Override path directly
            svc._dead_letter_path = dead_letter_path
            await svc.notify(
                "test_profile",
                Message(to=Recipient(email="a@b.com"), body="test"),
            )
    finally:
        del os.environ["DEAD_LETTER_PATH"]

    assert os.path.exists(dead_letter_path)
    with open(dead_letter_path) as f:
        entry = json.loads(f.readline())

    assert "id" in entry
    assert "timestamp" in entry
    assert entry["profile"] == "test_profile"
    assert entry["channel"] == "email"
    assert "recipient" in entry
    assert "attempts" in entry
    assert "last_error" in entry
    assert "payload" in entry


async def test_final_result_kind_is_transient_exhausted(config, tmp_path):
    channel = MagicMock(spec=Channel)
    channel.send = AsyncMock(return_value=_transient_result())

    dead_letter_path = str(tmp_path / "dl.jsonl")
    with patch("notification_service.service.asyncio.sleep", new_callable=AsyncMock):
        svc = _build_service(config, {"email": channel}, tmp_path)
        svc._dead_letter_path = dead_letter_path
        result = await svc.notify(
            "test_profile",
            Message(to=Recipient(email="a@b.com"), body="test"),
        )

    rr = result.recipients[0]
    assert rr.channels["email"].ok is False
    assert rr.channels["email"].error.kind == "transient_exhausted"
