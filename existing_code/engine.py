"""
simulation/engine.py
--------------------
Multi-device simulation orchestrator for the Industrial Rotating Asset
Intelligence Platform.

SimulationEngine is the top-level coordinator. It:
  1. Loads all configuration from PostgreSQL at startup
  2. Constructs one DeviceSimulator per motor
  3. Constructs one SensorSimulator per sensor per motor
  4. Starts all sensor coroutines concurrently via asyncio.gather()
  5. Runs a device heartbeat loop that advances simulated time and checks
     for phase transitions
  6. Periodically refreshes simulation_config from the DB (for runtime changes)
  7. Persists device state to DB on graceful shutdown

Three motors are demonstrated simultaneously at different lifecycle stages:
  motor_01 — healthy_baseline (watches degrade in real time)
  motor_02 — progressive_degradation (AHI declining, RUL active)
  motor_03 — accelerated_wear (near critical, urgent recommendation)

This gives the executive demo audience an immediate, compelling dashboard:
three motors, three different health states, all monitored in real time.

Architecture note:
  SimulationEngine is intentionally stateless in terms of MQTT messages
  (it doesn't see sensor values). Its only job is orchestration, lifecycle
  management, and persistence. All intelligence (AI, AHI, RUL) is downstream
  in the ingestion/ML services that consume from MQTT.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from db.connection import get_connection, DatabaseConnection
from db.queries import (
    get_simulation_config,
    get_equipment_list,
    get_all_sensors,
    get_profile_phases,
    get_sensor_phase_configs,
    get_sensor_correlations,
    get_fault_schedule,
    upsert_device_state,
)
from mqtt.publisher import AsyncMQTTPublisher
from simulation.correlation import CorrelationEngine
from simulation.device import DeviceSimulator, PhaseSpec, SensorPhaseConfig
from simulation.fault import FaultInjector
from simulation.sensor import SensorSimulator
from utils.logger import get_logger
from utils.time import TimeCompressor

logger = get_logger(__name__)

# How often the device heartbeat loop advances time (real seconds)
HEARTBEAT_INTERVAL_SECONDS = 0.1

# How often simulation_config is refreshed from the DB (real seconds)
CONFIG_REFRESH_INTERVAL_SECONDS = 60.0


class SimulationEngine:
    """
    Top-level orchestrator for the multi-device IIoT simulation.

    One SimulationEngine instance is created by main.py and runs for the
    lifetime of the process.

    Usage:
        engine = SimulationEngine()
        await engine.start()        # blocks until cancelled
        await engine.shutdown()     # called from signal handler
    """

    def __init__(self):
        self._publisher:   AsyncMQTTPublisher | None = None
        self._compressor:  TimeCompressor | None     = None
        self._devices:     list[DeviceSimulator]     = []
        self._sim_config:  dict[str, str]            = {}
        self._running:     bool                      = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """
        Full startup sequence: load config → build devices → run all coroutines.

        Blocks until all coroutines are complete (i.e., until cancelled).
        """
        logger.info(
            "SimulationEngine starting",
            extra={"event": "engine_start"},
        )

        # 1. Load config from DB
        with DatabaseConnection() as conn:
            self._sim_config = get_simulation_config(conn)

        logger.info(
            "Simulation config loaded",
            extra={"event": "config_loaded", "config": self._sim_config},
        )

        # 2. Init time compressor
        compression_factor = float(self._sim_config.get("compression_factor", "3600"))
        self._compressor = TimeCompressor(compression_factor=compression_factor)

        # 3. Connect MQTT publisher
        broker = self._sim_config.get("mqtt_broker", "mosquitto")
        port   = int(self._sim_config.get("mqtt_port", "1883"))
        self._publisher = AsyncMQTTPublisher(broker=broker, port=port)
        self._publisher.connect()

        # 4. Build all device + sensor simulators
        await self._build_devices()

        # 5. Run everything
        self._running = True
        await self._run()

    async def shutdown(self) -> None:
        """
        Graceful shutdown: persist all device states, stop MQTT.

        Called from the signal handler in main.py.
        """
        logger.info(
            "SimulationEngine shutdown initiated",
            extra={"event": "engine_shutdown_start"},
        )
        self._running = False

        # Persist device states to DB
        await self._persist_all_device_states()

        # Stop MQTT
        if self._publisher:
            self._publisher.stop()

        logger.info(
            "SimulationEngine shutdown complete",
            extra={"event": "engine_shutdown_complete"},
        )

    # ------------------------------------------------------------------
    # Device construction
    # ------------------------------------------------------------------

    async def _build_devices(self) -> None:
        """
        Load all data from DB and construct DeviceSimulator + SensorSimulator
        instances for each motor.
        """
        with DatabaseConnection() as conn:
            equipment_list  = get_equipment_list(conn)
            all_sensors     = get_all_sensors(conn)
            profile_name    = self._sim_config.get("profile_name", "motor_degradation_v1")
            raw_phases      = get_profile_phases(conn, profile_name)
            raw_sensor_cfgs = get_sensor_phase_configs(conn, profile_name)
            correlations    = get_sensor_correlations(conn, profile_name)
            auto_loop       = self._sim_config.get("auto_loop", "true").lower() == "true"
            transition_pct  = float(self._sim_config.get("gradual_transition_pct", "0.0"))

        # Parse phases into typed objects
        phases: list[PhaseSpec] = [
            PhaseSpec(
                phase_id=        int(row["phase_id"]),
                phase_name=      row["phase_name"],
                phase_order=     int(row["phase_order"]),
                duration_hours=  float(row["duration_hours"]),
                transition=      row["transition"],
            )
            for row in raw_phases
        ]

        # Parse sensor phase configs into typed objects
        sensor_phase_cfgs: list[SensorPhaseConfig] = [
            SensorPhaseConfig(
                phase_name=      row["phase_name"],
                phase_order=     int(row["phase_order"]),
                duration_hours=  float(row["duration_hours"]),
                sensor_name=     row["sensor_name"],
                min_value=       float(row["min_value"]),
                max_value=       float(row["max_value"]),
                noise_model=     row["noise_model"],
                noise_intensity= float(row["noise_intensity"]),
                behavior_type=   row["behavior_type"],
            )
            for row in raw_sensor_cfgs
        ]

        # Index sensors by equipment_id
        sensors_by_equipment: dict[int, list[dict]] = {}
        for s in all_sensors:
            sensors_by_equipment.setdefault(int(s["equipment_id"]), []).append(s)

        # Build one DeviceSimulator + N SensorSimulators per motor
        for equip in equipment_list:
            equipment_id = int(equip["equipment_id"])

            # Fault schedule for this device
            with DatabaseConnection() as conn:
                fault_rows = get_fault_schedule(conn, equipment_id)

            fault_injector    = FaultInjector(fault_rows)
            correlation_engine = CorrelationEngine()
            correlation_engine.load_correlations(correlations)

            # Pre-load phase ranges for the starting phase
            initial_phase_cfgs = [
                {"sensor_name": c.sensor_name, "min_value": c.min_value, "max_value": c.max_value}
                for c in sensor_phase_cfgs
                if c.phase_name == equip.get("current_phase", "healthy_baseline")
            ]
            correlation_engine.update_phase_ranges(initial_phase_cfgs)

            device = DeviceSimulator(
                equipment_meta=    equip,
                phases=            phases,
                sensor_configs=    sensor_phase_cfgs,
                compressor=        self._compressor,
                initial_age_hours= float(equip.get("simulated_age_hours", 0.0)),
                auto_loop=         auto_loop,
                transition_pct=    transition_pct,
            )
            self._devices.append(device)

            # Sensor simulators for this device
            device_sensors = sensors_by_equipment.get(equipment_id, [])
            device._sensor_simulators = [   # attach for coroutine access
                SensorSimulator(
                    sensor_meta=    sensor_meta,
                    device=         device,
                    correlation=    correlation_engine,
                    fault_injector= fault_injector,
                    publisher=      self._publisher,
                    compressor=     self._compressor,
                )
                for sensor_meta in device_sensors
            ]

        logger.info(
            "All devices built",
            extra={
                "event":        "devices_built",
                "device_count": len(self._devices),
                "devices":      [d.equipment_name for d in self._devices],
            },
        )

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        """
        Launch all sensor coroutines and the device heartbeat loop concurrently.

        All tasks are gathered under a single asyncio.gather() call so that
        a CancelledError propagates to all of them simultaneously on shutdown.
        """
        sensor_tasks = [
            asyncio.create_task(
                sensor.run(),
                name=f"{device.equipment_name}_{sensor._sensor_name}",
            )
            for device in self._devices
            for sensor in device._sensor_simulators
        ]

        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(),
            name="device_heartbeat",
        )

        config_refresh_task = asyncio.create_task(
            self._config_refresh_loop(),
            name="config_refresh",
        )

        all_tasks = sensor_tasks + [heartbeat_task, config_refresh_task]

        logger.info(
            "All simulation tasks started",
            extra={
                "event":       "tasks_started",
                "sensor_tasks": len(sensor_tasks),
            },
        )

        try:
            await asyncio.gather(*all_tasks)
        except asyncio.CancelledError:
            logger.info(
                "Simulation tasks cancelled",
                extra={"event": "tasks_cancelled"},
            )
            # Cancel all individual tasks
            for task in all_tasks:
                if not task.done():
                    task.cancel()
            # Wait for cancellation to propagate
            await asyncio.gather(*all_tasks, return_exceptions=True)
            raise

    # ------------------------------------------------------------------
    # Heartbeat loop — advances time for all devices
    # ------------------------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        """
        Advance simulated time for all devices at a fixed real-time interval.

        This is separate from the sensor coroutines — sensors read device
        state; the heartbeat writes it. Using a single heartbeat loop
        (rather than per-device loops) keeps time advancement consistent
        and avoids concurrent write conflicts on DeviceSimulator state.
        """
        last_tick = asyncio.get_event_loop().time()

        while True:
            try:
                await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
                now = asyncio.get_event_loop().time()
                elapsed = now - last_tick
                last_tick = now

                for device in self._devices:
                    transitioned = await device.advance(elapsed)

                    if transitioned:
                        # Update correlation engine ranges for new phase
                        # (this is a best-effort lookup from the device's new phase)
                        # Sensor coroutines will rebuild their models on next tick
                        logger.info(
                            "Device phase changed — correlation ranges will update on next sensor tick",
                            extra={
                                "event":    "heartbeat_phase_change",
                                "equipment": device.equipment_name,
                                "new_phase": device.current_phase_name,
                            },
                        )

            except asyncio.CancelledError:
                logger.info(
                    "Heartbeat loop cancelled",
                    extra={"event": "heartbeat_cancelled"},
                )
                raise

            except Exception as exc:
                logger.error(
                    "Heartbeat loop error",
                    extra={"event": "heartbeat_error", "error": str(exc)},
                )
                # Continue running — don't let a transient error kill all sensors

    # ------------------------------------------------------------------
    # Config refresh loop
    # ------------------------------------------------------------------

    async def _config_refresh_loop(self) -> None:
        """
        Periodically refresh simulation_config from the DB.

        Enables runtime changes (e.g., compression_factor, anomaly_rate)
        without a container restart. The refresh interval is itself read
        from the config on startup.
        """
        refresh_interval = float(
            self._sim_config.get("db_config_refresh_sec", str(CONFIG_REFRESH_INTERVAL_SECONDS))
        )

        while True:
            try:
                await asyncio.sleep(refresh_interval)

                with DatabaseConnection() as conn:
                    new_config = get_simulation_config(conn)

                # Detect changes
                changed_keys = [k for k in new_config if new_config[k] != self._sim_config.get(k)]
                if changed_keys:
                    logger.info(
                        "Simulation config refreshed — changes detected",
                        extra={
                            "event":        "config_refreshed",
                            "changed_keys": changed_keys,
                        },
                    )
                    self._sim_config = new_config

                    # Update compression factor if changed
                    if "compression_factor" in changed_keys:
                        new_cf = float(new_config["compression_factor"])
                        self._compressor.compression_factor = new_cf
                        logger.info(
                            "Compression factor updated",
                            extra={
                                "event":              "compression_factor_updated",
                                "compression_factor": new_cf,
                            },
                        )

                    # Propagate auto_loop changes to all running devices
                    if "auto_loop" in changed_keys:
                        new_auto_loop = new_config["auto_loop"].lower() == "true"
                        for device in self._devices:
                            device.auto_loop = new_auto_loop
                        logger.info(
                            "auto_loop updated on all devices",
                            extra={
                                "event":     "auto_loop_updated",
                                "auto_loop": new_auto_loop,
                            },
                        )

            except asyncio.CancelledError:
                logger.info(
                    "Config refresh loop cancelled",
                    extra={"event": "config_refresh_cancelled"},
                )
                raise

            except Exception as exc:
                logger.error(
                    "Config refresh error",
                    extra={"event": "config_refresh_error", "error": str(exc)},
                )

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    async def _persist_all_device_states(self) -> None:
        """
        Write all device simulation states to the DB.

        Called during graceful shutdown so the simulation resumes from the
        correct phase and age on next startup.
        """
        try:
            with DatabaseConnection() as conn:
                for device in self._devices:
                    upsert_device_state(
                        conn=                conn,
                        equipment_id=        device.equipment_id,
                        current_phase=       device.current_phase_name,
                        simulated_age_hours= device.simulated_age_hours,
                        phase_offset_hours=  float(device._meta.get("phase_offset_hours", 0.0)),
                    )
                conn.commit()
            logger.info(
                "All device states persisted",
                extra={
                    "event":   "states_persisted",
                    "devices": [d.equipment_name for d in self._devices],
                },
            )
        except Exception as exc:
            logger.error(
                "Failed to persist device states",
                extra={"event": "state_persist_error", "error": str(exc)},
            )