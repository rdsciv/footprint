# Cloud Context for the Footprint Statusline

> Compiled from the "CLōD" / cloud-infrastructure article set in the Obsidian
> vault (`raw/`) plus supporting energy/water/grid articles. Purpose: give the
> footprint statusline project a reference layer for the **location & routing**
> dimension of inference cost, energy, water, and carbon — the part that the
> token-driven `coefficients.json` treats only as static `region_presets`.
>
> This is a *context* file (background + sanity-check numbers + pointers), not a
> patch. It does not change `coefficients.json` or `footprint_statusline.py`.
> Suggested coefficient edits are flagged under "Implications" with no edits made.

## TL;DR for the statusline

- The footprint tool already attributes energy/water/carbon from **token counts**.
  The cloud articles confirm a *second* driver the project currently approximates
  with coarse `region_presets`: **where the inference physically runs**.
- Electricity price per kWh backing AI inference varies **~3x across US states**
  and **4–5x globally** by location (CLōD, "The Hidden Variable…"). This is the
  same axis that drives carbon intensity (grid mix) and water intensity (cooling
  climate + thermoelectric generation). So `region_presets` is the right lever;
  the articles justify investing in it / a live grid signal.
- CLōD's "energy-aware routing" optimizes **electricity cost**, not carbon. Its
  own framing (cheapest electron = often surplus renewable) only *loosely*
  correlates with lower carbon/water. This **agrees** with `coefficients.json`
  `routing_signal_notes`: do NOT treat price routing as a carbon proxy. Keep that
  prohibition.
- Sanity-check anchors extracted below: Altman's ~0.34 Wh/query, aggregate
  ~1.3 gal water per kWh of data-center electricity, and marginal grid emissions
  of 0.5–0.63 tCO₂/MWh at evening peak. Use these to validate the per-tier
  coefficients, not to replace them.

## Source articles (vault `raw/`)

| Article | What it contributes |
| --- | --- |
| `clod the Hidden Variable in Your AI Inference Bill Where It Actually Runs.md` | Geography spread of electricity cost (3x US / 4–5x global); "two variables" framing (model + location). |
| `How CLōD's Patented Energy Aware Routing Cuts Your Inference Bill by Up to 60.md` | Mechanism of real-time energy-price routing; +50 ms max latency; up to 60% cheaper; "cheapest electron ≈ surplus renewable" caveat. |
| `How CLōD Scaled AI Workload Routing with Real-Time Energy Intelligence.md` | Production proof: 6+ datacenters, 4 power markets (incl. ERCOT), 500+ MW, live price/curtailment/demand-response signals. |
| `Why Energy-Aware Routing Gets More Valuable Every Time You Scale clod.md` | Model prices are converging; energy layer becomes the structural cost edge at scale. |
| `Water Rights The Hidden Asset the Market Still Values at Zero.md` | Aggregate data-center water intensity (~1.3 gal/kWh) and indirect (thermoelectric) water share. |
| `California Grid Utilization Explorer.md` | Marginal emission rate by hour (0.5–0.63 tCO₂/MWh evening peak, ≈0 midday); camel-profile emissions reduction. |
| `Sunrun's new distributed AI data center pilot could be the start of something big.md` | Sam Altman's ~0.34 Wh/query; 1 kWh ≈ 2,900 queries ≈ 2.9M tokens. |
| `The Grid Was Not Built for This!.md`, `Texas grid slammed by 519 power-hungry AI data center requests…md` | Scale of AI load (438,595 MW requested in TX alone) — context for why location matters. |
| `LōD Launches CLōD, the World's First Compute Flexibility Platform for AI Inference.md` | CLōD runs on LōD energy platform managing "billions of kWh annually" — scale anchor for live signal credibility. |

## Key extracted figures (with provenance)

### Energy / cost geography
- US commercial electricity rates vary **>3x across states**; globally the
  per-kWh price behind AI inference varies **4–5x** by physical location
  ("The Hidden Variable…", lines 27–29).
- CLōD-hosted models run **up to 60% cheaper** than direct provider pricing via
  energy-aware routing; max added latency **~50 ms**; early deployments processed
  "billions of tokens" ("Energy Aware Routing Cuts…", lines 37, 41).
- Energy is described as **the largest single operating expense** in inference
  infrastructure (recurring, unlike amortized hardware) ("The Hidden Variable…",
  lines 15–21). This is the strategic reason the statusline's energy line is the
  most policy-relevant metric.

### Energy per query (sanity check for per-token coefficients)
- Sam Altman: an average query uses **~0.34 Wh**; 1 kWh → **~2,900 queries**
  ("Sunrun…", line 27).
- A simple reply is **200–2,000 tokens** (avg ~1,000); at 1,000 tok/query, 1 kWh
  ≈ **2.9M tokens** ("Sunrun…", line 29).
- Implied sanity band: ~0.34 Wh / 1,000 tok average ≈ 0.34 Wh per 1k tokens for a
  *typical* query. Cross-check against `coefficients.json` tiers:
  - `small` central: (e_in 0.05 + e_out 0.15)/1k × PUE 1.2 ≈ 0.24 Wh/1k → inside
    the Altman band for cache-light queries. ✅
  - `mid` central: (0.15 + 0.45)/1k × 1.2 ≈ 0.72 Wh/1k → above Altman; expected,
    since Altman's "average query" is assistant-scale, not coding-agent scale. ✅
  - `frontier` central: (0.3 + 0.9)/1k × 1.2 ≈ 1.44 Wh/1k → above; consistent with
    URI's 18.35–40 Wh *per query* (not per 1k tok) cited in `coefficients.json`. ✅
  Conclusion: existing tiers are directionally consistent with the public
  0.34 Wh/query anchor. No coefficient change warranted from this alone.

