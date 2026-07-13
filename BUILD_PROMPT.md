# Build prompt: Footprint statusline for Claude Code

Copy everything below the line into a fresh Claude Code session started in this repo.

---

Build a Claude Code statusline that displays live token counts plus estimated energy (Wh), water (mL), and carbon (gCO2e) for the current session. The methodology is fully specified in this repo — `METHODOLOGY.md`, `coefficients.json`, `LIMITATIONS_AND_FAQ.md`. Read all three before writing code. Your job is faithful implementation, not re-derivation: every number, equation, and display rule comes from those files.

"Accurate" here means three things, in priority order: (1) token attribution from the session transcript is exactly right, (2) the arithmetic matches the core equations and coefficient table exactly, (3) the display never claims more precision than the methodology allows.

## Step 0 — Verify inputs empirically before coding against them

Do not trust remembered field names. First:

1. Register a throwaway statusline command that dumps its stdin JSON to a temp file, trigger it, and read the actual schema (expect fields like `session_id`, `transcript_path`, `model.id`, `model.display_name`, `cost.*` — but confirm).
2. Open a real transcript JSONL from `~/.claude/projects/` and confirm the shape of assistant entries: `message.usage` with `input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `output_tokens`; `message.id`; `message.model`; `isSidechain`. Code against what you observe.

## Token attribution (the accuracy-critical part)

The stdin JSON does not carry token counts — parse the transcript at `transcript_path` and sum usage across all assistant entries. Rules:

- `tok_in` = `input_tokens` + `cache_creation_input_tokens`. Cache *writes* are fully computed prefill; only cache *reads* get the discounted coefficient.
- `tok_cache` = `cache_read_input_tokens`.
- `tok_out` = `output_tokens`. Thinking/reasoning tokens are already inside this figure and cost full `e_out` (METHODOLOGY.md §2.4) — do not subtract them.
- Deduplicate by `message.id`, keeping the last-seen usage for each id. Streaming can write the same message multiple times; double-counting here is the single most likely accuracy bug.
- Include sidechain/subagent entries — they are real API calls.
- Tier each entry by its own `message.usage`-adjacent `message.model`, not the session's current model. Sessions mix models (subagents on Haiku, main thread on Opus), and per-entry tiering is a large accuracy win.

## Computation

- Load `coefficients.json` at runtime (path via `FOOTPRINT_COEFFS` env var, default: alongside the script). Never hardcode a coefficient. The file is versioned precisely so numbers can change without touching code.
- Energy: `(tok_in·e_in + tok_cache·e_cache + tok_out·e_out) / 1000 × PUE` — coefficients are per 1K tokens; PUE from `infrastructure_overhead.PUE_typical_hyperscale.central`.
- Tier via `model_tier_lookup` substring match on model id. Unknown model → `mid` tier AND a visible "?" uncertainty marker in the display — never silently guess `small`.
- Water: `energy_kWh × (WUE_site + EWIF)` using `region_presets`; region from `FOOTPRINT_REGION` env var (`hot_arid|temperate|cool_humid|hot_humid`), default `temperate`.
- Carbon: `energy_kWh × CI` using the location-based figure from `carbon_intensity_accounting_note` (345 g/kWh, google_2024). Location-based is the mandated default (§1.4).
- Uncertainty range: compute low/high totals by multiplying the low bounds together and the high bounds together respectively (§5.1's stated propagation rule). Carry full precision internally; round only at display.
- Do NOT implement: live APIs (roadmap-only), routing-based carbon adjustments (§3.5 explicitly forbids this), per-city WUE lookups, or the `time_of_day_seasonal_modifiers` unless behind an off-by-default env flag (they are labeled speculative).

## Display rules — non-negotiable, from §5.2

- Energy: 2 significant figures. Water: 1–2 sig figs with a `~` prefix. Carbon: always suffixed `(loc-based)` — never a bare gCO2e number. No value ever shows more digits than these rules allow.
- One line. Suggested shape (adapt, but keep every element):
  `⚡ 0.42 Wh [0.15–1.3] 💧 ~0.9 mL 🌫 0.14 g (loc-based) | 12k in · 45k cache · 3.2k out`
- The [low–high] range is not optional decoration — show it, or (if space-constrained) behind a `FOOTPRINT_VERBOSE=1` env flag, but the point estimate must never appear without the range being one flag away.
- Failure honesty: missing transcript → `–` placeholders; unreadable coefficients file → an explicit error glyph. Never render a fabricated `0.0`.

## Engineering constraints

- Single self-contained script, Python 3 stdlib only. First line of stdout is the statusline.
- Must return in <300 ms on a 10 MB transcript. Full re-parse per invocation is fine if it meets that budget; add byte-offset caching keyed on `session_id` only if measurement says you need it. Measure, don't assume.
- Skip malformed JSONL lines without crashing.
- Register it in `.claude/settings.json` under `statusLine` when done.

## Verification — do all of these, show output for each

1. **Hand-check fixture**: a synthetic transcript with round-number usage (e.g. 10,000 in / 50,000 cache-read / 2,000 out on a `sonnet` model). Compute expected Wh by hand in a comment (show the arithmetic), assert the script's internal value matches exactly and the displayed value rounds correctly to 2 sig figs.
2. **Dedupe**: same `message.id` appearing 3× with growing usage → counted once, final value.
3. **Cache-write attribution**: `cache_creation_input_tokens` lands in `tok_in` at `e_in`, not in `tok_cache`.
4. **Mixed models**: fixture with Haiku sidechain + Opus main entries → each priced at its own tier; assert total differs from single-tier pricing.
5. **Unknown model** → mid tier + "?" marker visible in output.
6. **Range propagation**: assert low = product of lows, high = product of highs, for a known fixture.
7. **Robustness**: truncated file, malformed line, missing usage field → no crash, no invented numbers.
8. **Timing**: run against the largest real transcript on this machine, print elapsed time, confirm <300 ms.

A claim of "done" requires all eight passing with visible test output — not "should work."
