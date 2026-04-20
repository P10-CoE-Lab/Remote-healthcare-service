"""
simulation/correlation.py
-------------------------
Leader/follower sensor correlation engine for the Industrial Rotating Asset
Intelligence Platform simulator.

Physical rationale:
    In a real electric motor, sensors are physically coupled:
    - Bearing vibration increases → friction increases → temperature rises (lag ~30s)
    - Vibration increases → mechanical load varies → current changes (lag ~10s)
    - Vibration increases → rotational instability → RPM fluctuates (lag ~5s)

    Without correlation, sensors would appear statistically independent — making
    anomaly detection trivially easy. With correlation, the AI must detect a
    genuine multivariate signal change, which is the real industrial challenge.

Correlation model:
    1. Leader sensor publishes its raw value each tick
    2. Leader's normalised deviation from its range midpoint is stored in a
       time-indexed rolling buffer
    3. Follower sensor queries the buffer at its configured lag (lag_seconds)
    4. Follower adds: leader_deviation * coupling_strength to its own base value

    follower_value = own_base_value + (leader_deviation * coupling_strength)

    leader_deviation = (leader_value - leader_midpoint) / (leader_range / 2)
    → normalised to [-1, 1] range, then scaled to follower's own range

Usage:
    engine = CorrelationEngine()
    engine.load_correlations(correlations)           # from db/queries.py
    engine.set_phase_ranges(phase_config_by_sensor)  # phase min/max per sensor

    # Each tick, update leader first:
    engine.record_leader_value("vibration_rms", value=2.3, sim_time=450.5)

    # Then followers read with lag:
    adjustment = engine.get_follower_adjustment("temperature", sim_time=450.5)
    adjusted_value = base_temperature + adjustment
"""

from __future__ import annotations

import collections
from dataclasses import dataclass, field
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class CorrelationRule:
    """A single leader→follower coupling rule loaded from the DB."""
    leader_sensor_name:   str
    follower_sensor_name: str
    coupling_strength:    float   # 0.0 (no effect) → 1.0 (full coupling)
    lag_seconds:          float   # simulated seconds of delay


@dataclass
class SensorRange:
    """Min/max range for a sensor in the current phase — used for normalisation."""
    min_value: float
    max_value: float

    @property
    def midpoint(self) -> float:
        return (self.min_value + self.max_value) / 2.0

    @property
    def half_range(self) -> float:
        hw = (self.max_value - self.min_value) / 2.0
        return hw if hw > 0 else 1.0   # guard against zero-width range


# ---------------------------------------------------------------------------
# Lag buffer for one leader sensor
# ---------------------------------------------------------------------------

class LagBuffer:
    """
    Time-indexed circular buffer holding recent (sim_time, normalised_value) pairs.

    Enables O(1) insertion and O(n) lookup at a specified lag.
    The buffer is sized to hold max_seconds of history at the configured
    sampling rate.

    n is small (~seconds of lag / sampling interval) so linear scan is fine.
    """

    def __init__(self, max_seconds: float = 120.0):
        """
        Args:
            max_seconds: Maximum lag history to retain (simulated seconds).
                         Set to the maximum lag_seconds across all correlations
                         for this leader, plus a safety margin.
        """
        self._buffer: collections.deque[tuple[float, float]] = collections.deque()
        self._max_seconds = max_seconds

    def record(self, sim_time: float, normalised_value: float) -> None:
        """
        Append a new (sim_time, normalised_value) entry.

        Evicts old entries beyond max_seconds to bound memory use.

        Args:
            sim_time:          Current simulated time (seconds).
            normalised_value:  Leader value normalised to [-1, 1].
        """
        self._buffer.append((sim_time, normalised_value))
        # Evict entries older than max_seconds
        cutoff = sim_time - self._max_seconds
        while self._buffer and self._buffer[0][0] < cutoff:
            self._buffer.popleft()

    def get_lagged_value(self, sim_time: float, lag_seconds: float) -> Optional[float]:
        """
        Retrieve the normalised value that was recorded at approximately
        (sim_time - lag_seconds).

        Uses linear interpolation between two bracketing entries for accuracy.

        Args:
            sim_time:    Current simulated time (seconds).
            lag_seconds: How far back to look.

        Returns:
            Normalised value at the lagged time, or None if insufficient history.
        """
        target_time = sim_time - lag_seconds
        if not self._buffer:
            return None

        # Find the two entries that bracket target_time
        prev: Optional[tuple[float, float]] = None
        for entry in self._buffer:
            t, v = entry
            if t >= target_time:
                if prev is None:
                    # Target is older than our oldest entry — return oldest
                    return v
                # Interpolate between prev and current entry
                t0, v0 = prev
                t1, v1 = t, v
                if t1 == t0:
                    return v0
                alpha = (target_time - t0) / (t1 - t0)
                return v0 + alpha * (v1 - v0)
            prev = entry

        # Target time is newer than our newest entry (lag too small) — return latest
        if prev is not None:
            return prev[1]
        return None


# ---------------------------------------------------------------------------
# Correlation engine
# ---------------------------------------------------------------------------

