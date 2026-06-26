"""
rule_engine/cloud/engine.py
-----------------------------
Cloud rule engine — windowed risk scoring.

Subscribes to iots/# on the MQTT broker.
Maintains a rolling buffer of readings per patient per sensor.
Delegates evaluation to an Analyzer (default: RuleAnalyzer).
Publishes confirmed alerts with a cumulative risk score.

Alert topic: alerts/cloud/{persona_id}/{rule_id}
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import defaultdict, deque
from typing import Any

import paho.mqtt.client as mqtt

from rule_engine.cloud.analyzer import Analyzer
from rule_engine.cloud.config import CloudConfig
from rule_engine.shared.logger import get_logger
from rule_engine.shared.models import RuleAlert, RuleReading

logger = get_logger(__name__)


class CloudEngine:
    """Windowed rule evaluator backed by a pluggable Analyzer."""

    def __init__(self, config: CloudConfig, mqtt_config: dict[str, Any], analyzer: Analyzer) -> None:
        self._config      = config
        self._mqtt_config = mqtt_config
        self._analyzer    = analyzer
        self._running     = False

        # Rolling buffer: device_id → sensor_name → deque of (timestamp, value)
        # Keyed by device_id (not persona_id) so two patients sharing the same persona
        # get completely separate buffers and never cross-contaminate each other's data.
        self._max_window = max((r.window_seconds for r in config.rules), default=120)
        self._buffers: dict[str, dict[str, deque]] = defaultdict(
            lambda: defaultdict(deque)
        )
        # Maps device_id → persona_id, compression, patient_label
        self._device_persona: dict[str, str]  = {}
        self._device_compression: dict[str, float] = {}
        self._device_label: dict[str, str] = {}

        # Queue bridging paho thread → asyncio
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=2000)

        self._client: mqtt.Client | None = None
        self._loop:   asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        """Connect to broker, subscribe, and run ingestion + evaluation loops."""
        self._running = True
        self._loop    = asyncio.get_running_loop()   # stored for thread-safe callbacks
        self._client  = self._make_client()
        self._connect(self._client)

        logger.info("Cloud engine started", extra={"event": "cloud_engine_start"})
        await self._ingest_loop()

    async def stop(self) -> None:
        """Gracefully shut down."""
        self._running = False
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
        logger.info("Cloud engine stopped", extra={"event": "cloud_engine_stop"})

    # ------------------------------------------------------------------
    # MQTT setup
    # ------------------------------------------------------------------

    def _make_client(self) -> mqtt.Client:
        broker = self._mqtt_config.get("broker", {})
        host   = os.environ.get("MQTT_HOST", broker.get("host", "localhost"))
        port   = int(os.environ.get("MQTT_PORT", broker.get("port", 1883)))
        user   = os.environ.get("MQTT_USERNAME", broker.get("username", "")) or None
        pw     = os.environ.get("MQTT_PASSWORD", broker.get("password", "")) or None
        prefix = os.environ.get("MQTT_TOPIC_PREFIX", self._mqtt_config.get("topic", {}).get("prefix", "iots"))

        self._host   = host
        self._port   = port
        self._prefix = prefix

        client = mqtt.Client(client_id="rule-engine-cloud", clean_session=True)
        if user:
            client.username_pw_set(user, pw)
        client.on_connect    = self._on_connect
        client.on_message    = self._on_message
        client.on_disconnect = self._on_disconnect
        return client

    def _connect(self, client: mqtt.Client) -> None:
        try:
            client.connect(self._host, self._port, keepalive=60)
            client.loop_start()
        except Exception as exc:
            logger.error("Cloud MQTT connect failed", extra={"event": "cloud_mqtt_error", "error": str(exc)})

    def _on_connect(self, client, userdata, flags, rc) -> None:
        if rc == 0:
            client.subscribe(f"{self._prefix}/#", qos=0)
            logger.info(
                "Cloud engine subscribed",
                extra={"event": "cloud_subscribed", "topic": f"{self._prefix}/#"},
            )
        else:
            logger.error("Cloud MQTT connect refused", extra={"event": "cloud_connect_refused", "rc": rc})

    def _on_message(self, client, userdata, msg) -> None:
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
            # Record arrival time NOW, in paho's thread, before any asyncio hand-off.
            # If we set the timestamp inside _ingest() (asyncio thread), all readings
            # queued during a 1-second timeout burst get the same timestamp, collapsing
            # the span of the rolling buffer to near-zero and breaking the span check.
            payload["_arrived"] = time.time()

            if self._loop is not None:
                # Thread-safe bridge: schedules put_nowait on the asyncio event loop,
                # which properly wakes up the coroutine waiting on queue.get().
                try:
                    self._loop.call_soon_threadsafe(self._queue.put_nowait, payload)
                except asyncio.QueueFull:
                    pass
        except Exception:
            pass

    def _on_disconnect(self, client, userdata, rc) -> None:
        if rc != 0:
            logger.warning("Cloud MQTT disconnected", extra={"event": "cloud_disconnected", "rc": rc})

    # ------------------------------------------------------------------
    # Loops
    # ------------------------------------------------------------------

    async def _ingest_loop(self) -> None:
        """Pull messages from the queue, buffer them, and evaluate after each reading.

        Evaluation is triggered by incoming data rather than a fixed timer so that
        compressed scenarios (60x) are not missed because the timer fires after the
        event has already passed.  A per-device rate-limit prevents redundant CPU work
        when readings arrive at high frequency.
        """
        # Last evaluation time per device_id — rate-limit to avoid too-frequent evaluation
        _last_eval: dict[str, float] = {}
        MIN_EVAL_INTERVAL = 0.25   # real seconds between evaluations for the same device

        while self._running:
            try:
                payload = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                self._ingest(payload)

                device_id = payload.get("device_id", "")
                now = time.time()
                if now - _last_eval.get(device_id, 0) >= MIN_EVAL_INTERVAL:
                    self._evaluate_all()
                    _last_eval[device_id] = now

            except asyncio.TimeoutError:
                # No readings for 1 s — still evaluate in case a window just completed
                self._evaluate_all()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Cloud ingest error", extra={"event": "cloud_ingest_error", "error": str(exc)})

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    def _ingest(self, payload: dict) -> None:
        # Use the arrival time stamped by _on_message (paho thread), not time.time()
        # here — ingestion may happen in a burst long after actual arrival.
        arrived_at = payload.pop("_arrived", None) or time.time()

        try:
            reading = RuleReading.from_mqtt_payload(payload)
        except Exception:
            return

        if not reading.device_id or not reading.sensor_name:
            return

        # Track persona, compression, and patient label for this device
        self._device_persona[reading.device_id]     = reading.persona_id
        self._device_compression[reading.device_id] = reading.compression
        if reading.patient_label:
            self._device_label[reading.device_id]   = reading.patient_label

        buf = self._buffers[reading.device_id][reading.sensor_name]
        buf.append((arrived_at, reading.value))

        # Prune readings older than the largest configured window
        cutoff = time.time() - self._max_window
        while buf and buf[0][0] < cutoff:
            buf.popleft()

    # ------------------------------------------------------------------
    # Evaluation — delegated to the Analyzer
    # ------------------------------------------------------------------

    def _evaluate_all(self) -> None:
        now = time.time()
        for device_id, sensor_buffers in self._buffers.items():
            persona_id    = self._device_persona.get(device_id, "")
            compression   = self._device_compression.get(device_id, 1.0)
            patient_label = self._device_label.get(device_id, device_id)

            alerts = self._analyzer.analyze(
                device_id=      device_id,
                persona_id=     persona_id,
                sensor_buffers= sensor_buffers,
                compression=    compression,
                now=            now,
            )
            for a in alerts:
                rule_alert = RuleAlert(
                    rule_id=        a.rule_id,
                    description=    a.description,
                    severity=       a.severity,
                    source=         "cloud",
                    persona_id=     persona_id,
                    device_id=      device_id,
                    sensor_name=    a.sensor_name,
                    sensor_value=   a.sensor_value,
                    threshold=      a.threshold,
                    risk_score=     a.risk_score,
                    risk_level=     a.risk_level,
                    conditions_met= a.conditions_met,
                    patient_label=  patient_label,
                )
                self._publish_alert(rule_alert)

    def _publish_alert(self, alert: RuleAlert) -> None:
        if self._client is None:
            return
        topic   = f"{self._config.alert_topic_prefix}/{alert.persona_id}/{alert.rule_id}"
        payload = json.dumps(alert.to_mqtt_payload())
        try:
            self._client.publish(topic, payload, qos=0)
            logger.info(
                "Cloud alert fired",
                extra={
                    "event":       "cloud_alert",
                    "rule_id":     alert.rule_id,
                    "persona_id":  alert.persona_id,
                    "risk_score":  alert.risk_score,
                    "risk_level":  alert.risk_level,
                    "severity":    alert.severity,
                    "topic":       topic,
                },
            )
        except Exception as exc:
            logger.error("Cloud alert publish failed", extra={"event": "cloud_publish_error", "error": str(exc)})
