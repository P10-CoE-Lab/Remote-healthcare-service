# Feature Implementation Plan — AI/ML Enhancements
# Read this before starting any implementation work.

## Purpose of this file
This is the single source of truth for implementing two AI/ML features on top of the existing
Remote Healthcare Monitoring system. The implementation chat should read only this file plus
the files listed in each section — do not re-read the entire codebase.

---

## What is already working (do not touch)

- Multi-patient simulator: `./start.sh` starts everything. Simulator publishes sensor readings
  via MQTT → Telegraf → InfluxDB. Grafana dashboards live at port 3000.
- Rule engine (`rule_engine/`): subscribes to MQTT, runs two evaluation layers:
  - **Edge engine**: fires instantly when a threshold is crossed (HR > 100, SpO₂ < 93%, etc.)
  - **Cloud engine**: evaluates sustained patterns over rolling windows (30s, 120s). Publishes
    alerts to `alerts/cloud/{persona_id}/{rule_id}`.
- Alert notifier: receives cloud alerts via MQTT, sends email via Notification Service.
- Demo API at port 8000: fleet management, patient vitals, alert feed.
- React UI: two views — **Client view** (what a client sees, display-only) and **Operator view**
  (add/remove patients, trigger clinical events).
- Two patient card components: `PatientCard.tsx` (operator) and `ClientPatientCard.tsx` (client).

## Files that must NOT be modified
- `docker/telegraf.conf`
- `docker/grafana/dashboards/`
- `Notification_Service/`
- `simulator/` (anything under this directory)
- `rule_engine/edge/` (edge engine stays static — only cloud layer is personalised)

---

## Architecture: how the new features plug in

```
MQTT (vitals stream)
      │
      ▼
CloudEngine (rule_engine/cloud/engine.py)
  - Maintains rolling sensor buffers per device
  - Calls analyzer.analyze() on every tick
      │
      ▼
PersonalisedAnalyzer  ← NEW — replaces RuleAnalyzer
  - LEARNING state: delegates to fallback RuleAnalyzer
  - ACTIVE state: runs Isolation Forest model per device
  - Returns AnalyzerAlert objects (same interface as before)
      │
      ▼
CloudEngine publishes RuleAlert to MQTT (unchanged)
      │
      ├──→ AlertNotifier (existing)
      │       └──→ context_builder.py  ← NEW — enriches alert with LLM explanation
      │               └──→ LLM (claude-haiku) → explanation added to email + alert
      │
      └──→ Demo API alert buffer (existing)
              └──→ GET /patients/{id}/summary  ← NEW endpoint
                      └──→ LLM (claude-sonnet) → full clinical narrative
```

One-line change to wire it up in `rule_engine/service.py`:
```python
# Before:
from rule_engine.cloud.rule_analyzer import RuleAnalyzer
analyzer = RuleAnalyzer(cloud_config)

# After:
from rule_engine.cloud.personalised_analyzer import PersonalisedAnalyzer
analyzer = PersonalisedAnalyzer(cloud_config, baseline_config)
```

---

## Feature 1 — Personalised AI Baseline (Isolation Forest)

### What it solves
Static population thresholds (HR > 100, SpO₂ < 93%) fire false alarms on patients with
different personal normals. The Isolation Forest learns each patient's normal pattern from
their first few minutes of readings, then detects deviations from their personal baseline
instead of a fixed number.

### Algorithm overview

**Step 1 — Feature extraction (called every evaluation cycle)**
From the current sensor buffers, build one feature vector:
```
[mean_HR_window, std_HR_window,
 mean_SpO2_window, std_SpO2_window,
 mean_HRV_window, std_HRV_window]
```
All windows are compression-adjusted: `effective_window = window_seconds / max(compression, 1.0)`
Use 30s for HR and SpO₂, 120s for HRV — same as the existing cloud rules.
Skip a window if fewer than 2 readings exist in it (same guard as RuleAnalyzer).

**Step 2 — Training phase (LEARNING state)**
Accumulate feature vectors in a per-device list until:
- At least `min_training_samples` vectors collected (configurable, default 40), AND
- At least `min_training_window_seconds` of real time has passed (configurable, default 90s)

During LEARNING, delegate all `analyze()` calls to the fallback `RuleAnalyzer` (static rules
run normally — patients get alerts and the UI shows live data from the start).

