"""Core footprint engine: transcript parsing, model tiering, and the
energy/water/carbon equations from METHODOLOGY.md.

Every coefficient comes from coefficients.json (path: FOOTPRINT_COEFFS env
var, default: bundled with this package) — nothing numeric is hardcoded
except unit conversions.

Accounting boundaries (METHODOLOGY.md §2.2/§1.2, v0.3.0):
  - Tier coefficients are interpreted as IT-equipment (node) energy per
    token. PUE converts IT energy to facility energy.
  - water_L = energy_IT_kWh × WUE_site + energy_facility_kWh × EWIF_grid
    (WUE is defined per IT kWh by its Microsoft source; EWIF applies to all
    electricity drawn from the grid, i.e. facility energy).
  - carbon_g = energy_facility_kWh × CI_grid.

Uncertainty semantics (§5.1): the [low–high] figures are a SCENARIO ENVELOPE
(every factor at its low/high bound simultaneously), not an independent
confidence interval. Full precision is carried internally; rounding happens
only at display.

Display rules (§5.2): energy 2 sig figs with [low–high] range; water 1–2 sig
figs with "~" prefix; carbon always tagged with its accounting basis; unknown
model tier -> "?" marker. Missing transcript -> "–" placeholders; unreadable
coefficients -> explicit error glyph, never a fabricated 0.0.
"""
import json
import math
import os

PLACEHOLDER_TOKENS = "– in · – cache · – out"
PLACEHOLDER_LINE = "⚡ – Wh 💧 – mL 🌫 – gCO2e (loc-based) | " + PLACEHOLDER_TOKENS
BOUNDS = ("central", "low", "high")
SUPPORTED_SCHEMA_MAJOR = 0

_ENERGY_KEYS = ("e_in_Wh_per_1k_tok", "e_cache_Wh_per_1k_tok", "e_out_Wh_per_1k_tok")


def _finite(v):
    """Return v as a finite float, or None."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def _check_range(d, where):
    if not isinstance(d, dict):
        raise ValueError("%s: expected {central,low,high} dict" % where)
    vals = [_finite(d.get(b)) for b in BOUNDS]
    if any(v is None for v in vals):
        raise ValueError("%s: central/low/high must be finite numbers" % where)
    c, lo, hi = vals
    if not (lo <= c <= hi) or lo < 0:
        raise ValueError("%s: requires 0 <= low <= central <= high" % where)


def validate_coefficients(coeffs):
    """Structural validation of coefficients.json. Raises ValueError on any
    shape/type/ordering problem so callers fail closed to the documented
    error display instead of computing with garbage."""
    if not isinstance(coeffs, dict):
        raise ValueError("coefficients: not a JSON object")
    ver = str(coeffs.get("$schema_version", ""))
    major = ver.split(".")[0] if ver else ""
    if not major.isdigit() or int(major) != SUPPORTED_SCHEMA_MAJOR:
        raise ValueError(
            "coefficients: schema version %r not supported (engine supports major %d)"
            % (ver, SUPPORTED_SCHEMA_MAJOR)
        )
    tiers = coeffs.get("model_tiers")
    if not isinstance(tiers, dict) or not tiers:
        raise ValueError("coefficients: model_tiers missing")
    for name, tier in tiers.items():
        if name.startswith(("$", "_")):
            continue
        for key in _ENERGY_KEYS:
            _check_range(tier.get(key), "model_tiers.%s.%s" % (name, key))
    if not isinstance(coeffs.get("model_tier_lookup"), dict):
        raise ValueError("coefficients: model_tier_lookup missing")
    _check_range(
        coeffs.get("infrastructure_overhead", {}).get("PUE_typical_hyperscale"),
        "infrastructure_overhead.PUE_typical_hyperscale",
    )
    regions = coeffs.get("region_presets")
    if not isinstance(regions, dict):
        raise ValueError("coefficients: region_presets missing")
    for name, reg in regions.items():
        if name.startswith(("$", "_")):
            continue
        _check_range(reg.get("WUE_site_L_per_kWh"), "region_presets.%s.WUE" % name)
        _check_range(
            reg.get("EWIF_offsite_L_per_kWh_seasonal_fallback"),
            "region_presets.%s.EWIF" % name,
        )
    note = coeffs.get("carbon_intensity_accounting_note", {})
    lb = note.get("google_2024_example_gCO2e_per_kWh", {}).get("location_based")
    if _finite(lb) is None:
        raise ValueError("coefficients: location_based CI missing")
    return coeffs


def load_coefficients():
    path = os.environ.get("FOOTPRINT_COEFFS") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "coefficients.json"
    )
    with open(path, encoding="utf-8") as f:
        return validate_coefficients(json.load(f))


def resolve_region(coeffs, region=None, site_region=None):
    """One resolution rule everywhere: explicit arg > FOOTPRINT_REGION >
    the configured site's climate class > temperate."""
    candidate = (
        region
        or os.environ.get("FOOTPRINT_REGION")
        or site_region
        or "temperate"
    )
    presets = coeffs.get("region_presets", {})
    if candidate not in presets or candidate.startswith(("_", "$")):
        candidate = "temperate"
    return candidate


