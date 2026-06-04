from __future__ import annotations

import os
from typing import Literal

import httpx

from notification_service.providers.base import (
    Provider,
    ProviderPayload,
    ProviderResult,
    classify_httpx_error,
)


class SlackProvider(Provider):
    name = "slack"

    def __init__(self) -> None:
        self._webhook_url = os.environ.get("SLACK_WEBHOOK_URL", "")

    async def send(self, payload: ProviderPayload) -> ProviderResult:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                self._webhook_url,
                json={"text": payload.body},
            )
            resp.raise_for_status()
            return ProviderResult(ok=True)

    def classify_error(self, error: Exception) -> Literal["transient", "permanent"]:
        if isinstance(error, httpx.HTTPStatusError):
            status = error.response.status_code
            if status == 429:
                return "transient"
            if status in (404, 400, 403):
                return "permanent"
            if status in (500, 502, 503, 504):
                return "transient"
            return "permanent"
        return classify_httpx_error(error)
