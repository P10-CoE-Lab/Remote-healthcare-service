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


class TwilioProvider(Provider):
    name = "twilio"

    def __init__(self) -> None:
        self._account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
        self._auth_token  = os.environ.get("TWILIO_AUTH_TOKEN",  "")
        self._from_number = os.environ.get("TWILIO_FROM_NUMBER", "")

    async def send(self, payload: ProviderPayload) -> ProviderResult:
        url = (
            f"https://api.twilio.com/2010-04-01/Accounts"
            f"/{self._account_sid}/Messages.json"
        )
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                url,
                auth=(self._account_sid, self._auth_token),
                data={
                    "From": self._from_number,
                    "To": payload.recipient.phone or "",
                    "Body": payload.body,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return ProviderResult(ok=True, message_id=data.get("sid"))

    def classify_error(self, error: Exception) -> Literal["transient", "permanent"]:
        if isinstance(error, httpx.HTTPStatusError):
            status = error.response.status_code
            if status == 429:
                return "transient"
            if status == 400:
                # Check Twilio-specific error codes
                try:
                    body = error.response.json()
                    code = str(body.get("code", ""))
                    # 21211 = invalid To number, 21606 = From not enabled for SMS
                    if code in ("21211", "21606", "21610"):
                        return "permanent"
                except Exception:
                    pass
                return "permanent"
            if status in (500, 502, 503, 504):
                return "transient"
            return "permanent"
        return classify_httpx_error(error)
