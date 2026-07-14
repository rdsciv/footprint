# LIVE-SIGNAL ROADMAP

`coefficients.json` today is a static, versioned snapshot. This document describes the path from that snapshot to hourly-refreshed, location- and time-aware estimates, and is organized by which term in the core model (METHODOLOGY.md §1.2) each live signal improves.

```
energy_IT_Wh       = tok_in * e_in + tok_cache * e_cache + tok_out * e_out
energy_facility_Wh = energy_IT_Wh * PUE
water_L   = energy_IT_kWh * WUE_site(location, t) + energy_facility_kWh * EWIF_grid(location, t)
carbon_g  = energy_facility_kWh * CI_grid(location, t)
```

## 1. Grid carbon intensity, CI_grid(location, t) — highest priority, most mature APIs

**WattTime** and **Electricity Maps** both provide APIs that expose real-time and forecast grid carbon intensity by balancing authority / region, and — critically for this methodology's marginal-vs-average distinction (LIMITATIONS_AND_FAQ.md) — WattTime specifically computes and exposes a *marginal* emissions signal (the emissions rate of the generating resource that would respond to a small change in demand), not just an average. Google's own carbon-intelligent computing platform is documented as combining Electricity Maps' hourly carbon-intensity forecasts with internal power-demand forecasting (METHODOLOGY.md §3.3) — i.e., this is a proven integration pattern at production scale, not a speculative one.

**Integration sketch:** given a user-configured or provider-disclosed region, call the grid-intensity API for the current hour (or a recent cached value, refreshed on an hourly cadence to stay within free-tier rate limits), and substitute the live value for the static `carbon_intensity_accounting_note` figures in `coefficients.json`. This single integration would upgrade the carbon term from a once-a-year-updated static number to genuinely hourly-resolved, and would let the tool switch its default from location-based-average to marginal — closing the gap flagged in the FAQ.

**Effort:** low. Both APIs are REST, well-documented, and have free tiers suitable for a low-frequency (hourly) client-side poll. This is the single highest-value, lowest-effort item on this roadmap.

## 2. Wholesale electricity price / LMP feeds — improves §3's price-routing coupling assessment, not a direct model input today

ISO/RTO operators (ERCOT, PJM, CAISO, MISO, and others) publish real-time and day-ahead locational marginal prices, typically via public dashboards and, for some ISOs, machine-readable APIs. This methodology currently treats the price-routing-footprint chain as too attenuated to use as a numeric coefficient input (METHODOLOGY.md §3.5) — but a live LMP feed would let a future version of this tool at least display *supporting context* (e.g., "grid prices in this region are currently elevated, which historically correlates with higher marginal carbon intensity") without claiming a precise causal adjustment. This is best framed as an informational overlay, not a term in the core equations, unless and until gateway-level routing telemetry (see item 4) closes the loop between "a route was chosen" and "a specific grid node served it."

**Effort:** moderate — ISO API access and data formats are heterogeneous across regions, and most only cover the US.

## 3. Weather-API wet-bulb temperature — improves WUE_site(location, t)

WUE_site's dependence on outside temperature (METHODOLOGY.md §4.1) is the most physically direct live-signal opportunity in the water term: any standard weather API that reports (or from which one can derive) wet-bulb temperature for a datacenter's metro area can drive an hourly WUE_site multiplier far more precisely than the static diurnal/seasonal multipliers currently in `coefficients.json`'s `time_of_day_seasonal_modifiers` block (which are explicitly labeled speculative placeholders). A simple threshold model — consistent with the economizer-threshold mechanism Microsoft itself describes (water introduced only above ~29.4°C/85°F) — could replace the flat seasonal multiplier with an actual temperature-triggered function once a specific facility's economizer threshold and cooling architecture are known or reasonably assumed for its region class.

**Effort:** low for the weather data itself (many free/low-cost weather APIs exist); moderate for calibrating the threshold-response curve per region class without per-facility ground truth (this remains a MODELED step even with live weather data, since the actual facility-level threshold is not public).

## 4. Provider/gateway routing telemetry — currently absent, would be the most valuable single addition to close the loop in §3

Today, no reviewed API (OpenRouter, provider-native APIs) exposes which physical facility, region, or grid node actually served a given request. If a provider or gateway ever begins exposing even a coarse "served from region X" field per response (analogous to how many cloud APIs already expose a region in their response metadata for other purposes), this would upgrade the entire routing-coupling analysis in METHODOLOGY.md §3 from "theoretically real but unmeasurable" to "directly actionable" — it would let the tool select the correct region preset (§4.3) and the correct live carbon/water signal (items 1 and 3) automatically per request, instead of relying on a user-configured default region. This is listed as a roadmap item rather than a current feature because it depends on a disclosure this methodology cannot compel from providers; it is flagged here so that if such disclosure appears, integrating it should be treated as a priority upgrade over refining any of the static coefficients.

## 5. Provider-published per-query energy/carbon/water disclosures — the ultimate replacement for tiered proxy coefficients

Google's arXiv:2508.15734 disclosure and Mistral's audited LCA are early examples of providers publishing their own measured, model-specific footprint figures. As more providers follow this pattern (and as existing ones update their own figures — Google's median-prompt energy figure alone fell 33x over about a year of internal efficiency work), the `model_tiers` table in `coefficients.json` should be progressively replaced, model by model, with provider-disclosed MEASURED coefficients in place of the current MODELED tier-proxy estimates. This is not a new integration so much as an ongoing maintenance commitment: each new provider disclosure is a strict upgrade over the tiered proxy for that specific model, and the coefficient table's schema (§5 of METHODOLOGY.md, `{central, low, high, label, source}`) is designed to accept a narrower-band MEASURED entry alongside or in place of a wider-band MODELED one without requiring a schema change.

## Suggested build order

1. Grid carbon intensity API (WattTime or Electricity Maps) — highest value, lowest effort, directly closes the marginal-vs-average gap.
2. Weather-API wet-bulb temperature — second-highest value, direct physical mechanism, moderate effort.
3. Per-model provider disclosures — ongoing maintenance task, not a one-time integration; track as new disclosures publish.
4. LMP feeds — informational overlay only, given the weak coupling established in §3; lower priority than items 1-3.
5. Gateway routing telemetry — currently blocked on provider/gateway disclosure that does not yet exist; revisit if this changes.
