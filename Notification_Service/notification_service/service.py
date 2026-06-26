from __future__ import annotations

import asyncio
import os
import random

from notification_service.channels.base import SendOptions
from notification_service.config import AppConfig
from notification_service.dead_letter import write_dead_letter
from notification_service.models import Message, Recipient
from notification_service.registry import ChannelRegistry, ProviderRegistry
from notification_service.result import (
    ChannelResult,
    NotificationError,
    NotificationResult,
    RecipientResult,
)
from notification_service.rule_evaluator import evaluate_condition
from notification_service.templates.registry import RenderedContent, TemplateRegistry

_CHANNEL_EXT = {"email": "html", "sms": "txt", "webhook": "json"}


class NotificationService:
    def __init__(
        self,
        config: AppConfig,
        provider_registry: ProviderRegistry,
        channel_registry: ChannelRegistry,
        template_registry: TemplateRegistry,
    ) -> None:
        self._config = config
        self._providers = provider_registry
        self._channels = channel_registry
        self._templates = template_registry
        self._dead_letter_path = os.environ.get(
            "DEAD_LETTER_PATH", "./logs/dead_letter.jsonl"
        )

    async def notify(self, profile_name: str, message: Message) -> NotificationResult:
        profile = self._config.profiles.get(profile_name)
        if profile is None:
            return NotificationResult(
                ok=False,
                recipients=[],
            )

        recipients: list[Recipient] = (
            message.to if isinstance(message.to, list) else [message.to]
        )

        rule = self._match_rule(profile_name, message.payload)

        options = SendOptions(
            priority=rule.priority,
            extra={"webhook_url": message.payload.get("webhook_url")},
        )

        recipient_results: list[RecipientResult] = list(
            await asyncio.gather(
                *[
                    self._send_to_recipient(profile_name, rule.channels, r, message, options)
                    for r in recipients
                ]
            )
        )

        ok = all(
            any(cr.ok for cr in rr.channels.values())
            for rr in recipient_results
        )
        return NotificationResult(ok=ok, recipients=recipient_results)

    def _match_rule(self, profile_name: str, payload: dict):
        profile = self._config.profiles[profile_name]
        for rule in profile.rules:
            if rule.default:
                return rule
            if rule.condition and evaluate_condition(rule.condition, payload):
                return rule
        return profile.rules[-1]

    async def _send_to_recipient(
        self,
        profile_name: str,
        channel_names: list[str],
        recipient: Recipient,
        message: Message,
        options: SendOptions,
    ) -> RecipientResult:
        results = await asyncio.gather(
            *[
                self._send_channel(profile_name, ch_name, recipient, message, options)
                for ch_name in channel_names
            ],
            return_exceptions=True,
        )

        channels: dict[str, ChannelResult] = {}
        for ch_name, result in zip(channel_names, results):
            if isinstance(result, BaseException):
                channels[ch_name] = ChannelResult(
                    ok=False,
                    error=NotificationError(
                        kind="permanent",
                        code="unexpected_error",
                        message=str(result),
                    ),
                )
            else:
                channels[ch_name] = result

        return RecipientResult(recipient=recipient, channels=channels)

    async def _send_channel(
        self,
        profile_name: str,
        channel_name: str,
        recipient: Recipient,
        message: Message,
        options: SendOptions,
    ) -> ChannelResult:
        channel = self._channels.get(channel_name)

        ext = _CHANNEL_EXT.get(channel_name, "txt")
        template_key = message.template or profile_name
        rendered_body = self._templates.render(
            profile=template_key,
            channel=channel_name,
            ext=ext,
            context={
                "recipient": recipient.model_dump(),
                "payload": message.payload,
                "subject": message.subject,
                "body": message.body,
            },
        )
        rendered = RenderedContent(subject=message.subject, body=rendered_body)

        max_attempts = self._config.retry.max_attempts
        last_result: ChannelResult | None = None

        for attempt in range(max_attempts):
            result = await channel.send(recipient, rendered, options)
            result.attempts = attempt + 1

            if result.ok or result.skipped:
                return result

            if result.error and result.error.kind == "permanent":
                return result

            if result.error and result.error.kind == "validation":
                return result

            # transient — backoff then retry
            if attempt < max_attempts - 1:
                delay = min(
                    1.0 * (2 ** attempt) + random.uniform(0, 0.5),
                    self._config.retry.max_delay,
                )
                await asyncio.sleep(delay)

            last_result = result

        # All primary attempts exhausted — try fallback provider
        provider_config = getattr(self._config.providers, channel_name, None)
        fallback_name = provider_config.fallback if provider_config else None

        if fallback_name:
            fallback_result = await channel.send(
                recipient, rendered, options, provider_name=fallback_name
            )
            fallback_result.attempts = (last_result.attempts if last_result else max_attempts) + 1
            if fallback_result.ok:
                return fallback_result

        # Write dead-letter and return transient_exhausted
        await write_dead_letter(
            {
                "profile": profile_name,
                "channel": channel_name,
                "recipient": recipient.model_dump(exclude_none=True),
                "attempts": last_result.attempts if last_result else max_attempts,
                "last_error": (
                    last_result.error.message
                    if last_result and last_result.error
                    else "unknown"
                ),
                "payload": message.payload,
            },
            self._dead_letter_path,
        )

        final_error = NotificationError(
            kind="transient_exhausted",
            code=(
                last_result.error.code
                if last_result and last_result.error
                else "exhausted"
            ),
            message=(
                last_result.error.message
                if last_result and last_result.error
                else "all retries failed"
            ),
        )
        return ChannelResult(
            ok=False,
            provider=last_result.provider if last_result else None,
            attempts=last_result.attempts if last_result else max_attempts,
            error=final_error,
        )
