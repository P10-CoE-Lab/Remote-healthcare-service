"""
simulation/noise.py
-------------------
Noise model implementations for the Industrial Rotating Asset Intelligence
Platform simulator.

Each noise model transforms a clean base signal value to produce realistic
sensor output that mirrors physical measurement characteristics.

Four models are supported (matching sensor_phase_config.noise_model):

  gaussian     — White noise. Models electronic/thermal sensor noise.
                 Most common model for healthy, stable sensors.

  drift        — Random walk accumulated offset. Models calibration drift,
                 thermal expansion effects, and slow systematic errors.
                 Characteristic of temperature sensors under load.

  burst        — Occasional large transients. Models intermittent mechanical
                 events (bearing defects, load spikes). Becomes more frequent
                 in degradation phases.

  quantization — Stepwise output. Models low-resolution ADCs or digital
                 measurement systems with limited precision.

Usage:
    from simulation.noise import GaussianNoise, DriftNoise, BurstNoise, QuantizationNoise

    model = GaussianNoise(intensity=0.02)
    noisy_value = model.apply(base_value=2.5, range_width=2.0)
"""

from __future__ import annotations

import math
import random
from abc import ABC, abstractmethod
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class NoiseModel(ABC):
    """
    Abstract base class for all noise models.

    Subclasses implement apply() to transform a clean signal value.

    Attributes:
        intensity: Dimensionless scale factor (0.0–1.0 typically).
                   Interpretation varies by model — see subclass docs.
    """

    def __init__(self, intensity: float):
        """
        Args:
            intensity: Noise scale factor from sensor_phase_config.noise_intensity.
        """
        self.intensity = intensity

    @abstractmethod
    def apply(self, base_value: float, range_width: float) -> float:
        """
        Apply noise to a base signal value.

        Args:
            base_value:  Clean signal value before noise.
            range_width: The sensor's (max - min) for this phase.
                         Used to scale noise relative to signal range.

        Returns:
            Noisy signal value. May exceed phase min/max slightly — callers
            should not hard-clamp as realistic sensors occasionally read
            outside expected ranges.
        """
        ...

    def reset(self) -> None:
        """Reset any stateful noise accumulator (e.g. drift offset)."""
        pass


# ---------------------------------------------------------------------------
# Gaussian noise — white noise, zero-mean
# ---------------------------------------------------------------------------

class GaussianNoise(NoiseModel):
    """
    Zero-mean Gaussian (white) noise.

    sigma = intensity * range_width

    This models:
      - Electronic noise in ADC circuits
      - Thermal noise in sensor electronics
      - Quantisation noise from high-resolution ADCs

    Appropriate for: healthy baseline, low-degradation phases.
    """

    def apply(self, base_value: float, range_width: float) -> float:
        sigma = self.intensity * range_width
        noise = random.gauss(0.0, sigma)
        return base_value + noise


# ---------------------------------------------------------------------------
# Drift noise — correlated random walk
# ---------------------------------------------------------------------------

class DriftNoise(NoiseModel):
    """
    Accumulated random walk drift.

    Each call adds a small random step to an internal accumulator.
    The accumulator mean-reverts weakly toward zero to prevent unbounded
    runaway over long simulations.

    This models:
      - Calibration drift in temperature sensors
      - Slow bias from thermal expansion
      - Sensor aging effects

    Appropriate for: temperature in early/progressive degradation phases.
    """

    def __init__(self, intensity: float):
        super().__init__(intensity)
        self._accumulated: float = 0.0

    def apply(self, base_value: float, range_width: float) -> float:
        # Step size proportional to intensity and range
        step_sigma = self.intensity * range_width * 0.1
        step = random.gauss(0.0, step_sigma)

        # Weak mean reversion: pull drift back toward zero at 2% per tick
        self._accumulated = self._accumulated * 0.98 + step

        # Cap accumulated drift at ±25% of range_width to prevent runaway
        max_drift = range_width * 0.25
        self._accumulated = max(-max_drift, min(max_drift, self._accumulated))

        return base_value + self._accumulated

    def reset(self) -> None:
        """Reset accumulated drift at phase transitions."""
        self._accumulated = 0.0


# ---------------------------------------------------------------------------
# Burst noise — intermittent transients
# ---------------------------------------------------------------------------

class BurstNoise(NoiseModel):
    """
    Intermittent large-amplitude transients (bursts).

    With a small probability each tick, a large additive spike is injected.
    Spike magnitude scales with intensity and phase range.

    This models:
      - Bearing surface defects creating impulse vibration events
      - Electrical arcing events in current measurement
      - Mechanical impact loads

    Appropriate for: accelerated_wear and failure_event phases.

    Attributes:
        burst_probability: Probability per tick that a burst occurs.
                           Scales with intensity (higher intensity → more frequent).
        burst_scale:       Multiplier for burst magnitude relative to range_width.
    """

    def __init__(self, intensity: float):
        super().__init__(intensity)
        # Probability scales with intensity: 0.12 intensity → ~12% chance per tick
        self.burst_probability = min(intensity, 0.5)
        self.burst_scale = 2.0 + intensity * 3.0   # 2x–3.5x range_width spike

    def apply(self, base_value: float, range_width: float) -> float:
        if random.random() < self.burst_probability:
            # Burst: large spike, sign randomly positive or negative
            sign        = random.choice([-1, 1])
            burst_mag   = self.burst_scale * range_width * random.uniform(0.3, 1.0)
            return base_value + sign * burst_mag
        # No burst this tick — small background gaussian still present
        background_sigma = self.intensity * range_width * 0.05
        return base_value + random.gauss(0.0, background_sigma)


# ---------------------------------------------------------------------------
# Quantization noise — stepwise discrete output
# ---------------------------------------------------------------------------

class QuantizationNoise(NoiseModel):
    """
    Quantisation noise — rounds the signal to a discrete resolution grid.

    resolution = intensity * range_width

    Higher intensity → coarser resolution → more visible steps.

    This models:
      - Low-bit-depth ADC output (8-bit industrial sensors)
      - Protocol-limited precision (e.g. Modbus RTU register scaling)
      - Digital encoders with limited counts-per-revolution
    """

    def apply(self, base_value: float, range_width: float) -> float:
        resolution = max(self.intensity * range_width, 1e-9)   # avoid div/0
        quantised  = round(base_value / resolution) * resolution
        return quantised


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_NOISE_REGISTRY: dict[str, type[NoiseModel]] = {
    "gaussian":     GaussianNoise,
    "drift":        DriftNoise,
    "burst":        BurstNoise,
    "quantization": QuantizationNoise,
}


def create_noise_model(noise_type: str, intensity: float) -> NoiseModel:
    """
    Factory function — creates the correct NoiseModel subclass by name.

    Args:
        noise_type: One of 'gaussian', 'drift', 'burst', 'quantization'.
                    Matches sensor_phase_config.noise_model values in DB.
        intensity:  Noise intensity factor from sensor_phase_config.

    Returns:
        Instantiated NoiseModel subclass.

    Raises:
        ValueError: If noise_type is not recognised.
    """
    cls = _NOISE_REGISTRY.get(noise_type)
    if cls is None:
        raise ValueError(
            f"Unknown noise model '{noise_type}'. "
            f"Valid options: {list(_NOISE_REGISTRY.keys())}"
        )
    return cls(intensity=intensity)