"""Modeled counterfactuals: what the session footprint would have been under
alternate model tiers, grid timing, or token-split shapes.

Every scenario is labeled as a modeled counterfactual — not a guarantee the
user can force provider routing or interactive deferral. Math goes through
engine.compute() or explicit carbon scaling (energy × CI ratio).
"""
from .engine import compute, fmt_sig, tier_for
from .recommend import best_window

# Canonical model ids that resolve cleanly to each tier via model_tier_lookup.
TIER_CANON = {
    "small": "haiku",
    "mid": "sonnet",
    "frontier": "opus",
}
TIER_DOWN = {
    "frontier": "mid",
    "mid": "small",
    "small": "small",
}

# Profiles mirrored from report.PROFILES (kept local to avoid circular import).
PROFILES = {
    "chat": (0.8, 0.0, 0.2),
    "agent": (0.05, 0.9, 0.05),
    "out-heavy": (0.2, 0.0, 0.8),
}


def _reprice(entries, coeffs, tier_for_entry):
    """Build new entries: same tokens, model remapped to a canon id for a tier.

    tier_for_entry(model, tier_name, known) -> target_tier_name
    """
    lookup = coeffs["model_tier_lookup"]
    out = {}
    for i, (tin, tcache, tout, model) in enumerate(entries.values()):
        tier_name, known = tier_for(model, lookup)
        target = tier_for_entry(model, tier_name, known)
        out["cf%d" % i] = (tin, tcache, tout, TIER_CANON[target])
    return out


def _delta_row(label, baseline, alt, notes, kind):
    """Compare two compute() results; positive savings = baseline - alt."""
    be, bw, bc = baseline["energy_Wh"][0], baseline["water_mL"][0], baseline["carbon_g"][0]
    ae, aw, ac = alt["energy_Wh"][0], alt["water_mL"][0], alt["carbon_g"][0]
    de, dw, dc = be - ae, bw - aw, bc - ac

    def pct(d, b):
        if b <= 0:
            return None
        return 100.0 * d / b

    # Primary lever metric: prefer energy savings when material, else carbon
    if abs(de) >= abs(dc) * 0.01 and be > 0 and de > 0:
        lever_metric = ("energy", pct(de, be))
    elif bc > 0 and dc > 0:
        lever_metric = ("carbon", pct(dc, bc))
    elif be > 0 and de != 0:
        lever_metric = ("energy", pct(de, be))
    else:
        lever_metric = ("carbon", pct(dc, bc) if bc else None)

    return {
        "label": label,
        "kind": kind,
        "notes": notes,
        "energy_Wh": ae,
        "water_mL": aw,
        "carbon_g": ac,
        "save_energy_Wh": de,
        "save_water_mL": dw,
        "save_carbon_g": dc,
        "save_energy_pct": pct(de, be),
        "save_water_pct": pct(dw, bw),
        "save_carbon_pct": pct(dc, bc),
        "lever_metric": lever_metric[0],
        "lever_pct": lever_metric[1],
        "energy_same": abs(de) < 1e-12 * max(1.0, abs(be)),
        "water_same": abs(dw) < 1e-12 * max(1.0, abs(bw)),
    }


def scenario_one_tier_down(entries, coeffs, region_key, baseline, live=None):
    """Each entry's tier steps down one class (small stays small)."""
    if not entries:
        return None

    def step(_model, tier_name, _known):
        return TIER_DOWN.get(tier_name, "mid")

    remapped = _reprice(entries, coeffs, step)
    # No-op if nothing actually stepped down
    lookup = coeffs["model_tier_lookup"]
    changed = False
    for (_tin, _tc, _to, model), (_a, _b, _c, new_m) in zip(
        entries.values(), remapped.values()
    ):
        t0, _ = tier_for(model, lookup)
        t1, _ = tier_for(new_m, lookup)
        if t0 != t1:
            changed = True
            break
    if not changed:
        return None
    alt = compute(remapped, coeffs, region_key, live=live)
    return _delta_row(
        "stepped every model one tier down",
        baseline,
        alt,
        "[modeled counterfactual]",
        "tier_down",
    )


