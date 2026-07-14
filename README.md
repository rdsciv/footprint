# footprint

**Your AI usage has a shadow. Now it's in your statusline.**

`footprint` puts live estimates of the **energy (Wh), water (mL), and carbon (gCO2e)** of your AI coding sessions where you can't ignore them — in the Claude Code statusline — and gives you a `/footprint` command that answers the two questions that actually change behavior: **which model should I use, and when should I run the heavy stuff?**

```
⚡ 240 Wh [72–850] 💧 ~310 mL 🌫 83 gCO2e (loc-based) | 114k in · 2.0M cache · 51k out
```

## Why this exists (and why it's honest)

Most AI-footprint numbers you've seen are either marketing or outrage. The truth is that **nobody outside a provider can measure your prompt's footprint** — but you *can* estimate it rigorously, with sourced coefficients, propagated uncertainty ranges, and labels that admit what's measured, what's modeled, and what's speculative. That's what this project does:

- **Every number ships with a [low–high] range.** The range is a *scenario envelope* — every factor at its pessimistic/optimistic bound at once — and is labeled as such, never dressed up as a confidence interval.
- **Every coefficient carries `{central, low, high, label, source}`** in [`coefficients.json`](./modelfootprint/coefficients.json) — `measured` (provider disclosure), `modeled` (derived from benchmarks), or `speculative` (labeled shape, off by default).
- **Carbon is always basis-labeled** — `(loc-based)` static average or `(live-grid …)` real-time — because location-based and market-based accounting differ 3–4× for the same electricity, and mixing them silently is how numbers lie.
- What we cannot know from outside (your batch occupancy, the provider's speculative-decoding hit rate, which facility served you) is documented in [LIMITATIONS_AND_FAQ.md](./LIMITATIONS_AND_FAQ.md), not papered over.

## Quickstart

Requires Python 3 (stdlib only — no dependencies).

```bash
git clone https://github.com/rdsciv/footprint && cd footprint
pip install .        # optional — bundles the coefficients and adds a `footprint` command

# statusline: add to .claude/settings.json in any project (or globally)
{ "statusLine": { "type": "command", "command": "python3 /path/to/footprint/footprint_statusline.py" } }

# session report / what-if from any terminal (or `footprint ...` if installed)
python3 -m modelfootprint report
python3 -m modelfootprint whatif opus 500k
python3 -m modelfootprint whatif sonnet 2M chat
```

In Claude Code, the `/footprint` skill (in [`skills/footprint/`](./skills/footprint/SKILL.md)) wraps the same engine: `/footprint` for the current session, `/footprint opus 500k` for hypotheticals.

### Go live (recommended — this is the fun part)

With a **free** [Electricity Maps](https://portal.electricitymaps.com/) personal API token, your carbon figure follows the actual grid hour by hour, and `/footprint` tells you things like *"Grid is at 480 g/kWh (evening peak); deferring to ~13:00 cuts this session's carbon ~5×."*

```bash
export FOOTPRINT_SITE=virginia        # or iowa|oregon|texas|phoenix|california|dublin|amsterdam|frankfurt|singapore
export FOOTPRINT_EM_TOKEN=...         # Electricity Maps token
python3 -m modelfootprint refresh     # hourly cache; statusline never touches the network
```

Weather-driven water estimates (dry-bulb economizer gate × wet-bulb draw intensity) come free with `FOOTPRINT_SITE` — no key needed. Optional: `FOOTPRINT_WT_USER/PASS` plus `FOOTPRINT_WT_REGION` for WattTime's marginal signal (the region is required — timing advice for the wrong grid is worse than none).

## The research

Five deep dives, each standalone (and each the receipts behind a coefficient):

| | takeaway |
|---|---|
| [01 · The energy of inference](./docs/01-energy-of-inference.md) | the honest unit is the token, priced by class, with a range — and cache reads are 90% of agent tokens |
| [02 · The duck curve](./docs/02-duck-curve-and-grid.md) | the same prompt carries **>10× different carbon by hour of day** on solar-heavy grids |
| [03 · Fuel mix & diesel backup](./docs/03-fuel-mix-and-diesel-backup.md) | wind ≈ 11, diesel backup ≈ 840 gCO2e/kWh — and 4,700 permitted diesel generators sit in one Virginia county |
| [04 · Water](./docs/04-water.md) | the power plant usually evaporates more water for your prompt than the datacenter does |
| [05 · Which model, when](./docs/05-which-model-when.md) | one tier down + batch API is the cheapest *and* cleanest configuration that exists |

Interactive version — calculator, duck-curve explorer, tier comparison: [`site/`](./site/index.html) (GitHub Pages; also runs locally via `python3 -m http.server`).

The rigorous spine: [METHODOLOGY.md](./METHODOLOGY.md) (equations, sources, uncertainty propagation) · [LIMITATIONS_AND_FAQ.md](./LIMITATIONS_AND_FAQ.md) · [LIVE_SIGNAL_ROADMAP.md](./LIVE_SIGNAL_ROADMAP.md).

## What it will tell you (typical figures)

- A chat message: **~0.1–1 Wh** — seconds of microwave time. Prompt guilt is bad math.
- A heavy agent session: **100–400+ Wh** — a laptop workday. Model tier and timing visibly matter.
- Same tokens, small tier instead of frontier: **~6× less energy.** Same session at solar noon instead of the evening ramp: **up to ~10× less carbon.** Those two multipliers are the whole point.

## Verification

Two stdlib-only suites, no network:

```bash
python3 test_footprint_statusline.py   # accuracy contract: dedupe, cache attribution, tiering, ranges, <300ms
python3 test_modelfootprint.py         # what-if parsing, live-cache fallback, weather model, recommendations
```

## Contributing

The two highest-value contributions are new **provider disclosures** (`model_overrides`) and new **grid zones/sites** — see [CONTRIBUTING.md](./CONTRIBUTING.md). Every new coefficient needs `{central, low, high, label, source}` or it doesn't merge.

MIT licensed.
