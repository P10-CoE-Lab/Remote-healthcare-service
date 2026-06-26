"""
rule_engine/cloud/analyzer.py
------------------------------
Analyzer interface for the CloudEngine.

CloudEngine calls analyzer.analyze() on every evaluation cycle and publishes
whatever alerts are returned.  Concrete implementations can range from
simple threshold rules (RuleAnalyzer) to ML-based anomaly detectors.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field


@dataclass
class AnalyzerAlert:
    """One alert produced by an Analyzer.analyze() call."""
    rule_id:        str
    description:    str
    severity:       str
    sensor_name:    str
    sensor_value:   float
    threshold:      float
    risk_score:     float
    risk_level:     str
    conditions_met: list[str] = field(default_factory=list)


class Analyzer(ABC):
    """Strategy interface for CloudEngine evaluation."""

    @abstractmethod
    def analyze(
        self,
        device_id:      str,
        persona_id:     str,
        sensor_buffers: dict[str, deque],   # sensor_name → deque of (timestamp, value)
        compression:    float,
        now:            float,              # wall-clock time for window cutoff
    ) -> list[AnalyzerAlert]:
        """Evaluate buffered readings and return any alerts that should fire."""
