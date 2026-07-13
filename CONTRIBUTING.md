# Contributing

Two kinds of contributions move this project forward more than anything else. Both are data, not code.

## 1. Provider disclosures → `model_overrides`

When a provider publishes a measured, model-specific footprint figure (like Google's arXiv:2508.15734 or Mistral's audited LCA), it belongs in `coefficients.json` under `model_overrides`. Requirements:

- **Primary source only** — the provider's own paper, blog, or audited report. No secondary summaries.
- State the **workload shape** (median prompt? fixed 400-token response?) and the **accounting basis** (market vs location-based carbon; on-site vs full water) in the `source` string. These differ across disclosures and silently mixing them is the failure mode this project exists to avoid.
- Every value gets `{..., label, source}`. A disclosure is `measured`; your extrapolation of it is `modeled` and should say so.

## 2. Grid zones and datacenter sites → `SITE_PRESETS`

`modelfootprint/live.py` maps site names to an Electricity Maps zone, representative coordinates, and a climate class. To add one: pick a real datacenter metro, find its EM zone id, choose the closest `region_presets` climate class, and note your reasoning in the PR.

## Everything else

- **Coefficient changes** need a source and must not narrow an uncertainty band without a `measured`-grade justification. Widening bands is cheap; narrowing them is expensive.
- **Code** is stdlib-only Python 3 by design (the statusline must start in milliseconds and install nowhere). Both test suites must pass: `python3 test_footprint_statusline.py && python3 test_modelfootprint.py`. New behavior needs a check in the suite.
- **Docs** claims need a source listed in the same doc. If a number in a doc disagrees with `coefficients.json`, that's a bug in one of them — say which.
- The display rules in METHODOLOGY.md §5.2 (sig figs, ranges, basis labels, no fabricated zeros) are not style preferences; PRs that add precision the methodology can't support will be asked to round it back off.