def scenario_all_tier(entries, coeffs, region_key, baseline, tier_name, live=None):
    """Reprice entire session as if every token used one tier."""
    if not entries or tier_name not in TIER_CANON:
        return None
    remapped = _reprice(entries, coeffs, lambda _m, _t, _k: tier_name)
    alt = compute(remapped, coeffs, region_key, live=live)
    return _delta_row(
        "used %s tier for all tokens" % tier_name,
        baseline,
        alt,
        "[modeled counterfactual]",
        "all_%s" % tier_name,
    )


def scenario_timing(baseline, live, coeffs=None):
    """Scale carbon only by cleanest/dirtiest CI in the live forecast.

    Energy and water are unchanged (IT load same; WUE is weather not hour-of-CI).
    Returns (best_row, worst_row) or (None, None).
    """
    win = best_window(live)
    if not win or not win.get("now_ci") or not win.get("best_ci"):
        return None, None
    now = float(win["now_ci"])
    if now <= 0:
        return None, None

    def scale_carbon(ci, label, kind):
        factor = float(ci) / now
        # Synthetic alt result: same energy/water, scaled carbon
        alt = {
            "energy_Wh": baseline["energy_Wh"],
            "water_mL": baseline["water_mL"],
            "carbon_g": tuple(c * factor for c in baseline["carbon_g"]),
        }
        # For "dirtier" we still want savings vs baseline = baseline - alt
        # (negative savings when dirtier)
        row = _delta_row(label, baseline, alt, "[modeled counterfactual · live forecast]", kind)
        row["ci_now"] = now
        row["ci_alt"] = float(ci)
        row["alt_t"] = win.get("best_t") if kind == "timing_best" else win.get("worst_t")
        return row

    best = scale_carbon(
        win["best_ci"],
        "deferred heavy work to cleanest hour (~%s)" % (win.get("best_t") or "?"),
        "timing_best",
    )
    worst = None
    if win.get("worst_ci") and win["worst_ci"] > now * 1.05:
        worst = scale_carbon(
            win["worst_ci"],
            "ran at dirtiest hour in next 24h (~%s)" % (win.get("worst_t") or "?"),
            "timing_worst",
        )
    # Only keep best if it actually saves carbon
    if best and (best["save_carbon_g"] or 0) <= 0:
        best = None
    return best, worst


def scenario_profile(entries, coeffs, region_key, baseline, profile, live=None):
    """Re-split total tokens into a profile (chat/agent/out-heavy) at dominant tier."""
    if not entries or profile not in PROFILES:
        return None
    totals = baseline["tokens"]
    total = totals["in"] + totals["cache"] + totals["out"]
    if total <= 0:
        return None
    # Dominant model by token volume
    counts = {}
    for tin, tcache, tout, model in entries.values():
        name = model or "unknown"
        counts[name] = counts.get(name, 0) + tin + tcache + tout
    dominant = max(counts, key=counts.get)
    fi, fc, fo = PROFILES[profile]
    tin = int(total * fi)
    tcache = int(total * fc)
    tout = total - tin - tcache
    alt_entries = {"whatif": (tin, tcache, tout, dominant)}
    alt = compute(alt_entries, coeffs, region_key, live=live)
    return _delta_row(
        "same tokens with '%s' split on %s" % (profile, dominant),
        baseline,
        alt,
        "[modeled counterfactual · profile reshape]",
        "profile_%s" % profile,
    )