def _key_matches(key, model_id):
    """True when `key` appears in model_id bounded by non-alphanumerics (or
    string edges) — 'o1' matches 'o1-preview' but not 'model-o12'."""
    start = 0
    while True:
        i = model_id.find(key, start)
        if i < 0:
            return False
        before_ok = i == 0 or not model_id[i - 1].isalnum()
        j = i + len(key)
        after_ok = j == len(model_id) or not model_id[j].isalnum()
        if before_ok and after_ok:
            return True
        start = i + 1


def tier_for(model_id, lookup):
    """Anchored substring match against model_tier_lookup; longest match wins
    (so 'gpt-4o-mini' resolves to its own entry, not 'gpt-4o').
    Returns (tier_name, known)."""
    mid = (model_id or "").lower()
    best_key = None
    for key, tier in lookup.items():
        if key.startswith(("_", "$")):
            continue
        if _key_matches(key, mid) and (best_key is None or len(key) > len(best_key[0])):
            best_key = (key, tier)
    if best_key:
        return best_key[1], True
    return lookup.get("_default_unknown_model", "mid"), False


def parse_transcript(path):
    """Sum usage across assistant entries, deduplicated by message.id
    (last-seen usage wins — streaming rewrites the same id multiple times).
    Sidechain entries are included: they are real API calls.
    Returns {id: (tok_in, tok_cache, tok_out, model_id)}."""
    entries = {}
    lineno = 0
    with open(path, "rb") as f:
        for raw in f:
            lineno += 1
            if b'"usage"' not in raw:
                continue
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(d, dict) or d.get("type") != "assistant":
                continue
            m = d.get("message")
            if not isinstance(m, dict):
                continue
            u = m.get("usage")
            if not isinstance(u, dict):
                continue

            def tok(key):
                v = u.get(key)
                return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0

            # Cache writes are fully computed prefill: they belong in tok_in
            # at e_in, NOT in tok_cache (only cache *reads* get e_cache).
            tok_in = tok("input_tokens") + tok("cache_creation_input_tokens")
            tok_cache = tok("cache_read_input_tokens")
            # Thinking tokens are already inside output_tokens and cost full
            # e_out (§2.4) — no subtraction.
            tok_out = tok("output_tokens")
            key = m.get("id") or d.get("uuid") or "line%d" % lineno
            entries[key] = (tok_in, tok_cache, tok_out, m.get("model"))
    return entries


def _entry_energy(tier, tok_in, tok_cache, tok_out, bound):
    return (
        tok_in * tier["e_in_Wh_per_1k_tok"][bound]
        + tok_cache * tier["e_cache_Wh_per_1k_tok"][bound]
        + tok_out * tier["e_out_Wh_per_1k_tok"][bound]
    ) / 1000.0


def _ci_bounds(coeffs, live_ci):
    """CI as (central, low, high). Live CI carries a MODELED ±25%
    forecast/measurement band; static CI uses the location-based hourly/
    regional spread from coefficients when present, else the point value."""
    if live_ci is not None:
        return (live_ci, live_ci * 0.75, live_ci * 1.25)
    note = coeffs["carbon_intensity_accounting_note"]
    central = note["google_2024_example_gCO2e_per_kWh"]["location_based"]
    rng = note.get("location_based_hourly_range_gCO2e_per_kWh")
    if isinstance(rng, dict):
        lo, hi = _finite(rng.get("low")), _finite(rng.get("high"))
        if lo is not None and hi is not None and lo <= central <= hi:
            return (central, lo, hi)
    return (central, central, central)


