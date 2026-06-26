"""
rule_engine/alert_notifier.py
------------------------------
MQTT cloud alert → email bridge.

Subscribes to alerts/cloud/# on the MQTT broker. For each confirmed
alert it calls the Notification Service HTTP API, which delivers the
email via Mailhog (dev) or Gmail (prod) depending on how the
Notification Service is configured.

Enabled only when ALERT_EMAIL is set. Safe to omit — the rest of the
rule engine continues working normally.

Environment variables:
    ALERT_EMAIL                 Recipient email address (required to enable)
    ALERT_EMAIL_NAME            Recipient display name (default: "Monitoring Team")
    NOTIFICATION_SERVICE_URL    Base URL of the Notification Service (default: http://localhost:8001)
    ALERT_COOLDOWN_SECONDS      Min seconds between emails for the same (device, rule) pair (default: 120)
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

import httpx
import paho.mqtt.client as mqtt

from rule_engine.shared.logger import get_logger

logger = get_logger(__name__)

NOTIFICATION_URL  = os.environ.get("NOTIFICATION_SERVICE_URL", "http://localhost:8001")
ALERT_EMAIL       = os.environ.get("ALERT_EMAIL", "")
ALERT_EMAIL_NAME  = os.environ.get("ALERT_EMAIL_NAME", "Monitoring Team")
COOLDOWN_SECONDS  = int(os.environ.get("ALERT_COOLDOWN_SECONDS", "120"))

# Only notify for these severities — skip "info" to avoid noise
_NOTIFY_SEVERITIES = {"warning", "critical", "emergency"}


class AlertNotifier:
    """Bridges MQTT cloud alerts to the Notification Service via HTTP."""

    def __init__(self, mqtt_config: dict[str, Any]) -> None:
        self._mqtt_config = mqtt_config
        self._running     = False
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=500)
        self._client: mqtt.Client | None = None
        self._loop:   asyncio.AbstractEventLoop | None = None
        # Cooldown: (device_id, rule_id) → last_notified monotonic timestamp
        self._last_sent: dict[tuple[str, str], float] = {}

    async def start(self) -> None:
        """Connect and start the dispatch loop. No-op when ALERT_EMAIL is unset."""
        if not ALERT_EMAIL:
            logger.info(
                "AlertNotifier disabled — set ALERT_EMAIL to enable email notifications",
                extra={"event": "notifier_disabled"},
            )
            return

        self._running = True
        self._loop    = asyncio.get_running_loop()
        self._client  = self._make_client()
        self._connect(self._client)
        logger.info(
            "Alert notifier started",
            extra={"event": "notifier_start", "recipient": ALERT_EMAIL},
        )
        await self._dispatch_loop()

    async def stop(self) -> None:
        """Graceful shutdown."""
        self._running = False
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
        logger.info("Alert notifier stopped", extra={"event": "notifier_stop"})

    # ------------------------------------------------------------------
    # MQTT
    # ------------------------------------------------------------------

    def _make_client(self) -> mqtt.Client:
        broker = self._mqtt_config.get("broker", {})
        self._host = os.environ.get("MQTT_HOST", broker.get("host", "localhost"))
        self._port = int(os.environ.get("MQTT_PORT", broker.get("port", 1883)))
        user = os.environ.get("MQTT_USERNAME", broker.get("username", "")) or None
        pw   = os.environ.get("MQTT_PASSWORD", broker.get("password", "")) or None

        client = mqtt.Client(client_id="alert-notifier", clean_session=True)
        if user:
            client.username_pw_set(user, pw)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        return client

    def _connect(self, client: mqtt.Client) -> None:
        try:
            client.connect(self._host, self._port, keepalive=60)
            client.loop_start()
        except Exception as exc:
            logger.error(
                "Notifier MQTT connect failed",
                extra={"event": "notifier_mqtt_error", "error": str(exc)},
            )

    def _on_connect(self, client, userdata, flags, rc) -> None:
        if rc == 0:
            client.subscribe("alerts/cloud/#", qos=0)
            logger.info(
                "Notifier subscribed to alerts/cloud/#",
                extra={"event": "notifier_subscribed"},
            )

    def _on_message(self, client, userdata, msg) -> None:
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
            if self._loop:
                self._loop.call_soon_threadsafe(self._queue.put_nowait, payload)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def _dispatch_loop(self) -> None:
        while self._running:
            try:
                alert = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                await self._handle_alert(alert)
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(
                    "Notifier dispatch error",
                    extra={"event": "notifier_error", "error": str(exc)},
                )

    async def _handle_alert(self, alert: dict) -> None:
        severity = alert.get("severity", "info")
        if severity not in _NOTIFY_SEVERITIES:
            return

        device_id = alert.get("device_id", "")
        rule_id   = alert.get("rule_id", "")
        key       = (device_id, rule_id)
        now       = time.monotonic()

        if now - self._last_sent.get(key, 0) < COOLDOWN_SECONDS:
            return  # still in cooldown — avoid flooding

        self._last_sent[key] = now

        patient_label = alert.get("patient_label") or device_id
        sensor_name   = alert.get("sensor_name", "").replace("_", " ").title()
        sensor_value  = alert.get("sensor_value", 0)
        threshold     = alert.get("threshold", 0)
        description   = alert.get("description", rule_id)
        risk_score    = alert.get("risk_score", 0)
        risk_level    = alert.get("risk_level", "none")
        unit          = alert.get("unit", "")

        # Try to generate an LLM clinical explanation for the email body
        email_body = (
            f"{description}\n"
            f"Patient: {patient_label}\n"
            f"{sensor_name}: {sensor_value} {unit} (threshold: {threshold} {unit})\n"
            f"Risk score: {risk_score} ({risk_level})"
        )
        explanation: str | None = None
        try:
            from rule_engine.llm.context_builder import build_alert_context
            from rule_engine.llm.summariser import explain_alert
            context     = await build_alert_context(alert)
            explanation = await explain_alert(context)
            email_body  = explanation
        except Exception as exc:
            logger.warning(
                "LLM explanation failed — using plain text body",
                extra={"event": "llm_email_fallback", "error": str(exc)},
            )

        subject = f"[{severity.upper()}] {description} — {patient_label}"

        notify_payload = {
            "patient_label":  patient_label,
            "device_id":      device_id,
            "persona_id":     alert.get("persona_id", ""),
            "rule_id":        rule_id,
            "description":    description,
            "severity":       severity,
            "sensor_name":    sensor_name,
            "sensor_value":   sensor_value,
            "unit":           unit,
            "threshold":      threshold,
            "risk_score":     risk_score,
            "risk_level":     risk_level,
            "conditions_met": alert.get("conditions_met", []),
            "timestamp_utc":  alert.get("timestamp_utc", ""),
            "source":         alert.get("source", "cloud"),
            "explanation":    explanation,
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{NOTIFICATION_URL}/notify",
                    json={
                        "profile": "health_alert",
                        "to":      {"email": ALERT_EMAIL, "name": ALERT_EMAIL_NAME},
                        "subject": subject,
                        "body":    email_body,
                        "payload": notify_payload,
                    },
                )
                data = resp.json()
                if resp.status_code == 200 and data.get("ok"):
                    logger.info(
                        "Alert email sent",
                        extra={
                            "event":     "notification_sent",
                            "rule_id":   rule_id,
                            "device_id": device_id,
                            "severity":  severity,
                            "patient":   patient_label,
                        },
                    )
                else:
                    logger.warning(
                        "Notification service returned error",
                        extra={"event": "notification_failed", "status": resp.status_code, "body": str(data)[:200]},
                    )
        except httpx.ConnectError:
            logger.warning(
                "Notification Service unreachable — is it running?",
                extra={"event": "notification_unreachable", "url": NOTIFICATION_URL},
            )
        except Exception as exc:
            logger.error(
                "Alert notification failed",
                extra={"event": "notification_error", "error": str(exc)},
            )
