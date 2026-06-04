from __future__ import annotations

import os

from notification_service.channels.email import EmailChannel
from notification_service.channels.sms import SMSChannel
from notification_service.channels.webhook import WebhookChannel
from notification_service.config import AppConfig, load_config
from notification_service.providers.email.gmail import GmailProvider
from notification_service.providers.email.mailhog import MailhogProvider
from notification_service.providers.email.outlook import OutlookProvider
from notification_service.providers.sms.aws_sns import AWSSNSProvider
from notification_service.providers.sms.twilio import TwilioProvider
from notification_service.providers.webhook.generic import GenericWebhookProvider
from notification_service.providers.webhook.slack import SlackProvider
from notification_service.registry import ChannelRegistry, ProviderRegistry
from notification_service.service import NotificationService
from notification_service.templates.registry import TemplateRegistry


def build_registries(
    config: AppConfig,
) -> tuple[ProviderRegistry, ChannelRegistry]:
    pr = ProviderRegistry()

    # Register all providers; each reads its env vars in __init__
    for provider in [
        GmailProvider(),
        OutlookProvider(),
        MailhogProvider(),
        TwilioProvider(),
        AWSSNSProvider(),
        SlackProvider(),
        GenericWebhookProvider(),
    ]:
        pr.register(provider)

    cr = ChannelRegistry()
    cr.register("email", EmailChannel(pr, config.providers.email.default))
    cr.register("sms", SMSChannel(pr, config.providers.sms.default))
    cr.register("webhook", WebhookChannel(pr, config.providers.webhook.default))

    return pr, cr


def create_service(config_path: str | None = None) -> NotificationService:
    path = config_path or os.environ.get(
        "NOTIFICATION_CONFIG_PATH", "./notification-config.yaml"
    )
    config = load_config(path)
    pr, cr = build_registries(config)
    tr = TemplateRegistry(os.environ.get("TEMPLATE_DIR", "./templates"))
    return NotificationService(config, pr, cr, tr)
