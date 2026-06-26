"""
rule_engine/service.py
-----------------------
Entry point for the standalone rule engine service.

Starts the edge and cloud engines simultaneously via asyncio.gather.
Both engines subscribe to MQTT independently.

Usage:
    python -m rule_engine.service

Environment variables (all optional — fall back to config file defaults):
    MQTT_HOST, MQTT_PORT, MQTT_USERNAME, MQTT_PASSWORD
    MQTT_TOPIC_PREFIX
    EDGE_RULES_CONFIG    (default: config/edge_rules.yaml)
    CLOUD_RULES_CONFIG   (default: config/cloud_rules.yaml)
    MQTT_CONFIG          (default: config/mqtt_config.yaml)
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path

import yaml

from rule_engine.alert_notifier import AlertNotifier
from rule_engine.cloud.config import load_cloud_config
from rule_engine.cloud.engine import CloudEngine
from rule_engine.cloud.personalised_analyzer import PersonalisedAnalyzer
from rule_engine.shared.logger import get_logger

logger = get_logger(__name__)


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        logger.warning(
            "Config file not found — using empty dict",
            extra={"event": "config_not_found", "path": str(path)},
        )
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


async def main() -> None:
    config_dir = Path("config")

    mqtt_config_path  = Path(os.environ.get("MQTT_CONFIG",        str(config_dir / "mqtt_config.yaml")))
    cloud_rules_path  = Path(os.environ.get("CLOUD_RULES_CONFIG", str(config_dir / "cloud_rules.yaml")))

    mqtt_config  = _load_yaml(mqtt_config_path)
    cloud_config = load_cloud_config(cloud_rules_path)

    raw_cloud = _load_yaml(cloud_rules_path)
    baseline_config = raw_cloud.get("personalised_baseline", {})
    analyzer     = PersonalisedAnalyzer(cloud_config, baseline_config, mqtt_config=mqtt_config)
    cloud_engine = CloudEngine(cloud_config, mqtt_config, analyzer)
    notifier     = AlertNotifier(mqtt_config)

    shutdown_event = asyncio.Event()
    loop = asyncio.get_event_loop()

    def _handle_signal():
        logger.info("Shutdown signal received", extra={"event": "shutdown_signal"})
        shutdown_event.set()

    import platform
    if platform.system() != "Windows":
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _handle_signal)
    else:
        signal.signal(signal.SIGINT, lambda s, f: _handle_signal())

    logger.info(
        "Rule engine service starting",
        extra={
            "event":       "service_start",
            "cloud_rules": len(cloud_config.rules),
        },
    )

    cloud_task    = asyncio.create_task(cloud_engine.start())
    notifier_task = asyncio.create_task(notifier.start())
    shutdown_task = asyncio.create_task(shutdown_event.wait())
    # Wait only on cloud_task and shutdown_task — notifier_task exits immediately
    # when ALERT_EMAIL is unset and must not trigger a service-wide shutdown.
    await asyncio.wait(
        [cloud_task, shutdown_task],
        return_when=asyncio.FIRST_COMPLETED,
    )

    await cloud_engine.stop()
    await notifier.stop()

    for task in (cloud_task, notifier_task):
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    logger.info("Rule engine service stopped", extra={"event": "service_stop"})


if __name__ == "__main__":
    asyncio.run(main())
