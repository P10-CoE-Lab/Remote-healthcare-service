from __future__ import annotations

import pytest

from notification_service.channels.email import EmailChannel
from notification_service.channels.sms import SMSChannel
from notification_service.channels.webhook import WebhookChannel
from notification_service.config import AppConfig, load_config
from notification_service.registry import ChannelRegistry, ProviderRegistry
from notification_service.service import NotificationService
from notification_service.templates.registry import TemplateRegistry

TEST_CONFIG_YAML = """\
providers:
  email:
    default: mailhog
    fallback: null
  sms:
    default: twilio
    fallback: null
  webhook:
    default: generic
    fallback: null
profiles:
  test_profile:
    rules:
      - condition: "payload.value > 90"
        channels: [email, sms]
        priority: critical
      - default: true
        channels: [email]
        priority: low
  sensor_alert:
    rules:
      - condition: "payload.value > 90"
        channels: [email, sms, webhook]
        priority: critical
      - condition: "payload.value > 70"
        channels: [email, sms]
        priority: high
      - default: true
        channels: [email]
        priority: medium
  webhook_profile:
    rules:
      - default: true
        channels: [webhook]
        priority: low
"""


@pytest.fixture
def test_config(tmp_path) -> AppConfig:
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(TEST_CONFIG_YAML)
    return load_config(str(cfg_file))


@pytest.fixture
def template_registry(tmp_path) -> TemplateRegistry:
    # Minimal templates directory — tests that need specific templates create them
    return TemplateRegistry(str(tmp_path / "templates"))


def make_service(
    config: AppConfig,
    provider_registry: ProviderRegistry,
    template_dir: str = "/tmp",
) -> NotificationService:
    cr = ChannelRegistry()
    cr.register("email", EmailChannel(provider_registry, config.providers.email.default))
    cr.register("sms", SMSChannel(provider_registry, config.providers.sms.default))
    cr.register("webhook", WebhookChannel(provider_registry, config.providers.webhook.default))
    tr = TemplateRegistry(template_dir)
    return NotificationService(config, provider_registry, cr, tr)
