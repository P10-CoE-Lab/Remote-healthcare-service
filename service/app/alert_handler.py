import logging
import os

import httpx
import yaml

import config

log = logging.getLogger("alert_handler")

_RULES_PATH = os.path.join(os.path.dirname(__file__), "healthcare-alert-rules.yaml")


def _load_rules() -> list[dict]:
    try:
        with open(_RULES_PATH) as f:
            return yaml.safe_load(f).get("alert_rules", [])
    except Exception as e:
        log.error("Failed to load alert rules from %s: %s — falling back to sensor_alert", _RULES_PATH, e)
        return []


# Loaded once at import time; restart the container to pick up changes.
_ALERT_RULES: list[dict] = _load_rules()


def _match_rule(sensor: str, condition: str, value: float) -> str:
    """Return the Notification Service profile for the first matching rule.

    Falls back to 'sensor_alert' (original behaviour) when no rule matches.
    """
    for rule in _ALERT_RULES:
        # sensor match
        if rule.get("sensor") not in ("*", sensor):
            continue
        # condition match
        allowed = rule.get("condition", [])
        if isinstance(allowed, str):
            allowed = [allowed]
        if condition not in allowed:
            continue
        # optional numeric range checks
        if "value_lt"    in rule and not (value <  rule["value_lt"]):    continue
        if "value_gt"    in rule and not (value >  rule["value_gt"]):    continue
        if "value_range" in rule:
            lo, hi = rule["value_range"]
            if not (lo <= value < hi):                                   continue
        return rule["profile"]

    return "sensor_alert"


def send_alert(msg: dict) -> None:
    condition = msg.get("condition", "normal")
    sensor    = msg.get("sensor_name", "unknown")
    unit      = msg.get("unit",  "")
    persona   = msg.get("persona_id",  "unknown")

    try:
        value = float(msg.get("value", 0))
    except (TypeError, ValueError):
        value = 0.0

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

    profile = _match_rule(sensor, condition, value)

    try:
        httpx.post(
            config.NOTIFICATION_URL,
            json={
                "profile": profile,
                "to":      recipient,
                "subject": f"[{condition.upper()}] {sensor} alert",
                "body":    f"{sensor} = {value} {unit} ({condition}) for {persona}",
                "payload": msg,
            },
            timeout=5,
        )
        log.info("ALERT OK    sensor=%-25s condition=%s value=%s %s  profile=%s", sensor, condition, value, unit, profile)
    except Exception as e:
        log.error("ALERT FAIL  sensor=%-25s condition=%s error=%s", sensor, condition, e)
