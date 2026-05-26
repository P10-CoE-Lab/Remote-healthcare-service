"""
Rules Engine Module

This module provides rule-based monitoring and alerting functionality for the
Human Vitals Simulator. It operates independently of the core simulation engine
and can be integrated with various data ingestion layers.

Components:
- engine: Core rules evaluation and management
- loader: Configuration loading and validation
"""

from .engine import RulesEngine
from .loader import (
    load_rules_config,
    get_default_rules_config,
    RulesConfig,
    Rule,
    RuleCondition,
    RuleAction,
    RulesConfigError
)
from .risk_loader import (
    load_risk_scoring_config,
    get_default_risk_scoring_config,
    RiskScoringConfig,
    RiskRule,
    RiskConfigError,
)
from .risk_scoring import RiskScoringEngine

__all__ = [
    'RulesEngine',
    'load_rules_config',
    'get_default_rules_config',
    'RulesConfig',
    'Rule',
    'RuleCondition',
    'RuleAction',
    'RulesConfigError',
    'load_risk_scoring_config',
    'get_default_risk_scoring_config',
    'RiskScoringConfig',
    'RiskRule',
    'RiskConfigError',
    'RiskScoringEngine',
]