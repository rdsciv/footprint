---
name: footprint
description: Report the estimated energy (Wh), water (mL), and carbon (gCO2e) footprint of the current Claude Code session in a /usage-style layout (totals, composition bars, contribution insights, counterfactual savings), or estimate a hypothetical usage ("what-if"). Use when the user runs /footprint, or asks about the environmental cost, energy use, water use, carbon emissions, or what they could have saved with a different model, timing, or prompt shape.
---

# Footprint: session environmental telemetry

All numbers come from the `modelfootprint` engine in this plugin/repo — never
estimate or recall footprint figures yourself; run the CLI and relay its
output. The engine's display rules (uncertainty ranges, accounting-basis
labels, scenario envelopes, modeled counterfactuals) are part of the
methodology: keep them intact when presenting.

## Locating the CLI

Set `MF_ROOT` to the directory containing the `modelfootprint/` package:
`${CLAUDE_PLUGIN_ROOT}` when running as an installed plugin, otherwise the
repository root (where this skill lives under `skills/footprint/`).

All commands below are run as:

```bash
PYTHONPATH="$MF_ROOT" python3 -m modelfootprint <command>
```

## No arguments → session report (Usage-style)

1. Refresh live grid/weather signals (safe with nothing configured; failures
   are recorded, never fatal, ~seconds):
   `PYTHONPATH="$MF_ROOT" python3 -m modelfootprint refresh`
2. `PYTHONPATH="$MF_ROOT" python3 -m modelfootprint report`
3. Relay the markdown report **verbatim** (it is already formatted like
   Claude's `/usage` panel). Optionally add at most 2 sentences of context if
   something stands out. Do **not** re-round numbers or invent savings.

### What the report contains

| Section | Purpose (mirrors `/usage`) |
|---------|----------------------------|
| **Session** | Energy / water / carbon totals with [low–high] envelopes + token mix |
| **Composition of energy** | ASCII bars: share of central energy from cache / output / fresh input |
| **What's contributing…** | % drivers + one action each (tier mix, cache shape, prefill, …) |
| **What you could have saved** | Modeled counterfactuals: tier steps, all-mid/small, clean-hour carbon, profile reshape |
| **By model** | Per-model tokens + energy |
| **Decisions** | WHEN (grid timing) and WHICH (tier) advice |

Counterfactuals are **modeled scenarios**, not promises the user can force
provider routing or always defer interactive work. Keep that framing if you
paraphrase.

The report finds the current session transcript automatically (most recently
modified transcript for this project).

## Arguments → what-if estimate

`/footprint <model> <tokens> [chat|agent|out-heavy] [in=N] [cache=N] [out=N]`

Examples:
- `/footprint opus 500k` → `... whatif opus 500k`
- `/footprint sonnet 2M chat` → `... whatif sonnet 2M chat`
- `/footprint haiku 0 in=10k cache=50k out=2k` → `... whatif haiku 0 in=10k cache=50k out=2k`

Pass the user's arguments through to `whatif` unchanged. If the CLI reports a
parse warning, show it and ask the user to restate rather than guessing.

## Configuration the user may ask about

- `FOOTPRINT_SITE` (virginia|iowa|oregon|texas|phoenix|california|dublin|amsterdam|frankfurt|singapore) — datacenter site preset: grid zone + weather coords + climate class
- `FOOTPRINT_EM_TOKEN` — Electricity Maps API token (free personal tier) for live grid carbon intensity
- `FOOTPRINT_WT_USER`/`FOOTPRINT_WT_PASS` + `FOOTPRINT_WT_REGION` — optional WattTime credentials and region (marginal percentile, timing advice only; the region is required, there is no default)
- `FOOTPRINT_REGION` — static climate fallback (hot_arid|temperate|cool_humid|hot_humid)
- `FOOTPRINT_LIVE=0` — disable live signals entirely

If the user asks why numbers are "estimates" or challenges accuracy, point
them to METHODOLOGY.md and LIMITATIONS_AND_FAQ.md in the repo rather than
defending the numbers yourself.