### Water intensity (cross-check for `region_presets` WUE/EWIF)
- **Aggregate data-center water demand ≈ 1.3 gallons per kWh** of electricity
  consumed (≈ **4.9 L/kWh**): ~0.1 gal/kWh direct on-site cooling + ~1.2 gal/kWh
  indirect thermoelectric generation water ("Water Rights…", line 142).
  - Mapping: direct ~0.1 gal/kWh (≈0.38 L/kWh) ≈ the project's `WUE_site`;
    indirect ~1.2 gal/kWh (≈4.5 L/kWh) ≈ the project's `EWIF_offsite`. The
    article's split **validates the two-term `water_L` equation** in
    `$core_equations`.
- 2023 direct cooling: **~17 billion gallons**; indirect (thermoelectric at
  ~0.20 gal/kWh over 176 TWh) **~35 billion gallons** — indirect ≈ 2x direct
  ("Water Rights…", lines 102, 112). Underscores that `EWIF_offsite` is the
  dominant term and should not be dropped.
- 2028 high case (580 TWh): **700+ billion gallons/yr withdrawal**, **115–150
  billion gallons/yr permanent consumptive loss** (up from ~35B in 2023)
  ("Water Rights…", line 154).
- US electricity ~60% thermoelectric; every such kWh consumes **~1.2 gal** at the
  cooling tower ("Water Rights…", line 142).

### Carbon intensity (cross-check for `carbon_intensity_accounting_note`)
- California grid **marginal** emission rate: **0.5–0.63 tCO₂/MWh** during evening
  peaks (= **500–630 gCO₂/kWh**), **≈0** midday surplus ("California Grid…",
  lines 287, 319).
  - The project's mandated default (location-based Google 2024 = **345 gCO₂/kWh**)
    sits between the CA evening-peak high and the midday near-zero — a reasonable
    *average* anchor, but it hides the **>10x diurnal swing** the grid article
    documents. This is exactly what `LIVE_SIGNAL_ROADMAP.md` and the speculative
    `time_of_day_seasonal_modifiers` exist to capture.
- "Camel profiles" (load following solar) cut marginal emissions **25–40%** vs
  flat load for a 1,000 MW facility ("California Grid…", line 319). Directly
  supports the live-signal thesis: routing/shifting inference to low-carbon hours
  is the real emissions lever, not model choice alone.

### Production credibility of live signals
- CLōD is a **production** system: 6+ datacenters, 4 power markets, 500+ MW,
  real-time price/curtailment/demand-response feeds ("CLōD Scaled…", lines 29–51).
  LōD's platform already manages "billions of kWh annually" ("LōD Launches…",
  line 47). This de-risks the `LIVE_SIGNAL_ROADMAP.md` plan to pull live grid
  signals (WattTime / Electricity Maps) — the mechanism is real, not theoretical.

## Implications for the footprint project

1. **Keep `region_presets` as the primary location lever.** The cloud articles
   confirm location is a 3–5x driver of energy cost and a major driver of water
   (climate) and carbon (grid mix). The two-term `water_L = energy × (WUE_site +
   EWIF)` equation is corroborated by the 1.3 gal/kWh direct+indirect split.
   → *No coefficient change.* Consider promoting `EWIF_offsite` visibility in the
   display, since it dominates total water.

2. **Do NOT add a price-routing → carbon correction.** CLōD optimizes electricity
   *cost*; "cheapest electron" often (not always) means surplus renewable. This is
   consistent with the existing `routing_signal_notes` prohibition. Leave it.

3. **Live grid signal is the highest-value upgrade.** Both the CLōD production
   evidence and the CA-grid marginal-emissions swing point the same way:
   `LIVE_SIGNAL_ROADMAP.md` (live CI + EWIF) is where the biggest accuracy gain
   lives — far more than re-tuning per-token tiers. The CA article's 500–630
   gCO₂/kWh evening-peak vs ≈0 midday is the concrete number to target.

4. **Add a sanity-check fixture.** Use Altman's 0.34 Wh/query (≈2.9M tok/kWh) as a
   regression anchor in `test_footprint_statusline.py`: a balanced `mid`-tier
   session should land in a band that doesn't contradict ~0.34 Wh/1k tok for
   cache-light assistant-scale traffic. Document as non-blocking.

5. **Display wording.** The statusline already tags carbon `(loc-based)`. Given
   the >10x diurnal CI swing, consider surfacing a coarse "grid mix" or
   time-of-day qualifier once live signals land — but only behind the existing
   off-by-default flag per `BUILD_PROMPT.md` (speculative modifiers stay off).

## Open questions for the user
- Should `EWIF_offsite` get its own display segment, or stay folded into `💧`?
- Want the Altman 0.34 Wh/query sanity anchor added as a non-blocking test?
- Any interest in a `region_presets` entry for ERCOT / Texas (CLōD's primary
  market, heavy thermoelectric baseload → high EWIF) given the TX grid articles?
