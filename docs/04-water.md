# Water: The Footprint Nobody Meters

*Part 4 of the [footprint](../README.md) deep-dive series.*

"How much water does my prompt use?" has become the most viral — and most mangled — question in AI environmental discourse. Numbers from 0.26 mL to "a bottle of water per email" circulate freely, and almost none of them state what they're counting. This doc lays out the two-term physical model this project uses, what makes water usage swing with the weather and the clock, and why the biggest term is the one nobody talks about.

## The two-term model

Water attribution has two physically distinct components (METHODOLOGY.md §1.2):

```
water_L = energy_IT_kWh × WUE_site + energy_facility_kWh × EWIF_grid
```

- **WUE_site (on-site)**: water evaporated by the datacenter's own cooling towers or adiabatic systems, per kWh of **IT energy** (that's how Microsoft's disclosure defines it, which is why it multiplies the pre-PUE energy). Fleet-wide average disclosure: **0.27 L/kWh** (FY25).
- **EWIF_grid (off-site)**: water evaporated *generating the electricity* — cooling towers at thermoelectric power plants (gas, coal, nuclear). US aggregate: **~1.2 gal/kWh ≈ 4.5 L/kWh** across the ~60% of US electricity that is thermoelectric.

The aggregate US datacenter figure — **~1.3 gallons of water per kWh consumed** — decomposes as ~0.1 gal direct + ~1.2 gal indirect. Read that again: **the power plant's cooling tower typically evaporates several times more water for your prompt than the datacenter's** (2023 US totals: ~17 billion gallons direct vs ~35 billion indirect). Any water number that only counts on-site cooling is missing the majority of the story. This split is why the tool's water estimate never drops the EWIF term, even though it's grid-dependent and uncertain.

## Withdrawal vs. consumption — the distinction that survives or kills a claim

Two different quantities hide under "water use" (METHODOLOGY.md §4.2):

- **Withdrawal**: water taken from a source and mostly *returned* (warmer). Once-through power-plant cooling withdraws enormous volumes but consumes little.
- **Consumption**: water evaporated — gone from the local watershed until it rains somewhere else.

Both matter (withdrawal stresses ecosystems; consumption depletes), but they differ by an order of magnitude and mixing them is the root of most viral-number fights. This project's figures are **consumption**. Projections for 2028 (580 TWh US datacenter load, high case): **700+ billion gallons/year withdrawal, 115–150 billion gallons/year consumed** — up from ~35 billion consumed in 2023.

## What makes water usage swing: climate, weather, and the clock

On-site WUE is not a constant; it's a function of **wet-bulb temperature** (the temperature air can be evaporatively cooled to — effectively a humidity-adjusted heat measure):

- **Climate (the big lever).** Modern hyperscale facilities use economizers: below a threshold outside temperature, they cool with outside air and use almost no water. Microsoft's disclosed threshold is **29.4 °C (85 °F)**. In Dublin or Amsterdam, water cooling is needed **<5% of hours** — WUE ≈ 0.02 L/kWh. In hot-humid Singapore, up to **40% of hours** — WUE ≈ 0.6. In hot-arid Phoenix, evaporative cooling is the *point* (it's extremely effective in dry air) — WUE around ~1.1, in the region Microsoft itself flags as its highest-priority for improvement (23% YoY gain disclosed). That's a **~50× spread by siting decision alone**, and it's why `FOOTPRINT_REGION`/`FOOTPRINT_SITE` matter more to your water number than anything about your prompt.
- **Season and hour (the honest wiggle).** Wet-bulb temperature peaks midday and in summer; many temperate facilities run near-zero water all winter and spike in July afternoons. The same solar-peak midday hours that are *best* for carbon (doc 02) are often *worst* for on-site water — a genuine tension the tool surfaces rather than hides.
- **Grid mix (the EWIF side).** Evening thermal ramps raise not just carbon but water: gas and coal plants evaporate cooling water per kWh, wind and PV essentially none. A wind-heavy midnight kWh can carry near-zero EWIF; a gas-peaker 7pm kWh carries the full thermoelectric burden.

`footprint` models the on-site term live when configured: Open-Meteo weather → wet-bulb (Stull approximation) → an economizer-threshold ramp between the region preset's low and high bounds. It's labeled MODELED because facility-level thresholds aren't public — and the static seasonal multipliers it replaces are labeled SPECULATIVE, because they are.

## How big is AI water use, really?

Both true at once:

- **Per prompt: small.** Google's measured median: **0.26 mL** (on-site consumption basis). This project's typical chat estimate lands at ~1–5 mL including EWIF — a sip is thousands of prompts.
- **In aggregate, in the wrong place: serious.** Water is local. A hundred megawatts of evaporative cooling in a Phoenix-class drought basin competes with municipal supply in a way the same facility in Dublin never would. 2023's ~52 billion gallons (direct+indirect) was already ~2× the direct figure most reporting cites; the 2028 high case triples consumptive loss. Water rights adjacent to datacenter corridors are being repriced accordingly.

The productive framing isn't "your prompt drinks water" — it's **siting, cooling architecture, and grid mix**, which is exactly the information the region presets and live signals encode.

## What `footprint` shows you

- Water always displays with a `~` prefix and a range — it is the *least* certain of the three metrics (site WUE + grid EWIF are both modeled).
- The EWIF (off-site) term is always included; the two-term split is documented in `coefficients.json` so you can see how much of "your" water is at the power plant.
- With `FOOTPRINT_SITE` configured, the on-site term follows actual weather at a representative datacenter metro, labeled `live-weather (modeled economizer ramp)`.

## Sources

- Microsoft datacenter water disclosures: fleet WUE 0.27 L/kWh (FY25), economizer threshold 29.4 °C, regional water-hours framing (Dublin <5%, hot-humid up to 40%)
- Google median prompt water 0.26 mL: [arXiv:2508.15734](https://arxiv.org/abs/2508.15734)
- US aggregate 1.3 gal/kWh (0.1 direct + 1.2 indirect), 2023 direct ~17B gal / indirect ~35B gal, 2028 projections: "Water Rights: The Hidden Asset" source set (CLOUD_CONTEXT.md); thermoelectric ~0.20 gal/kWh over 176 TWh
- Withdrawal vs consumption treatment: METHODOLOGY.md §4.2 and USGS water-use definitions
- Stull wet-bulb approximation: Stull, R. (2011), *J. Appl. Meteor. Climatol.* 50, 2267–2269
