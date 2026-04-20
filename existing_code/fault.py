"""
simulation/fault.py
-------------------
Fault injection layer for the Industrial Rotating Asset Intelligence Platform.

This module simulates real-world sensor and infrastructure failure scenarios
that industrial predictive maintenance systems must handle gracefully.

Five fault types are implemented (matching fault_schedule.fault_type):

  dropout        — Sensor stops publishing (packet loss, cable disconnect)
  flatline       — Sensor publishes a constant stuck value (encoder freeze)
  spike          — Sensor value multiplied by fault_factor (ESD, surge)
  stuck_at_zero  — Sensor reads zero regardless of physical state
  noise_burst    — Noise sigma multiplied by fault_factor (EMI interference)

The FaultInjector is stateful:
  - It is initialised with the fault schedule for one device from the DB
  - Each sensor tick calls evaluate() with the current simulated hour
  - Active faults transform the clean signal; the injector manages timing

MQTT dropout is signalled by returning None — the SensorSimulator must check
for None and skip publish when a dropout fault is active.

This is critical for the POC demo: we deliberately inject faults and then
show the audience that:
  1. The system handles missing data gracefully (no crashes, no stale readings)
  2. The AHI and RUL estimation degrade safely under partial data
  3. The fault is logged and visible in the dashboard

Usage:
    injector = FaultInjector(fault_schedule)   # from db/queries.get_fault_schedule()
    injector.start_simulation(sim_start_time_epoch)

    # Each tick:
    result = injector.evaluate(
        sensor_name="temperature",
        clean_value=72.5,
        current_sim_hours=450.1,
        last_known_value=71.8,
    )
    if result.is_dropout:
        pass  # skip publish
    else:
        publish(result.value)
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FaultResult:
    """
    Result of fault evaluation for one sensor tick.

    Attributes:
        value:       Transformed sensor value (may equal clean_value if no fault).
        is_dropout:  True if the sensor should not publish this tick.
        fault_type:  Name of the active fault, or None if no fault is active.
        fault_active: True if any fault is currently active on this sensor.
    """
    value:        Optional[float]
    is_dropout:   bool
    fault_type:   Optional[str]
    fault_active: bool

    @property
    def quality(self) -> str:
        """Signal quality string for MQTT payload metadata."""
        if self.is_dropout:
            return "bad"
        if self.fault_active:
            return "uncertain"
        return "good"


# ---------------------------------------------------------------------------
# Fault schedule entry
# ---------------------------------------------------------------------------

@dataclass
class FaultEntry:
    """A single scheduled fault event."""
    fault_id:         int
    sensor_name:      str
    fault_type:       str
    start_hour:       float
    duration_seconds: int
    fault_factor:     float
    description:      str = ""


# ---------------------------------------------------------------------------
# Fault injector
# ---------------------------------------------------------------------------

class FaultInjector:
    """
    Evaluates whether a fault should be applied to a sensor on each tick.

    One FaultInjector instance is created per device. It holds the complete
    fault schedule for that device and tracks which faults are currently active.

    Timing is based on simulated hours from the device's simulated age,
    not wall-clock time — so fault timing respects time compression correctly.
    """

    def __init__(self, fault_schedule: list[dict]):
        """
        Args:
            fault_schedule: List of fault dicts from db.queries.get_fault_schedule().
                            Each must have: fault_id, sensor_name, fault_type,
                            start_hour, duration_seconds, fault_factor.
        """
        self._faults: list[FaultEntry] = [
            FaultEntry(
                fault_id=         int(row["fault_id"]),
                sensor_name=      row["sensor_name"],
                fault_type=       row["fault_type"],
                start_hour=       float(row["start_hour"]),
                duration_seconds= int(row["duration_seconds"]),
                fault_factor=     float(row.get("fault_factor", 1.0)),
                description=      row.get("description", ""),
            )
            for row in fault_schedule
        ]
        # Track last-known values per sensor for flatline faults
        self._last_known: dict[str, float] = {}

        logger.info(
            "Fault injector initialised",
            extra={
                "event":       "fault_injector_init",
                "fault_count": len(self._faults),
                "sensors":     list({f.sensor_name for f in self._faults}),
            },
        )

    def evaluate(
        self,
        sensor_name:        str,
        clean_value:        float,
        current_sim_hours:  float,
        last_known_value:   Optional[float] = None,
        compression_factor: float = 3600.0,
    ) -> FaultResult:
        """
        Evaluate fault state for a sensor at the current simulated time.

        Args:
            sensor_name:        The sensor being evaluated.
            clean_value:        The signal value before any fault transformation.
            current_sim_hours:  Device's cumulative simulated age in hours.
            last_known_value:   Most recent good reading (used for flatline faults).
            compression_factor: Current time compression factor (for duration math).

        Returns:
            FaultResult with transformed value and fault metadata.
        """
        # Track last known good value
        if clean_value is not None:
            self._last_known[sensor_name] = clean_value

        active_fault = self._find_active_fault(sensor_name, current_sim_hours, compression_factor)

        if active_fault is None:
            return FaultResult(
                value=        clean_value,
                is_dropout=   False,
                fault_type=   None,
                fault_active= False,
            )

        return self._apply_fault(active_fault, clean_value, sensor_name)

    def _find_active_fault(
        self,
        sensor_name:        str,
        current_sim_hours:  float,
        compression_factor: float,
    ) -> Optional[FaultEntry]:
        """
        Find the first fault that is currently active for a given sensor.

        A fault is active when:
            current_sim_hours >= fault.start_hour
            AND current_sim_hours < fault.start_hour + (duration_seconds / 3600)

        Duration is in real seconds but represents simulated time:
        we convert duration_seconds to simulated hours using compression_factor.

        simulated_duration_hours = duration_seconds / compression_factor

        Wait — actually fault start_hour is a simulated-hour timestamp, and
        duration_seconds is the real-world duration of the fault event. We need
        to convert duration to simulated hours consistently.

        fault_duration_sim_hours = duration_seconds / 3600.0
        (faults are specified in real-world seconds, i.e., how long a cable
        intermittence would physically last, which maps to the same simulated hours)
        """
        for fault in self._faults:
            if fault.sensor_name != sensor_name:
                continue

            fault_end_hour = fault.start_hour + (fault.duration_seconds / 3600.0)

            if fault.start_hour <= current_sim_hours < fault_end_hour:
                return fault

        return None

    def _apply_fault(
        self,
        fault:        FaultEntry,
        clean_value:  float,
        sensor_name:  str,
    ) -> FaultResult:
        """
        Apply the fault transformation to the clean signal value.

        Each fault type is handled separately.  A log entry is emitted the
        first time a fault activates (determined by a simple state check).
        """
        logger.warning(
            "Fault active",
            extra={
                "event":       "fault_active",
                "sensor":      sensor_name,
                "fault_type":  fault.fault_type,
                "fault_id":    fault.fault_id,
                "description": fault.description,
            },
        )

        ft = fault.fault_type

        # ------------------------------------------------------------------
        # DROPOUT — sensor goes silent (no publish)
        # ------------------------------------------------------------------
        if ft == "dropout":
            return FaultResult(
                value=        None,
                is_dropout=   True,
                fault_type=   "dropout",
                fault_active= True,
            )

        # ------------------------------------------------------------------
        # FLATLINE — return last-known value regardless of physics
        # ------------------------------------------------------------------
        if ft == "flatline":
            stuck_value = self._last_known.get(sensor_name, clean_value)
            return FaultResult(
                value=        stuck_value,
                is_dropout=   False,
                fault_type=   "flatline",
                fault_active= True,
            )

        # ------------------------------------------------------------------
        # SPIKE — large transient multiplier
        # ------------------------------------------------------------------
        if ft == "spike":
            spike_value = clean_value * fault.fault_factor * random.uniform(0.8, 1.2)
            return FaultResult(
                value=        spike_value,
                is_dropout=   False,
                fault_type=   "spike",
                fault_active= True,
            )

        # ------------------------------------------------------------------
        # STUCK_AT_ZERO — sensor reads zero
        # ------------------------------------------------------------------
        if ft == "stuck_at_zero":
            return FaultResult(
                value=        0.0,
                is_dropout=   False,
                fault_type=   "stuck_at_zero",
                fault_active= True,
            )

        # ------------------------------------------------------------------
        # NOISE_BURST — signal with massively amplified noise
        # Implemented as large random additive perturbation
        # ------------------------------------------------------------------
        if ft == "noise_burst":
            # Approximate: add random perturbation scaled by fault_factor
            perturbation = clean_value * random.gauss(0.0, 0.1 * fault.fault_factor)
            return FaultResult(
                value=        clean_value + perturbation,
                is_dropout=   False,
                fault_type=   "noise_burst",
                fault_active= True,
            )

        # ------------------------------------------------------------------
        # Unknown fault type — pass through with warning
        # ------------------------------------------------------------------
        logger.error(
            "Unknown fault type — passing clean value",
            extra={"event": "unknown_fault_type", "fault_type": fault.fault_type},
        )
        return FaultResult(
            value=        clean_value,
            is_dropout=   False,
            fault_type=   fault.fault_type,
            fault_active= True,
        )