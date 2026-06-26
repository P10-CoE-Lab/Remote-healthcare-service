from __future__ import annotations

import asyncio
import os
import smtplib
from email.mime.text import MIMEText
from typing import Literal

from notification_service.providers.base import Provider, ProviderPayload, ProviderResult


class MailhogProvider(Provider):
    name = "mailhog"

    def __init__(self) -> None:
        self._host = os.environ.get("MAILHOG_HOST", "mailhog")
        self._port = int(os.environ.get("MAILHOG_PORT", "1025"))
        self._sender = os.environ.get("MAILHOG_SENDER", "noreply@notification-service.local")

    def _send_sync(self, to: str, subject: str, body: str) -> None:
        msg = MIMEText(body, "plain")
        msg["From"] = self._sender
        msg["To"] = to
        msg["Subject"] = subject
        with smtplib.SMTP(self._host, self._port, timeout=10) as server:
            server.sendmail(self._sender, [to], msg.as_string())

    async def send(self, payload: ProviderPayload) -> ProviderResult:
        to = payload.recipient.email or ""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, self._send_sync, to, payload.subject, payload.body
        )
        return ProviderResult(ok=True)

    def classify_error(self, error: Exception) -> Literal["transient", "permanent"]:
        # SMTPException is a subclass of OSError in Python 3.3+; check it first
        if isinstance(error, smtplib.SMTPException):
            return "permanent"
        if isinstance(error, (ConnectionRefusedError, OSError)):
            return "transient"
        return "permanent"
