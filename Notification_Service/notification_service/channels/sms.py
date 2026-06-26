from __future__ import annotations

from notification_service.channels.base import Channel, SendOptions
from notification_service.models import Recipient
from notification_service.providers.base import Provider, ProviderPayload
from notification_service.registry import ProviderRegistry
from notification_service.result import ChannelResult, NotificationError
from notification_service.templates.registry import RenderedContent


class SMSChannel(Channel):
    def __init__(self, registry: ProviderRegistry, default_provider: str) -> None:
        self._registry = registry
        self._default_provider = default_provider

    async def send(
        self,
        recipient: Recipient,
        rendered: RenderedContent,
        options: SendOptions,
        provider_name: str | None = None,
    ) -> ChannelResult:
        if not recipient.phone:
            return ChannelResult(
                ok=False,
                skipped=True,
                error=NotificationError(
                    kind="skipped",
                    code="no_phone",
                    message="Recipient has no phone number",
                ),
            )

        provider: Provider = self._registry.get(provider_name or self._default_provider)
        payload = ProviderPayload(
            recipient=recipient,
            body=rendered.body,
        )
        try:
            result = await provider.send(payload)
            if result.ok:
                return ChannelResult(
                    ok=True,
                    provider=provider.name,
                    message_id=result.message_id,
                )
            return ChannelResult(
                ok=False,
                provider=provider.name,
                error=NotificationError(
                    kind="permanent",
                    code=result.error_code or "send_failed",
                    message=result.error_message or "provider returned ok=False",
                ),
            )
        except Exception as e:
            kind = provider.classify_error(e)
            return ChannelResult(
                ok=False,
                provider=provider.name,
                error=NotificationError(
                    kind=kind,
                    code=type(e).__name__,
                    message=str(e),
                ),
            )
