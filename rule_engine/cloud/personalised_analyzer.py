"""
rule_engine/cloud/personalised_analyzer.py
-------------------------------------------
Isolation Forest–based personalised anomaly detector.

Replaces RuleAnalyzer as the CloudEngine's Analyzer.  For each device it:
  - LEARNING state: delegates to fallback RuleAnalyzer while collecting feature
    vectors from the incoming sensor stream.
  - ACTIVE state: scores each tick with a fitted IsolationForest and fires alerts
    when the anomaly probability exceeds the configured threshold.

SHAP TreeExplainer is fitted alongside the model and used to produce per-sensor
contribution percentages that replace the static conditions_met strings.

Configuration lives in the personalised_baseline: block of cloud_rules.yaml.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from rule_engine.cloud.analyzer import Analyzer, AnalyzerAlert
from rule_engine.cloud.config import CloudConfig
from rule_engine.cloud.rule_analyzer import RuleAnalyzer
from rule_engine.shared.logger import get_logger

logger = get_logger(__name__)

# Sensor names and feature vector layout
_HR_SENSOR  = "heart_rate"
_SPO2_SENSOR = "spo2"
_HRV_SENSOR  = "heart_rate_variability"

# Feature vector index ranges for each sensor group
_HR_IDX   = (0, 1)  # mean, std
_SPO2_IDX = (2, 3)
_HRV_IDX  = (4, 5)

_FEATURE_NAMES = [
    "HR mean", "HR std",
    "SpO₂ mean", "SpO₂ std",
    "HRV mean", "HRV std",
]

# Per-feature minimum std used when augmenting training data.
# If the patient's training period was unusually stable, these floors prevent
# the model from treating all realistic variation as anomalous.
# Order matches _FEATURE_NAMES: [hr_mean, hr_std, spo2_mean, spo2_std, hrv_mean, hrv_std]
_CLINICAL_MIN_TRAIN_STD = np.array([5.0, 2.0, 1.5, 0.5, 10.0, 5.0])

# Absolute minimum deviations from personal mean before the AI will fire.
# Acts as a hard floor so small natural fluctuations never trigger an alert,
# regardless of how tight the training baseline was.
_CLINICAL_MIN_DEV_HR   = 8.0   # bpm
_CLINICAL_MIN_DEV_SPO2 = 2.0   # %
_CLINICAL_MIN_DEV_HRV  = 12.0  # ms


@dataclass
class DeviceBaseline:
    """Per-device baseline stats exposed to the demo API and UI."""
    state:             str    # "learning" | "active"
    samples_collected: int
    samples_required:  int
    hr_mean:           float  = 0.0
    hr_std:            float  = 0.0
    spo2_mean:         float  = 0.0
    spo2_std:          float  = 0.0
    hrv_mean:          float  = 0.0
    hrv_std:           float  = 0.0
    trained_at:        float  = 0.0  # monotonic timestamp when training completed


@dataclass
class _DeviceState:
    """Internal per-device model state (not exposed externally)."""
    training_vectors:  list[list[float]] = field(default_factory=list)
    training_start:    float             = 0.0  # monotonic, set on first sample
    model:             Any               = None  # IsolationForest or None
    explainer:         Any               = None  # shap.TreeExplainer or None
    score_min:         float             = -0.5
    score_max:         float             =  0.0
    last_alert:        dict[str, float]  = field(default_factory=dict)  # rule_id → monotonic


class PersonalisedAnalyzer(Analyzer):
    """Isolation Forest anomaly detector with LEARNING → ACTIVE lifecycle."""

    def __init__(
        self,
        config:             CloudConfig,
        baseline_config:    dict,
        on_baseline_ready:  Callable[[str, dict], None] | None = None,
        mqtt_config:        dict | None = None,
    ) -> None:
        self._config          = config
        self._bc              = baseline_config
        self._on_baseline_ready = on_baseline_ready
        self._mqtt_config     = mqtt_config or {}

        self._min_samples  = int(self._bc.get("min_training_samples", 40))
        self._min_window   = float(self._bc.get("min_training_window_seconds", 90))
        self._threshold    = float(self._bc.get("anomaly_threshold", 0.70))
        self._contamination = float(self._bc.get("contamination", 0.05))
        self._win_hr       = float(self._bc.get("feature_window_hr_seconds", 30))
        self._win_hrv      = float(self._bc.get("feature_window_hrv_seconds", 120))
        self._cooldown     = float(self._bc.get("cooldown_seconds", 60))

        self._fallback     = RuleAnalyzer(config)
        self._baselines:   dict[str, DeviceBaseline] = {}
        self._state:       dict[str, _DeviceState]   = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(
        self,
        device_id:      str,
        persona_id:     str,
        sensor_buffers: dict[str, deque],
        compression:    float,
        now:            float,
    ) -> list[AnalyzerAlert]:
        """Evaluate one device tick; returns zero or more alerts."""
        if device_id not in self._state:
            self._state[device_id] = _DeviceState(training_start=time.monotonic())
            self._baselines[device_id] = DeviceBaseline(
                state="learning",
                samples_collected=0,
                samples_required=self._min_samples,
            )

        baseline = self._baselines[device_id]
        state    = self._state[device_id]

        if baseline.state == "learning":
            return self._learning_tick(device_id, persona_id, sensor_buffers, compression, now, state, baseline)
        else:
            return self._active_tick(device_id, persona_id, sensor_buffers, compression, now, state, baseline)

    def get_baseline(self, device_id: str) -> DeviceBaseline | None:
        """Return the current baseline state for a device, or None if unknown."""
        return self._baselines.get(device_id)

    def get_all_baselines(self) -> dict[str, DeviceBaseline]:
        """Return all device baselines keyed by device_id."""
        return dict(self._baselines)

    # ------------------------------------------------------------------
    # Learning phase
    # ------------------------------------------------------------------

    def _learning_tick(
        self,
        device_id:      str,
        persona_id:     str,
        sensor_buffers: dict[str, deque],
        compression:    float,
        now:            float,
        state:          _DeviceState,
        baseline:       DeviceBaseline,
    ) -> list[AnalyzerAlert]:
        vec = self._extract_features(sensor_buffers, compression, now)
        if vec is not None:
            state.training_vectors.append(vec)
            baseline.samples_collected = len(state.training_vectors)

        real_elapsed = time.monotonic() - state.training_start
        real_min_window = self._min_window / max(compression, 1.0)
        ready = (
            len(state.training_vectors) >= self._min_samples
            and real_elapsed >= real_min_window
        )
        if ready:
            self._train(device_id, persona_id, state, baseline)

        # Always delegate to static rules during LEARNING
        return self._fallback.analyze(device_id, persona_id, sensor_buffers, compression, now)

    def _train(
        self,
        device_id: str,
        persona_id: str,
        state: _DeviceState,
        baseline: DeviceBaseline,
    ) -> None:
        """Fit IsolationForest + SHAP explainer on accumulated training vectors."""
        from sklearn.ensemble import IsolationForest
        import shap

        X = np.array(state.training_vectors, dtype=float)
        n = len(X)

        # Trim outer 10% by L2 norm
        norms = np.linalg.norm(X, axis=1)
        lo = int(0.10 * n)
        hi = int(0.90 * n)
        if hi > lo:
            order = np.argsort(norms)
            X = X[order[lo:hi]]

        try:
            # Augment training data with clinical jitter so the model is calibrated
            # against realistic variation, not an artificially flat baseline.
            # If the patient was unusually stable during training, raw std per feature
            # may be near-zero — the IF score range would then be tiny, causing any
            # real-world fluctuation to normalise to anomaly=1.0 immediately.
            actual_stds = X.std(axis=0)
            jitter_stds = np.maximum(actual_stds, _CLINICAL_MIN_TRAIN_STD)
            rng = np.random.default_rng(42)
            jitter = rng.normal(0, jitter_stds, X.shape)
            X_aug = np.vstack([X, X + jitter, X - jitter])

            model = IsolationForest(
                contamination=self._contamination,
                random_state=42,
                n_estimators=100,
            )
            model.fit(X_aug)

            scores = model.score_samples(X_aug)
            state.score_min = float(scores.min())
            state.score_max = float(scores.max())
            state.model     = model

            state.explainer = shap.TreeExplainer(model)
        except Exception as exc:
            logger.error(
                "IF training failed — staying in LEARNING",
                extra={"event": "if_train_error", "device_id": device_id, "error": str(exc)},
            )
            return

        # Compute per-sensor baseline stats from trimmed training data
        baseline.hr_mean   = float(np.mean(X[:, 0]))
        baseline.hr_std    = float(np.std(X[:, 0]))
        baseline.spo2_mean = float(np.mean(X[:, 2]))
        baseline.spo2_std  = float(np.std(X[:, 2]))
        baseline.hrv_mean  = float(np.mean(X[:, 4]))
        baseline.hrv_std   = float(np.std(X[:, 4]))
        baseline.trained_at = time.monotonic()
        baseline.state      = "active"

        # Brief cooldown after transition so the first AI alert isn't immediate
        now_mono = time.monotonic()
        for ai_rule in ("H2-cloud-ai", "H3-cloud-ai", "H1-cloud-ai", "H4-cloud-ai", "H-COMBINED-ai"):
            state.last_alert[ai_rule] = now_mono - self._cooldown * 0.5

        logger.info(
            "IF model trained",
            extra={
                "event":      "if_trained",
                "device_id":  device_id,
                "persona_id": persona_id,
                "samples":    len(X),
            },
        )

        baseline_dict = self._baseline_as_dict(device_id, baseline)

        if self._on_baseline_ready is not None:
            try:
                self._on_baseline_ready(device_id, baseline_dict)
            except Exception as exc:
                logger.warning(
                    "on_baseline_ready callback failed",
                    extra={"event": "baseline_callback_error", "error": str(exc)},
                )

        self._publish_baseline(device_id, baseline_dict)

    # ------------------------------------------------------------------
    # Active phase
    # ------------------------------------------------------------------

    def _active_tick(
        self,
        device_id:      str,
        persona_id:     str,
        sensor_buffers: dict[str, deque],
        compression:    float,
        now:            float,
        state:          _DeviceState,
        baseline:       DeviceBaseline,
    ) -> list[AnalyzerAlert]:
        vec = self._extract_features(sensor_buffers, compression, now)
        if vec is None:
            return []
        return self._score_and_alert(device_id, persona_id, vec, state, baseline)

    def _score_and_alert(
        self,
        device_id: str,
        persona_id: str,
        vec:       list[float],
        state:     _DeviceState,
        baseline:  DeviceBaseline,
    ) -> list[AnalyzerAlert]:
        # Hard clinical gate — if all readings are within their minimum meaningful
        # deviation from the personal mean, skip IF scoring entirely.
        # This prevents false alerts caused by small natural fluctuations regardless
        # of how tight the training baseline was.
        hr_dev   = abs(vec[0] - baseline.hr_mean)
        spo2_dev = abs(vec[2] - baseline.spo2_mean)
        hrv_dev  = abs(vec[4] - baseline.hrv_mean)
        if (hr_dev   < _CLINICAL_MIN_DEV_HR   and
                spo2_dev < _CLINICAL_MIN_DEV_SPO2 and
                hrv_dev  < _CLINICAL_MIN_DEV_HRV):
            return []

        X = np.array([vec], dtype=float)
        try:
            raw_score = float(state.model.score_samples(X)[0])
        except Exception:
            return []

        span = state.score_max - state.score_min
        if span < 1e-9:
            anomaly = 0.0
        else:
            anomaly = 1.0 - (raw_score - state.score_min) / span
            anomaly = max(0.0, min(1.0, anomaly))

        if anomaly <= self._threshold:
            return []

        now_mono = time.monotonic()
        rule_id  = self._map_rule_id(vec, baseline)

        if now_mono - state.last_alert.get(rule_id, 0.0) < self._cooldown:
            return []
        state.last_alert[rule_id] = now_mono

        shap_conditions = self._shap_conditions_safe(state, X, vec, baseline)

        # Use a representative sensor + value for the alert metadata
        hr_value = vec[0]
        risk_score = min(round(anomaly * 100, 1), 100.0)
        risk_level = self._score_to_level(risk_score)

        return [AnalyzerAlert(
            rule_id=        rule_id,
            description=    f"Personalised anomaly — deviation from {persona_id} personal baseline",
            severity=       "warning" if risk_score < 85 else "critical",
            sensor_name=    _HR_SENSOR,
            sensor_value=   hr_value,
            threshold=      baseline.hr_mean + 2 * baseline.hr_std,
            risk_score=     risk_score,
            risk_level=     risk_level,
            conditions_met= shap_conditions,
        )]

    def _shap_conditions_safe(
        self,
        state:    _DeviceState,
        X:        "np.ndarray",
        vec:      list[float],
        baseline: DeviceBaseline,
    ) -> list[str]:
        try:
            return self._shap_conditions(state.explainer, X, vec, baseline)
        except Exception as exc:
            logger.debug(
                "SHAP computation failed — using fallback conditions",
                extra={"event": "shap_error", "error": str(exc)},
            )
            return self._fallback_conditions(vec, baseline)

    def _shap_conditions(
        self,
        explainer: Any,
        X:         "np.ndarray",
        vec:       list[float],
        baseline:  DeviceBaseline,
    ) -> list[str]:
        sv = explainer.shap_values(X)
        # sv shape: (n_samples, n_features) or list thereof
        if isinstance(sv, list):
            sv = sv[0]
        shap_row = np.abs(sv[0])

        # Group by sensor (mean+std per sensor)
        hr_contrib   = shap_row[0] + shap_row[1]
        spo2_contrib = shap_row[2] + shap_row[3]
        hrv_contrib  = shap_row[4] + shap_row[5]

        total = hr_contrib + spo2_contrib + hrv_contrib
        if total < 1e-9:
            return self._fallback_conditions(vec, baseline)

        hr_pct   = round(hr_contrib / total * 100)
        spo2_pct = round(spo2_contrib / total * 100)
        hrv_pct  = 100 - hr_pct - spo2_pct  # ensure sum=100

        # Sort by contribution (descending) for display
        groups = sorted([
            ("HR deviation from personal baseline", hr_pct),
            ("SpO₂ below personal normal", spo2_pct),
            ("HRV stress contribution", hrv_pct),
        ], key=lambda x: x[1], reverse=True)

        conditions = [f"{label}: {pct}%" for label, pct in groups]
        conditions.append(
            f"Personal HR baseline: {baseline.hr_mean:.0f}±{baseline.hr_std:.0f} bpm"
        )
        conditions.append(
            f"Personal SpO₂ baseline: {baseline.spo2_mean:.1f}±{baseline.spo2_std:.1f}%"
        )
        return conditions

    def _fallback_conditions(self, vec: list[float], baseline: DeviceBaseline) -> list[str]:
        return [
            f"HR deviation from personal baseline: current {vec[0]:.0f} vs normal {baseline.hr_mean:.0f} bpm",
            f"Personal HR baseline: {baseline.hr_mean:.0f}±{baseline.hr_std:.0f} bpm",
            f"Personal SpO₂ baseline: {baseline.spo2_mean:.1f}±{baseline.spo2_std:.1f}%",
        ]

    # ------------------------------------------------------------------
    # Feature extraction
    # ------------------------------------------------------------------

    def _extract_features(
        self,
        sensor_buffers: dict[str, deque],
        compression:    float,
        now:            float,
    ) -> list[float] | None:
        """Build [mean_HR, std_HR, mean_SpO2, std_SpO2, mean_HRV, std_HRV] or None."""
        eff_hr  = self._win_hr  / max(compression, 1.0)
        eff_hrv = self._win_hrv / max(compression, 1.0)

        hr_vals   = self._window_values(sensor_buffers, _HR_SENSOR,   eff_hr,  now)
        spo2_vals = self._window_values(sensor_buffers, _SPO2_SENSOR, eff_hr,  now)
        hrv_vals  = self._window_values(sensor_buffers, _HRV_SENSOR,  eff_hrv, now)

        if hr_vals is None or spo2_vals is None or hrv_vals is None:
            return None

        return [
            float(np.mean(hr_vals)),   float(np.std(hr_vals)),
            float(np.mean(spo2_vals)), float(np.std(spo2_vals)),
            float(np.mean(hrv_vals)),  float(np.std(hrv_vals)),
        ]

    @staticmethod
    def _window_values(
        sensor_buffers: dict[str, deque],
        sensor:         str,
        window_s:       float,
        now:            float,
    ) -> list[float] | None:
        buf = sensor_buffers.get(sensor)
        if not buf or len(buf) < 2:
            return None
        vals = [v for ts, v in buf if ts >= now - window_s]
        if len(vals) < 2:
            return None
        return vals

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _map_rule_id(self, vec: list[float], baseline: DeviceBaseline) -> str:
        """Map the most anomalous feature group to the nearest rule_id suffix."""
        hr_dev   = abs(vec[0] - baseline.hr_mean) / max(baseline.hr_std, 0.1)
        spo2_dev = abs(vec[2] - baseline.spo2_mean) / max(baseline.spo2_std, 0.1)
        hrv_dev  = abs(vec[4] - baseline.hrv_mean)  / max(baseline.hrv_std,  0.1)

        dominant = max(
            ("hr",   hr_dev),
            ("spo2", spo2_dev),
            ("hrv",  hrv_dev),
            key=lambda x: x[1],
        )[0]

        if dominant == "hr":
            return "H2-cloud-ai" if vec[0] > baseline.hr_mean else "H3-cloud-ai"
        elif dominant == "spo2":
            return "H1-cloud-ai"
        elif dominant == "hrv":
            return "H4-cloud-ai"
        return "H-COMBINED-ai"

    def _score_to_level(self, score: float) -> str:
        levels = self._config.risk_levels
        if score >= levels.get("critical", 80):
            return "critical"
        if score >= levels.get("high", 60):
            return "high"
        if score >= levels.get("medium", 30):
            return "medium"
        if score >= levels.get("low", 10):
            return "low"
        return "none"

    def _publish_baseline(self, device_id: str, baseline_dict: dict) -> None:
        """Publish baseline state to MQTT topic baselines/{device_id}."""
        if not self._mqtt_config:
            return
        import json
        import paho.mqtt.publish as mqtt_publish
        broker = self._mqtt_config.get("broker", {})
        import os
        host = os.environ.get("MQTT_HOST", broker.get("host", "localhost"))
        port = int(os.environ.get("MQTT_PORT", broker.get("port", 1883)))
        try:
            mqtt_publish.single(
                topic=   f"baselines/{device_id}",
                payload= json.dumps(baseline_dict),
                hostname=host,
                port=    port,
                qos=     0,
                retain=  True,
            )
            logger.info(
                "Baseline published to MQTT",
                extra={"event": "baseline_published", "device_id": device_id},
            )
        except Exception as exc:
            logger.warning(
                "Baseline MQTT publish failed",
                extra={"event": "baseline_publish_error", "device_id": device_id, "error": str(exc)},
            )

    @staticmethod
    def _baseline_as_dict(device_id: str, baseline: DeviceBaseline) -> dict:
        return {
            "device_id":    device_id,
            "state":        baseline.state,
            "hr_mean":      round(baseline.hr_mean, 2),
            "hr_std":       round(baseline.hr_std, 2),
            "spo2_mean":    round(baseline.spo2_mean, 2),
            "spo2_std":     round(baseline.spo2_std, 2),
            "hrv_mean":     round(baseline.hrv_mean, 2),
            "hrv_std":      round(baseline.hrv_std, 2),
            "samples_used": baseline.samples_collected,
        }
