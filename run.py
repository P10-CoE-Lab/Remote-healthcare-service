"""
run.py
------
Single entry point for the Vitals Simulator.

Usage:
    python run.py \\
      --persona personas/welder_factory.yaml \\
      --scenario scenarios/worker/fatigue_escalation.yaml \\
      [--compression 60] \\
      [--demo]

        # Batch mode: run every persona once with an auto-matched scenario
        python run.py \\
            --all-personas \\
            [--personas-dir personas] \\
            [--scenarios-dir scenarios] \\
            [--compression 60]

When --demo is set, the FastAPI demo control UI is started alongside
the simulation engine on DEMO_API_PORT (default 8000).

Signal handling:
    SIGTERM and SIGINT both trigger a graceful shutdown within 5 seconds.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
from pathlib import Path
from typing import Any

import yaml

from simulator.core.scenario_loader import load_scenario
from simulator.core.scenario_engine import ScenarioEngine
from simulator.core.persona_loader import load_persona
from simulator.sensors.base import SensorReading
from simulator.transport.mqtt_publisher import MQTTPublisher
from simulator.utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> dict:
    """Load a YAML config file, returning an empty dict if not found."""
    if not path.exists():
        logger.warning(
            "Config file not found — using defaults",
            extra={"event": "config_not_found", "path": str(path)},
        )
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


# ---------------------------------------------------------------------------
# Publish callback
# ---------------------------------------------------------------------------

def _make_publish_callback(publisher: MQTTPublisher, engine: ScenarioEngine):
    """Create the async publish callback passed to the engine."""
    async def publish(
        reading:    SensorReading,
        persona_id: str,
        poc_type:   str,
        device_id:  str,
    ) -> None:
        status = engine.get_status()
        publisher.publish(
            reading=       reading,
            persona_id=    persona_id,
            poc_type=      poc_type,
            device_id=     device_id,
            sequence_num=  status.sequence_number,
            compression=   status.compression,
        )
    return publish


def _discover_persona_files(personas_dir: Path) -> list[Path]:
    """Return all persona YAMLs except template files."""
    if not personas_dir.exists():
        raise FileNotFoundError(f"Personas directory not found: {personas_dir}")

    persona_files = sorted(
        path for path in personas_dir.glob("*.yaml")
        if not path.name.startswith("_")
    )
    if not persona_files:
        raise RuntimeError(f"No persona files found in: {personas_dir}")
    return persona_files


def _discover_scenarios_by_persona(scenarios_dir: Path) -> dict[str, list[Path]]:
    """Index scenario files by the persona id they target."""
    if not scenarios_dir.exists():
        raise FileNotFoundError(f"Scenarios directory not found: {scenarios_dir}")

    result: dict[str, list[Path]] = {}
    for scenario_path in sorted(scenarios_dir.rglob("*.yaml")):
        if scenario_path.name.startswith("_"):
            continue
        raw: dict[str, Any] | None = yaml.safe_load(
            scenario_path.read_text(encoding="utf-8")
        )
        if not isinstance(raw, dict):
            continue
        persona_id = raw.get("persona")
        if not isinstance(persona_id, str) or not persona_id:
            continue
        result.setdefault(persona_id, []).append(scenario_path)
    return result


def _choose_default_scenario(persona_id: str, scenario_map: dict[str, list[Path]]) -> Path:
    """Pick a stable default scenario for one persona."""
    candidates = sorted(scenario_map.get(persona_id, []))
    if not candidates:
        raise RuntimeError(
            f"No scenario found for persona '{persona_id}'. "
            "Add a scenario YAML with matching 'persona' field."
        )

    # Prefer normal/resting baselines when available for predictable demos.
    preferred_keywords = ("normal", "resting", "shift")
    for keyword in preferred_keywords:
        for candidate in candidates:
            if keyword in candidate.stem.lower():
                return candidate

    return candidates[0]


def _load_run_matrix(matrix_path: Path) -> list[tuple[Path, Path]]:
    """Load persona+scenario run pairs from a single YAML matrix file."""
    resolved_matrix_path = _resolve_run_matrix_path(matrix_path)

    raw = yaml.safe_load(resolved_matrix_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise RuntimeError(f"Run matrix must be a YAML mapping: {resolved_matrix_path}")

    runs = raw.get("runs")
    if not isinstance(runs, list) or not runs:
        raise RuntimeError(
            f"Run matrix must contain a non-empty 'runs' list: {resolved_matrix_path}"
        )

    pairs: list[tuple[Path, Path]] = []
    for idx, item in enumerate(runs):
        if not isinstance(item, dict):
            raise RuntimeError(f"runs[{idx}] must be a mapping in {resolved_matrix_path}")

        persona = item.get("persona")
        scenarios = item.get("scenarios")
        if not isinstance(persona, str) or not persona:
            raise RuntimeError(f"runs[{idx}].persona must be a string in {resolved_matrix_path}")
        if not isinstance(scenarios, list) or not scenarios:
            raise RuntimeError(
                f"runs[{idx}].scenarios must be a non-empty list in {resolved_matrix_path}"
            )

        persona_path = Path(persona)
        for s_idx, scenario in enumerate(scenarios):
            if not isinstance(scenario, str) or not scenario:
                raise RuntimeError(
                    f"runs[{idx}].scenarios[{s_idx}] must be a string in {resolved_matrix_path}"
                )
            pairs.append((persona_path, Path(scenario)))

    return pairs


def _resolve_run_matrix_path(matrix_path: Path) -> Path:
    """Resolve a run-matrix path with support for config/ fallback."""
    resolved_matrix_path = matrix_path
    if not resolved_matrix_path.exists() and not resolved_matrix_path.is_absolute():
        config_fallback = Path("config") / resolved_matrix_path
        if config_fallback.exists():
            resolved_matrix_path = config_fallback

    if not resolved_matrix_path.exists():
        raise FileNotFoundError(f"Run matrix file not found: {matrix_path}")

    return resolved_matrix_path


def _load_run_matrix_runtime(matrix_path: Path) -> dict[str, Any]:
    """Load optional runtime metadata from a run matrix file."""
    resolved_matrix_path = _resolve_run_matrix_path(matrix_path)
    raw = yaml.safe_load(resolved_matrix_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return {}

    runtime = raw.get("runtime")
    if runtime is None:
        return {}
    if not isinstance(runtime, dict):
        raise RuntimeError(f"'runtime' must be a mapping in {resolved_matrix_path}")

    return runtime


def _select_demo_run_from_matrix(
    run_plan: list[tuple[Path, Path]],
    runtime: dict[str, Any],
) -> tuple[Path, Path, str | None]:
    """Pick the demo run entry from matrix runtime metadata or default to first."""
    default_demo = runtime.get("default_demo")
    reason: str | None = None

    if isinstance(default_demo, dict):
        persona = default_demo.get("persona")
        scenario = default_demo.get("scenario")
        reason_raw = default_demo.get("reason")
        if isinstance(reason_raw, str) and reason_raw.strip():
            reason = reason_raw.strip()

        if isinstance(persona, str) and isinstance(scenario, str):
            return Path(persona), Path(scenario), reason

    if not run_plan:
        raise RuntimeError("Run matrix does not contain any runnable entries")

    return run_plan[0][0], run_plan[0][1], reason


async def _run_single_simulation(
    *,
    persona_path: Path,
    scenario_path: Path,
    compression: float | None,
    demo: bool,
    sim_config: dict,
    mqtt_config: dict,
) -> None:
    """Execute one persona+scenario run until completion or shutdown signal."""
    engine = ScenarioEngine(
        sim_config=sim_config,
        mqtt_config=mqtt_config,
    )
    engine.load(persona_path=persona_path, scenario_path=scenario_path)

    if compression is not None:
        engine.set_compression(compression)

    publisher = MQTTPublisher(mqtt_config)
    publisher.connect()
    engine.set_mqtt_publisher(publisher)

    publish_cb = _make_publish_callback(publisher, engine)

    loop = asyncio.get_event_loop()
    shutdown_event = asyncio.Event()

    def _handle_signal():
        logger.info("Shutdown signal received", extra={"event": "shutdown_signal"})
        shutdown_event.set()

    import platform
    if platform.system() != "Windows":
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _handle_signal)
    else:
        signal.signal(signal.SIGINT, lambda s, f: _handle_signal())

    logger.info(
        "Simulator starting",
        extra={
            "event": "simulator_start",
            "persona": str(persona_path),
            "scenario": str(scenario_path),
            "demo": demo,
        },
    )

    engine_task = asyncio.create_task(engine.start(publish_cb))

    demo_task = None
    if demo:
        demo_task = asyncio.create_task(_start_demo_api(engine))

    shutdown_task = asyncio.create_task(shutdown_event.wait())
    wait_tasks = [shutdown_task] if demo else [engine_task, shutdown_task]
    await asyncio.wait(wait_tasks, return_when=asyncio.FIRST_COMPLETED)

    await engine.stop()
    if demo_task and not demo_task.done():
        demo_task.cancel()
        try:
            await demo_task
        except asyncio.CancelledError:
            pass

    publisher.stop()
    logger.info("Simulator shutdown complete", extra={"event": "simulator_shutdown"})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Vitals Simulator — health and safety sensor data simulator"
    )
    parser.add_argument(
        "--persona",
        default=None,
        help="Path to persona YAML file (e.g. personas/welder_factory.yaml)",
    )
    parser.add_argument(
        "--scenario",
        default=None,
        help="Path to scenario YAML file (e.g. scenarios/worker/fatigue_escalation.yaml)",
    )
    parser.add_argument(
        "--all-personas",
        action="store_true",
        help="Run all personas sequentially with auto-matched scenarios",
    )
    parser.add_argument(
        "--run-matrix",
        default=None,
        help="Path to YAML file containing persona+scenario runs to execute sequentially",
    )
    parser.add_argument(
        "--personas-dir",
        default="personas",
        help="Directory containing persona YAML files (default: personas)",
    )
    parser.add_argument(
        "--scenarios-dir",
        default="scenarios",
        help="Directory used to auto-match scenarios in --all-personas mode",
    )
    parser.add_argument(
        "--compression",
        type=float,
        default=None,
        help="Time compression factor override (overrides scenario YAML value)",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Start the FastAPI demo control UI alongside the simulation",
    )
    parser.add_argument(
        "--population",
        action="store_true",
        help=(
            "Start in population mode: empty fleet, no initial persona/scenario. "
            "Use the demo API (/patients/add) to add patients dynamically. "
            "Implies --demo."
        ),
    )
    args = parser.parse_args()

    # --population implies --demo and is exclusive with other modes
    if args.population:
        if args.persona or args.scenario:
            parser.error("--persona/--scenario cannot be used with --population")
        if args.all_personas:
            parser.error("--all-personas cannot be used with --population")
        if args.run_matrix:
            parser.error("--run-matrix cannot be used with --population")
        args.demo = True   # population mode always starts the demo API
    else:
        selected_modes = int(bool(args.all_personas)) + int(bool(args.run_matrix))
        if selected_modes > 1:
            parser.error("Use only one batch mode: either --all-personas or --run-matrix")

        if args.all_personas:
            if args.persona:
                parser.error("--persona cannot be used with --all-personas")
            if args.scenario:
                parser.error("--scenario cannot be used with --all-personas")
            if args.demo:
                parser.error("--demo is not supported with --all-personas")
        elif args.run_matrix:
            if args.persona or args.scenario:
                parser.error("--persona/--scenario cannot be used with --run-matrix")
        else:
            if not args.persona or not args.scenario:
                parser.error("--persona and --scenario are required unless --all-personas or --population is set")

    # Load config files
    config_dir  = Path("config")
    sim_config  = _load_yaml(config_dir / "simulator_config.yaml")
    mqtt_config = _load_yaml(config_dir / "mqtt_config.yaml")

    if args.population:
        await _run_population_mode(
            sim_config=  sim_config,
            mqtt_config= mqtt_config,
        )
        return

    if args.all_personas:
        personas_dir = Path(args.personas_dir)
        scenarios_dir = Path(args.scenarios_dir)

        persona_files = _discover_persona_files(personas_dir)
        scenario_map = _discover_scenarios_by_persona(scenarios_dir)

        run_plan: list[tuple[Path, Path]] = []
        for persona_path in persona_files:
            persona = load_persona(persona_path)
            scenario_path = _choose_default_scenario(persona.persona_id, scenario_map)
            run_plan.append((persona_path, scenario_path))

        logger.info(
            "Batch persona run starting",
            extra={
                "event": "batch_start",
                "count": len(run_plan),
                "personas": [str(p) for p, _ in run_plan],
            },
        )

        failures: list[str] = []
        for persona_path, scenario_path in run_plan:
            try:
                await _run_single_simulation(
                    persona_path=persona_path,
                    scenario_path=scenario_path,
                    compression=args.compression,
                    demo=False,
                    sim_config=sim_config,
                    mqtt_config=mqtt_config,
                )
            except Exception as exc:
                failure = (
                    f"persona={persona_path} scenario={scenario_path} error={exc}"
                )
                failures.append(failure)
                logger.error("Batch run failed", extra={"event": "batch_failure", "detail": failure})

        if failures:
            for failure in failures:
                logger.error("Batch failure", extra={"event": "batch_failure_detail", "detail": failure})
            raise RuntimeError(
                f"Batch run finished with {len(failures)} failure(s). See logs for details."
            )

        logger.info("Batch persona run completed", extra={"event": "batch_complete"})
        return

    if args.run_matrix:
        matrix_path = Path(args.run_matrix)
        run_plan = _load_run_matrix(matrix_path)
        runtime = _load_run_matrix_runtime(matrix_path)

        if args.demo:
            demo_port = runtime.get("demo_api_port")
            if demo_port is not None:
                os.environ["DEMO_API_PORT"] = str(demo_port)

            demo_persona, demo_scenario, demo_reason = _select_demo_run_from_matrix(
                run_plan,
                runtime,
            )
            if demo_reason:
                logger.info(
                    "Demo run selected from matrix",
                    extra={
                        "event": "matrix_demo_selected",
                        "persona": str(demo_persona),
                        "scenario": str(demo_scenario),
                        "reason": demo_reason,
                    },
                )

            await _run_single_simulation(
                persona_path=demo_persona,
                scenario_path=demo_scenario,
                compression=args.compression,
                demo=True,
                sim_config=sim_config,
                mqtt_config=mqtt_config,
            )
            return

        logger.info(
            "Run matrix execution starting",
            extra={
                "event": "matrix_start",
                "count": len(run_plan),
                "matrix": args.run_matrix,
            },
        )

        failures: list[str] = []
        for persona_path, scenario_path in run_plan:
            try:
                await _run_single_simulation(
                    persona_path=persona_path,
                    scenario_path=scenario_path,
                    compression=args.compression,
                    demo=False,
                    sim_config=sim_config,
                    mqtt_config=mqtt_config,
                )
            except Exception as exc:
                failure = (
                    f"persona={persona_path} scenario={scenario_path} error={exc}"
                )
                failures.append(failure)
                logger.error("Run matrix item failed", extra={"event": "matrix_failure", "detail": failure})

        if failures:
            for failure in failures:
                logger.error("Run matrix failure", extra={"event": "matrix_failure_detail", "detail": failure})
            raise RuntimeError(
                f"Run matrix finished with {len(failures)} failure(s). See logs for details."
            )

        logger.info("Run matrix execution completed", extra={"event": "matrix_complete"})
        return

    await _run_single_simulation(
        persona_path=Path(args.persona),
        scenario_path=Path(args.scenario),
        compression=args.compression,
        demo=args.demo,
        sim_config=sim_config,
        mqtt_config=mqtt_config,
    )


async def _run_population_mode(
    *,
    sim_config: dict,
    mqtt_config: dict,
) -> None:
    """Start the demo API with an empty fleet; patients are added dynamically."""
    try:
        import uvicorn
        from demo.demo_api import create_app
        from simulator.transport.mqtt_publisher import MQTTPublisher

        publisher = MQTTPublisher(mqtt_config)
        publisher.connect()

        port = int(os.environ.get("DEMO_API_PORT", 8000))
        app = create_app(
            engine=None,
            sim_config=      sim_config,
            mqtt_config=     mqtt_config,
            shared_publisher=publisher,
        )

        loop = asyncio.get_event_loop()
        shutdown_event = asyncio.Event()

        def _handle_signal():
            logger.info("Shutdown signal received", extra={"event": "shutdown_signal"})
            shutdown_event.set()

        import platform
        if platform.system() != "Windows":
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, _handle_signal)
        else:
            signal.signal(signal.SIGINT, lambda s, f: _handle_signal())

        config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")
        server = uvicorn.Server(config)

        logger.info(
            "Population mode starting — fleet is empty, use /patients/add",
            extra={"event": "population_mode_start", "port": port},
        )

        server_task = asyncio.create_task(server.serve())
        shutdown_task = asyncio.create_task(shutdown_event.wait())
        await asyncio.wait([server_task, shutdown_task], return_when=asyncio.FIRST_COMPLETED)

        server.should_exit = True
        publisher.stop()
        logger.info("Population mode shutdown complete", extra={"event": "population_mode_shutdown"})

    except ImportError:
        logger.error(
            "uvicorn not installed — population mode unavailable. Add uvicorn to requirements.txt.",
            extra={"event": "population_mode_import_error"},
        )
    except Exception as exc:
        logger.error(
            "Population mode error",
            extra={"event": "population_mode_error", "error": str(exc)},
        )


async def _start_demo_api(engine) -> None:
    """Start the FastAPI demo control UI in an asyncio-compatible server."""
    try:
        import uvicorn
        from demo.demo_api import create_app

        port = int(os.environ.get("DEMO_API_PORT", 8000))
        app  = create_app(engine)

        config = uvicorn.Config(
            app,
            host="0.0.0.0",
            port=port,
            log_level="warning",   # suppress uvicorn access logs
        )
        server = uvicorn.Server(config)

        logger.info(
            "Demo API starting",
            extra={"event": "demo_api_start", "port": port},
        )
        await server.serve()
    except ImportError:
        logger.error(
            "uvicorn not installed — demo API unavailable. "
            "Add uvicorn to requirements.txt.",
            extra={"event": "demo_api_import_error"},
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error(
            "Demo API error",
            extra={"event": "demo_api_error", "error": str(exc)},
        )


if __name__ == "__main__":
    asyncio.run(main())