class CorrelationEngine:
    """
    Manages leader/follower sensor coupling across all correlation rules.

    One CorrelationEngine instance is shared across all sensors of a device.
    Leader sensors record their values each tick; follower sensors query for
    their adjustment.

    The engine is phase-aware: sensor ranges are updated at phase transitions
    so normalisation is always relative to the current expected operating range.
    """

    def __init__(self):
        # correlation_rules: follower_name → list of rules that affect it
        self._rules_by_follower: dict[str, list[CorrelationRule]] = {}
        # lag_buffers: leader_name → LagBuffer
        self._lag_buffers: dict[str, LagBuffer] = {}
        # Current phase ranges: sensor_name → SensorRange
        self._phase_ranges: dict[str, SensorRange] = {}

    def load_correlations(self, correlations: list[dict]) -> None:
        """
        Load correlation rules from DB query result.

        Args:
            correlations: List of dicts from db.queries.get_sensor_correlations().
                          Each dict has keys:
                              leader_sensor_name, follower_sensor_name,
                              coupling_strength, lag_seconds.
        """
        self._rules_by_follower.clear()
        self._lag_buffers.clear()

        for row in correlations:
            rule = CorrelationRule(
                leader_sensor_name=   row["leader_sensor_name"],
                follower_sensor_name= row["follower_sensor_name"],
                coupling_strength=    float(row["coupling_strength"]),
                lag_seconds=          float(row["lag_seconds"]),
            )

            # Index by follower for fast lookup
            self._rules_by_follower.setdefault(rule.follower_sensor_name, []).append(rule)

            # Ensure a lag buffer exists for this leader
            if rule.leader_sensor_name not in self._lag_buffers:
                # Buffer size: max lag + 50% safety margin
                max_lag = max(
                    r["lag_seconds"] for r in correlations
                    if r["leader_sensor_name"] == rule.leader_sensor_name
                )
                self._lag_buffers[rule.leader_sensor_name] = LagBuffer(
                    max_seconds=max_lag * 1.5 + 10.0
                )

        logger.info(
            "Correlation rules loaded",
            extra={
                "event":    "correlations_loaded",
                "leaders":  list(self._lag_buffers.keys()),
                "followers": list(self._rules_by_follower.keys()),
                "total_rules": sum(len(v) for v in self._rules_by_follower.values()),
            },
        )

    def update_phase_ranges(self, phase_configs: list[dict]) -> None:
        """
        Update sensor range information for normalisation.

        Call this at phase transitions so the correlation engine normalises
        leader deviations relative to the current phase's expected range.

        Args:
            phase_configs: List of sensor phase config dicts for the current phase.
                           Each must have: sensor_name, min_value, max_value.
        """
        for cfg in phase_configs:
            self._phase_ranges[cfg["sensor_name"]] = SensorRange(
                min_value=float(cfg["min_value"]),
                max_value=float(cfg["max_value"]),
            )

    def record_leader_value(
        self,
        sensor_name: str,
        value:       float,
        sim_time:    float,
    ) -> None:
        """
        Record a leader sensor's current value into its lag buffer.

        Call this once per tick for every sensor that acts as a leader in any
        correlation rule. Order matters: leaders must be recorded before their
        followers query for adjustments in the same tick.

        Args:
            sensor_name: e.g. 'vibration_rms'
            value:       The clean (post-behavior, post-noise) value.
            sim_time:    Current simulated time in seconds.
        """
        buf = self._lag_buffers.get(sensor_name)
        if buf is None:
            return   # This sensor is not a leader in any rule

        sensor_range = self._phase_ranges.get(sensor_name)
        if sensor_range is None:
            return   # No range info — can't normalise

        # Normalise: deviation from midpoint, scaled to [-1, 1]
        normalised = (value - sensor_range.midpoint) / sensor_range.half_range
        buf.record(sim_time, normalised)

    def get_follower_adjustment(
        self,
        sensor_name: str,
        sim_time:    float,
    ) -> float:
        """
        Calculate the total correlation adjustment for a follower sensor.

        Sums contributions from all leader→follower rules that apply to
        this sensor, each with their respective lag and coupling strength.

        Args:
            sensor_name: Follower sensor name, e.g. 'temperature'.
            sim_time:    Current simulated time in seconds.

        Returns:
            Additive adjustment to apply to the follower's base value.
            Returns 0.0 if no correlations apply or insufficient history.
        """
        rules = self._rules_by_follower.get(sensor_name)
        if not rules:
            return 0.0

        follower_range = self._phase_ranges.get(sensor_name)
        if follower_range is None:
            return 0.0

        total_adjustment = 0.0

        for rule in rules:
            buf = self._lag_buffers.get(rule.leader_sensor_name)
            if buf is None:
                continue

            lagged_norm = buf.get_lagged_value(sim_time, rule.lag_seconds)
            if lagged_norm is None:
                continue

            # Scale normalised leader deviation to follower's range
            adjustment = lagged_norm * rule.coupling_strength * follower_range.half_range
            total_adjustment += adjustment

        return total_adjustment

    def is_leader(self, sensor_name: str) -> bool:
        """Return True if this sensor acts as a leader in any correlation rule."""
        return sensor_name in self._lag_buffers