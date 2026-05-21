import logging
from datetime import datetime, timezone

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

import config

log = logging.getLogger("influx_writer")

_client    = InfluxDBClient(url=config.INFLUXDB_URL, token=config.INFLUXDB_TOKEN, org=config.INFLUXDB_ORG)
_write_api = _client.write_api(write_options=SYNCHRONOUS)


def verify_connection() -> None:
    """
    Verify InfluxDB is reachable and the configured org + bucket exist.
    Raises RuntimeError with a clear message if anything is wrong.
    Called once at startup before the MQTT loop begins.
    """
    # 1. Ping — checks URL and token are valid
    health = _client.health()
    if health.status != "pass":
        raise RuntimeError(
            f"InfluxDB health check failed: status={health.status} message={health.message}"
        )
    log.info("InfluxDB reachable at %s  status=%s", config.INFLUXDB_URL, health.status)

    # 2. Verify org exists
    orgs_api = _client.organizations_api()
    orgs     = orgs_api.find_organizations(org=config.INFLUXDB_ORG)
    if not orgs:
        raise RuntimeError(
            f"InfluxDB org '{config.INFLUXDB_ORG}' not found. "
            "Check INFLUXDB_ORG env var and that InfluxDB was initialised correctly."
        )
    log.info("InfluxDB org '%s' found (id=%s)", config.INFLUXDB_ORG, orgs[0].id)

    # 3. Verify bucket exists
    buckets_api = _client.buckets_api()
    bucket      = buckets_api.find_bucket_by_name(config.INFLUXDB_BUCKET)
    if bucket is None:
        raise RuntimeError(
            f"InfluxDB bucket '{config.INFLUXDB_BUCKET}' not found in org '{config.INFLUXDB_ORG}'. "
            "Check INFLUXDB_BUCKET env var and that InfluxDB was initialised correctly."
        )
    log.info("InfluxDB bucket '%s' found (id=%s)", config.INFLUXDB_BUCKET, bucket.id)


def _parse_timestamp(ts_str: str) -> datetime:
    """Parse the simulator's ISO-8601 timestamp, falling back to utcnow."""
    try:
        return datetime.fromisoformat(ts_str)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


def write(msg: dict) -> None:
    sensor = msg.get("sensor_name", "?")
    try:
        point = (
            Point("vitals")                          # fixed measurement name
            # --- tags (low-cardinality metadata) ---
            .tag("sensor_name", sensor)
            .tag("device_id",   msg.get("device_id",  ""))
            .tag("persona_id",  msg.get("persona_id", ""))
            .tag("poc_type",    msg.get("poc_type",   ""))
            .tag("condition",   msg.get("condition",  "normal"))
            .tag("unit",        msg.get("unit",       ""))
            .tag("phase",       msg.get("phase",      ""))
            .tag("quality",     msg.get("quality",    ""))
            # --- fields (numeric measurements) ---
            .field("value",           float(msg["value"]))
            .field("fault_active",    int(msg.get("fault_active", False)))
            .field("sequence_number", int(msg.get("sequence_number", 0)))
            # --- timestamp from the simulator, not the server clock ---
            .time(_parse_timestamp(msg.get("timestamp_utc", "")), WritePrecision.NS)
        )
        _write_api.write(bucket=config.INFLUXDB_BUCKET, record=point)
        log.info("WRITE OK  sensor=%-25s bucket=%s", sensor, config.INFLUXDB_BUCKET)
    except Exception as e:
        log.error("WRITE FAIL sensor=%-25s error=%s", sensor, e)
