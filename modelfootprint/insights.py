"""Usage-style session insights: energy composition and contribution drivers.

Pure computation over transcript entries + a compute() result — no network.
Mirrors the cognitive pattern of Claude Code's /usage panel: totals → %
drivers → one-line actions. Does not invent skill/subagent attribution when
the transcript does not expose those fields.
"""
from .engine import compute, fmt_sig, tier_for

BAR_WIDTH = 16
TIER_ORDER = ("small", "mid", "frontier")


def _bar(frac):
    """ASCII bar for a fraction in [0, 1]."""
    frac = max(0.0, min(1.0, float(frac)))
    filled = int(round(frac * BAR_WIDTH))
    filled = min(BAR_WIDTH, max(0, filled))
    return "█" * filled + "░" * (BAR_WIDTH - filled)


def _pct(part, whole):
    if not whole or whole <= 0:
        return 0.0
    return 100.0 * part / whole


def energy_by_token_class(entries, coeffs, region_key, live=None):
    """Central facility energy (Wh) attributed to fresh in / cache / out.

    Each token class is priced alone (other classes zeroed) so the three
    shares sum to the session central energy within floating-point noise.
    """
    parts = {"in": 0.0, "cache": 0.0, "out": 0.0}
    for i, (tin, tcache, tout, model) in enumerate(entries.values()):
        model = model or "unknown"
        if tin:
            r = compute({"e%d" % i: (tin, 0, 0, model)}, coeffs, region_key, live=live)
            parts["in"] += r["energy_Wh"][0]
        if tcache:
            r = compute({"e%d" % i: (0, tcache, 0, model)}, coeffs, region_key, live=live)
            parts["cache"] += r["energy_Wh"][0]
        if tout:
            r = compute({"e%d" % i: (0, 0, tout, model)}, coeffs, region_key, live=live)
            parts["out"] += r["energy_Wh"][0]
    return parts


def energy_by_tier(entries, coeffs, region_key, live=None):
    """Central facility energy (Wh) by resolved model tier + unknown flag."""
    by_tier = {t: 0.0 for t in TIER_ORDER}
    unknown_wh = 0.0
    lookup = coeffs["model_tier_lookup"]
    for i, (tin, tcache, tout, model) in enumerate(entries.values()):
        if not (tin or tcache or tout):
            continue
        tier_name, known = tier_for(model, lookup)
        r = compute({"e%d" % i: (tin, tcache, tout, model)}, coeffs, region_key, live=live)
        wh = r["energy_Wh"][0]
        by_tier[tier_name] = by_tier.get(tier_name, 0.0) + wh
        if not known:
            unknown_wh += wh
    return by_tier, unknown_wh


def composition_lines(parts, total_wh):
    """Markdown lines for the energy composition bars."""
    labels = (
        ("cache", "cache reads"),
        ("out", "output"),
        ("in", "fresh input"),
    )
    lines = [
        "### Composition of energy",
        "",
        "_Share of central energy estimate — not a usage quota._",
        "",
        "```",
    ]
    if total_wh <= 0:
        lines.append("(no energy to attribute)")
        lines.append("```")
        return lines
    # Sort by share descending for scanability (like Usage insights order)
    ranked = sorted(labels, key=lambda kv: -parts.get(kv[0], 0.0))
    for key, label in ranked:
        wh = parts.get(key, 0.0)
        frac = wh / total_wh
        lines.append(
            "%s  %s  %s%%  (%s Wh)"
            % (_bar(frac), label.ljust(12), fmt_sig(_pct(wh, total_wh)), fmt_sig(wh))
        )
    lines.append("```")
    return lines


def token_mix(entries):
    tin = tcache = tout = 0
    for a, b, c, _ in entries.values():
        tin += a
        tcache += b
        tout += c
    total = tin + tcache + tout
    return {
        "in": tin,
        "cache": tcache,
        "out": tout,
        "total": total,
        "cache_share": (tcache / total) if total else 0.0,
        "in_share": (tin / total) if total else 0.0,
        "out_share": (tout / total) if total else 0.0,
    }


