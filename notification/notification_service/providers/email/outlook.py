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

_TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
_GRAPH_SEND_URL = "https://graph.microsoft.com/v1.0/users/{sender}/sendMail"


class OutlookProvider(Provider):
    name = "outlook"

    def __init__(self) -> None:
        self._client_id     = os.environ.get("OUTLOOK_CLIENT_ID",     "")
        self._client_secret = os.environ.get("OUTLOOK_CLIENT_SECRET", "")
        self._tenant_id     = os.environ.get("OUTLOOK_TENANT_ID",     "")
        self._sender        = os.environ.get("OUTLOOK_SENDER",        "")

    async def _get_access_token(self, client: httpx.AsyncClient) -> str:
        url = _TOKEN_URL.format(tenant=self._tenant_id)
        resp = await client.post(
            url,
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials",
            },
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    async def send(self, payload: ProviderPayload) -> ProviderResult:
        async with httpx.AsyncClient(timeout=15.0) as client:
            token = await self._get_access_token(client)
            url = _GRAPH_SEND_URL.format(sender=self._sender)
            body = {
                "message": {
                    "subject": payload.subject,
                    "body": {"contentType": "HTML", "content": payload.body},
                    "toRecipients": [
                        {"emailAddress": {"address": payload.recipient.email or ""}}
                    ],
                },
                "saveToSentItems": False,
            }
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json=body,
            )
            resp.raise_for_status()
            # Graph sendMail returns 202 with no body
            return ProviderResult(ok=True, message_id=None)

    def classify_error(self, error: Exception) -> Literal["transient", "permanent"]:
        if isinstance(error, httpx.HTTPStatusError):
            status = error.response.status_code
            if status == 401:
                return "permanent"
            if status in (429, 500, 502, 503, 504):
                return "transient"
            return "permanent"
        return classify_httpx_error(error)
