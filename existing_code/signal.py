"""
simulation/signal.py
--------------------
Signal behavior type implementations for the Industrial Rotating Asset
Intelligence Platform simulator.

Each behavior type determines how a sensor's base value evolves within a
simulation phase as degradation_progress advances from 0.0 → 1.0.

Four types are supported (matching sensor_phase_config.behavior_type):

  monotonic_rising    — Value trends from min toward max as phase progresses.
                        Models vibration, temperature, and current rising
                        with bearing/winding degradation.

  monotonic_falling   — Value trends from max toward min as phase progresses.
                        Models RPM dropping as motor efficiency decreases.

  oscillating_stable  — Value oscillates around the range midpoint. Oscillation
                        amplitude increases with degradation_progress.
                        Models healthy baseline sensors with natural variation.

  digital             — Returns 0 or 1. Flip frequency increases with degradation.
                        Models status bits, relay states, or protection trips.

Usage:
    from simulation.signal import create_signal_generator, BehaviorType

    generator = create_signal_generator("monotonic_rising")
    base_value = generator.generate(
        phase_min=0.5,
        phase_max=1.2,
        degradation_progress=0.6,   # 60% through the phase
    )
"""

from __future__ import annotations

import math
import random
from abc import ABC, abstractmethod
from enum import Enum

from utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Enum for type safety
# ---------------------------------------------------------------------------

class BehaviorType(str, Enum):
    MONOTONIC_RISING    = "monotonic_rising"
    MONOTONIC_FALLING   = "monotonic_falling"
    OSCILLATING_STABLE  = "oscillating_stable"
    DIGITAL             = "digital"


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class SignalGenerator(ABC):
    """
    Abstract base for signal behavior generators.

    Each generator produces a clean base value (before noise is applied)
    that reflects how the sensor should evolve over the course of a phase.
    """

    @abstractmethod
    def generate(
        self,
        phase_min:            float,
        phase_max:            float,
        degradation_progress: float,
    ) -> float:
        """
        Generate a base signal value for this tick.

        Args:
            phase_min:            Minimum value defined for this sensor in this phase.
            phase_max:            Maximum value defined for this sensor in this phase.
            degradation_progress: Normalised phase completion in [0.0, 1.0].
                                   0.0 = just entered phase, 1.0 = phase complete.

        Returns:
            Base value before noise and correlation are applied.
        """
        ...


# ---------------------------------------------------------------------------
# Monotonic Rising
# ---------------------------------------------------------------------------

class MonotonicRisingGenerator(SignalGenerator):
    """
    Value trends linearly from phase_min toward phase_max as the phase progresses.

    At degradation_progress = 0.0 → value ≈ phase_min
    At degradation_progress = 1.0 → value ≈ phase_max

    Models: vibration rising with bearing wear, temperature with thermal degradation,
    current rising with increased motor resistance.

    Adds a small random walk around the trend line for realism.
    """

    def generate(
        self,
        phase_min:            float,
        phase_max:            float,
        degradation_progress: float,
    ) -> float:
        # Linear interpolation from min to max
        trend_value = phase_min + (phase_max - phase_min) * degradation_progress
        # Small local oscillation ±1% of range_width for realism
        range_width   = phase_max - phase_min
        local_jitter  = random.uniform(-0.01, 0.01) * range_width
        return trend_value + local_jitter


# ---------------------------------------------------------------------------
# Monotonic Falling
# ---------------------------------------------------------------------------

class MonotonicFallingGenerator(SignalGenerator):
    """
    Value trends linearly from phase_max toward phase_min as the phase progresses.

    At degradation_progress = 0.0 → value ≈ phase_max
    At degradation_progress = 1.0 → value ≈ phase_min

    Models: RPM dropping as motor efficiency decreases under load and wear.
    """

    def generate(
        self,
        phase_min:            float,
        phase_max:            float,
        degradation_progress: float,
    ) -> float:
        # Linear interpolation from max to min
        trend_value = phase_max - (phase_max - phase_min) * degradation_progress
        range_width  = phase_max - phase_min
        local_jitter = random.uniform(-0.01, 0.01) * range_width
        return trend_value + local_jitter


# ---------------------------------------------------------------------------
# Oscillating Stable
# ---------------------------------------------------------------------------

class OscillatingStableGenerator(SignalGenerator):
    """
    Value oscillates around the range midpoint with constant amplitude.

    Models stable healthy operating conditions — a healthy motor has
    consistent, non-growing variance throughout the phase. Amplitude
    is fixed at 5% of the sensor range for the entire phase duration.

    Models:
      - All sensors during healthy_baseline (natural operating variance)
    """

    def __init__(self):
        pass

    def generate(
        self,
        phase_min:            float,
        phase_max:            float,
        degradation_progress: float,
    ) -> float:
        range_width = phase_max - phase_min
        midpoint    = (phase_min + phase_max) / 2.0

        # Constant amplitude — healthy motor has stable, non-growing variance
        amplitude   = 0.05 * range_width
        oscillation = random.uniform(-amplitude, amplitude)
        return midpoint + oscillation


# ---------------------------------------------------------------------------
# Digital
# ---------------------------------------------------------------------------

class DigitalGenerator(SignalGenerator):
    """
    Returns discrete 0 or 1 values.

    In healthy phases: holds steady at the normal state (1 by convention —
    motor running, protection relay closed).

    As degradation progresses, flip probability increases, modelling
    intermittent protection trips, relay chatter, or encoder glitches.

    Flip probability:
        P(flip) = 0.001 + 0.2 * degradation_progress^2

    At full degradation: ~20% chance of state flip per tick.
    """

    def __init__(self):
        self._state: int = 1   # Normal state: motor running

    def generate(
        self,
        phase_min:            float,
        phase_max:            float,
        degradation_progress: float,
    ) -> float:
        flip_probability = 0.001 + 0.2 * (degradation_progress ** 2)
        if random.random() < flip_probability:
            self._state = 1 - self._state   # toggle
        return float(self._state)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_SIGNAL_REGISTRY: dict[str, type[SignalGenerator]] = {
    BehaviorType.MONOTONIC_RISING:   MonotonicRisingGenerator,
    BehaviorType.MONOTONIC_FALLING:  MonotonicFallingGenerator,
    BehaviorType.OSCILLATING_STABLE: OscillatingStableGenerator,
    BehaviorType.DIGITAL:            DigitalGenerator,
}


def create_signal_generator(behavior_type: str) -> SignalGenerator:
    """
    Factory — creates the correct SignalGenerator by behavior type name.

    Args:
        behavior_type: One of the BehaviorType enum values.
                       Matches sensor_phase_config.behavior_type in DB.

    Returns:
        Instantiated SignalGenerator subclass.

    Raises:
        ValueError: If behavior_type is not recognised.
    """
    cls = _SIGNAL_REGISTRY.get(behavior_type)
    if cls is None:
        raise ValueError(
            f"Unknown behavior type '{behavior_type}'. "
            f"Valid options: {list(_SIGNAL_REGISTRY.keys())}"
        )
    return cls()