def contribution_insights(entries, coeffs, region_key, res, live=None):
    """List of insight dicts: {headline, detail, kind}.

    Pattern matches Claude /usage contribution blurbs: bold claim + action.
    """
    insights = []
    total_wh = res["energy_Wh"][0]
    if total_wh <= 0:
        return insights

    parts = energy_by_token_class(entries, coeffs, region_key, live=live)
    by_tier, unknown_wh = energy_by_tier(entries, coeffs, region_key, live=live)
    mix = token_mix(entries)

    # Frontier energy share
    frontier_wh = by_tier.get("frontier", 0.0)
    if frontier_wh / total_wh >= 0.25:
        insights.append({
            "kind": "tier",
            "headline": (
                "**%s%% of your energy came from frontier-tier models**"
                % fmt_sig(_pct(frontier_wh, total_wh))
            ),
            "detail": (
                "Stepping heavy subagents or routine tool loops to a mid-tier "
                "model usually cuts that slice sharply — capability is your call; "
                "the energy difference is not."
            ),
        })

    mid_wh = by_tier.get("mid", 0.0)
    small_wh = by_tier.get("small", 0.0)
    if mid_wh / total_wh >= 0.5 and frontier_wh / total_wh < 0.25:
        insights.append({
            "kind": "tier",
            "headline": (
                "**%s%% of your energy was mid-tier**"
                % fmt_sig(_pct(mid_wh, total_wh))
            ),
            "detail": (
                "Mid is the coding-agent default. For bulk classify/summarize "
                "passes, a small tier often holds quality and saves energy."
            ),
        })
    elif small_wh / total_wh >= 0.7:
        insights.append({
            "kind": "tier",
            "headline": (
                "**%s%% of your energy was already small-tier**"
                % fmt_sig(_pct(small_wh, total_wh))
            ),
            "detail": "Low-energy model mix — further wins are mostly timing and shape.",
        })

    # Cache token share (usage shape)
    if mix["cache_share"] >= 0.5:
        insights.append({
            "kind": "shape",
            "headline": (
                "**%s%% of tokens were cache reads**"
                % fmt_sig(mix["cache_share"] * 100)
            ),
            "detail": (
                "Agent-shaped sessions: cache is the cheapest token class, but "
                "output and fresh prefill still dominate energy when the model "
                "is frontier. Keep long-running agents; avoid re-prefilling "
                "the same context from scratch."
            ),
        })
    elif mix["in_share"] >= 0.5:
        insights.append({
            "kind": "shape",
            "headline": (
                "**%s%% of tokens were fresh input**"
                % fmt_sig(mix["in_share"] * 100)
            ),
            "detail": (
                "Chat-shaped / low-cache work pays full prefill energy. "
                "Reuse threads, enable prompt caching, or /compact mid-task "
                "when context bloats."
            ),
        })

    # Energy from fresh input specifically
    in_wh = parts.get("in", 0.0)
    if in_wh / total_wh >= 0.2:
        insights.append({
            "kind": "prefill",
            "headline": (
                "**%s%% of energy came from fresh input (prefill)**"
                % fmt_sig(_pct(in_wh, total_wh))
            ),
            "detail": (
                "Long contexts without cache hits are expensive even when "
                "output is short. Compact or start a new task when the "
                "working set drifts."
            ),
        })

    out_wh = parts.get("out", 0.0)
    if out_wh / total_wh >= 0.4:
        insights.append({
            "kind": "output",
            "headline": (
                "**%s%% of energy came from output tokens**"
                % fmt_sig(_pct(out_wh, total_wh))
            ),
            "detail": (
                "Decode is ~3× input energy per token. Prefer concise answers "
                "and smaller models for verbose intermediate steps."
            ),
        })

    if unknown_wh / total_wh >= 0.05 or res.get("unknown_model"):
        insights.append({
            "kind": "unknown",
            "headline": (
                "**%s%% of energy used the unknown-model envelope**"
                % fmt_sig(_pct(unknown_wh, total_wh))
            ),
            "detail": (
                "Unrecognized model ids use mid-tier central with a "
                "small-low…frontier-high band and a ? marker. Add a "
                "model_tier_lookup key when you know the class."
            ),
        })

    return insights


def models_summary(entries, coeffs):
    """Compact model list for the session header: name (tier)."""
    lookup = coeffs["model_tier_lookup"]
    seen = {}
    for tin, tcache, tout, model in entries.values():
        if not (tin or tcache or tout):
            continue
        name = model or "unknown"
        tier, known = tier_for(name, lookup)
        seen[name] = (tier, known)
    parts = []
    for name, (tier, known) in sorted(seen.items(), key=lambda kv: kv[0]):
        mark = "" if known else "?"
        parts.append("%s (%s%s)" % (name, tier, mark))
    return parts


def format_insights_section(insights):
    lines = [
        "### What's contributing to your footprint?",
        "",
        "_Approximate, based on this session's transcript — independent "
        "characteristics of usage, not a double-counted breakdown._",
        "",
    ]
    if not insights:
        lines.append("_No strong drivers beyond ordinary mix — see composition above._")
        return lines
    for ins in insights:
        lines.append(ins["headline"])
        lines.append(ins["detail"])
        lines.append("")
    # drop trailing blank
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def by_model_rows(entries, coeffs, region_key, live=None):
    """Rows for the by-model table: (model, tier, known, tin, tcache, tout, energy_wh)."""
    by_model = {}
    for tin, tcache, tout, model in entries.values():
        name = model or "unknown"
        d = by_model.setdefault(name, {"in": 0, "cache": 0, "out": 0})
        d["in"] += tin
        d["cache"] += tcache
        d["out"] += tout
    rows = []
    lookup = coeffs["model_tier_lookup"]
    for model, t in sorted(by_model.items(), key=lambda kv: -sum(kv[1].values())):
        tier_name, known = tier_for(model, lookup)
        sub = {"m": (t["in"], t["cache"], t["out"], model)}
        sub_res = compute(sub, coeffs, region_key, live=live)
        rows.append(
            (model, tier_name, known, t["in"], t["cache"], t["out"], sub_res["energy_Wh"][0])
        )
    return rows
