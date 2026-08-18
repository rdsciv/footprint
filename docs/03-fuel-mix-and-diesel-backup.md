# Fuel Mix and the Diesel in the Basement

*Part 3 of the [footprint](../README.md) deep-dive series.*

A kilowatt-hour is not a kilowatt-hour. Depending on what generates it, the same unit of electricity powering the same inference carries anywhere from **~11 to ~1,000 grams of CO₂e** — a two-orders-of-magnitude spread that dwarfs every model-efficiency difference in this project. This doc walks the generation ladder, and then goes down to the basement, where the diesel generators live.

<img src="./img/generation-ci.svg" alt="Lifecycle carbon intensity by generation source, gCO2e per kWh: wind 11, nuclear 12, hydro 24, utility solar 43, gas combined-cycle 490, coal 820, diesel backup genset ~780 direct" width="100%">

## The generation ladder

Lifecycle carbon intensity (construction + fuel + operation + decommissioning), median figures from the IPCC AR5 harmonization with NREL cross-checks — these are the `generation_carbon_intensity` entries in [`coefficients.json`](../modelfootprint/coefficients.json):

| source | gCO₂e/kWh (median) | range |
|---|---|---|
| wind (onshore) | **11** | 7–56 |
| nuclear | **12** | 4–110 |
| hydro | **24** | 1–2,200* |
| solar (utility PV) | **~43** | 10–80 |
| gas (combined cycle) | **490** | 410–650 |
| coal | **820** | 740–910 |
| **diesel (backup genset, direct)** | **~780** | 680–890 |

*\*the hydro high tail is tropical reservoirs with high methane flux; temperate hydro sits near the median.*

Two things to take from the ladder. First, the renewable-to-fossil gap is **not incremental — it's 20–80×**. Wind-powered inference and diesel-powered inference are different products with the same output. Second, grid mix is a *location* variable: an Iowa datacenter (wind-heavy MISO) and a Singapore datacenter (gas-heavy) can differ 5–10× in carbon per identical kWh, before any time-of-day effect. This is why `FOOTPRINT_SITE` exists and why doc 02's live grid signal is the single most valuable configuration in this tool.

## The diesel fleet is bigger than you think

Every serious datacenter keeps diesel generators for outages — that's good engineering. What's less appreciated is the scale. **Virginia DEQ's 2023 variance record counted 4,021 Tier II and 130 Tier IV units in Loudoun = 4,151 permitted generators.** A 2025 Piedmont Environmental Council census puts *eastern* Loudoun nearer **4,700 units / ~12 GW** nameplate — that is PEC, not a live DEQ table, and nameplate is not simultaneous compute (2N backup ≫ IT load). Either way: gigawatts of diesel sit in one county.

How often do they actually run?

- **Permitted ceiling**: Virginia air permits typically cap each generator at **500 hours/year** across all purposes (~21 days), with routine allowances around **50 hours/year of non-emergency use** (testing, commissioning) before stricter controls trigger.
- **Typical reality**: JLARC (Dec 2024) — operators reported 0–2 minor outages per site in two years, typically 1–5 hours. Monthly tests plus rare events. *Annualized diesel kWh is usually well under 1%.*
- **Payment vs order**: PJM capacity / ELRP can pay enrolled *non-emergency* backup to drop grid load (2026/27 BRA cleared ~$329/MW-day). Virginia **emergency** general-permit units are barred from economic DR (9VAC5-540-40 D.2). Separately, 2026 DOE FPA §202(c) orders *authorized* PJM and ERCOT to direct datacenter backup generation — including hyperscalers — as a last resort before/during EEA-3. Authorization is not a public list of campuses that actually started.
- **The growth vector**: DEQ's APG-576 revision (Tier 4 BACT for applications on/after 1 Jul 2026) and 2025 planned-outage guidance are the policy edges. ERCOT's interconnection queue creates the same pressure: when the grid can't serve everyone at peak, on-site generation becomes a market or emergency resource.

## Why the *when* makes diesel matter more than its energy share

Here is where docs 02 and 03 meet. Diesel's ~780 g/kWh *direct* is in the same ballpark as a coal *plant's* lifecycle 820 — those are different scopes, so do not say “2.5% worse than coal.” The annual energy share is tiny. The catch is **correlation with the dirtiest hours**:

