"""
simulator/sensors/battery_sensor.py
--------------------------------------
Device battery level sensor for the Vitals Simulator.

Produces:
  - battery (%) — device-level, not physiological

This sensor is independent of scenario phases, persona baselines,
ConditionMapper, CorrelationEngine, and FaultController. It is
always present on every simulated device.

Battery drains slowly in real wall-clock time (not compressed time)
to stay realistic regardless of the time compression factor.

Two operator-triggered events drive the demo narrative:
  battery_dead    — immediately drops to 0%, device shows Disconnected
  restart_device  — restores battery to 85%, device resumes publishing
"""

from __future__ import annotations

import random
import time
from typing import Any

from simulator.sensors.base import BaseSensor, SensorReading
from simulator.utils.logger import get_logger

logger = get_logger(__name__)

# Real-time drain rate: ~0.003%/s = 0.18%/min = ~10.8%/hr
# At this rate a 83% battery reaches 0% in ~7.7 hours — cosmetically realistic.
# In a 10-minute demo it drains ≈ 1.8%, staying visibly in the 80s.
_DEFAULT_DRAIN_RATE_PER_SECOND: float = 0.003


class BatterySensor(BaseSensor):
    """
    Simulates the wearable device battery.

    Starts at a random level between 81% and 87% so every patient looks
    slightly different. Drains in real time. Operator events can force
    it to 0% (dead) or restore it to 85% (restarted).
    """

    def __init__(
        self,
        initial_level: float | None = None,
        drain_rate_per_second: float = _DEFAULT_DRAIN_RATE_PER_SECOND,
    ) -> None:
        if initial_level is None:
            initial_level = random.uniform(81.0, 87.0)
        self._level: float = round(initial_level, 1)
        self._drain_rate: float = drain_rate_per_second
        self._last_tick_wall: float = time.monotonic()
        self._dead: bool = False

    # ------------------------------------------------------------------
    # BaseSensor interface
    # ------------------------------------------------------------------

    @property
    def sensor_name(self) -> str:
        return "battery"

    @property
    def sampling_interval_ms(self) -> int:
        # Publish every 10 seconds of real time — battery changes slowly.
        # Using a large simulated interval keeps this at ~10s regardless
        # of compression because the engine divides by the compression factor.
        return 10_000

    def update_params(self, params: Any) -> None:
        # Battery ignores phase changes — device-level sensor.
        pass

    def tick(
        self,
        phase_name: str,
        sim_time_seconds: float,
        phase_progress: float = 0.0,
        override_value: float | None = None,
    ) -> SensorReading:
        """Generate one battery reading, applying real-time drain."""
        now = time.monotonic()

        if override_value is not None and override_value == 0.0:
            # battery_dead event
            self._dead = True
            self._level = 0.0
        elif not self._dead:
            elapsed_real = now - self._last_tick_wall
            self._level = max(0.0, self._level - self._drain_rate * elapsed_real)
            if self._level <= 0.0:
                self._dead = True
                self._level = 0.0

        self._last_tick_wall = now

        level = round(self._level, 1)
        condition = (
            "normal"   if level > 20.0 else
            "warning"  if level > 10.0 else
            "critical"
        )

        return SensorReading(
            sensor_name=  "battery",
            value=        level,
            unit=         "%",
            condition=    condition,
            quality=      "good",
            fault_active= False,
            phase=        phase_name,
            extra=        {},
        )

    # ------------------------------------------------------------------
    # Battery-specific helpers
    # ------------------------------------------------------------------

    @property
    def is_dead(self) -> bool:
        """True when battery is at 0% (device off)."""
        return self._dead

    @property
    def level(self) -> float:
        """Current battery percentage."""
        return self._level

    def restore(self, level: float = 85.0) -> None:
        """Reset battery to a charged level (called on restart_device event)."""
        self._level = round(level, 1)
        self._dead = False
        self._last_tick_wall = time.monotonic()
        logger.info(
            "Battery restored",
            extra={"event": "battery_restored", "level": self._level},
        )
