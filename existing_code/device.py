"""
simulation/device.py
--------------------
Device phase lifecycle manager for the Industrial Rotating Asset Intelligence
Platform simulator.

DeviceSimulator tracks one motor's position in the degradation lifecycle:
  healthy_baseline → early_degradation → progressive_degradation
  → accelerated_wear → failure_event → (loop back to healthy_baseline)

Each motor is independent. motor_01 starts at age 0h, motor_02 at 700h,
motor_03 at 900h — giving the demo dashboard three visually distinct
health states immediately.

Key responsibilities:
  - Advance simulated_age_hours on each update tick
  - Determine which phase the device is currently in
  - Compute degradation_progress (0.0 → 1.0) within the current phase
  - Interpolate sensor configs across gradual phase transitions
  - Expose per-sensor state to SensorSimulator coroutines
  - Persist state to DB on shutdown so the simulation resumes correctly

Phase interpolation:
  At gradual transitions, the sensor config smoothly interpolates between
  the outgoing and incoming phase over a transition window (default 15% of
  the incoming phase duration). This prevents discontinuous jumps that would
  produce unrealistic step changes in the ML signal.

Thread safety:
  DeviceSimulator is accessed from multiple sensor coroutines concurrently.
  All shared state mutations (simulated_age_hours, current_phase_idx) are
  protected by asyncio.Lock() — safe in a single-threaded asyncio event loop.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional

from utils.logger import get_logger
from utils.time import TimeCompressor

logger = get_logger(__name__)

# Fraction of the incoming phase duration used for transition interpolation.
# Set to 0.0 so the live simulator uses pure phase config throughout —
# identical to backfill — ensuring a seamless handoff at startup.
DEFAULT_TRANSITION_PCT = 0.15


@dataclass
class PhaseSpec:
    """Parsed phase definition from the DB."""
    phase_id:        int
    phase_name:      str
    phase_order:     int
    duration_hours:  float
    transition:      str    # 'gradual' | 'abrupt'


@dataclass
class SensorPhaseConfig:
    """Sensor value config for one phase."""
    phase_name:      str
    phase_order:     int
    duration_hours:  float
    sensor_name:     str
    min_value:       float
    max_value:       float
    noise_model:     str
    noise_intensity: float
    behavior_type:   str


class DeviceSimulator:
    """
    Manages the full simulation lifecycle for one industrial motor.

    Sensor coroutines call get_sensor_state() each tick to obtain:
      - Current phase name
      - Phase-specific sensor config (with optional interpolation)
      - Degradation progress (0.0 → 1.0) within the current phase

    Args:
        equipment_meta:     Dict with equipment fields from DB.
        phases:             Ordered list of PhaseSpec for this profile.
        sensor_configs:     All sensor_phase_config rows for all phases.
        compressor:         Shared TimeCompressor.
        initial_age_hours:  Simulated age at startup (from device_simulation_state).
        auto_loop:          If True, reset to healthy_baseline after failure_event.
        transition_pct:     Fraction of incoming phase for gradual transitions.
    """

    def __init__(
        self,
        equipment_meta:    dict[str, Any],
        phases:            list[PhaseSpec],
        sensor_configs:    list[SensorPhaseConfig],
        compressor:        TimeCompressor,
        initial_age_hours: float = 0.0,
        auto_loop:         bool  = True,
        transition_pct:    float = DEFAULT_TRANSITION_PCT,
    ):
        self._meta            = equipment_meta
        self._equipment_name  = equipment_meta["equipment"]
        self._equipment_id    = equipment_meta["equipment_id"]
        self._phases          = phases                       # ordered list
        self._compressor      = compressor
        self._auto_loop       = auto_loop
        self._transition_pct  = transition_pct
        self._lock            = asyncio.Lock()

        # Build lookup: phase_order → {sensor_name → SensorPhaseConfig}
        self._phase_sensor_map: dict[int, dict[str, SensorPhaseConfig]] = {}
        for cfg in sensor_configs:
            self._phase_sensor_map.setdefault(cfg.phase_order, {})[cfg.sensor_name] = cfg

        # Initialise runtime state
        self._simulated_age_hours: float = initial_age_hours
        self._current_phase_idx:   int   = self._phase_idx_for_age(initial_age_hours)
        self._phase_entry_age:     float = self._phase_start_age(self._current_phase_idx)

        logger.info(
            "DeviceSimulator initialised",
            extra={
                "event":              "device_init",
                "equipment":          self._equipment_name,
                "initial_age_hours":  initial_age_hours,
                "starting_phase":     self._current_phase.phase_name,
                "phase_entry_age":    round(self._phase_entry_age, 2),
            },
        )

    # ------------------------------------------------------------------
    # Public API — called by SensorSimulator coroutines
    # ------------------------------------------------------------------

    def get_sensor_state(
        self,
        sensor_name: str,
    ) -> tuple[str, Optional[dict[str, Any]], float]:
        """
        Return the current phase, sensor config, and degradation_progress.

        This is called every tick from each sensor coroutine — it must be
        fast (no I/O, no locks — sensors read a consistent snapshot).

        Args:
            sensor_name: e.g. 'vibration_rms'

        Returns:
            Tuple of:
              - phase_name (str)
              - sensor config dict (or None if not found)
              - degradation_progress float in [0.0, 1.0]
        """
        phase = self._current_phase
        progress = self._degradation_progress()

        # Check if we're in a gradual transition window
        next_idx = self._current_phase_idx + 1
        in_transition = (
            phase.transition == "gradual"
            and next_idx < len(self._phases)
            and self._in_transition_window(progress)
        )

        if in_transition:
            config = self._interpolated_config(sensor_name, next_idx, progress)
        else:
            phase_configs = self._phase_sensor_map.get(phase.phase_order, {})
            cfg = phase_configs.get(sensor_name)
            config = self._config_to_dict(cfg) if cfg else None

        return phase.phase_name, config, progress

    async def advance(self, real_elapsed_seconds: float) -> bool:
        """
        Advance simulated age using the compressor for all phases.

        delta_hours = real_elapsed_seconds * compression_factor / 3600

        Args:
            real_elapsed_seconds: Real wall-clock seconds elapsed since last call.

        Returns:
            True if a phase transition occurred.
        """
        async with self._lock:
            delta_hours = self._compressor.real_seconds_to_sim_hours(real_elapsed_seconds)
            self._simulated_age_hours += delta_hours
            transitioned = self._check_phase_transition()
            return transitioned

    @property
    def simulated_age_hours(self) -> float:
        """Current simulated age in hours (read by SensorSimulator)."""
        return self._simulated_age_hours

    @property
    def current_phase_name(self) -> str:
        """Name of the currently active phase."""
        return self._current_phase.phase_name

    @property
    def equipment_id(self) -> int:
        return self._equipment_id

    @property
    def equipment_name(self) -> str:
        return self._equipment_name

    @property
    def auto_loop(self) -> bool:
        return self._auto_loop

    @auto_loop.setter
    def auto_loop(self, value: bool) -> None:
        self._auto_loop = value

    # ------------------------------------------------------------------
    # Phase management
    # ------------------------------------------------------------------

    def _check_phase_transition(self) -> bool:
        """
        Check if simulated_age_hours has exceeded the current phase boundary.

        Advances to the next phase if needed, or loops back to healthy_baseline
        if auto_loop is enabled.

        Returns True if a transition occurred.
        """
        elapsed_in_phase = self._simulated_age_hours - self._phase_entry_age
        current = self._current_phase

        if elapsed_in_phase < current.duration_hours:
            return False   # still in current phase

        # Phase complete — advance
        next_idx = self._current_phase_idx + 1

        if next_idx >= len(self._phases):
            if self._auto_loop:
                logger.info(
                    "Device completed full lifecycle — looping to healthy_baseline",
                    extra={
                        "event":     "device_lifecycle_loop",
                        "equipment": self._equipment_name,
                        "total_age": round(self._simulated_age_hours, 1),
                    },
                )
                self._current_phase_idx = 0
                self._phase_entry_age   = self._simulated_age_hours
            else:
                # Stay in failure_event
                return False
        else:
            self._current_phase_idx = next_idx
            self._phase_entry_age   = self._simulated_age_hours

        logger.info(
            "Device phase transition",
            extra={
                "event":           "phase_transition",
                "equipment":       self._equipment_name,
                "previous_phase":  current.phase_name,
                "new_phase":       self._current_phase.phase_name,
                "sim_age_hours":   round(self._simulated_age_hours, 1),
            },
        )
        return True

    def _phase_idx_for_age(self, age_hours: float) -> int:
        """
        Determine which phase index corresponds to a given simulated age.

        Used at startup to resume from the correct phase after offset.
        """
        cumulative = 0.0
        for idx, phase in enumerate(self._phases):
            cumulative += phase.duration_hours
            if age_hours < cumulative:
                return idx
        return len(self._phases) - 1   # clamp to last phase

    def _phase_start_age(self, phase_idx: int) -> float:
        """Return the cumulative simulated age at which phase_idx begins."""
        return sum(p.duration_hours for p in self._phases[:phase_idx])

    def _degradation_progress(self) -> float:
        """
        Compute progress through the current phase as a fraction [0.0, 1.0].
        """
        elapsed = self._simulated_age_hours - self._phase_entry_age
        return self._compressor.phase_progress(elapsed, self._current_phase.duration_hours)

    def _in_transition_window(self, progress: float) -> bool:
        """Return True if we are in the gradual transition window at the end of a phase."""
        return progress >= (1.0 - self._transition_pct)

    # ------------------------------------------------------------------
    # Config interpolation
    # ------------------------------------------------------------------

    def _interpolated_config(
        self,
        sensor_name: str,
        next_phase_idx: int,
        progress: float,
    ) -> Optional[dict[str, Any]]:
        """
        Smoothly interpolate sensor config between current and next phase.

        Fraction within transition window:
            alpha = 0.0 at transition window start → 1.0 at end of phase

        Interpolated:
            min_value = lerp(current_min, next_min, alpha)
            max_value = lerp(current_max, next_max, alpha)

        Noise/behavior type: switch to next phase values when alpha > 0.5.
        """
        current_order  = self._current_phase.phase_order
        current_map    = self._phase_sensor_map.get(current_order, {})
        next_order     = self._phases[next_phase_idx].phase_order
        next_map       = self._phase_sensor_map.get(next_order, {})

        curr_cfg = current_map.get(sensor_name)
        next_cfg = next_map.get(sensor_name)

        if curr_cfg is None:
            return self._config_to_dict(next_cfg) if next_cfg else None
        if next_cfg is None:
            return self._config_to_dict(curr_cfg)

        # alpha: position within the transition window (0 → 1)
        transition_start = 1.0 - self._transition_pct
        alpha = (self._degradation_progress() - transition_start) / self._transition_pct
        alpha = max(0.0, min(1.0, alpha))

        # Interpolate numeric ranges
        interp_min = curr_cfg.min_value + (next_cfg.min_value - curr_cfg.min_value) * alpha
        interp_max = curr_cfg.max_value + (next_cfg.max_value - curr_cfg.max_value) * alpha

        # Discrete values: switch at midpoint
        behavior = next_cfg.behavior_type if alpha >= 0.5 else curr_cfg.behavior_type
        noise_m  = next_cfg.noise_model   if alpha >= 0.5 else curr_cfg.noise_model
        noise_i  = curr_cfg.noise_intensity + (next_cfg.noise_intensity - curr_cfg.noise_intensity) * alpha

        return {
            "phase_name":     curr_cfg.phase_name,  # keep current for logging
            "sensor_name":    sensor_name,
            "min_value":      interp_min,
            "max_value":      interp_max,
            "noise_model":    noise_m,
            "noise_intensity": noise_i,
            "behavior_type":  behavior,
        }

    @staticmethod
    def _config_to_dict(cfg: Optional[SensorPhaseConfig]) -> Optional[dict[str, Any]]:
        """Convert a SensorPhaseConfig dataclass to a plain dict."""
        if cfg is None:
            return None
        return {
            "phase_name":      cfg.phase_name,
            "sensor_name":     cfg.sensor_name,
            "min_value":       cfg.min_value,
            "max_value":       cfg.max_value,
            "noise_model":     cfg.noise_model,
            "noise_intensity": cfg.noise_intensity,
            "behavior_type":   cfg.behavior_type,
        }

    # ------------------------------------------------------------------
    # Internal shortcuts
    # ------------------------------------------------------------------

    @property
    def _current_phase(self) -> PhaseSpec:
        return self._phases[self._current_phase_idx]