When training threshold is reached:
1. Trim outliers: drop top 10% and bottom 10% of vectors by total magnitude before fitting
2. Fit `IsolationForest(contamination=0.05, random_state=42)` on the trimmed set
3. Fit `shap.TreeExplainer(model)` on the same training data
4. Record per-sensor baseline stats (mean and std of training data) for UI display
5. Transition to ACTIVE state. Copy cooldown state from fallback RuleAnalyzer.
6. Log the transition with device_id and number of training samples used.

**Step 3 — Detection phase (ACTIVE state)**
On each `analyze()` call:
1. Extract current feature vector
2. Score it: `score = model.score_samples([vec])[0]` — higher = more normal
3. Convert to anomaly probability: `anomaly = 1 - (score - min_score) / (max_score - min_score)`
   where min/max are computed from training data during fit
4. If `anomaly > anomaly_threshold` (configurable, default 0.7):
   a. Compute SHAP values: `shap_values = explainer.shap_values([vec])[0]`
   b. Convert SHAP values to feature contribution percentages (abs value, normalised to sum 100%)
   c. Build `AnalyzerAlert` with SHAP-derived `conditions_met`
   d. Apply cooldown check before returning alert

**The conditions_met field changes from rule text to personal context:**
```python
# Before (RuleAnalyzer):
conditions_met = ["heart_rate > 100 sustained 30s"]

# After (PersonalisedAnalyzer):
conditions_met = [
    "HR deviation from personal baseline: 58%",
    "SpO₂ below personal normal: 31%",
    "HRV stress contribution: 11%",
    f"Personal HR baseline: {mean_hr:.0f}±{std_hr:.0f} bpm",
    f"Personal SpO₂ baseline: {mean_spo2:.1f}±{std_spo2:.1f}%",
]
```
This is what feeds the LLM in Feature 2.

### Handling the rule_id for MQTT compatibility
The MQTT alert schema expects a `rule_id`. Map the dominant SHAP contributor to the nearest
existing rule:
- HR is dominant → `"H2-cloud-ai"` (if anomaly direction is high) or `"H3-cloud-ai"` (low)
- SpO₂ is dominant → `"H1-cloud-ai"`
- HRV is dominant → `"H4-cloud-ai"`
- No single dominant feature → `"H-COMBINED-ai"`

Use `"-ai"` suffix so existing Telegraf/Grafana filtering still works (they filter by prefix).

### Configuration — add to cloud_rules.yaml
```yaml
# Add this block to the bottom of config/cloud_rules.yaml
personalised_baseline:
  enabled: true
  min_training_samples: 40          # feature vectors needed before training
  min_training_window_seconds: 90   # real-time minimum before training
  anomaly_threshold: 0.70           # 0–1, how anomalous before alert fires
  contamination: 0.05               # IsolationForest contamination param
  feature_window_hr_seconds: 30     # window for HR/SpO2 features
  feature_window_hrv_seconds: 120   # window for HRV features
  cooldown_seconds: 60              # per-device cooldown between AI alerts
```

### New file: rule_engine/cloud/personalised_analyzer.py
Key class structure:
```python
@dataclass
class DeviceBaseline:
    """Per-device baseline stats for UI display."""
    state: str              # "learning" | "active"
    samples_collected: int
    samples_required: int
    hr_mean: float
    hr_std: float
    spo2_mean: float
    spo2_std: float
    hrv_mean: float
    hrv_std: float
    trained_at: float       # monotonic timestamp when training completed

class PersonalisedAnalyzer(Analyzer):
    def __init__(self, config: CloudConfig, baseline_config: dict) -> None: ...
    def analyze(self, device_id, persona_id, sensor_buffers, compression, now) -> list[AnalyzerAlert]: ...
    def get_baseline(self, device_id: str) -> DeviceBaseline | None: ...
    def get_all_baselines(self) -> dict[str, DeviceBaseline]: ...
    # Internal:
    def _extract_features(self, sensor_buffers, compression, now) -> list[float] | None: ...
    def _train(self, device_id: str) -> None: ...
    def _score_and_alert(self, device_id, persona_id, vec, compression, now) -> list[AnalyzerAlert]: ...
    def _shap_conditions(self, shap_values, vec, baseline) -> list[str]: ...
```

### New API endpoint: GET /patients/{patient_id}/baseline
Add to `demo/demo_api.py`. Needs access to the analyzer instance.

**How to share the analyzer with the API:** Pass it into `create_app()` as a new optional kwarg.
In `service.py`, the analyzer is instantiated before `create_app()` is called... 

Wait — the Demo API and rule_engine/service.py are separate processes. The demo API cannot call
`analyzer.get_baseline()` directly.

