from __future__ import annotations

from notification_service.channels.base import Channel
from notification_service.providers.base import Provider


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, Provider] = {}

    def register(self, provider: Provider) -> None:
        self._providers[provider.name] = provider

    def get(self, name: str) -> Provider:
        if name not in self._providers:
            raise KeyError(f"Provider '{name}' not registered")
        return self._providers[name]


class ChannelRegistry:
    def __init__(self) -> None:
        self._channels: dict[str, Channel] = {}

    def register(self, name: str, channel: Channel) -> None:
        self._channels[name] = channel

    def get(self, name: str) -> Channel:
        if name not in self._channels:
            raise KeyError(f"Channel '{name}' not registered")
        return self._channels[name]
