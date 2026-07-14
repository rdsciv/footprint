/* modelfootprint browser engine — the JavaScript mirror of
 * modelfootprint/engine.py. Keep the two in lockstep: the Python test suite
 * runs site/golden_check.mjs to compare this file's output against the
 * Python engine at full precision, so any drift fails CI.
 *
 * Accounting boundaries (METHODOLOGY.md v0.3.0):
 *   energy_IT       = per-token coefficients (IT/node boundary)
 *   energy_facility = energy_IT * PUE
 *   water           = energy_IT_kWh * WUE_site + energy_facility_kWh * EWIF
 *   carbon          = energy_facility_kWh * CI
 * [low, high] are a scenario envelope, not a confidence interval.
 */
(function (root) {
  "use strict";
  var BOUNDS = ["central", "low", "high"];

  function energyIT(C, tierName, tokens) {
    var t = C.model_tiers[tierName];
    return BOUNDS.map(function (b) {
      return (
        (tokens.in * t.e_in_Wh_per_1k_tok[b] +
          tokens.cache * t.e_cache_Wh_per_1k_tok[b] +
          tokens.out * t.e_out_Wh_per_1k_tok[b]) / 1000
      );
    });
  }

  function ciBounds(C) {
    var note = C.carbon_intensity_accounting_note;
    var central = note.google_2024_example_gCO2e_per_kWh.location_based;
    var rng = note.location_based_hourly_range_gCO2e_per_kWh;
    if (rng && typeof rng.low === "number" && typeof rng.high === "number" &&
        rng.low <= central && central <= rng.high) {
      return [central, rng.low, rng.high];
    }
    return [central, central, central];
  }

  function compute(C, tierName, tokens, regionKey) {
    var it = energyIT(C, tierName, tokens);
    var pue = C.infrastructure_overhead.PUE_typical_hyperscale;
    var energy = BOUNDS.map(function (b, i) { return it[i] * pue[b]; });
    var r = C.region_presets[regionKey];
    var water = BOUNDS.map(function (b, i) {
      return it[i] * r.WUE_site_L_per_kWh[b] +
        energy[i] * r.EWIF_offsite_L_per_kWh_seasonal_fallback[b];
    });
    var ci = ciBounds(C);
    var carbon = energy.map(function (e, i) { return (e / 1000) * ci[i]; });
    return { energyIT: it, energy: energy, water: water, carbon: carbon, ci: ci[0] };
  }

  /* Mirror of Python fmt_sig, including negative-decimal rounding
   * (1234 -> "1200") and the power-of-ten crossing correction
   * (0.0999 -> "0.10", not "0.100"). Exact .5 halves can differ between
   * Python's banker's rounding and JS Math.round — never a concern for
   * measured floats, and the golden fixtures avoid constructed halves. */
  function roundTo(x, d) {
    var f = Math.pow(10, d);
    return Math.round(x * f) / f;
  }
  function fmtSig(x, n) {
    n = n || 2;
    if (x === 0) return "0";
    var d = n - 1 - Math.floor(Math.log10(Math.abs(x)));
    var y = roundTo(x, d);
    if (Math.abs(y) >= Math.pow(10, Math.floor(Math.log10(Math.abs(x))) + 1)) {
      d -= 1;
      y = roundTo(x, d);
    }
    return d > 0 ? y.toFixed(d) : String(Math.round(y));
  }

  function fmtTok(n) {
    if (n < 1e3) return String(n);
    if (n < 1e4) return (n / 1e3).toFixed(1) + "k";
    if (n < 1e6) return Math.round(n / 1e3) + "k";
    return (n / 1e6).toFixed(1) + "M";
  }

  root.MFEngine = {
    BOUNDS: BOUNDS,
    energyIT: energyIT,
    ciBounds: ciBounds,
    compute: compute,
    fmtSig: fmtSig,
    fmtTok: fmtTok,
  };
})(typeof globalThis !== "undefined" ? globalThis : this);
