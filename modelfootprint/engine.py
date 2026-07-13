"""Core footprint engine: transcript parsing, model tiering, and the
energy/water/carbon equations from METHODOLOGY.md.

Every coefficient comes from coefficients.json (path: FOOTPRINT_COEFFS env
var, default at the repo root alongside this package) — nothing numeric is
hardcoded except unit conversions.

Display rules (METHODOLOGY.md §5.2): energy 2 sig figs with [low–high] range;
water 1–2 sig figs with "~" prefix; carbon always tagged with its accounting
basis; unknown model tier -> "?" marker. Missing transcript -> "–"
placeholders; unreadable coefficients -> explicit error glyph, never a
fabricated 0.0.
"""
import json
import math
import os

PLACEHOLDER_TOKENS = "– in · – cache · – out"
PLACEHOLDER_LINE = "⚡ – Wh 💧 – mL 🌫 – gCO2e (loc-based) | " + PLACEHOLDER_TOKENS
BOUNDS = ("central", "low", "high")


def load_coefficients():
    path = os.environ.get("FOOTPRINT_COEFFS") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "coefficients.json",
    )
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def tier_for(model_id, lookup):
    """Substring-match model id against model_tier_lookup; longest match wins
    (so 'gpt-5-nano' resolves to its own entry, not 'gpt-5').
    Returns (tier_name, known)."""
    mid = (model_id or "").lower()
    best_key = None
    for key, tier in lookup.items():
        if key.startswith(("_", "$")):
            continue
        if key in mid and (best_key is None or len(key) > len(best_key[0])):
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
                return v if isinstance(v, int) and v >= 0 else 0

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


def compute(entries, coeffs, region_key, live=None):
    """Core equations from coefficients.json $core_equations:
      energy_Wh = (tok_in*e_in + tok_cache*e_cache + tok_out*e_out) * PUE
                  (coefficients are per 1K tokens)
      water_L   = energy_kWh * (WUE_site + EWIF_grid)
      carbon_g  = energy_kWh * CI_grid   (location-based, §1.4)
    Range propagation (§5.1): low = product of low bounds, high = product of
    high bounds. Full precision carried; rounding happens only at display.

    `live` (optional): a fresh live-signal snapshot dict (see live.py). When
    it carries ci_g_per_kwh / wue_site_L_per_kWh, those replace the static
    CI / WUE_site central values; the energy-side range still propagates, and
    the result is annotated so the display can label the basis honestly.
    """
    tiers = coeffs["model_tiers"]
    lookup = coeffs["model_tier_lookup"]
    totals = {"in": 0, "cache": 0, "out": 0}
    unknown_model = False
    pre_pue = [0.0, 0.0, 0.0]  # Wh at central/low/high, before PUE
    for tok_in, tok_cache, tok_out, model in entries.values():
        totals["in"] += tok_in
        totals["cache"] += tok_cache
        totals["out"] += tok_out
        tier_name, known = tier_for(model, lookup)
        if not known and (tok_in or tok_cache or tok_out):
            unknown_model = True
        t = tiers[tier_name]
        for i, b in enumerate(BOUNDS):
            pre_pue[i] += (
                tok_in * t["e_in_Wh_per_1k_tok"][b]
                + tok_cache * t["e_cache_Wh_per_1k_tok"][b]
                + tok_out * t["e_out_Wh_per_1k_tok"][b]
            ) / 1000.0

    pue = coeffs["infrastructure_overhead"]["PUE_typical_hyperscale"]
    energy_Wh = tuple(pre_pue[i] * pue[b] for i, b in enumerate(BOUNDS))

    region = coeffs["region_presets"][region_key]
    wue = region["WUE_site_L_per_kWh"]
    ewif = region["EWIF_offsite_L_per_kWh_seasonal_fallback"]

    live_wue = live.get("wue_site_L_per_kWh") if live else None
    live_ci = live.get("ci_g_per_kwh") if live else None

    if live_wue is not None:
        # Live wet-bulb-driven WUE_site is a point estimate: substitute it for
        # the central value, keep the static low/high spread for the EWIF term
        # and the energy range. Never narrower than the energy range allows.
        water_mL = (
            energy_Wh[0] * (live_wue + ewif["central"]),
            energy_Wh[1] * (min(live_wue, wue["low"]) + ewif["low"]),
            energy_Wh[2] * (max(live_wue, wue["high"]) + ewif["high"]),
        )
    else:
        # energy_kWh * (L/kWh) * 1000 mL/L == energy_Wh * (L/kWh) numerically
        water_mL = tuple(
            energy_Wh[i] * (wue[b] + ewif[b]) for i, b in enumerate(BOUNDS)
        )

    static_ci = coeffs["carbon_intensity_accounting_note"][
        "google_2024_example_gCO2e_per_kWh"
    ]["location_based"]
    ci = live_ci if live_ci is not None else static_ci
    carbon_g = tuple(e / 1000.0 * ci for e in energy_Wh)

    return {
        "tokens": totals,
        "energy_Wh": energy_Wh,
        "water_mL": water_mL,
        "carbon_g": carbon_g,
        "unknown_model": unknown_model,
        "ci_basis": "live-grid" if live_ci is not None else "loc-based",
        "ci_g_per_kwh": ci,
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