- Grid-stress events — the moments a 202(c) order or an ISO emergency might start permitted backup — are exactly the evening-ramp / heat-wave hours when the grid's own marginal intensity is already at its 500–630 g/kWh worst.
- A prompt served **if that campus were on diesel** rides on **~780 g/kWh direct combustion** (680–890; EPA 10.21 kg CO₂/gal × 0.067–0.080 gal/kWh). Versus wind at 11 g, that is tens of times dirtier. IPCC AR5 Table A.III.2 has **no oil/diesel row** — the old 840 figure was a circulating oil-*plant* lifecycle midpoint (SRREN-era), not AR5, and mixed scopes with coal's 820 lifecycle number.
- **We cannot see it from outside.** Behind-the-meter diesel does not appear in Electricity Maps, WattTime, or ISO “oil” fuel mix (those are grid-connected peakers). If a campus islands, public CI can even look *cleaner* because load dropped. A public tool that silently folds 780 g/kWh into every prompt is inventing a measurement.
- Beyond CO₂, diesel gensets emit NOx and PM2.5 *in the neighborhood* — the local air-quality dimension that has made Loudoun and Prince William County residents the loudest stakeholders in this fight (arXiv:2509.21312 quantifies the Texas version).

The system-level fix is real and underway: batteries (CAISO went from 0.5 to 13+ GW in five years) and gas/hydrogen fuel cells are displacing diesel for both backup and peak-shaving, and hyperscalers are early adopters. But in 2026, the diesel fleet is still growing with the buildout.

## Contracts vs. electrons: reading provider claims

When a provider says "we run on 100% renewable energy," that is usually a **market-based** claim: they purchased certificates or PPAs matching their annual consumption. Physically — **location-based** — their facilities draw whatever the local grid serves each hour. Google's own 2024 numbers make the gap concrete: **345 g/kWh location-based vs 94 g/kWh market-based** for the same fleet, same year. Both accountings are legitimate and audited; they answer different questions. This project reports **location-based** (the electrons), because it's the number your marginal prompt actually moves, and labels it on every figure. A provider running "100% renewable" on contracts can still be serving your 7pm prompt from a gas peaker — and, during a grid emergency, from the diesels.

## What `footprint` does with this

- The generation ladder ships in `coefficients.json` as labeled, sourced context (`generation_carbon_intensity_gCO2e_per_kWh`). Session `carbon_g` uses grid-level CI only.
- `diesel_backup.direct_g_per_kWh` (780 [680–890]) is a **MODELED overlay**: `/footprint` and the site can show “if this kWh were diesel.” It is never written into `carbon_g` unless a measured on-site signal exists (none is public).
- `diesel_risk` on the live snapshot is `none` until a later regional emergency feed. Even then it is a balancing-area flag, not “this building.”
- Live grid CI (doc 02) embeds the *grid* fuel mix of your configured zone. It does **not** include basement gensets.
- Carbon figures are always tagged `(loc-based)` or `(live-grid …)`.

## Sources

- IPCC AR5 WG3 Annex III, lifecycle emission factors (wind/nuclear/hydro/gas/coal — **no oil/diesel row**); [NREL LCA harmonization](https://data.nrel.gov/submissions/171)
- Diesel genset **direct** ~780 g/kWh: [EPA GHG Emission Factors Hub 2024](https://www.epa.gov/system/files/documents/2024-02/ghg-emission-factors-hub-2024.xlsx) Distillate No. 2 10.21 kg CO₂/gal × 0.067–0.080 gal/kWh; [EIA](https://www.eia.gov/environment/emissions/co2_vol_mass.php) 10.19 kg/gal cross-check
- Loudoun census: 2023 DEQ 4,151 (4,021 Tier II + 130 Tier IV) via [Bay Journal](https://www.bayjournal.com/news/energy/virginia-deq-tightens-footprint-extends-comment-period-on-data-center-variance/article_777bb33c-bd2f-11ed-bd3d-93ee2229a1de.html); PEC 2025 ~4,700 / 12 GW [PEC](https://www.pecva.org/work/energy-work/proposed-increase-to-data-center-diesel-generator-use/)
- Permit hours: [9VAC5-540-170](https://law.lis.virginia.gov/admincodefull/title9/agency5/chapter540/), [40 CFR 63.6640(f)](https://www.ecfr.gov/current/title-40/chapter-I/subchapter-C/part-63/subpart-ZZZZ/section-63.6640); emergency units barred from economic DR: 9VAC5-540-40 D.2
- JLARC Dec 2024: [Rpt598](https://jlarc.virginia.gov/pdfs/reports/Rpt598.pdf)
- DOE 202(c) 2026: [energy.gov CESER index](https://www.energy.gov/ceser/2026-doe-202c-orders)
- [Virginia DEQ issued air permits](https://www.deq.virginia.gov/news-info/shortcuts/permits/air/issued-air-permits-for-data-centers), [VPM](https://www.vpm.org/news/2025-12-17/virginia-data-centers-diesel-backup-generators-deq-loudoun-turner-dowd)
- Texas datacenter air-quality assessment: [arXiv:2509.21312](https://arxiv.org/abs/2509.21312)
- Google location vs market-based: [arXiv:2508.15734](https://arxiv.org/abs/2508.15734)
- Battery displacement of peakers: [GridStatus CAISO 2025](https://blog.gridstatus.io/caiso-solar-storage-spring-2025/)