def compute(entries, coeffs, region_key, live=None):
    """Core equations (METHODOLOGY.md v0.3.0, coefficients $core_equations):
      energy_IT_Wh       = tok_in*e_in + tok_cache*e_cache + tok_out*e_out
                           (coefficients are per 1K tokens, IT/node boundary)
      energy_facility_Wh = energy_IT_Wh * PUE
      water_L            = energy_IT_kWh * WUE_site
                           + energy_facility_kWh * EWIF_grid
      carbon_g           = energy_facility_kWh * CI_grid  (location-based §1.4)
    [low–high] is a scenario envelope: every factor at its bound at once
    (§5.1). Full precision carried; rounding only at display.

    Unknown models take the mid tier's central estimate but an envelope that
    spans small-tier lows to frontier-tier highs — the promised wider band.

    `live` (optional): a fresh live-signal snapshot dict (see live.py).
    ci_g_per_kwh / wue_site_L_per_kWh, when present and finite, replace the
    static central values; the result is annotated so the display can label
    the basis honestly. Non-finite live values are ignored, never propagated.
    """
    tiers = coeffs["model_tiers"]
    lookup = coeffs["model_tier_lookup"]
    totals = {"in": 0, "cache": 0, "out": 0}
    unknown_model = False
    it_wh = [0.0, 0.0, 0.0]  # IT-boundary Wh at central/low/high, before PUE
    unknown_low_tier = tiers.get("small")
    unknown_high_tier = tiers.get("frontier")
    for tok_in, tok_cache, tok_out, model in entries.values():
        totals["in"] += tok_in
        totals["cache"] += tok_cache
        totals["out"] += tok_out
        tier_name, known = tier_for(model, lookup)
        t = tiers[tier_name]
        if known or unknown_low_tier is None or unknown_high_tier is None:
            per_bound = (t, t, t)
        else:
            # Unknown model: central at the default tier, envelope across the
            # full small-low..frontier-high tier spread.
            if tok_in or tok_cache or tok_out:
                unknown_model = True
            per_bound = (t, unknown_low_tier, unknown_high_tier)
        for i, b in enumerate(BOUNDS):
            it_wh[i] += _entry_energy(per_bound[i], tok_in, tok_cache, tok_out, b)

    pue = coeffs["infrastructure_overhead"]["PUE_typical_hyperscale"]
    energy_Wh = tuple(it_wh[i] * pue[b] for i, b in enumerate(BOUNDS))

    region = coeffs["region_presets"][region_key]
    wue = region["WUE_site_L_per_kWh"]
    ewif = region["EWIF_offsite_L_per_kWh_seasonal_fallback"]

    live_wue = _finite(live.get("wue_site_L_per_kWh")) if live else None
    live_ci = _finite(live.get("ci_g_per_kwh")) if live else None

    # Water: WUE applies to IT energy (its Microsoft source defines L per IT
    # kWh); EWIF applies to all grid electricity, i.e. facility energy.
    if live_wue is not None:
        # Live WUE is a point estimate for the central value; the envelope
        # never narrows below the static preset spread.
        water_mL = (
            it_wh[0] * live_wue + energy_Wh[0] * ewif["central"],
            it_wh[1] * min(live_wue, wue["low"]) + energy_Wh[1] * ewif["low"],
            it_wh[2] * max(live_wue, wue["high"]) + energy_Wh[2] * ewif["high"],
        )
    else:
        # kWh * (L/kWh) * 1000 mL/L == Wh * (L/kWh) numerically
        water_mL = tuple(
            it_wh[i] * wue[b] + energy_Wh[i] * ewif[b] for i, b in enumerate(BOUNDS)
        )

    ci = _ci_bounds(coeffs, live_ci)
    carbon_g = tuple(energy_Wh[i] / 1000.0 * ci[i] for i in range(3))

    return {
        "tokens": totals,
        "energy_Wh": energy_Wh,
        "energy_IT_Wh": tuple(it_wh),
        "water_mL": water_mL,
        "carbon_g": carbon_g,
        "unknown_model": unknown_model,
        "ci_basis": "live-grid" if live_ci is not None else "loc-based",
        "ci_g_per_kwh": ci[0],
        "wue_basis": "live-weather" if live_wue is not None else "preset",
    }


def fmt_sig(x, n=2):
    """Format x to n significant figures, plain decimal notation."""
    if x == 0:
        return "0"
    d = n - 1 - math.floor(math.log10(abs(x)))
    y = round(x, d)
    if abs(y) >= 10 ** (math.floor(math.log10(abs(x))) + 1):
        # rounding crossed a power of ten (e.g. 0.0999 -> 0.10): one less decimal
        d -= 1
        y = round(x, d)
    return "%.*f" % (d, y) if d > 0 else str(int(round(y)))


def fmt_tok(n):
    if n < 1000:
        return str(n)
    if n < 10000:
        return "%.1fk" % (n / 1000.0)
    if n < 1000000:
        return "%.0fk" % (n / 1000.0)
    return "%.1fM" % (n / 1000000.0)


def render(res, verbose=False):
    e, w, c = res["energy_Wh"], res["water_mL"], res["carbon_g"]
    q = "?" if res["unknown_model"] else ""
    if res.get("ci_basis") == "live-grid":
        c_tag = "(live-grid %sg)" % fmt_sig(res["ci_g_per_kwh"])
    else:
        c_tag = "(loc-based)"
    parts = [
        "⚡ %s%s Wh [%s–%s]" % (fmt_sig(e[0]), q, fmt_sig(e[1]), fmt_sig(e[2])),
        "💧 ~%s mL" % fmt_sig(w[0])
        + (" [%s–%s]" % (fmt_sig(w[1]), fmt_sig(w[2])) if verbose else ""),
        "🌫 %s gCO2e %s" % (fmt_sig(c[0]), c_tag)
        + (" [%s–%s]" % (fmt_sig(c[1]), fmt_sig(c[2])) if verbose else ""),
    ]
    t = res["tokens"]
    tokens = "%s in · %s cache · %s out" % (
        fmt_tok(t["in"]),
        fmt_tok(t["cache"]),
        fmt_tok(t["out"]),
    )
    return " ".join(parts) + " | " + tokens
