from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from pydantic import BaseModel

from notification_service.factory import create_service
from notification_service.models import Message, Recipient
from notification_service.result import NotificationResult


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.service = create_service()
    yield


app = FastAPI(title="Notification Service", lifespan=lifespan)


class NotifyRequest(BaseModel):
    profile: str
    to: Recipient | list[Recipient]
    subject: str = ""
    body: str
    payload: dict = {}
    template: str | None = None


# TODO: add authentication middleware here before production use
@app.post("/notify", response_model=NotificationResult)
async def notify(request: NotifyRequest, req: Request) -> NotificationResult:
    message = Message(**request.model_dump(exclude={"profile"}))
    return await req.app.state.service.notify(request.profile, message)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}