def all_counterfactuals(entries, coeffs, region_key, baseline, live=None):
    """Ordered list of scenario rows for the savings table (skips no-ops)."""
    rows = []
    td = scenario_one_tier_down(entries, coeffs, region_key, baseline, live=live)
    if td:
        rows.append(td)
    for tier in ("mid", "small"):
        r = scenario_all_tier(entries, coeffs, region_key, baseline, tier, live=live)
        if r and (r["save_energy_Wh"] or 0) > 1e-9:
            rows.append(r)
    best, worst = scenario_timing(baseline, live, coeffs)
    if best:
        rows.append(best)
    if worst:
        rows.append(worst)
    # Profile reshape only when current mix is strongly one shape
    mix_total = baseline["tokens"]["in"] + baseline["tokens"]["cache"] + baseline["tokens"]["out"]
    if mix_total > 0:
        cache_share = baseline["tokens"]["cache"] / mix_total
        if cache_share >= 0.5:
            # already agent-like — show chat reshape cost (often higher energy)
            pr = scenario_profile(entries, coeffs, region_key, baseline, "chat", live=live)
            if pr:
                rows.append(pr)
        else:
            pr = scenario_profile(entries, coeffs, region_key, baseline, "agent", live=live)
            if pr:
                rows.append(pr)
    return rows


def largest_lever(rows):
    """Scenario with the largest positive relative savings (energy or carbon)."""
    best = None
    best_score = 0.0
    for r in rows:
        pct = r.get("lever_pct")
        if pct is None or pct <= 0:
            continue
        if pct > best_score:
            best_score = pct
            best = r
    return best


def _fmt_same_or(val, unit, same, prefix=""):
    if same:
        return "same"
    return "%s%s %s" % (prefix, fmt_sig(val), unit)


def format_savings_section(rows):
    """Markdown for 'What you could have saved'."""
    lines = [
        "### What you could have saved",
        "",
        "_Modeled counterfactuals — not a promise you can force provider routing "
        "or always defer interactive work. Carbon timing uses live forecast when "
        "available; tier swaps reprice the same tokens._",
        "",
    ]
    if not rows:
        lines.append(
            "_No alternate scenarios produced a different central estimate "
            "(or no live forecast for timing)._"
        )
        return lines

    lines += [
        "| if you had… | energy | water | carbon | vs this session |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        # vs column: prefer energy % when energy changes; else carbon %
        vs_bits = []
        if not r.get("energy_same") and r.get("save_energy_pct") is not None:
            pe = r["save_energy_pct"]
            vs_bits.append("%s%s%% energy" % ("−" if pe > 0 else "+", fmt_sig(abs(pe))))
        if r.get("save_carbon_pct") is not None and abs(r["save_carbon_pct"]) >= 0.5:
            pc = r["save_carbon_pct"]
            if r.get("energy_same") or abs(pc) > abs(r.get("save_energy_pct") or 0) * 0.5:
                vs_bits.append("%s%s%% carbon" % ("−" if pc > 0 else "+", fmt_sig(abs(pc))))
        if not vs_bits:
            vs = "≈ same"
        else:
            vs = ", ".join(vs_bits)

        e_cell = _fmt_same_or(r["energy_Wh"], "Wh", r.get("energy_same") and r["kind"].startswith("timing"))
        # timing scenarios always same energy
        if r["kind"].startswith("timing"):
            e_cell = "same"
            w_cell = "same"
        else:
            w_cell = "~%s mL" % fmt_sig(r["water_mL"])
        c_cell = "%s gCO2e" % fmt_sig(r["carbon_g"])
        lines.append(
            "| %s | %s | %s | %s | %s |"
            % (r["label"], e_cell, w_cell, c_cell, vs)
        )

    lever = largest_lever(rows)
    lines.append("")
    if lever and lever.get("lever_pct") and lever["lever_pct"] > 0:
        lines.append(
            "**Largest lever this session: %s** (~%s%% %s) %s"
            % (
                lever["label"],
                fmt_sig(lever["lever_pct"]),
                lever["lever_metric"],
                lever.get("notes") or "",
            )
        )
    else:
        lines.append("_No large positive lever among the scenarios above._")
    return lines
