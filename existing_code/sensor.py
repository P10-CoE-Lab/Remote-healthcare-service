"""
simulation/sensor.py
--------------------
Per-sensor async coroutine for the Industrial Rotating Asset Intelligence
Platform simulator.

Each physical sensor on each motor runs as its own independent asyncio
coroutine (SensorSimulator), at its own configured sampling rate.

Isolation principle:
    Each SensorSimulator has its own try/except. If one sensor fails, it
    logs the error, backs off, and retries. It never propagates exceptions
    that would cancel other sensor coroutines.

Signal generation pipeline per tick:
    1. Get current degradation_progress from DeviceSimulator
    2. Get phase-specific sensor config (min, max, noise, behavior)
    3. Generate base value using behavior type (monotonic_rising, etc.)
    4. Apply noise model (gaussian, drift, burst, quantization)
    5. If leader sensor: record value in CorrelationEngine lag buffer
    6. If follower sensor: add correlation adjustment from CorrelationEngine
    7. Apply fault transformation (dropout, flatline, spike, etc.)
    8. Build MQTT payload with full metadata
    9. Publish via AsyncMQTTPublisher
    10. Sleep for compressed sampling interval

MQTT payload schema:
    {
        "timestamp":    "2024-01-15T10:23:45.123Z",  // ISO 8601 UTC
        "value":        2.34,
        "unit":         "mm/s",
        "sensor_id":    "motor_01_vibration_rms",
        "sensor_name":  "vibration_rms",
        "equipment":    "motor_01",
        "department":   "Motor_Department",
        "line":         "Assembly_Line_1",
        "plant":        "AutoPlant_A",
        "phase":        "progressive_degradation",
        "quality":      "good",
        "fault_active": false
    }
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional

from mqtt.publisher import AsyncMQTTPublisher
from mqtt.topics import build_topic_from_dict
from simulation.correlation import CorrelationEngine
from simulation.fault import FaultInjector
from simulation.noise import NoiseModel, create_noise_model
from simulation.signal import SignalGenerator, create_signal_generator
from utils.logger import get_logger
from utils.time import TimeCompressor

logger = get_logger(__name__)

# Backoff for sensor-level error recovery
SENSOR_ERROR_BACKOFF_SECONDS = 2.0
MAX_SENSOR_BACKOFF_SECONDS   = 30.0


class SensorSimulator:
    """
    Simulates one physical sensor on one piece of equipment.

    Runs as a long-lived asyncio coroutine that publishes sensor readings
    at the configured compressed sampling rate until cancelled.

    Dependencies are injected (DeviceSimulator, CorrelationEngine, etc.)
    rather than constructed internally — keeping each component testable
    in isolation.

    Attributes:
        sensor_meta:    Dict with full sensor context from DB (sensor_name, unit, etc.)
        device:         Reference to the DeviceSimulator for phase/progress queries.
        correlation:    Shared CorrelationEngine for the device.
        fault_injector: Shared FaultInjector for the device.
        publisher:      Shared AsyncMQTTPublisher.
        compressor:     Shared TimeCompressor.
    """

    def __init__(
        self,
        sensor_meta:    dict[str, Any],
        device:         Any,                  # DeviceSimulator (avoid circular import)
        correlation:    CorrelationEngine,
        fault_injector: FaultInjector,
        publisher:      AsyncMQTTPublisher,
        compressor:     TimeCompressor,
    ):
        self._meta         = sensor_meta
        self._device       = device
        self._correlation  = correlation
        self._faults       = fault_injector
        self._publisher    = publisher
        self._compressor   = compressor

        self._sensor_name  = sensor_meta["sensor_name"]
        self._sensor_code  = sensor_meta["sensor_code"]
        self._equipment    = sensor_meta["equipment"]
        self._unit         = sensor_meta.get("unit", "")
        self._interval_ms  = int(sensor_meta["sampling_interval_ms"])

        self._topic        = build_topic_from_dict(sensor_meta)

        # Per-sensor stateful components (reset on phase transition)
        self._signal_gen:  Optional[SignalGenerator] = None
        self._noise_model: Optional[NoiseModel]      = None
        self._current_phase: str = ""

        # For flatline/dropout: track last published value
        self._last_value:  Optional[float] = None

        logger.info(
            "SensorSimulator created",
            extra={
                "event":      "sensor_created",
                "equipment":  self._equipment,
                "sensor":     self._sensor_name,
                "topic":      self._topic,
                "interval_ms": self._interval_ms,
            },
        )

    # ------------------------------------------------------------------
    # Main coroutine
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """
        Main async sensor loop.

        Runs indefinitely until cancelled. Each iteration:
        - Refreshes signal/noise components if phase has changed
        - Generates, noises, correlates, and fault-checks the value
        - Publishes to MQTT
        - Sleeps for the compressed sampling interval

        Error isolation: any exception within the loop is caught, logged,
        and the sensor backs off before retrying. The coroutine never raises.
        """
        backoff = SENSOR_ERROR_BACKOFF_SECONDS

        while True:
            try:
                await self._tick()
                backoff = SENSOR_ERROR_BACKOFF_SECONDS   # reset on success

                sleep_secs = self._compressor.ms_to_real_seconds(self._interval_ms)
                await asyncio.sleep(sleep_secs)

            except asyncio.CancelledError:
                logger.info(
                    "Sensor coroutine cancelled",
                    extra={
                        "event":    "sensor_cancelled",
                        "equipment": self._equipment,
                        "sensor":   self._sensor_name,
                    },
                )
                raise   # Must re-raise CancelledError — do not swallow

            except Exception as exc:
                logger.error(
                    "Sensor tick error — backing off",
                    extra={
                        "event":      "sensor_tick_error",
                        "equipment":  self._equipment,
                        "sensor":     self._sensor_name,
                        "error":      str(exc),
                        "backoff_sec": backoff,
                    },
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, MAX_SENSOR_BACKOFF_SECONDS)

    # ------------------------------------------------------------------
    # Single tick
    # ------------------------------------------------------------------

    async def _tick(self) -> None:
        """Execute one sensor sampling tick."""
        # 1. Get phase and progress from device
        phase_name, phase_config, progress = self._device.get_sensor_state(self._sensor_name)

        if phase_config is None:
            logger.debug(
                "No phase config available yet — skipping tick",
                extra={"event": "sensor_no_config", "sensor": self._sensor_name},
            )
            return

        # 2. Rebuild signal/noise models if phase changed
        if phase_name != self._current_phase:
            self._rebuild_models(phase_name, phase_config)

        # 3. Extract range parameters
        phase_min   = float(phase_config["min_value"])
        phase_max   = float(phase_config["max_value"])
        range_width = phase_max - phase_min

        # 4. Generate base value from behavior type
        base_value = self._signal_gen.generate(
            phase_min=            phase_min,
            phase_max=            phase_max,
            degradation_progress= progress,
        )

        # 5. Apply noise model
        noisy_value = self._noise_model.apply(
            base_value=  base_value,
            range_width= range_width,
        )

        # 6. Get simulated time from device (seconds)
        sim_time_seconds = self._device.simulated_age_hours * 3600.0

        # 7. If this is a leader sensor: record for followers
        if self._correlation.is_leader(self._sensor_name):
            self._correlation.record_leader_value(
                sensor_name=self._sensor_name,
                value=      noisy_value,
                sim_time=   sim_time_seconds,
            )

        # 8. If this is a follower: add correlation adjustment
        adjustment = self._correlation.get_follower_adjustment(
            sensor_name=self._sensor_name,
            sim_time=   sim_time_seconds,
        )
        correlated_value = noisy_value + adjustment

        # 9. Apply fault transformation
        sim_hours = self._device.simulated_age_hours
        fault_result = self._faults.evaluate(
            sensor_name=        self._sensor_name,
            clean_value=        correlated_value,
            current_sim_hours=  sim_hours,
            last_known_value=   self._last_value,
            compression_factor= self._compressor.compression_factor,
        )

        # 10. Skip publish on dropout
        if fault_result.is_dropout:
            logger.debug(
                "Sensor dropout — skipping publish",
                extra={
                    "event":    "sensor_dropout",
                    "equipment": self._equipment,
                    "sensor":   self._sensor_name,
                    "sim_hour": round(sim_hours, 2),
                },
            )
            return

        # Physical floor: no real sensor on this motor can read below zero
        final_value = round(max(0.0, fault_result.value), 4)
        self._last_value = final_value

        # 11. Build payload
        payload = self._build_payload(
            value=       final_value,
            phase=       phase_name,
            quality=     fault_result.quality,
            fault_active=fault_result.fault_active,
        )

        # 12. Publish
        self._publisher.publish(self._topic, payload)

        logger.debug(
            "Sensor value published",
            extra={
                "event":      "value_published",
                "equipment":  self._equipment,
                "sensor":     self._sensor_name,
                "phase":      phase_name,
                "value":      final_value,
                "unit":       self._unit,
                "topic":      self._topic,
                "sim_hour":   round(sim_hours, 2),
                "progress":   round(progress, 3),
                "fault":      fault_result.fault_type,
                "quality":    fault_result.quality,
            },
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _rebuild_models(self, phase_name: str, phase_config: dict) -> None:
        """
        Instantiate new signal generator and noise model for the new phase.

        Called when a phase transition is detected. The old models are
        discarded — stateful models (drift, oscillating) start fresh
        in the new phase, which is correct behaviour.
        """
        behavior_type = phase_config["behavior_type"]
        noise_type    = phase_config["noise_model"]
        noise_int     = float(phase_config["noise_intensity"])

        self._signal_gen  = create_signal_generator(behavior_type)
        self._noise_model = create_noise_model(noise_type, noise_int)
        self._current_phase = phase_name

        logger.info(
            "Sensor models rebuilt for new phase",
            extra={
                "event":         "sensor_phase_transition",
                "equipment":     self._equipment,
                "sensor":        self._sensor_name,
                "new_phase":     phase_name,
                "behavior_type": behavior_type,
                "noise_model":   noise_type,
                "noise_intensity": noise_int,
            },
        )

    def _build_payload(
        self,
        value:        float,
        phase:        str,
        quality:      str,
        fault_active: bool,
    ) -> dict[str, Any]:
        """
        Construct the MQTT payload dict following the UNS payload schema.

        All metadata is included so subscribers (ingestion service, Grafana,
        ML inference) have full context without needing to join other data.
        """
        return {
            "timestamp":    datetime.now(tz=timezone.utc).isoformat(),
            "value":        value,
            "unit":         self._unit,
            "sensor_id":    self._sensor_code,
            "sensor_name":  self._sensor_name,
            "equipment":    self._equipment,
            "department":   self._meta["department"],
            "line":         self._meta["line"],
            "plant":        self._meta["plant"],
            "phase":        phase,              # ground-truth phase label (for ML validation)
            "quality":      quality,            # good | uncertain | bad
            "fault_active": fault_active,
        }