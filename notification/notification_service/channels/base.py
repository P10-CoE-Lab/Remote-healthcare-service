from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel

from notification_service.models import Recipient
from notification_service.result import ChannelResult
from notification_service.templates.registry import RenderedContent


class SendOptions(BaseModel):
    priority: str = "medium"
    extra: dict = {}


class Channel(ABC):
    @abstractmethod
    async def send(
        self,
        recipient: Recipient,
        rendered: RenderedContent,
        options: SendOptions,
        provider_name: str | None = None,
    ) -> ChannelResult:
        """
        provider_name: when set, overrides the default provider with this key.
        Used by the facade to invoke the fallback provider without changing
        channel wiring.
        """
        ...
