from __future__ import annotations

from pydantic import BaseModel


class Recipient(BaseModel):
    email: str | None = None
    phone: str | None = None
    device_token: str | None = None
    name: str | None = None


class Message(BaseModel):
    to: Recipient | list[Recipient]
    subject: str = ""
    body: str
    payload: dict = {}
    template: str | None = None
