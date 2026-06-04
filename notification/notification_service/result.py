from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from notification_service.models import Recipient


class NotificationError(BaseModel):
    # "transient" is internal to the retry loop — never present in a final result
    # returned to callers. The facade converts it to "transient_exhausted" before
    # returning NotificationResult.
    kind: Literal[
        "transient",
        "permanent",
        "transient_exhausted",
        "validation",
        "no_provider",
        "skipped",
    ]
    code: str
    message: str


class ChannelResult(BaseModel):
    ok: bool
    skipped: bool = False
    provider: str | None = None
    message_id: str | None = None
    error: NotificationError | None = None
    attempts: int = 0


class RecipientResult(BaseModel):
    recipient: Recipient
    channels: dict[str, ChannelResult]


class NotificationResult(BaseModel):
    ok: bool
    recipients: list[RecipientResult]
