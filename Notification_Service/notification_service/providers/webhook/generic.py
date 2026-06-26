from __future__ import annotations

from typing import Literal

import httpx

from notification_service.providers.base import (
    Provider,
    ProviderPayload,
    ProviderResult,
    classify_httpx_error,
)


class GenericWebhookProvider(Provider):
    name = "generic"

    async def send(self, payload: ProviderPayload) -> ProviderResult:
        url = payload.extra.get("url")
        if not url:
            raise ValueError("webhook url missing from ProviderPayload.extra['url']")
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                content=payload.body,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            return ProviderResult(ok=True)

    def classify_error(self, error: Exception) -> Literal["transient", "permanent"]:
        if isinstance(error, ValueError):
            # Missing URL is a configuration error — permanent
            return "permanent"
        if isinstance(error, httpx.HTTPStatusError):
            if error.response.status_code >= 500:
                return "transient"
            return "permanent"
        return classify_httpx_error(error)
