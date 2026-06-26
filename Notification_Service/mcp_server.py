from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from notification_service.factory import create_service
from notification_service.models import Message, Recipient

_service = create_service()
mcp = FastMCP("notification-service")


@mcp.tool()
async def send_notification(
    profile: str,
    to: dict | list[dict],
    body: str,
    subject: str = "",
    payload: dict = {},
    template: str | None = None,
) -> str:
    """Send a notification via the configured channels for a given profile."""
    if isinstance(to, list):
        recipient: Recipient | list[Recipient] = [Recipient(**r) for r in to]
    else:
        recipient = Recipient(**to)

    message = Message(
        to=recipient,
        subject=subject,
        body=body,
        payload=payload,
        template=template,
    )
    result = await _service.notify(profile, message)
    return result.model_dump_json()


if __name__ == "__main__":
    mcp.run()
