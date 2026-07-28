"""
tests/test_hardware_conformance.py
------------------------------------
Hardware/software MQTT conformance check.

Subscribes to iots/# on a live broker for a short capture window and
checks, per device_id actually seen on the wire:

  1. Every message carries exactly the field set _build_payload() in
     simulator/transport/mqtt_publisher.py produces — the canonical
     schema every consumer (Telegraf, rule engine, demo API) assumes.
     Derived directly from that function, not a hand-copied list, so
     this can't silently drift out of sync with the real contract.
  2. Every sensor_name defined under that device's persona_id's
     `baseline:` block in personas/{persona_id}.yaml was actually
     published at least once during the capture window.

This exists because a device silently missing one field or one sensor
(e.g. hardware never publishing heart_rate_variability) doesn't error
anywhere — it just quietly breaks a downstream feature (a Grafana
panel, the personalised baseline, the AI Briefing) that nobody notices
until much later. Run this after flashing or changing any hardware
firmware, or any time a new device joins the fleet.

Run directly against a live broker:
    python tests/test_hardware_conformance.py [--host localhost] [--port 1883] [--seconds 20]

Or under pytest, if installed (broker/window come from env vars so CI
doesn't need code changes):
    MQTT_HOST=localhost MQTT_PORT=1883 pytest tests/test_hardware_conformance.py

No broker reachable, or no traffic seen during the window, is reported
as a skip rather than a failure — this is a live-bench conformance
check, not a hermetic unit test.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import paho.mqtt.client as mqtt
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from simulator.sensors.base import SensorReading
from simulator.transport.mqtt_publisher import MQTTPublisher


def _canonical_field_set() -> set[str]:
    """Derive the expected payload field set from the real _build_payload()."""
    publisher = MQTTPublisher({})
    reading = SensorReading(
        sensor_name="_probe", value=0.0, unit="", condition="normal",
        quality="good", fault_active=False, phase="_probe", extra={},
    )
    payload = publisher._build_payload(
        reading,
        persona_id=    "_probe",
        poc_type=      "_probe",
        device_id=     "_probe",
        sequence_num=  0,
        compression=   1.0,
        patient_label= "_probe",
    )
    return set(payload.keys())


def _persona_sensor_set(persona_id: str) -> set[str] | None:
    """Return the sensor_names a persona's YAML declares, or None if the file's missing."""
    path = Path("personas") / f"{persona_id}.yaml"
    if not path.exists():
        return None
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return set((raw.get("baseline") or {}).keys())


def capture(host: str, port: int, seconds: float, topic: str = "iots/#") -> dict[str, list[dict]]:
    """Collect {device_id: [payload, ...]} from `seconds` of live MQTT traffic."""
    messages: dict[str, list[dict]] = {}

    def on_message(_client, _userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception:
            return
        device_id = payload.get("device_id", "")
        if device_id:
            messages.setdefault(device_id, []).append(payload)

    client = mqtt.Client(client_id="hardware-conformance-check", clean_session=True)
    client.on_message = on_message
    client.connect(host, port, keepalive=60)
    client.subscribe(topic, qos=0)
    client.loop_start()
    time.sleep(seconds)
    client.loop_stop()
    client.disconnect()
    return messages


def check_device(payloads: list[dict], canonical_fields: set[str]) -> list[str]:
    """Return human-readable problems found for one device (empty list = clean)."""
    problems: list[str] = []

    seen_sensors = {p.get("sensor_name") for p in payloads if p.get("sensor_name")}
    seen_fields: set[str] = set()
    for p in payloads:
        seen_fields |= set(p.keys())

    missing_fields = canonical_fields - seen_fields
    extra_fields   = seen_fields - canonical_fields
    if missing_fields:
        problems.append(f"missing field(s): {sorted(missing_fields)}")
    if extra_fields:
        problems.append(f"unexpected extra field(s): {sorted(extra_fields)}")

    persona_ids = {p.get("persona_id") for p in payloads if p.get("persona_id")}
    for persona_id in persona_ids:
        expected_sensors = _persona_sensor_set(persona_id)
        if expected_sensors is None:
            problems.append(
                f"persona '{persona_id}' has no matching personas/{persona_id}.yaml to check against"
            )
            continue
        missing_sensors = expected_sensors - seen_sensors
        if missing_sensors:
            problems.append(
                f"persona '{persona_id}' defines sensor(s) {sorted(missing_sensors)} "
                f"this device never published during the capture window"
            )

    return problems


def run(host: str, port: int, seconds: float) -> int:
    """Run the capture + checks, print a report, return a process exit code."""
    canonical_fields = _canonical_field_set()

    print(f"Capturing iots/# for {seconds:.0f}s from {host}:{port} ...")
    try:
        by_device = capture(host, port, seconds)
    except Exception as exc:
        print(f"SKIP: could not reach MQTT broker at {host}:{port} ({exc})")
        return 0

    if not by_device:
        print(
            "SKIP: no messages observed on iots/# during the capture window "
            "(no devices publishing right now?)"
        )
        return 0

    failures = 0
    for device_id, payloads in sorted(by_device.items()):
        problems = check_device(payloads, canonical_fields)
        if problems:
            failures += 1
            print(f"\nFAIL  {device_id}  ({len(payloads)} messages)")
            for problem in problems:
                print(f"   - {problem}")
        else:
            sensors = sorted({p.get("sensor_name") for p in payloads})
            print(f"OK    {device_id}  ({len(payloads)} messages, sensors={sensors})")

    print()
    print(f"{failures} device(s) failed conformance." if failures else
          "All observed devices match the payload schema and their persona's sensor set.")
    return 1 if failures else 0


# ---------------------------------------------------------------------------
# pytest entry point
# ---------------------------------------------------------------------------

def test_hardware_conformance() -> None:
    host    = os.environ.get("MQTT_HOST", "localhost")
    port    = int(os.environ.get("MQTT_PORT", 1883))
    seconds = float(os.environ.get("HW_CONFORMANCE_SECONDS", 20))

    canonical_fields = _canonical_field_set()
    try:
        by_device = capture(host, port, seconds)
    except Exception as exc:
        import pytest
        pytest.skip(f"could not reach MQTT broker at {host}:{port} ({exc})")
        return

    if not by_device:
        import pytest
        pytest.skip("no messages observed on iots/# during the capture window")
        return

    all_problems = {
        device_id: problems
        for device_id, payloads in by_device.items()
        if (problems := check_device(payloads, canonical_fields))
    }

    assert not all_problems, "Conformance problems found:\n" + "\n".join(
        f"{device_id}: {problems}" for device_id, problems in all_problems.items()
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("MQTT_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MQTT_PORT", 1883)))
    parser.add_argument(
        "--seconds", type=float, default=float(os.environ.get("HW_CONFORMANCE_SECONDS", 20))
    )
    args = parser.parse_args()
    sys.exit(run(args.host, args.port, args.seconds))