**Solution:** PersonalisedAnalyzer publishes its baseline state to a small shared dict that the
demo API can read — or simpler: add a `GET /baseline` HTTP endpoint to a tiny internal server
running inside the rule engine process, and have the demo API proxy it.

**Simpler solution for POC:** Publish baseline state to MQTT as a special topic
`baselines/{device_id}` whenever a device transitions to ACTIVE. Demo API subscribes to this
topic and caches the baseline per device_id. The patient status endpoint then includes it.

**Baseline MQTT payload** (published once when ACTIVE state is reached):
```json
{
  "device_id": "sim-cardiac_patient-a3f1",
  "patient_label": "Patient 001",
  "state": "active",
  "hr_mean": 67.2, "hr_std": 4.1,
  "spo2_mean": 97.4, "spo2_std": 0.8,
  "hrv_mean": 48.3, "hrv_std": 7.2,
  "samples_used": 43
}
```
Topic: `baselines/{device_id}`

Demo API subscribes to `baselines/#` in the same subscriber started by `_start_alert_subscriber()`.
Stores in `_patient_baselines: dict[str, dict]` (module-level, like `_patient_risk`).

### API changes: extend patient status response
`_build_patient_status()` already builds the patient dict. Add:
```python
baseline = _patient_baselines.get(patient_id, {})
return {
    ...existing fields...,
    "baseline_state": baseline.get("state", "learning"),   # "learning" | "active"
    "baseline_info": baseline,   # full baseline dict or {}
}
```

### New dependencies
```
scikit-learn>=1.3.0
shap>=0.44.0
```

---

## Feature 2 — LLM Clinical Summaries

### Two modes

**Mode A — Per-alert explanation (fires automatically when cloud alert fires)**
- Speed tier: use a fast/cheap model (Haiku, GPT-4o-mini, or equivalent)
- Triggered inside `alert_notifier.py` after cooldown check passes
- Builds context from: alert fields + InfluxDB 5-min history + patient baseline (from alert payload)
- Output: 3–4 sentence clinical explanation
- Included in the notification email body (replaces current plain-text body)
- Also returned via the `/alerts/{alert_id}/explanation` endpoint so the UI can show it

**Mode B — On-demand patient summary (operator/client presses button)**
- Quality tier: use a smarter/larger model (Sonnet, GPT-4o, or equivalent)
- Triggered by `GET /patients/{patient_id}/summary` endpoint in demo_api.py
- Builds richer context: full 5-min multi-sensor trend + alert history + baseline
- Output: structured 4-section clinical narrative
- Not cached — generated fresh on each call (live LLM call is part of the demo)

### Context builder design (rule_engine/llm/context_builder.py)

The context builder is used by both modes. It queries InfluxDB and formats the context dict.

**InfluxDB query:** last 5 real minutes of HR, SpO₂, HRV for the device.
Reuse the exact Flux query pattern already in `demo/demo_api.py:get_vitals()`. 

**Trend analysis — compute for each sensor:**
Split the 5-minute history into three windows:
- Recent: last 0–90 seconds
- Mid: 90–180 seconds ago
- Earlier: 180–300 seconds ago

For each window, compute mean. Direction logic:
```python
recent_mean > earlier_mean * 1.03  →  "rising (+X%)"
recent_mean < earlier_mean * 0.97  →  "falling (-X%)"
otherwise                           →  "stable"
```

**Context dict structure:**
```python
{
    "patient": {
        "label": "Patient 001",
        "persona_id": "cardiac_patient",
        "description": "Female, 65, known cardiac risk",  # from persona YAML
        "monitoring_session_minutes": 8.4,
    },
    "baseline": {
        "hr_normal": "67 ± 4 bpm",       # from baseline MQTT cache or alert payload
        "spo2_normal": "97.4 ± 0.8%",
        "hrv_normal": "48 ± 7 ms",
        "personalised": True,
    },
    "current_vitals": {
        "heart_rate": {"value": 118, "unit": "bpm", "trend": "rising (+28%)"},
        "spo2":       {"value": 93.1, "unit": "%",  "trend": "falling (-4%)"},
        "hrv":        {"value": 28,   "unit": "ms", "trend": "falling (-42%)"},
    },
    "alert": {                             # present in Mode A, None in Mode B
        "rule_id": "H2-cloud-ai",
        "description": "Personalised tachycardia — deviation from patient baseline",
        "severity": "warning",
        "anomaly_score": 0.84,
        "shap_contributions": [
            "HR deviation from personal baseline: 58%",
            "SpO₂ below personal normal: 31%",
        ],
    },
    "recent_session_alerts": [            # last 3 alerts in this session
        {"time_ago": "6 min", "description": "...", "severity": "..."},
    ],
}
```

