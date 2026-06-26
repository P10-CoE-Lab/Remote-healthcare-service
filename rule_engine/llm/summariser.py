"""
rule_engine/llm/summariser.py
------------------------------
Calls the LLM provider with the two clinical prompt templates.

Mode A — per-alert explanation: 3–4 sentence clinical narrative (fast tier).
Mode B — on-demand patient summary: four-section clinical briefing (quality tier).
"""

from __future__ import annotations

import json
from typing import Any

from rule_engine.llm.provider import LLMError, LLMProvider, make_provider
from rule_engine.shared.logger import get_logger

logger = get_logger(__name__)

_FAST_PROVIDER:    LLMProvider | None = None
_QUALITY_PROVIDER: LLMProvider | None = None


def _fast() -> LLMProvider:
    global _FAST_PROVIDER
    if _FAST_PROVIDER is None:
        _FAST_PROVIDER = make_provider("fast")
    return _FAST_PROVIDER


def _quality() -> LLMProvider:
    global _QUALITY_PROVIDER
    if _QUALITY_PROVIDER is None:
        _QUALITY_PROVIDER = make_provider("quality")
    return _QUALITY_PROVIDER


_ALERT_PROMPT = """\
You are a clinical decision support assistant for a remote cardiac monitoring system.
A personalised AI model has detected an anomaly for the patient below.

Write a clinical explanation in 3–4 sentences for the attending nurse. Use plain clinical
language. Explain: (1) what was detected relative to this patient's personal normal,
(2) the trend over the last few minutes, (3) the clinical significance, (4) a suggested
immediate action.

Do not mention Isolation Forest, SHAP, anomaly scores, or machine learning terms.
Do not repeat the numbers from the data verbatim — interpret them clinically.

Patient data:
{context_json}"""


_SUMMARY_PROMPT = """\
You are a senior clinical monitoring assistant reviewing a patient's remote monitoring session.
Write a concise clinical briefing for the attending clinician. Structure your response in exactly
these four sections, each 2–3 sentences:

**Current Status**: What is the patient's condition right now based on the latest vitals?
**Session Trend**: How have vitals changed from the start to now across the full monitoring session? Are things stable, improving, or worsening?
**Alert Pattern**: Look at the alert_history section of the data. The field total_alerts_fired gives the total number of alerts triggered this session; the alerts list gives the details of each one. Report the count, describe the severity pattern, and state what it suggests clinically. If total_alerts_fired is 0 and the alerts list is empty, then state that no alerts were triggered.
**Recommendation**: What immediate clinical action do you recommend?

Write in plain clinical language. Do not mention machine learning, AI, anomaly scores, or SHAP.
Do not start any sentence with "The patient". Use the patient's label (e.g. "Patient 001").

Patient data:
{context_json}"""


async def explain_alert(context: dict[str, Any]) -> str:
    """Generate a 3–4 sentence clinical explanation for an alert (fast tier)."""
    prompt = _ALERT_PROMPT.format(context_json=json.dumps(context, indent=2))
    try:
        return await _fast().complete(prompt, max_tokens=300)
    except LLMError as exc:
        logger.warning("LLM alert explanation failed", extra={"event": "llm_error", "error": str(exc)})
        return _fallback_explanation(context)


async def generate_summary(context: dict[str, Any]) -> str:
    """Generate a four-section patient briefing on demand (quality tier)."""
    prompt = _SUMMARY_PROMPT.format(context_json=json.dumps(context, indent=2))
    try:
        return await _quality().complete(prompt, max_tokens=600)
    except LLMError as exc:
        logger.warning("LLM summary failed", extra={"event": "llm_error", "error": str(exc)})
        return _fallback_summary(context)


def _fallback_explanation(ctx: dict) -> str:
    p    = ctx.get("patient", {}).get("label", "The patient")
    hr   = ctx.get("current_vitals", {}).get("heart_rate", {})
    spo2 = ctx.get("current_vitals", {}).get("spo2", {})
    bl   = ctx.get("baseline", {})
    return (
        f"{p}'s heart rate is {hr.get('trend','changing')} and has deviated from their "
        f"established baseline of {bl.get('hr_normal','expected range')}. "
        f"Oxygen saturation is {spo2.get('value','—')}% with a {spo2.get('trend','changing')} pattern. "
        f"Recommend clinical review within 15 minutes."
    )


def _fallback_summary(ctx: dict) -> str:
    p    = ctx.get("patient", {}).get("label", "Patient")
    hr   = ctx.get("current_vitals", {}).get("heart_rate", {})
    spo2 = ctx.get("current_vitals", {}).get("spo2", {})
    return (
        f"**Current Status**\n{p} vitals: HR {hr.get('value','—')} bpm ({hr.get('trend','stable')}), "
        f"SpO₂ {spo2.get('value','—')}% ({spo2.get('trend','stable')}).\n\n"
        f"**Recent Trend**\nVitals have been {hr.get('trend','stable')} over the last 5 minutes.\n\n"
        f"**Alert History**\nSession alert activity is logged in the alert feed.\n\n"
        f"**Recommendation**\nContinue routine monitoring and reassess as needed."
    )
