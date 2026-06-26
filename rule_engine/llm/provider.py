"""
rule_engine/llm/provider.py
----------------------------
Provider-agnostic LLM abstraction.

Switch providers by setting LLM_PROVIDER in the environment:
  mock       — no API key needed; fills a clinical template
  anthropic  — uses Anthropic SDK (LLM_API_KEY required)
  openai     — uses OpenAI SDK (LLM_API_KEY required)
  gemini     — uses google-genai SDK v2+ (LLM_API_KEY required)

Tier determines model selection when LLM_MODEL is not explicitly overridden:
  fast    — per-alert explanations (cheap/fast)
  quality — on-demand patient summaries (smarter/larger)
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod

try:
    import anthropic as _anthropic
    _HAS_ANTHROPIC = True
except ImportError:
    _HAS_ANTHROPIC = False

try:
    from openai import AsyncOpenAI as _AsyncOpenAI
    _HAS_OPENAI = True
except ImportError:
    _HAS_OPENAI = False

try:
    from google import genai as _genai
    _HAS_GEMINI = True
except ImportError:
    _HAS_GEMINI = False


class LLMError(Exception):
    """Raised when an LLM call fails."""


class LLMProvider(ABC):
    """One method. Every concrete implementation must support it."""

    @abstractmethod
    async def complete(self, prompt: str, max_tokens: int = 400) -> str:
        """Send prompt, return response text. Raises LLMError on failure."""


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------

_ANTHROPIC_DEFAULTS = {
    "fast":    "claude-haiku-4-5-20251001",
    "quality": "claude-sonnet-4-6",
}


class AnthropicProvider(LLMProvider):
    """Anthropic SDK provider."""

    def __init__(self, tier: str = "fast") -> None:
        if not _HAS_ANTHROPIC:
            raise LLMError("anthropic package not installed — pip install anthropic")
        api_key = os.environ.get("LLM_API_KEY", "")
        if not api_key:
            raise LLMError("LLM_API_KEY env var not set for Anthropic provider")
        self._client = _anthropic.AsyncAnthropic(api_key=api_key)
        fast_default    = _ANTHROPIC_DEFAULTS["fast"]
        quality_default = _ANTHROPIC_DEFAULTS["quality"]
        self._model = os.environ.get(
            "LLM_FAST_MODEL" if tier == "fast" else "LLM_QUALITY_MODEL",
            fast_default if tier == "fast" else quality_default,
        ) or (fast_default if tier == "fast" else quality_default)

    async def complete(self, prompt: str, max_tokens: int = 400) -> str:
        try:
            msg = await self._client.messages.create(
                model=      self._model,
                max_tokens= max_tokens,
                messages=   [{"role": "user", "content": prompt}],
            )
            return msg.content[0].text
        except Exception as exc:
            raise LLMError(f"Anthropic call failed: {exc}") from exc


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------

_OPENAI_DEFAULTS = {
    "fast":    "gpt-4o-mini",
    "quality": "gpt-4o",
}


class OpenAIProvider(LLMProvider):
    """OpenAI SDK provider."""

    def __init__(self, tier: str = "fast") -> None:
        if not _HAS_OPENAI:
            raise LLMError("openai package not installed — pip install openai")
        api_key = os.environ.get("LLM_API_KEY", "")
        if not api_key:
            raise LLMError("LLM_API_KEY env var not set for OpenAI provider")
        self._client = _AsyncOpenAI(api_key=api_key)
        fast_default    = _OPENAI_DEFAULTS["fast"]
        quality_default = _OPENAI_DEFAULTS["quality"]
        self._model = os.environ.get(
            "LLM_FAST_MODEL" if tier == "fast" else "LLM_QUALITY_MODEL",
            fast_default if tier == "fast" else quality_default,
        ) or (fast_default if tier == "fast" else quality_default)

    async def complete(self, prompt: str, max_tokens: int = 400) -> str:
        try:
            resp = await self._client.chat.completions.create(
                model=       self._model,
                max_tokens=  max_tokens,
                messages=    [{"role": "user", "content": prompt}],
            )
            return resp.choices[0].message.content or ""
        except Exception as exc:
            raise LLMError(f"OpenAI call failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------

_GEMINI_DEFAULTS = {
    "fast":    "gemini-2.0-flash",
    "quality": "gemini-2.0-flash",
}


class GeminiProvider(LLMProvider):
    """google-genai SDK v2+ provider (NOT google-generativeai)."""

    def __init__(self, tier: str = "fast") -> None:
        if not _HAS_GEMINI:
            raise LLMError("google-genai package not installed — pip install google-genai")
        api_key = os.environ.get("LLM_API_KEY", "")
        if not api_key:
            raise LLMError("LLM_API_KEY env var not set for Gemini provider")
        self._client = _genai.Client(api_key=api_key)
        fast_default    = _GEMINI_DEFAULTS["fast"]
        quality_default = _GEMINI_DEFAULTS["quality"]
        self._model = os.environ.get(
            "LLM_FAST_MODEL" if tier == "fast" else "LLM_QUALITY_MODEL",
            fast_default if tier == "fast" else quality_default,
        ) or (fast_default if tier == "fast" else quality_default)

    async def complete(self, prompt: str, max_tokens: int = 400) -> str:
        try:
            loop = __import__("asyncio").get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self._client.models.generate_content(
                    model=    self._model,
                    contents= prompt,
                )
            )
            return response.text or ""
        except Exception as exc:
            raise LLMError(f"Gemini call failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Mock (no API key needed)
# ---------------------------------------------------------------------------

class MockProvider(LLMProvider):
    """Returns a canned clinical template filled with real patient data.

    Produces realistic-looking output for demos without an API key.
    """

    async def complete(self, prompt: str, max_tokens: int = 400) -> str:
        import json, re
        ctx_match = re.search(r"\{.*\}", prompt, re.DOTALL)
        ctx: dict = {}
        if ctx_match:
            try:
                ctx = json.loads(ctx_match.group())
            except Exception:
                pass
        if "**Current Status**" in prompt or "four sections" in prompt:
            return _mock_summary(ctx)
        return _mock_alert_explanation(ctx)


def _mock_alert_explanation(ctx: dict) -> str:
    p     = ctx.get("patient", {}).get("label", "The patient")
    hr    = ctx.get("current_vitals", {}).get("heart_rate", {})
    spo2  = ctx.get("current_vitals", {}).get("spo2", {})
    bl    = ctx.get("baseline", ctx.get("personal_baseline", {}))
    hr_n  = bl.get("hr_normal", "expected range")
    trend = hr.get("trend", "stable")
    spo2v = spo2.get("value", "unknown")
    spo2t = spo2.get("trend", "stable")
    return (
        f"{p}'s heart rate is {trend} and has deviated significantly from their "
        f"established personal baseline of {hr_n}. "
        f"Oxygen saturation is currently {spo2v}% with a {spo2t} pattern "
        f"over the last 5 minutes. "
        f"This combination suggests possible haemodynamic stress; recommend clinical review "
        f"within 15 minutes and consider contacting the patient directly."
    )


def _mock_summary(ctx: dict) -> str:
    p     = ctx.get("patient", {}).get("label", "The patient")
    hr    = ctx.get("current_vitals", {}).get("heart_rate", {})
    spo2  = ctx.get("current_vitals", {}).get("spo2", {})
    hrv   = ctx.get("current_vitals", {}).get("hrv", {})
    bl    = ctx.get("personal_baseline", ctx.get("baseline", {}))

    # alert_history is the current schema; fall back to legacy key names
    ah          = ctx.get("alert_history", {})
    alerts      = ah.get("alerts", ctx.get("session_alerts", ctx.get("recent_session_alerts", [])))
    alert_count = ah.get("total_alerts_fired", len(alerts))

    if alert_count > 0:
        severities = [a.get("severity", "warning") for a in alerts]
        n_crit = sum(1 for s in severities if s in ("critical", "emergency"))
        sev_summary = f"{n_crit} critical" if n_crit else "warning-level"
        alert_text = (
            f"{alert_count} alert(s) were triggered this session "
            f"({sev_summary}). The pattern indicates an evolving clinical event."
        )
    else:
        alert_text = "No alerts were triggered this session. Vital signs are within monitored parameters."

    hrv_val = hrv.get("value") or 50
    return (
        f"**Current Status**\n"
        f"{p} is currently showing heart rate {hr.get('value','—')} bpm "
        f"({hr.get('trend','stable')}) and SpO₂ {spo2.get('value','—')}% "
        f"({spo2.get('trend','stable')}). "
        f"HRV is {hrv.get('value','—')} ms, consistent with "
        f"{'mild stress' if hrv_val < 35 else 'normal variability'}.\n\n"
        f"**Session Trend**\n"
        f"Over the session, heart rate has been {hr.get('trend','stable')} relative to "
        f"the personal baseline of {bl.get('hr_normal','expected range')}. "
        f"SpO₂ has remained {spo2.get('trend','stable')} at {spo2.get('value','—')}%, "
        f"{'within' if (spo2.get('value') or 97) >= 95 else 'below'} acceptable range.\n\n"
        f"**Alert Pattern**\n"
        f"{alert_text}\n\n"
        f"**Recommendation**\n"
        f"{'Recommend immediate clinical review and direct patient contact.' if (hr.get('value') or 70) > 105 or (spo2.get('value') or 97) < 94 else 'Continue routine monitoring. No immediate action required at this time.'} "
        f"{'Consider escalating to on-call physician if vitals do not stabilise within 15 minutes.' if (hr.get('value') or 70) > 105 else 'Reassess in 30 minutes or sooner if new alerts fire.'}"
    )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def make_provider(tier: str = "fast") -> LLMProvider:
    """Create and return a provider for the given tier.

    Reads LLM_PROVIDER env var (default: mock).
    """
    backend = os.environ.get("LLM_PROVIDER", "mock").lower()
    if backend == "anthropic":
        return AnthropicProvider(tier=tier)
    if backend == "openai":
        return OpenAIProvider(tier=tier)
    if backend == "gemini":
        return GeminiProvider(tier=tier)
    return MockProvider()