**How to get persona description for context:**
Read the persona YAML file for the device's persona_id. The description field is at the top of
every persona YAML. Cache this at startup (it's static). Map: `persona_id → description string`.
The persona YAML files live at `personas/{persona_id}.yaml`.

### LLM provider abstraction (rule_engine/llm/provider.py)

The LLM layer is provider-agnostic. One abstract base class, concrete implementations swapped
via a single environment variable. The rest of the code only calls `provider.complete()`.

```python
# rule_engine/llm/provider.py

class LLMProvider(ABC):
    """One method. Every concrete implementation must support it."""
    @abstractmethod
    async def complete(self, prompt: str, max_tokens: int = 400) -> str:
        """Send prompt, return response text. Raises LLMError on failure."""

class AnthropicProvider(LLMProvider):
    """Uses the Anthropic SDK. Requires LLM_API_KEY + LLM_MODEL env vars."""
    # LLM_MODEL defaults: fast tier → "claude-haiku-4-5-20251001"
    #                     quality tier → "claude-sonnet-4-6"

class OpenAIProvider(LLMProvider):
    """Uses the OpenAI SDK. Requires LLM_API_KEY + LLM_MODEL env vars."""
    # LLM_MODEL defaults: fast tier → "gpt-4o-mini"
    #                     quality tier → "gpt-4o"

class GeminiProvider(LLMProvider):
    """Uses google-genai SDK (v2+). Requires LLM_API_KEY + LLM_MODEL env vars.
    Install: pip install google-genai
    Import:  from google import genai
    Note: google-generativeai is deprecated as of Nov 2025 — do NOT use it.
    LLM_MODEL defaults: fast tier → "gemini-2.0-flash"
                        quality tier → "gemini-2.0-pro"
    """

class MockProvider(LLMProvider):
    """Returns a canned clinical template. No API key needed. Use for demos without a key."""
    # Fills in patient name, key vitals, and alert type from context dict.
    # Output looks like a real summary but is template-generated.
```

**Factory function** — called once at startup:
```python
def make_provider(tier: str) -> LLMProvider:
    """tier is 'fast' (per-alert) or 'quality' (on-demand summary)."""
    backend = os.environ.get("LLM_PROVIDER", "mock").lower()
    if backend == "anthropic":
        return AnthropicProvider(tier=tier)
    elif backend == "openai":
        return OpenAIProvider(tier=tier)
    elif backend == "gemini":
        return GeminiProvider(tier=tier)
    else:
        return MockProvider()
```

**Environment variables (add to root .env):**
```bash
LLM_PROVIDER=gemini        # mock | anthropic | openai | gemini — start with gemini
LLM_API_KEY=               # your Gemini API key (from Google AI Studio)
LLM_FAST_MODEL=            # optional override — default for gemini: gemini-2.0-flash
LLM_QUALITY_MODEL=         # optional override — default for gemini: gemini-2.0-pro
```

The `MockProvider` works with no API key and produces a realistic-looking output by filling a
template with actual patient data (name, vitals, trend direction, alert type). Good enough to
show the UI and the flow in a demo. When a real key is available, change `LLM_PROVIDER` to
`anthropic` or `openai` — everything else stays the same.

### Prompt templates (rule_engine/llm/summariser.py)

The prompts are provider-independent — they are just strings passed to `provider.complete()`.

**Mode A prompt (fast tier — per-alert):**
```
You are a clinical decision support assistant for a remote cardiac monitoring system.
A personalised AI model has detected an anomaly for the patient below.

Write a clinical explanation in 3–4 sentences for the attending nurse. Use plain clinical
language. Explain: (1) what was detected relative to this patient's personal normal,
(2) the trend over the last few minutes, (3) the clinical significance, (4) a suggested
immediate action.

Do not mention Isolation Forest, SHAP, anomaly scores, or machine learning terms.
Do not repeat the numbers from the data verbatim — interpret them clinically.

Patient data:
{context_json}
```

**Mode B prompt (quality tier — on-demand summary):**
```
You are a senior clinical monitoring assistant reviewing a patient's remote monitoring session.
Write a concise clinical briefing for the care team. Structure your response in exactly these
four sections, each 2–3 sentences:

**Current Status**: What is the patient's condition right now?
**Recent Trend**: How have vitals changed over the last 5 minutes? Are things stable, improving, or worsening?
**Alert History**: What alerts have fired this session and what pattern do they suggest?
**Recommendation**: What immediate clinical action do you recommend?

Write in plain clinical language. Do not mention machine learning or AI systems.
Do not start any sentence with "The patient". Use the patient's label (e.g. "Patient 001").

Patient data:
{context_json}
```

**MockProvider template output (Mode A):**
```python
def _mock_alert_explanation(ctx: dict) -> str:
    p = ctx["patient"]["label"]
    hr = ctx["current_vitals"]["heart_rate"]
    spo2 = ctx["current_vitals"]["spo2"]
    trend_hr = hr["trend"]
    return (
        f"{p}'s heart rate is {trend_hr} and has deviated significantly from their "
        f"established personal baseline of {ctx['baseline']['hr_normal']}. "
        f"Oxygen saturation is currently {spo2['value']}% with a {spo2['trend']} pattern "
        f"over the last 5 minutes. "
        f"This combination suggests possible haemodynamic stress; recommend clinical review "
        f"within 15 minutes and consider contacting the patient directly."
    )
```

**MockProvider template output (Mode B):**
Fill each of the four sections with short clinical sentences derived from the context dict.
The result looks like a real summary and demonstrates the UI layout for a demo without a key.

### New files for Feature 2
```
rule_engine/llm/__init__.py
rule_engine/llm/provider.py           — LLMProvider ABC + AnthropicProvider + OpenAIProvider + MockProvider + make_provider()
rule_engine/llm/context_builder.py    — builds context dict from InfluxDB + baseline cache
rule_engine/llm/summariser.py         — uses provider.complete() with the two prompt templates
```

### Modified files for Feature 2

**rule_engine/alert_notifier.py**
After cooldown check passes (line ~157), before building `notify_payload`:
1. Call `context_builder.build_alert_context(alert, influx_config)` — async
2. Call `summariser.explain_alert(context)` — async, returns string
3. Attach explanation to `notify_payload` as `"explanation"` field
4. Use explanation as the email body instead of the current plain-text body
5. Publish enriched alert back to `alerts/cloud/{persona_id}/{rule_id}/enriched` so the
   demo API alert buffer can pick up the explanation field

Wait — the demo API alert buffer fills from the MQTT subscriber, not from alert_notifier.
Simpler approach: the rule engine does NOT need to re-publish. The explanation is only in the email.
The demo API gets explanations via the separate per-alert endpoint. See below.

**Revised approach for explanation display in UI:**
Add `GET /alerts/{alert_id}/explanation` endpoint to `demo/demo_api.py`. This is called lazily
when the user expands an alert row in the UI (not pre-fetched for all alerts). It:
1. Finds the alert in `_alert_buffer` by id
2. Checks if `explanation` field already cached on the alert dict
3. If not: builds context, calls Haiku, stores explanation on the alert dict, returns it
4. If yes: returns cached explanation immediately

This avoids calling the LLM for every alert on page load — only when a user expands one.

**demo/demo_api.py**
Add two endpoints:
1. `GET /alerts/{alert_id}/explanation` — lazy LLM explanation for one alert (Haiku)
2. `GET /patients/{patient_id}/summary` — full session summary (Sonnet)

Both endpoints need access to the InfluxDB config (already available as env vars in the file).

### New dependencies (add to requirements.txt)
```
# Feature 2 — LLM (install the ones matching your provider; mock needs none)
google-genai>=2.0.0           # if LLM_PROVIDER=gemini  ← active now (NOT google-generativeai, that's deprecated)
anthropic>=0.30.0             # if LLM_PROVIDER=anthropic
openai>=1.30.0                # if LLM_PROVIDER=openai
httpx>=0.27.0                 # already likely present; used by SDK clients
```
Add all to requirements.txt so the code can import them conditionally. Wrap provider
imports in try/except so startup does not fail when a library is not installed:
```python
# In provider.py
try:
    import anthropic as _anthropic
    _HAS_ANTHROPIC = True
except ImportError:
    _HAS_ANTHROPIC = False
```

---

## UI Changes (highest priority — this is what clients see)

### Overview of what changes

Both views get new AI-related elements. Priority: client view looks impressive, operator view
shows more technical detail. No existing layout should be disrupted.

### Type additions (demo/ui/src/types/index.ts)

Add to `PatientState`:
```typescript
baseline_state?: 'learning' | 'active';
baseline_info?: {
  hr_mean: number; hr_std: number;
  spo2_mean: number; spo2_std: number;
  hrv_mean: number; hrv_std: number;
  samples_used: number;
} | null;
```

Add to `ClinicalAlert`:
```typescript
explanation?: string | null;       // LLM-generated explanation (lazy-loaded)
shap_contributions?: string[];     // top SHAP driver strings from conditions_met
```

### PatientCard.tsx (operator view) — changes

**1. AI baseline badge in the header** (next to the existing RiskBadge):
```
● Patient 001        [Risk 85 CRITICAL] [AI Active] [Grafana] [Delete]
```
- While LEARNING: small grey pill "Learning…" with a subtle pulsing animation
- When ACTIVE: small green pill "AI Active" (Brain icon from lucide-react)

**2. Personalised normal ranges** (below the vitals numbers):
When baseline_state is 'active', show under each vital number:
```
HR  118             SpO₂  93.1          HRV  28
bpm                  %                  ms
↑ normal: 62–76     normal: 96–99%      normal: 41–55ms
```
Where the normal range is `mean ± 1.5*std` from baseline_info.
Color: slate-400 for label, slate-500 for range. Small text (text-[10px]).

**3. "Patient Briefing" button** (in the Actions section, next to Trigger Event):
A secondary button (outlined, not filled) with a Brain or FileText icon.
Label: "AI Briefing"
On click: opens SummaryModal (new component).
Only show this button when `baseline_state === 'active'`.

**4. Sparkline threshold line** — the sparkline currently shows `threshold={100}` (static).
When `baseline_state === 'active'`, pass the personalised upper threshold instead:
`threshold={hr_mean + 2 * hr_std}` for HR sparkline.
This changes the reference line from a fixed 100 to the patient's personal range.

### ClientPatientCard.tsx (client view) — changes

Client view is what a client sees during a demo. It should look polished, not technical.

**1. Status badge becomes AI-aware:**
Currently shows a text label (e.g. "Critical", "Normal"). When baseline is active:
- Normal risk + AI active: show "AI Monitoring" in a subtle blue-green pill
- Alert risk + AI active: keep existing "Warning"/"Critical" badge but add a small Brain icon

**2. Personalised normal ranges** (same as operator, but slightly larger text since the tiles
are bigger in ClientPatientCard):
Under each VitalTile, show "normal: 96–99%" in text-[10px] slate-400.

**3. Status color stripe** at top of card — currently color-coded by risk level.
No change to the stripe, but when AI is active, add a subtle "AI Personalised" watermark text
in the bottom-right corner of the card (text-[9px] text-slate-300, non-intrusive).

### AlertFeed.tsx (operator view alert panel) — changes

The expanded detail panel already shows `conditions_met`. Add:

**1. SHAP contribution bar (in expanded detail):**
When the alert has SHAP contributions in `conditions_met` (AI alerts will start with
"HR deviation from personal baseline:"), render them as a small horizontal bar chart.
Parse the percentage from the string and draw a proportional bar.
Example:
```
HR deviation ████████████░░░░░░░░ 58%
SpO₂ drop    ██████░░░░░░░░░░░░░░ 31%
HRV stress   ██░░░░░░░░░░░░░░░░░░ 11%
```
This is a visually strong demo element. Use simple `<div>` bars with Tailwind width classes,
not a chart library.

**2. "Explain" button in expanded detail:**
Label: "AI Explanation"
On click: calls `GET /alerts/{alert_id}/explanation`
Shows a loading spinner while fetching, then renders the LLM explanation text below the SHAP bars.
Cache the explanation in local component state after first load.

### ClientAlertFeed.tsx (client view alert panel) — changes

Client view shows simplified alerts. When an alert has an explanation available:
- Show a small italic "Tap to read clinical briefing" hint under the alert description
- On click: expand inline to show the explanation text (no modal needed, just inline expand)
- Use a subtle animation (max-height transition)

The explanation adds clinical narrative for clients: *"This patient's oxygen has been declining
for 4 minutes, now 4 points below her personal baseline of 97.4%. Recommend call within 15 minutes."*
This is the demo moment.

### New component: SummaryModal.tsx

A full-screen modal triggered from the "AI Briefing" button on PatientCard.

Layout:
```
┌─────────────────────────────────────────────────────────┐
│  Patient Briefing: Patient 001                    [✕]   │
│  AI-generated clinical summary  ·  claude-sonnet        │
│─────────────────────────────────────────────────────────│
│  CURRENT STATUS                                         │
│  [LLM text — 2–3 sentences]                             │
│                                                         │
│  RECENT TREND                                           │
│  [LLM text — 2–3 sentences]                             │
│                                                         │
│  ALERT HISTORY                                          │
│  [LLM text — 2–3 sentences]                             │
│                                                         │
│  RECOMMENDATION                                         │
│  [LLM text — 2–3 sentences, slightly larger/bolder]     │
│─────────────────────────────────────────────────────────│
│  Personalised baselines used: HR 67±4bpm · SpO₂ 97.4%  │
│  Generated at 14:23:01  ·  Compression 10x              │
│                                    [Regenerate]  [Close] │
└─────────────────────────────────────────────────────────┘
```

While loading: spinner + "Analysing patient trend…"
Section headers use the `**Bold**` markdown from the LLM — parse them client-side.
The footer shows baseline info so the client can see personalisation is active.

### New API function (demo/ui/src/api/patients.ts)
```typescript
export async function getPatientSummary(patientId: string): Promise<{ summary: string }>;
export async function getAlertExplanation(alertId: string): Promise<{ explanation: string }>;
```

---

## Complete list of files to create or modify

### New files
```
rule_engine/cloud/personalised_analyzer.py   — Isolation Forest + SHAP analyzer
rule_engine/llm/__init__.py                  — empty
rule_engine/llm/provider.py                  — LLMProvider ABC + all concrete providers + MockProvider
rule_engine/llm/context_builder.py           — InfluxDB query + context dict builder
rule_engine/llm/summariser.py                — calls provider.complete() with the two prompts
demo/ui/src/components/SummaryModal.tsx      — AI Briefing modal
```

### Modified files
```
rule_engine/service.py                       — swap RuleAnalyzer → PersonalisedAnalyzer (1 line + import)
rule_engine/alert_notifier.py                — add Haiku explanation to email body
demo/demo_api.py                             — subscribe to baselines/#, add 2 endpoints, extend patient status
config/cloud_rules.yaml                      — add personalised_baseline: section
requirements.txt                             — add scikit-learn, shap, anthropic
demo/ui/src/types/index.ts                   — add baseline_state, baseline_info, explanation fields
demo/ui/src/api/patients.ts                  — add getPatientSummary, getAlertExplanation
demo/ui/src/components/PatientCard.tsx       — AI badge, normal ranges, AI Briefing button, personalised sparkline threshold
demo/ui/src/components/ClientPatientCard.tsx — AI badge, normal ranges, subtle AI watermark
demo/ui/src/components/AlertFeed.tsx         — SHAP bar, Explain button
demo/ui/src/components/ClientAlertFeed.tsx   — inline LLM explanation on expand
```

---

## Implementation order

Work in this exact sequence. Each step is independently testable.

### Step 1 — Config + dependencies
1. Add `personalised_baseline:` block to `config/cloud_rules.yaml`
2. Add `scikit-learn`, `shap`, `anthropic` to `requirements.txt`
3. `pip install -r requirements.txt`

### Step 2 — PersonalisedAnalyzer (backend, no UI)
1. Create `rule_engine/cloud/personalised_analyzer.py`
2. Modify `rule_engine/service.py` to use it
3. Verify: run `./start.sh`, add a patient, watch logs — should see "IF model trained for device"
   after ~90 real seconds. Should see AI alerts in the alert feed after that.

### Step 3 — Baseline MQTT publish + demo API baseline cache
1. Add baseline MQTT publish to `personalised_analyzer.py` (on ACTIVE transition)
2. Add `baselines/#` subscription to `demo/demo_api.py`
3. Add `baseline_state` and `baseline_info` fields to `_build_patient_status()`
4. Verify: `curl http://localhost:8000/patients` — should show `baseline_state: "learning"` then
   transition to `"active"` after training.

### Step 4 — LLM infrastructure
1. Create `rule_engine/llm/provider.py` — all providers including MockProvider
2. Create `rule_engine/llm/context_builder.py`
3. Create `rule_engine/llm/summariser.py` (both prompt modes)
4. Set `LLM_PROVIDER=gemini` and `LLM_API_KEY=<your-gemini-key>` in root `.env`
   (fallback: use `LLM_PROVIDER=mock` if key is not ready — MockProvider works without any key)
5. Add `GET /patients/{patient_id}/summary` to `demo/demo_api.py`
6. Add lazy `GET /alerts/{alert_id}/explanation` to `demo/demo_api.py`
7. Verify: `curl http://localhost:8000/patients/{id}/summary` returns clinical narrative
   (will be a canned template response from MockProvider — that is expected and correct)

### Step 5 — Alert notifier enrichment
1. Modify `rule_engine/alert_notifier.py` to call Haiku and include explanation in email
2. Verify: trigger an alert, check Mailhog at port 8025 — email should contain the
   LLM-generated clinical narrative

### Step 6 — UI: PatientCard (operator view)
1. Add AI badge to `PatientCard.tsx`
2. Add personalised normal ranges under vitals
3. Update sparkline threshold to use personalised value
4. Add "AI Briefing" button
5. Create `SummaryModal.tsx`
6. Wire button → modal → API call
7. Verify in browser: badge appears, normal ranges show under vitals, modal opens with narrative

### Step 7 — UI: ClientPatientCard (client view)
1. Add AI badge + personalised ranges
2. Verify: client view at `http://localhost:8000` shows updated cards

### Step 8 — UI: Alert feeds (both)
1. Add SHAP bar to `AlertFeed.tsx` expanded detail
2. Add "AI Explanation" button → lazy load explanation
3. Add inline expansion + explanation to `ClientAlertFeed.tsx`
4. Verify: expand an alert → SHAP bars appear → click Explain → LLM text loads

### Step 9 — Types + API layer
Add type changes and API functions as each UI step requires them. Don't do this step separately.

---

## Important technical constraints

### Time compression
The `analyze()` method receives `compression` as a parameter. Use it everywhere windows are computed:
```python
effective_window = nominal_window_seconds / max(compression, 1.0)
```
This is already done in `rule_analyzer.py` — copy the same pattern.
Training samples accumulate faster at high compression (demo runs at 10x) — this is expected and
good. The `min_training_window_seconds` guard prevents training on too-short a sample even at high
compression.

### Fallback cooldown continuity
When transitioning from LEARNING → ACTIVE, call:
```python
self._last_alert = dict(self._fallback._last_alert)
```
This prevents a burst of AI alerts firing immediately after transition (the cooldown state from
the static rules carries over).

### Isolation Forest stability
At low compression (1x), the training window might be crossed during a clinical event rather than
a stable phase. The 10% outlier trim handles this — extreme values are excluded before fitting.

### SHAP with Isolation Forest
Use `shap.TreeExplainer`, not `shap.Explainer`. The latter is slow for tree models.
`shap.TreeExplainer(model).shap_values(X)` returns shape `(n_samples, n_features)`.
For a single sample: `shap_values[0]` gives feature contributions.

### LLM provider configuration
Add these to root `.env` (start.sh sources it automatically):
```bash
LLM_PROVIDER=gemini      # mock | anthropic | openai | gemini
LLM_API_KEY=             # Gemini key from Google AI Studio (currently active)
LLM_FAST_MODEL=          # optional — provider picks sensible default if blank
LLM_QUALITY_MODEL=       # optional — provider picks sensible default if blank
```
To switch providers later: change `LLM_PROVIDER` and `LLM_API_KEY`, restart with `./stop.sh && ./start.sh`.
No code changes needed — the rest of the system is completely unaware of which provider is running.

### asyncio in alert_notifier.py
`alert_notifier.py` is already async. The LLM call (via `anthropic` async client) fits naturally
into `_handle_alert()`. No threading needed.

### InfluxDB access from rule engine
The `context_builder.py` needs InfluxDB connection details. Read from the same env vars already
used in `demo_api.py`:
```python
INFLUX_URL    = os.environ.get("INFLUXDB_URL",    "http://localhost:8086")
INFLUX_TOKEN  = os.environ.get("INFLUXDB_TOKEN",  "my-token")
INFLUX_ORG    = os.environ.get("INFLUXDB_ORG",    "iot_org")
INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET", "iot_poc")
```

---

## Demo script (the 6-step moment)

1. Open `http://localhost:8000` (client view) and `http://localhost:3000` (Grafana) side by side
2. Add Patient A: "Athlete" persona + bradycardia scenario. Card shows "Learning baseline…"
3. Wait ~30-60 seconds (10x compression). Badge changes to "AI Active". Normal ranges appear:
   "HR normal: 46–58 bpm" — the system has learned the athlete's unusually low resting HR.
4. Add Patient B: cardiac patient + tachycardia scenario.
   Static threshold would fire at HR 100. AI fires earlier when the IF model detects deviation.
5. Alert fires. Expand it in the alert feed — SHAP bars appear. Click "AI Explanation" —
   clinical narrative loads: *"HR has risen 28 bpm above this patient's personal baseline over
   the last 4 minutes, coinciding with an 8% drop in oxygen saturation…"*
6. Click "AI Briefing" on the patient card — Sonnet summary loads with four sections.
   Point at the "Recommendation" section — this is the moment.

The contrast to show explicitly: Patient A's HR of 52 bpm → NO alert (personal normal).
Same HR value for Patient B (higher resting baseline) → alert fires. Same number, different story.
