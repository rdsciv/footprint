"""Session reports and what-if estimates, rendered as markdown for the
/footprint command and the CLI.

Layout follows Claude Code's /usage cognitive pattern:
  Session totals → composition bars → contribution insights →
  counterfactual savings → by-model detail → decisions.

Display rules are inherited from METHODOLOGY.md §5.2 via engine.fmt_sig:
2 sig figs, ranges always shown, carbon always basis-labeled, no fabricated
zeros — a section that has no data says so.
"""
import glob
import os
import re

from .counterfactuals import all_counterfactuals, format_savings_section
from .engine import compute, fmt_sig, fmt_tok, parse_transcript, tier_for
from .insights import (
    by_model_rows,
    composition_lines,
    contribution_insights,
    energy_by_token_class,
    format_insights_section,
    models_summary,
)
from .recommend import tier_alternatives, when_advice, which_advice

TOKEN_RE = re.compile(r"^(\d+(?:\.\d+)?)([kKmM]?)$")

# What-if token-split profiles (share of total tokens as in/cache/out).
# 'agent' mirrors the cache-dominated shape of real coding-agent transcripts;
# 'chat' is a cache-less assistant exchange. Directional, like everything here.
PROFILES = {
    "chat": (0.8, 0.0, 0.2),
    "agent": (0.05, 0.9, 0.05),
    "out-heavy": (0.2, 0.0, 0.8),
}
DEFAULT_PROFILE = "agent"


def find_transcript(cwd=None):
    """Locate the most recently modified transcript for this project, plus a
    count of other transcripts active in the last hour (concurrent sessions
    make "most recent" ambiguous — the report says so rather than guessing).
    Claude Code stores transcripts under ~/.claude/projects/<munged-cwd>/.
    Returns (path_or_None, concurrent_count)."""
    cwd = cwd or os.getcwd()
    munged = re.sub(r"[^A-Za-z0-9]", "-", cwd)
    candidates = glob.glob(
        os.path.join(os.path.expanduser("~"), ".claude", "projects", munged, "*.jsonl")
    )
    if not candidates:
        return None, 0
    import time as _time

    newest = max(candidates, key=os.path.getmtime)
    cutoff = _time.time() - 3600
    concurrent = sum(
        1 for c in candidates if c != newest and os.path.getmtime(c) > cutoff
    )
    return newest, concurrent


def parse_token_count(s):
    m = TOKEN_RE.match(s.strip())
    if not m:
        return None
    n = float(m.group(1))
    suffix = m.group(2).lower()
    if suffix == "k":
        n *= 1e3
    elif suffix == "m":
        n *= 1e6
    return int(n)


def whatif_entries(model, total_tokens, profile=DEFAULT_PROFILE, explicit=None):
    """Build a synthetic single-entry usage dict for a hypothetical run."""
    if explicit:
        tin = explicit.get("in", 0)
        tcache = explicit.get("cache", 0)
        tout = explicit.get("out", 0)
    else:
        fi, fc, fo = PROFILES[profile]
        tin = int(total_tokens * fi)
        tcache = int(total_tokens * fc)
        tout = total_tokens - tin - tcache
    return {"whatif": (tin, tcache, tout, model)}


def _equivalences(res, coeffs):
    anchors = coeffs.get("equivalence_anchors")
    if not anchors:
        return []
    out = []
    e, w, c = res["energy_Wh"][0], res["water_mL"][0], res["carbon_g"][0]
    ph = anchors.get("smartphone_full_charge_Wh")
    if ph and e > 0:
        out.append("≈ %s smartphone charges" % fmt_sig(e / ph["central"]))
    led = anchors.get("led_bulb_W")
    if led and e > 0:
        out.append(
            "≈ %s min of a %sW LED bulb"
            % (fmt_sig(e / led["central"] * 60), fmt_sig(led["central"]))
        )
    bottle = anchors.get("water_bottle_mL")
    if bottle and w > 0:
        out.append(
            "≈ %s%% of a %s mL water bottle"
            % (fmt_sig(w / bottle["central"] * 100), fmt_sig(bottle["central"]))
        )
    car = anchors.get("ice_car_gCO2e_per_km")
    if car and c > 0:
        out.append(
            "≈ driving %s m in an average gas car"
            % fmt_sig(c / car["central"] * 1000)
        )
    return out


def _carbon_basis_line(res, live):
    if res["ci_basis"] == "live-grid":
        stale = " ⚠stale" if live and live.get("stale") else ""
        zone = (live or {}).get("zone") or "?"
        return "live-grid %s g/kWh · %s%s" % (
            fmt_sig(res["ci_g_per_kwh"]),
            zone,
            stale,
        )
    return "location-based static avg %g g/kWh (Google fleet proxy)" % res[
        "ci_g_per_kwh"
    ]


def _water_basis_label(res):
    if res["wue_basis"] == "live-weather":
        return "live-weather WUE (modeled economizer ramp)"
    return "region preset"


def _metrics_block(res, live, models=None, q_note="", heading="### Session"):
    """Usage-style fixed-width totals block."""
    e, w, c = res["energy_Wh"], res["water_mL"], res["carbon_g"]
    t = res["tokens"]
    lines = [
        heading,
        "",
        "```",
        "Energy:   %s Wh  [%s–%s]%s"
        % (fmt_sig(e[0]), fmt_sig(e[1]), fmt_sig(e[2]), q_note),
        "Water:    ~%s mL  [%s–%s]  (%s)"
        % (fmt_sig(w[0]), fmt_sig(w[1]), fmt_sig(w[2]), _water_basis_label(res)),
        "Carbon:   %s gCO2e  [%s–%s]  (%s)"
        % (fmt_sig(c[0]), fmt_sig(c[1]), fmt_sig(c[2]), _carbon_basis_line(res, live)),
        "Tokens:   %s in · %s cache · %s out"
        % (fmt_tok(t["in"]), fmt_tok(t["cache"]), fmt_tok(t["out"])),
    ]
    if models:
        lines.append("Models:   %s" % ", ".join(models))
    lines.append("```")
    return lines


