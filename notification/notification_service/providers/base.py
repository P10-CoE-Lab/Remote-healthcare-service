from __future__ import annotations

import httpx
from abc import ABC, abstractmethod
from typing import Literal

from pydantic import BaseModel

from notification_service.models import Recipient


class ProviderPayload(BaseModel):
    recipient: Recipient
    subject: str = ""
    body: str
    extra: dict = {}


class ProviderResult(BaseModel):
    ok: bool
    message_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None


def classify_httpx_error(error: Exception) -> Literal["transient", "permanent"]:
    if isinstance(error, (httpx.TimeoutException, httpx.NetworkError)):
        return "transient"
    if isinstance(error, httpx.HTTPStatusError):
        if error.response.status_code in (429, 500, 502, 503, 504):
            return "transient"
        return "permanent"
    return "permanent"


class Provider(ABC):
    name: str  # must be set as a class attribute in every subclass

    @abstractmethod
    async def send(self, payload: ProviderPayload) -> ProviderResult: ...

    @abstractmethod
    def classify_error(self, error: Exception) -> Literal["transient", "permanent"]: ...
