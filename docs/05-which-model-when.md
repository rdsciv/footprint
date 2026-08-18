# Which Model, When: A Decision Guide

*Part 5 of the [footprint](../README.md) deep-dive series — the practical payoff of docs 1–4.*

You control three levers over your AI footprint, in descending order of what most people assume and ascending order of actual leverage for everyday use: **whether** to prompt, **which model** you use, and **when/where** the compute runs. Docs 1–4 built the physics; this doc turns it into decisions.

## Lever 1: Which model (the 6× you control every day)

Per-token energy scales hard with model tier. From [`coefficients.json`](../modelfootprint/coefficients.json) (central estimates, output tokens, ×PUE 1.2):

| tier | class examples | Wh per 1k output tok | vs small |
|---|---|---|---|
| small | Haiku-class, GPT-5-nano, Gemini Flash, Llama-8B | **0.18** | 1× |
| mid | Sonnet-class, GPT-5-mini, Gemini (base), DeepSeek, Qwen, Mistral mid | **0.54** | 3× |
| frontier | Opus/Fable-class, GPT-5(-pro), o-series, Grok-4, Gemini Pro, Mistral Large | **1.08** | 6× |

Measured provider anchors bracket these tiers: Google's median Gemini Apps prompt at **0.24 Wh** (full-stack, measured) sits at assistant scale; URI's GPT-5-class estimates (**18–40 Wh/query**) at long-form frontier scale; Mistral Large 2's audited **1.14 gCO₂e / 45 mL per 400-token response** includes amortized training (wider scope than this tool). Uncertainty bands are 2–3× (all tier coefficients are MODELED — see LIMITATIONS_AND_FAQ.md) — but the *ordering* small < mid < frontier is robust across every source.

**The honest decision rule:**

1. **Default one tier lower than your habit.** Most summarization, extraction, formatting, simple code edits, and short Q&A are small-tier tasks. Frontier models are for frontier problems: deep reasoning, long-horizon agent work, hard debugging.
2. **A wrong answer is the most expensive answer.** If the small model fails and you re-run on frontier, you paid for both. When you *know* the task is hard, going straight to frontier is the greener path. Footprint-per-*solved-task*, not per-token, is the real metric.
3. **Let the tool check your habit**: `/footprint` ends every session report with the counterfactual — "same tokens on small tier: −83% energy." If you keep seeing that on sessions Haiku could have handled, that's your signal. Try `python3 -m modelfootprint whatif haiku 500k` vs `whatif opus 500k`.
4. **Reasoning modes are output multipliers.** Thinking tokens bill (and burn) as output — the 3×-per-token decode class — and can multiply output volume several-fold. Extended thinking on a frontier model is the single most energy-dense mode you can select; spend it on problems that need it.
5. **Cache is your friend; context bloat is not.** In agent sessions, cache reads (90%+ of tokens, ~5–10× discounted) mean a long-running session is far cheaper than its raw token count suggests. What resets that advantage: needlessly clearing context, and stuffing prompts with unused material that gets re-read every turn.

## Lever 2: When and where (the 10× the grid controls)

From docs 2–3, for *deferrable, heavy* work — batch evals, dataset generation, overnight agent runs:

- **Best**: midday on a solar-heavy grid (marginal CI approaches zero; CAISO curtails surplus), or any hour on hydro/nuclear/wind-heavy grids (Nordics, Quebec, Pacific NW).
- **Worst**: the 17:00–21:00 evening ramp on solar-heavy grids (500–630 gCO₂/kWh marginal) and grid-emergency hours, when *if* a campus were on backup diesel (~780 g/kWh direct, MODELED) carbon would jump. We cannot observe that from outside.
- **Easiest win**: batch APIs. The 50% discount (OpenAI, Anthropic) hands the provider a 24-hour window to schedule your job — temporal flexibility is exactly what the duck curve rewards. Batch is the rare case where the cheap option and the clean option point the same way *by mechanism, not just correlation*.
- Configure the live signal (`FOOTPRINT_SITE` + free Electricity Maps token) and `/footprint` will literally tell you the ratio: "deferring to 13:00 cuts this session's carbon ~5×."

One honest tension: midday is carbon-best but water-worst for on-site cooling (peak wet-bulb, doc 04). Carbon-wise the midday advantage usually dominates; in drought-stressed regions, weight water higher — the tool shows both.

## Lever 3: Whether (the frame that keeps you sane)

For scale, with everything above priced in:

- One chat message (~0.1–1 Wh) ≈ seconds of microwave time. **Individual light use is not an environmental event** — guilt-per-prompt is bad math.
- One heavy agent session (100–400+ Wh) ≈ a laptop workday. Real, worth optimizing, still small next to a commute.
- A *product* serving millions of prompts, or a team running agent fleets around the clock — that's where these percentages compound into megawatt-hours, and where defaulting to the right tier and batch-scheduling heavy work becomes an engineering responsibility rather than a personal virtue.

The point of measurement isn't guilt — it's that **you can't manage what you can't see**. Put it in the statusline, notice the one session in ten where a frontier model idled through small-tier work, and fix the default.

## The 30-second decision card

```
Is the task genuinely hard (deep reasoning, long agent horizon)?
├─ yes → frontier model, no guilt — right tool for the job
│         └─ deferrable & heavy? → batch API / midday run
└─ no  → one tier down (small for mechanical work)
          ├─ interactive? → just prompt; footprint is trivial
          └─ bulk/deferrable? → small tier + batch API = the
             cheapest AND cleanest configuration that exists
```

## Sources

Tier coefficients and their derivation: [`coefficients.json`](../modelfootprint/coefficients.json), [METHODOLOGY.md](../METHODOLOGY.md) §2; measured anchors: Google [arXiv:2508.15734](https://arxiv.org/abs/2508.15734), Mistral audited LCA (2025), URI GPT-5 estimates; grid timing: [doc 02](./02-duck-curve-and-grid.md); diesel/fuel mix: [doc 03](./03-fuel-mix-and-diesel-backup.md); water tension: [doc 04](./04-water.md); batch discounts: OpenAI/Anthropic public pricing.