def _envelope_footer():
    return (
        "_Estimates, not measurements; low–high figures are a scenario envelope, "
        "not a confidence interval. See METHODOLOGY.md and LIMITATIONS_AND_FAQ.md._"
    )


def session_report(coeffs, region, transcript=None, live=None):
    """Full markdown report for a real session (Usage-shaped). Returns a string."""
    concurrent = 0
    if transcript:
        tpath = transcript
    else:
        tpath, concurrent = find_transcript()
    if not tpath or not os.path.isfile(tpath):
        return (
            "## Session footprint\n\nNo transcript found for this project — "
            "nothing to report (and no numbers will be invented)."
        )
    lines = []
    if concurrent:
        lines += [
            "_⚠ %d other session(s) in this project were active in the last "
            "hour — this report covers the most recently active transcript, "
            "which may not be the one you're in. Pass --transcript to pin it._"
            % concurrent,
            "",
        ]
    entries = parse_transcript(tpath)
    if not entries:
        return "## Session footprint\n\nTranscript has no assistant usage entries yet."

    res = compute(entries, coeffs, region, live=live)
    q_note = "  (includes unknown-model entries at mid central + wide envelope)" if res[
        "unknown_model"
    ] else ""
    models = models_summary(entries, coeffs)
    lines += ["## Session footprint", ""]
    lines += _metrics_block(res, live, models=models, q_note=q_note)

    eq = _equivalences(res, coeffs)
    if eq:
        lines += ["", "*" + " · ".join(eq) + "*"]

    # Composition bars
    parts = energy_by_token_class(entries, coeffs, region, live=live)
    lines += [""] + composition_lines(parts, res["energy_Wh"][0])

    # Contribution insights
    insights = contribution_insights(entries, coeffs, region, res, live=live)
    lines += [""] + format_insights_section(insights)

    # Counterfactual savings
    rows = all_counterfactuals(entries, coeffs, region, res, live=live)
    lines += [""] + format_savings_section(rows)

    # By model detail
    lines += [
        "",
        "### By model",
        "",
        "| model | tier | in | cache read | out | energy (central) |",
        "|---|---|---|---|---|---|",
    ]
    for model, tier_name, known, tin, tcache, tout, ewh in by_model_rows(
        entries, coeffs, region, live=live
    ):
        lines.append(
            "| %s | %s%s | %s | %s | %s | %s Wh |"
            % (
                model,
                tier_name,
                "" if known else "?",
                fmt_tok(tin),
                fmt_tok(tcache),
                fmt_tok(tout),
                fmt_sig(ewh),
            )
        )

    when = when_advice(live, coeffs)
    which = which_advice(res, coeffs)
    if when or which:
        lines += ["", "### Decisions", ""]
        lines += ["- " + s for s in when + which]

    if live and live.get("errors"):
        lines += ["", "### Live-signal status", ""]
        lines += ["- ⚠ %s" % e for e in live["errors"]]
    elif not live:
        lines += [
            "",
            "_No live grid/weather snapshot (run `python3 -m modelfootprint refresh` "
            "with FOOTPRINT_SITE + FOOTPRINT_EM_TOKEN set) — static coefficients used, "
            "labeled above._",
        ]
    lines += ["", _envelope_footer()]
    return "\n".join(lines)


def whatif_report(
    coeffs,
    region,
    model,
    total_tokens,
    profile=DEFAULT_PROFILE,
    explicit=None,
    live=None,
):
    entries = whatif_entries(model, total_tokens or 0, profile, explicit)
    tin, tcache, tout, _ = entries["whatif"]
    tier_name, known = tier_for(model, coeffs["model_tier_lookup"])
    res = compute(entries, coeffs, region, live=live)
    q_note = "" if known else "  (model not recognized — mid central + wide envelope)"
    lines = [
        "## What-if footprint",
        "",
        "### Workload",
        "",
        "```",
        "Model:    %s (%s tier%s)"
        % (model, tier_name, "" if known else ", unrecognized"),
        "Split:    %s — %s in / %s cache / %s out"
        % (
            "explicit" if explicit else profile,
            fmt_tok(tin),
            fmt_tok(tcache),
            fmt_tok(tout),
        ),
        "Tokens:   %s total" % fmt_tok(tin + tcache + tout),
        "```",
        "",
    ]
    lines += _metrics_block(
        res,
        live,
        models=["%s (%s%s)" % (model, tier_name, "" if known else "?")],
        q_note=q_note,
        heading="### Estimate",
    )

    eq = _equivalences(res, coeffs)
    if eq:
        lines += ["", "*" + " · ".join(eq) + "*"]

    parts = energy_by_token_class(entries, coeffs, region, live=live)
    lines += [""] + composition_lines(parts, res["energy_Wh"][0])

    # Counterfactuals for what-if too
    rows = all_counterfactuals(entries, coeffs, region, res, live=live)
    lines += [""] + format_savings_section(rows)

    alts = tier_alternatives(res["tokens"], coeffs)
    lines += [
        "",
        "### Same tokens on each tier",
        "",
        "| tier | energy central | low–high |",
        "|---|---|---|",
    ]
    for name, e, _desc in alts:
        marker = " ← this estimate" if name == tier_name else ""
        lines.append(
            "| %s | %s Wh%s | %s–%s |"
            % (name, fmt_sig(e[0]), marker, fmt_sig(e[1]), fmt_sig(e[2]))
        )
    lines += ["", _envelope_footer()]
    return "\n".join(lines)
