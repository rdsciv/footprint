"""Recommendation layer: WHEN to prompt (grid timing) and WHICH model to use.

Pure computation over an already-fetched live snapshot (live.py) and the
static coefficients — no network here.

Honesty rules carried over from METHODOLOGY.md:
  - §3.5: electricity *price* is never presented as a carbon proxy.
  - Static time-of-day shapes are labeled speculative, because they are.
  - WattTime's percentile is relative timing information, never g/kWh.
"""
from .engine import BOUNDS, fmt_sig


def best_window(snapshot):
    """From a live CI forecast, find the cleanest hour in the next 24 h.
    Returns None when the snapshot has no usable forecast + current CI."""
    if not snapshot:
        return None
    now_ci = snapshot.get("ci_g_per_kwh")
    forecast = snapshot.get("ci_forecast")
    if now_ci is None or not forecast:
        return None
    pts = [p for p in forecast[:24] if isinstance(p.get("ci"), (int, float))]
    if not pts:
        return None
    best = min(pts, key=lambda p: p["ci"])
    worst = max(pts, key=lambda p: p["ci"])
    return {
        "now_ci": now_ci,
        "best_ci": best["ci"],
        "best_t": best["t"],
        "worst_ci": worst["ci"],
        "worst_t": worst["t"],
        "ratio": (now_ci / best["ci"]) if best["ci"] > 0 else None,
    }


def when_advice(snapshot, coeffs):
    """Human-readable when-to-prompt lines, each honestly labeled with its
    basis. Returns a list of strings (possibly empty)."""
    lines = []
    win = best_window(snapshot)
    if win:
        if win["ratio"] and win["ratio"] >= 1.3:
            lines.append(
                "Grid now: %s gCO2e/kWh. Cleanest hour in next 24h: ~%s g at %s "
                "— deferring heavy sessions there cuts carbon ~%sx. [live forecast]"
                % (
                    fmt_sig(win["now_ci"]),
                    fmt_sig(win["best_ci"]),
                    win["best_t"],
                    fmt_sig(win["ratio"]),
                )
            )
        else:
            lines.append(
                "Grid now: %s gCO2e/kWh — already near the cleanest hour of the "
                "next 24h (~%s g at %s). Good time to prompt. [live forecast]"
                % (fmt_sig(win["now_ci"]), fmt_sig(win["best_ci"]), win["best_t"])
            )
    elif snapshot and snapshot.get("ci_g_per_kwh") is not None:
        lines.append(
            "Grid now: %s gCO2e/kWh [live, no forecast on this API plan]."
            % fmt_sig(snapshot["ci_g_per_kwh"])
        )

    pct = snapshot.get("moer_percentile") if snapshot else None
    if pct is not None:
        band = (
            "dirty (top third of this grid's marginal-emissions range)"
            if pct >= 67
            else "clean (bottom third)"
            if pct <= 33
            else "mid-range"
        )
        lines.append(
            "Marginal signal (WattTime): %dth percentile — %s. "
            "[relative index, not g/kWh]" % (round(pct), band)
        )

    if not lines:
        mods = coeffs.get("time_of_day_seasonal_modifiers", {})
        if mods:
            lines.append(
                "No live grid signal configured. Static shape (SPECULATIVE, see "
                "coefficients.json): solar-peak hours (~10:00-16:00 local) tend "
                "toward lower marginal carbon on solar-heavy grids; the evening "
                "peak (~17:00-21:00) tends dirtier. Configure FOOTPRINT_EM_TOKEN "
                "+ FOOTPRINT_SITE for real numbers."
            )
    return lines


def _energy_for_tier(totals, tier, pue):
    return tuple(
        (
            totals["in"] * tier["e_in_Wh_per_1k_tok"][b]
            + totals["cache"] * tier["e_cache_Wh_per_1k_tok"][b]
            + totals["out"] * tier["e_out_Wh_per_1k_tok"][b]
        )
        / 1000.0
        * pue[b]
        for b in BOUNDS
    )


def tier_alternatives(totals, coeffs):
    """Same token profile re-priced at every tier. Returns
    [(tier_name, energy_Wh_tuple, description), ...] ordered small->frontier."""
    pue = coeffs["infrastructure_overhead"]["PUE_typical_hyperscale"]
    out = []
    for name in ("small", "mid", "frontier"):
        tier = coeffs["model_tiers"][name]
        out.append((name, _energy_for_tier(totals, tier, pue), tier.get("description", "")))
    return out


def which_advice(res, coeffs):
    """Given a computed session result, suggest the cheapest tier that is a
    real alternative, with the honest caveat that capability is the user's
    call. Returns a list of strings."""
    totals = res["tokens"]
    if not any(totals.values()):
        return []
    alts = tier_alternatives(totals, coeffs)
    actual = res["energy_Wh"][0]
    lines = []
    cache_share = (
        totals["cache"] / float(totals["in"] + totals["cache"] + totals["out"])
        if any(totals.values())
        else 0.0
    )
    small = next(a for a in alts if a[0] == "small")
    if actual > 0 and small[1][0] < actual * 0.9:
        pct = (1 - small[1][0] / actual) * 100
        lines.append(
            "Same token volume on a small-tier model (Haiku-class): %s Wh central "
            "— about %d%% less energy. Whether a small model can do this task is "
            "your call; the footprint difference is not."
            % (fmt_sig(small[1][0]), round(pct))
        )
    if cache_share >= 0.8:
        lines.append(
            "%d%% of this session's tokens were cache reads — already the "
            "cheapest token class (~5-10x below fresh input)." % round(cache_share * 100)
        )
    return lines
