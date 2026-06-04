from __future__ import annotations

import asyncio
import os
from typing import Literal

from notification_service.providers.base import Provider, ProviderPayload, ProviderResult


class AWSSNSProvider(Provider):
    name = "aws_sns"

    def __init__(self) -> None:
        self._access_key = os.environ.get("AWS_ACCESS_KEY_ID",     "")
        self._secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
        self._region     = os.environ.get("AWS_REGION",            "us-east-1")

    def _send_sync(self, phone: str, message: str) -> str:
        import boto3  # imported here so missing boto3 only fails at send time

        client = boto3.client(
            "sns",
            region_name=self._region,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
        )
        resp = client.publish(PhoneNumber=phone, Message=message)
        return resp.get("MessageId", "")

    async def send(self, payload: ProviderPayload) -> ProviderResult:
        phone = payload.recipient.phone or ""
        loop = asyncio.get_event_loop()
        message_id = await loop.run_in_executor(
            None, self._send_sync, phone, payload.body
        )
        return ProviderResult(ok=True, message_id=message_id)

    def classify_error(self, error: Exception) -> Literal["transient", "permanent"]:
        try:
            from botocore.exceptions import ClientError

            if isinstance(error, ClientError):
                code = error.response["Error"]["Code"]
                if code in ("InvalidParameter", "InvalidParameterValue", "KMSDisabled"):
                    return "permanent"
                if code in ("Throttling", "ThrottlingException", "RequestThrottled"):
                    return "transient"
                return "permanent"
        except ImportError:
            pass
        return "permanent"
