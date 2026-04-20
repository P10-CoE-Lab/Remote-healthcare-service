"""
backfill/generator.py
---------------------
Per-motor historical data generator for the backfill script.

Generates raw sensor telemetry for all phases up to a motor's
computed phase_offset_hours, writing past-timestamped data directly
to InfluxDB.

Reuses existing simulation modules without modification:
  - simulation/signal.py      → create_signal_generator() + generate()
  - simulation/noise.py       → create_noise_model() + apply()
  - simulation/correlation.py → CorrelationEngine

Phase iteration logic:
  - Reads phases in phase_order
  - Iterates until cumulative duration reaches offset_hours
  - Last phase may be partial — generates only the remaining hours
  - On resume: skips phases up to and including resume_from_phase,
    deletes only partial (incomplete) phase data from InfluxDB,
    continues from the next phase

Timestamps are calculated backwards from now:
  history_start = now - timedelta(hours=offset_hours)
  Each tick advances forward by sampling_interval_ms

Resumability:
  After each phase completes, calls upsert_backfill_progress() to
  record the checkpoint. On rerun with resume_from_phase set, already-
  completed phases are skipped without deleting or rewriting their data.

Progress tracking:
  Returns (last_phase_name, last_timestamp, total_points_written)
  so the caller can write device_simulation_state and demo_snapshot.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

from influxdb_client import Point

from simulation.signal import create_signal_generator
from simulation.noise import create_noise_model
from simulation.correlation import CorrelationEngine
from db.queries import upsert_backfill_progress
from utils.logger import get_logger

logger = get_logger(__name__)

BATCH_SIZE = 5000


class MotorGenerator:
    """
    Generates complete historical telemetry for one motor.

    For each phase up to offset_hours:
      1. Determines full or partial phase duration
      2. Generates per-sensor values tick by tick
      3. Applies signal behavior, noise, and inter-sensor correlation
      4. Writes batches of Points to InfluxDB via BackfillWriter
      5. Records phase checkpoint to backfill_progress after each phase

    Args:
        motor:             Equipment dict from get_equipment_list().
        sensors:           Sensor dicts from get_sensors_for_equipment().
        phases:            All phase dicts from get_profile_phases().
        phase_configs:     All sensor_phase_config dicts.
        correlations:      Correlation rules from get_sensor_correlations().
        writer:            BackfillWriter instance.
        pg_conn:           Open Postgres connection for progress updates.
        equipment_id:      Equipment primary key.
        offset_hours:      Computed phase_offset_hours for this motor.
        resume_from_phase: If set, skip phases up to and including this one.
        prior_points:      Points already written before resuming.
    """

    def __init__(
        self,
        motor:             dict[str, Any],
        sensors:           list[dict[str, Any]],
        phases:            list[dict[str, Any]],
        phase_configs:     list[dict[str, Any]],
        correlations:      list[dict[str, Any]],
        writer,
        pg_conn,
        equipment_id:      int,
        offset_hours:      float,
        resume_from_phase: str | None = None,
        prior_points:      int        = 0,
        transition_pct:    float      = 0.0,
    ):
        self._motor             = motor
        self._sensors           = sensors
        self._phases            = phases
        self._phase_configs     = phase_configs
        self._correlations      = correlations
        self._writer            = writer
        self._pg_conn           = pg_conn
        self._equipment_id      = equipment_id
        self._equipment_name    = motor["equipment"]
        self._offset_hours      = offset_hours
        self._resume_from_phase = resume_from_phase
        self._prior_points      = prior_points
        self._transition_pct    = transition_pct

        # Build lookup: phase_name → {sensor_name → config dict}
        self._phase_sensor_map = self._build_phase_sensor_map()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def generate(self) -> tuple[str, datetime | None, int]:
        """
        Generate all historical data for this motor and write to InfluxDB.

        Returns:
            Tuple of (last_phase_name, last_timestamp, total_points_written).
            Returns ("", None, 0) if nothing was generated.
        """
        now           = datetime.now(tz=timezone.utc)
        history_start = now - timedelta(hours=self._offset_hours)
        history_days  = self._offset_hours / 24.0

        phase_plan    = self._build_phase_plan()

        if not phase_plan:
            logger.warning(
                "Empty phase plan — nothing to generate",
                extra={"event": "empty_phase_plan", "equipment": self._equipment_name},
            )
            return ("", None, 0)

        # Determine which phases to actually generate (skip completed ones)
        phases_to_generate, skip_duration = self._apply_resume(phase_plan)

        if not phases_to_generate:
            logger.info(
                "All phases already completed — nothing to generate",
                extra={"event": "all_phases_complete", "equipment": self._equipment_name},
            )
            return ("", None, 0)

        partial_flag = phases_to_generate[-1]["partial"]
        print(
            f"{self._equipment_name}: offset={self._offset_hours:.2f}h "
            f"({history_days:.1f} days) | {len(phases_to_generate)} phase(s) to generate"
            + (" (last partial)" if partial_flag else "")
        )
        logger.info(
            "Starting motor generation",
            extra={
                "event":              "motor_generation_start",
                "equipment":          self._equipment_name,
                "offset_hours":       self._offset_hours,
                "phases_to_generate": len(phases_to_generate),
                "resuming":           self._resume_from_phase is not None,
            },
        )

        # On fresh start: delete all existing motor data for idempotency
        # On resume: skip delete — already-written phases are kept
        if self._resume_from_phase is None:
            self._writer.delete_motor_data(self._equipment_name)

        total_points   = self._prior_points
        # phase_start_ts for the first phase to generate
        phase_start_ts = history_start + timedelta(hours=skip_duration)
        last_phase     = ""
        last_ts: datetime | None = None

        for idx, entry in enumerate(phases_to_generate):
            phase_name          = entry["phase_name"]
            duration_hours      = entry["duration_hours"]
            full_duration_hours = entry["full_duration_hours"]
            last_phase          = phase_name

            # Determine next phase name for smooth transitions
            next_phase_name = None
            if self._transition_pct > 0 and idx < len(phases_to_generate) - 1:
                next_phase_name = phases_to_generate[idx + 1]["phase_name"]

            points_written, phase_last_ts = self._generate_phase(
                phase_name=          phase_name,
                duration_hours=      duration_hours,
                full_duration_hours= full_duration_hours,
                phase_start_ts=      phase_start_ts,
                next_phase_name=     next_phase_name,
            )

            total_points  += points_written
            if phase_last_ts:
                last_ts = phase_last_ts

            phase_start_ts = phase_start_ts + timedelta(hours=duration_hours)

            # Record checkpoint after each completed phase
            upsert_backfill_progress(
                conn=                 self._pg_conn,
                equipment_id=         self._equipment_id,
                status=               "running",
                last_completed_phase= phase_name,
                total_points_written= total_points,
            )
            self._pg_conn.commit()

            logger.info(
                "Phase checkpoint saved",
                extra={
                    "event":        "phase_checkpoint",
                    "equipment":    self._equipment_name,
                    "phase":        phase_name,
                    "points":       points_written,
                    "total_so_far": total_points,
                },
            )

        print(
            f"  {self._equipment_name} complete — {total_points:,} total points written"
        )
        logger.info(
            "Motor generation complete",
            extra={
                "event":        "motor_generation_complete",
                "equipment":    self._equipment_name,
                "total_points": total_points,
                "last_phase":   last_phase,
            },
        )

        return (last_phase, last_ts, total_points)

    # ------------------------------------------------------------------
    # Phase plan — which phases to generate and for how long
    # ------------------------------------------------------------------

    def _build_phase_plan(self) -> list[dict[str, Any]]:
        """
        Build an ordered list of phase entries covering offset_hours.

        Each entry has: phase_name, duration_hours, partial (bool).
        The last entry may be partial if offset_hours falls mid-phase.

        Returns:
            List of phase plan dicts.
        """
        plan       = []
        cumulative = 0.0
        offset     = self._offset_hours

        for phase in sorted(self._phases, key=lambda p: p["phase_order"]):
            if cumulative >= offset:
                break

            remaining     = offset - cumulative
            full_duration = float(phase["duration_hours"])

            if cumulative + full_duration <= offset:
                plan.append({
                    "phase_name":          phase["phase_name"],
                    "duration_hours":      full_duration,
                    "full_duration_hours": full_duration,
                    "partial":             False,
                })
                cumulative += full_duration
            else:
                plan.append({
                    "phase_name":          phase["phase_name"],
                    "duration_hours":      remaining,
                    "full_duration_hours": full_duration,   # true full phase duration
                    "partial":             True,
                })
                break

        return plan

    def _apply_resume(
        self,
        phase_plan: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], float]:
        """
        Filter the phase plan for resumability.

        If resume_from_phase is set, skip all phases up to and including
        that phase and return the remaining phases to generate.

        Also returns the cumulative duration of skipped phases so the
        caller can calculate the correct phase_start_ts.

        Args:
            phase_plan: Full phase plan from _build_phase_plan().

        Returns:
            Tuple of (phases_to_generate, skipped_duration_hours).
        """
        if self._resume_from_phase is None:
            return phase_plan, 0.0

        skip_duration = 0.0
        for i, entry in enumerate(phase_plan):
            skip_duration += entry["duration_hours"]
            if entry["phase_name"] == self._resume_from_phase:
                remaining = phase_plan[i + 1:]
                logger.info(
                    "Resume: skipping completed phases",
                    extra={
                        "event":          "resume_skip",
                        "equipment":      self._equipment_name,
                        "skipped_phases": [e["phase_name"] for e in phase_plan[:i+1]],
                        "remaining":      [e["phase_name"] for e in remaining],
                    },
                )
                return remaining, skip_duration

        # resume_from_phase not found in plan — regenerate everything
        logger.warning(
            "resume_from_phase not found in phase plan — regenerating from start",
            extra={
                "event":             "resume_phase_not_found",
                "equipment":         self._equipment_name,
                "resume_from_phase": self._resume_from_phase,
            },
        )
        return phase_plan, 0.0

    # ------------------------------------------------------------------
    # Per-phase generation
    # ------------------------------------------------------------------

    def _generate_phase(
        self,
        phase_name:          str,
        duration_hours:      float,
        full_duration_hours: float,
        phase_start_ts:      datetime,
        next_phase_name:     str | None = None,
    ) -> tuple[int, datetime | None]:
        """
        Generate all sensor data for one phase and write to InfluxDB.

        Supports smooth phase transitions: in the last N% of the phase (where N is
        transition_pct), sensor config values smoothly interpolate between current
        and next phase bounds.

        Args:
            phase_name:          e.g. 'healthy_baseline'
            duration_hours:      Hours to actually generate (may be partial).
            full_duration_hours: True full phase duration from phase_definition.
                                 Used for progress calculation so partial phases
                                 never reach progress=1.0 — preventing the
                                 end-of-phase signal surge in partial generations.
            phase_start_ts:      UTC datetime this phase begins.
            next_phase_name:     Name of next phase for transition blending.
                                 If None or transition_pct=0, no blending occurs.

        Returns:
            Tuple of (points_written, last_timestamp).
        """
        # DEBUG: Log transition configuration
        logger.info(
            "Starting _generate_phase",
            extra={
                "event":              "phase_generation_start",
                "equipment":          self._equipment_name,
                "phase":              phase_name,
                "next_phase":         next_phase_name,
                "transition_pct":     self._transition_pct,
                "duration_hours":     duration_hours,
            },
        )
        sensor_configs = self._phase_sensor_map.get(phase_name, {})
        if not sensor_configs:
            logger.warning(
                "No sensor config for phase — skipping",
                extra={
                    "event":     "phase_config_missing",
                    "equipment": self._equipment_name,
                    "phase":     phase_name,
                },
            )
            return 0, None

        min_interval_ms   = min(s["sampling_interval_ms"] for s in self._sensors)
        est_per_sensor    = int(duration_hours * 3600 / (min_interval_ms / 1000))
        est_total         = est_per_sensor * len(self._sensors)

        print(
            f"  {self._equipment_name} | {phase_name} | ~{est_total:,} points"
        )

        correlation = CorrelationEngine()
        correlation.load_correlations(self._correlations)
        correlation.update_phase_ranges([
            {
                "sensor_name": name,
                "min_value":   cfg["min_value"],
                "max_value":   cfg["max_value"],
            }
            for name, cfg in sensor_configs.items()
        ])

        signal_generators = {
            s["sensor_name"]: create_signal_generator(
                sensor_configs[s["sensor_name"]]["behavior_type"]
            )
            for s in self._sensors
            if s["sensor_name"] in sensor_configs
        }
        noise_models = {
            s["sensor_name"]: create_noise_model(
                sensor_configs[s["sensor_name"]]["noise_model"],
                sensor_configs[s["sensor_name"]]["noise_intensity"],
            )
            for s in self._sensors
            if s["sensor_name"] in sensor_configs
        }

        all_points:    list[Point]       = []
        total_written: int               = 0
        batch_count:   int               = 0
        total_batches: int               = math.ceil(est_total / BATCH_SIZE) if est_total > 0 else 1
        last_ts:       datetime | None   = None

        # Get next phase sensor configs if we're doing smooth transitions
        next_phase_configs = {}
        if self._transition_pct > 0 and next_phase_name:
            next_phase_configs = self._phase_sensor_map.get(next_phase_name, {})

        for sensor in self._sensors:
            sensor_name = sensor["sensor_name"]
            if sensor_name not in sensor_configs:
                continue

            interval_ms          = sensor["sampling_interval_ms"]
            interval_sec         = interval_ms / 1000.0
            duration_sec         = duration_hours * 3600.0
            full_duration_sec    = full_duration_hours * 3600.0
            total_ticks          = int(duration_sec / interval_sec)
            sensor_cfg           = sensor_configs[sensor_name]
            phase_min            = sensor_cfg["min_value"]
            phase_max            = sensor_cfg["max_value"]
            range_width          = phase_max - phase_min
            generator            = signal_generators[sensor_name]
            noise                = noise_models[sensor_name]

            for tick in range(total_ticks):
                # Progress is always relative to the FULL phase duration.
                # This ensures partial phases never reach progress=1.0,
                # preventing the end-of-phase signal surge.
                progress   = (tick * interval_sec) / full_duration_sec
                sim_time   = tick * interval_sec

                # Apply smooth transition blending if in transition window
                blended_min = phase_min
                blended_max = phase_max

                if self._transition_pct > 0 and progress >= (1.0 - self._transition_pct):
                    # In transition window — blend with next phase config
                    next_cfg = next_phase_configs.get(sensor_name)
                    if next_cfg:
                        # DEBUG: Log first transition encountered
                        if not hasattr(self, '_transition_logged'):
                            logger.info(
                                "Transition blending active",
                                extra={
                                    "event":         "transition_active",
                                    "equipment":     self._equipment_name,
                                    "phase":         phase_name,
                                    "sensor":        sensor_name,
                                    "progress":     progress,
                                    "transition_pct": self._transition_pct,
                                },
                            )
                            self._transition_logged = True
                        # Calculate alpha: 0.0 at transition start → 1.0 at phase end
                        transition_start = 1.0 - self._transition_pct
                        alpha = (progress - transition_start) / self._transition_pct
                        alpha = max(0.0, min(1.0, alpha))

                        # Linearly interpolate numeric bounds
                        blended_min = phase_min + (next_cfg["min_value"] - phase_min) * alpha
                        blended_max = phase_max + (next_cfg["max_value"] - phase_max) * alpha

                        # Update range width for noise
                        range_width = blended_max - blended_min

                base_value  = generator.generate(blended_min, blended_max, progress)
                noisy_value = noise.apply(base_value, range_width)

                correlation.record_leader_value(sensor_name, noisy_value, sim_time)
                adjustment  = correlation.get_follower_adjustment(sensor_name, sim_time)
                final_value = noisy_value + adjustment

                ts = phase_start_ts + timedelta(seconds=tick * interval_sec)
                if last_ts is None or ts > last_ts:
                    last_ts = ts

                point = self._writer.build_point(
                    sensor=    sensor,
                    value=     final_value,
                    phase=     phase_name,
                    timestamp= ts,
                )
                all_points.append(point)

                if len(all_points) >= BATCH_SIZE:
                    batch_count += 1
                    self._writer.write_batch(all_points, batch_count, total_batches)
                    total_written += len(all_points)
                    all_points = []

        if all_points:
            batch_count += 1
            self._writer.write_batch(all_points, batch_count, total_batches)
            total_written += len(all_points)

        return total_written, last_ts

    # ------------------------------------------------------------------
    # Phase sensor map builder
    # ------------------------------------------------------------------

    def _build_phase_sensor_map(self) -> dict[str, dict[str, Any]]:
        """
        Build lookup: phase_name → {sensor_name → config dict}.

        Constructed once at init from get_sensor_phase_configs() results.

        Returns:
            Nested dict for O(1) config lookup during generation.
        """
        result: dict[str, dict[str, Any]] = {}
        for cfg in self._phase_configs:
            phase  = cfg["phase_name"]
            sensor = cfg["sensor_name"]
            result.setdefault(phase, {})[sensor] = cfg
        return result