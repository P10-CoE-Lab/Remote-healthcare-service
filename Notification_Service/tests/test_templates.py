from __future__ import annotations

import os

import pytest

from notification_service.templates.registry import TemplateRegistry


@pytest.fixture
def template_dir(tmp_path):
    d = tmp_path / "templates"
    d.mkdir()
    profile = d / "test_profile"
    profile.mkdir()

    (profile / "email.html").write_text(
        "<b>{{ payload.sensor_id }}</b> = {{ payload.value }}"
    )
    (profile / "email.txt").write_text(
        "Sensor: {{ payload.sensor_id }} Value: {{ payload.value }}"
    )
    (profile / "sms.txt").write_text(
        "{{ payload.sensor_id }}={{ payload.value }}"
    )
    (profile / "webhook.json").write_text(
        '{"id": "{{ payload.sensor_id }}", "v": {{ payload.value }}}'
    )

    fallback_profile = d / "fallback_profile"
    fallback_profile.mkdir()
    (fallback_profile / "default.txt").write_text("Default: {{ body }}")

    xss_profile = d / "xss_profile"
    xss_profile.mkdir()
    (xss_profile / "email.html").write_text("<p>{{ payload.comment }}</p>")

    return str(d)


@pytest.fixture
def registry(template_dir):
    return TemplateRegistry(template_dir)


def test_renders_correct_template(registry):
    context = {
        "payload": {"sensor_id": "S1", "value": 95},
        "subject": "Test",
        "body": "fallback",
        "recipient": {},
    }
    body = registry.render("test_profile", "email", "txt", context)
    assert "S1" in body
    assert "95" in body


def test_html_template_renders(registry):
    context = {
        "payload": {"sensor_id": "S2", "value": 80},
        "subject": "Test",
        "body": "fallback",
        "recipient": {},
    }
    body = registry.render("test_profile", "email", "html", context)
    assert "S2" in body
    assert "80" in body


def test_webhook_json_template_renders(registry):
    context = {
        "payload": {"sensor_id": "S3", "value": 42},
        "subject": "",
        "body": "",
        "recipient": {},
    }
    body = registry.render("test_profile", "webhook", "json", context)
    assert '"S3"' in body
    assert "42" in body


def test_fallback_to_default_txt(registry):
    context = {"payload": {}, "subject": "", "body": "the body text", "recipient": {}}
    # fallback_profile has no email.txt, only default.txt
    body = registry.render("fallback_profile", "email", "txt", context)
    assert "Default: the body text" == body


def test_fallback_to_body_when_no_template(registry):
    context = {"payload": {}, "subject": "", "body": "raw body", "recipient": {}}
    # no_template_profile doesn't exist at all
    body = registry.render("no_template_profile", "email", "txt", context)
    assert body == "raw body"


def test_html_autoescape_prevents_xss(registry):
    context = {
        "payload": {"comment": "<script>alert('xss')</script>"},
        "subject": "",
        "body": "",
        "recipient": {},
    }
    body = registry.render("xss_profile", "email", "html", context)
    assert "<script>" not in body
    assert "&lt;script&gt;" in body


def test_json_template_not_autoescaped(registry):
    context = {
        "payload": {"sensor_id": "S<1>", "value": 10},
        "subject": "",
        "body": "",
        "recipient": {},
    }
    body = registry.render("test_profile", "webhook", "json", context)
    # JSON template should NOT escape < to &lt;
    assert "S<1>" in body
