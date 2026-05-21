import logging

import httpx

import config

log = logging.getLogger("alert_handler")


def send_alert(msg: dict) -> None:
    condition = msg.get("condition", "normal")
    sensor    = msg.get("sensor_name", "unknown")
    value     = msg.get("value", "?")
    unit      = msg.get("unit",  "")
    persona   = msg.get("persona_id",  "unknown")

    # Only alert on warning or critical readings
    if condition not in ("warning", "critical"):
        return

    # Skip if no recipient is configured
    if not config.ALERT_EMAIL and not config.ALERT_PHONE:
        log.warning("ALERT SKIP  sensor=%-25s condition=%s — no recipient configured", sensor, condition)
        return

    recipient = {}
    if config.ALERT_EMAIL:
        recipient["email"] = config.ALERT_EMAIL
    if config.ALERT_PHONE:
        recipient["phone"] = config.ALERT_PHONE

    try:
        httpx.post(
            config.NOTIFICATION_URL,
            json={
                "profile": "sensor_alert",
                "to":      recipient,
                "subject": f"[{condition.upper()}] {sensor} alert",
                "body":    f"{sensor} = {value} {unit} ({condition}) for {persona}",
                "payload": msg,
            },
            timeout=5,
        )
        log.info("ALERT OK    sensor=%-25s condition=%s value=%s %s", sensor, condition, value, unit)
    except Exception as e:
        log.error("ALERT FAIL  sensor=%-25s condition=%s error=%s", sensor, condition, e)
