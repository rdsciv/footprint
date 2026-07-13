#!/usr/bin/env python3
"""Verification suite for the modelfootprint package additions (v0.2):
what-if parsing, live-cache handling, weather->WUE model, recommendations,
tier lookup refresh, and the Altman 0.34 Wh/query sanity anchor (non-blocking).

Stdlib only, run directly: python3 test_modelfootprint.py
No test here touches the network.
"""
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
TMP = tempfile.mkdtemp(prefix="modelfootprint_test_")
os.environ["FOOTPRINT_CACHE"] = os.path.join(TMP, "live.json")  # isolate before imports

from modelfootprint import engine, live, recommend, report  # noqa: E402

COEFFS = engine.load_coefficients()
passed = 0


def check(name, cond, detail=""):
    global passed
    assert cond, "FAIL %s %s" % (name, detail)
    passed += 1
    print("PASS %s %s" % (name, detail))


def write_cache(snap):
    with open(os.environ["FOOTPRINT_CACHE"], "w") as f:
        json.dump(snap, f)


# ---------------------------------------------------------------------------
# 1. What-if parsing
# ---------------------------------------------------------------------------
check("1a '500k' -> 500000", report.parse_token_count("500k") == 500000)
check("1b '2M' -> 2000000", report.parse_token_count("2M") == 2000000)
check("1c '1200' -> 1200", report.parse_token_count("1200") == 1200)
check("1d '1.5m' -> 1500000", report.parse_token_count("1.5m") == 1500000)
check("1e garbage -> None", report.parse_token_count("lots") is None)

e = report.whatif_entries("opus", 100000, "chat")
tin, tcache, tout, model = e["whatif"]
check("1f chat profile 80/0/20", (tin, tcache, tout) == (80000, 0, 20000), repr(e))
e = report.whatif_entries("haiku", 0, explicit={"in": 10000, "cache": 50000, "out": 2000})
check("1g explicit split honored", e["whatif"][:3] == (10000, 50000, 2000))
e = report.whatif_entries("sonnet", 1000000, "agent")
tin, tcache, tout, _ = e["whatif"]
check("1h agent profile sums exactly to total", tin + tcache + tout == 1000000)

# ---------------------------------------------------------------------------
# 2. Live cache: missing, fresh, stale, too-old; never a network call.
# ---------------------------------------------------------------------------
check("2a missing cache -> None", live.read_cached() is None)
write_cache({"fetched_at": time.time(), "ci_g_per_kwh": 100.0, "zone": "TEST"})
snap = live.read_cached()
check("2b fresh cache read", snap is not None and snap["ci_g_per_kwh"] == 100.0)
check("2c fresh cache not stale", snap["stale"] is False)
write_cache({"fetched_at": time.time() - 2 * 3600, "ci_g_per_kwh": 100.0})
snap = live.read_cached()
check("2d 2h-old cache usable but flagged stale", snap is not None and snap["stale"] is True)
write_cache({"fetched_at": time.time() - 4 * 3600, "ci_g_per_kwh": 100.0})
check("2e 4h-old cache -> None (beyond MAX_AGE)", live.read_cached() is None)
write_cache({"fetched_at": "corrupt"})
check("2f corrupt fetched_at -> None", live.read_cached() is None)
with open(os.environ["FOOTPRINT_CACHE"], "w") as f:
    f.write("{not json")
check("2g corrupt json -> None", live.read_cached() is None)

# ---------------------------------------------------------------------------
# 3. Live overrides in compute(): CI substitution exact, energy unchanged,
#    fallback identical to static when live is None.
# ---------------------------------------------------------------------------
entries = {"m": (10000, 50000, 2000, "claude-sonnet-5")}
static = engine.compute(entries, COEFFS, "temperate")
lively = engine.compute(entries, COEFFS, "temperate", live={"ci_g_per_kwh": 100.0})
check("3a energy identical with live CI", lively["energy_Wh"] == static["energy_Wh"])
# 4.68 Wh * 100 g/kWh / 1000 = 0.468 g
check("3b live carbon central == 0.468 exactly", abs(lively["carbon_g"][0] - 0.468) < 1e-12, repr(lively["carbon_g"]))
check("3c live basis labeled", lively["ci_basis"] == "live-grid" and static["ci_basis"] == "loc-based")
wet = engine.compute(entries, COEFFS, "temperate", live={"wue_site_L_per_kWh": 0.40})
# central water = 4.68 * (0.40 + 1.0 EWIF) = 6.552
check("3d live WUE central water == 6.552", abs(wet["water_mL"][0] - 6.552) < 1e-12, repr(wet["water_mL"]))
check("3e live WUE range >= static envelope",
      wet["water_mL"][1] <= static["water_mL"][1] + 1e-12 and wet["water_mL"][2] >= static["water_mL"][2] - 1e-12)
check("3f render tags live grid", "(live-grid 100g)" in engine.render(lively))
check("3g render tags static", "(loc-based)" in engine.render(static))

# ---------------------------------------------------------------------------
# 4. Weather model: Stull wet-bulb sanity + economizer ramp bounds.
# ---------------------------------------------------------------------------
wb = live.wet_bulb_stull(20.0, 50.0)
check("4a Stull(20C, 50%) ~= 13.7C", abs(wb - 13.7) < 0.3, "%.2f" % wb)
wue_preset = COEFFS["region_presets"]["temperate"]["WUE_site_L_per_kWh"]
check("4b cool day -> preset low", live.wue_from_weather(10.0, wue_preset) == wue_preset["low"])
check("4c hot day -> preset high", live.wue_from_weather(35.0, wue_preset) == wue_preset["high"])
mid_c = live.wue_from_weather(29.4 - 3.0, wue_preset)
check("4d mid-ramp strictly between bounds", wue_preset["low"] < mid_c < wue_preset["high"], "%r" % mid_c)

# ---------------------------------------------------------------------------
# 5. Recommendations
# ---------------------------------------------------------------------------
fc = [{"t": "T%02d" % h, "ci": ci} for h, ci in enumerate([400, 300, 200, 100, 50, 80, 300, 450])]
win = recommend.best_window({"ci_g_per_kwh": 400.0, "ci_forecast": fc})
check("5a best window found at min", win["best_ci"] == 50 and win["best_t"] == "T04")
check("5b ratio 8x", abs(win["ratio"] - 8.0) < 1e-9)
lines = recommend.when_advice({"ci_g_per_kwh": 400.0, "ci_forecast": fc}, COEFFS)
check("5c defer advice fires", any("deferring" in s for s in lines), repr(lines))
lines = recommend.when_advice(None, COEFFS)
check("5d no live -> static advice labeled SPECULATIVE", any("SPECULATIVE" in s for s in lines), repr(lines))
lines = recommend.when_advice({"moer_percentile": 90.0}, COEFFS)
check("5e WattTime percentile never presented as g/kWh", any("not g/kWh" in s for s in lines), repr(lines))

res = engine.compute({"m": (10000, 200000, 5000, "claude-fable-5")}, COEFFS, "temperate")
lines = recommend.which_advice(res, COEFFS)
check("5f small-tier suggestion with % saving", any("small-tier" in s and "%" in s for s in lines), repr(lines))
check("5g cache-share note (93% cache)", any("cache reads" in s for s in lines), repr(lines))

# ---------------------------------------------------------------------------
# 6. Tier lookup refresh
# ---------------------------------------------------------------------------
lk = COEFFS["model_tier_lookup"]
for mid_id, want in [
    ("claude-fable-5", "frontier"),
    ("claude-mythos-5", "frontier"),
    ("gemini-2.5-flash", "small"),
    ("gemini-2.5-pro", "frontier"),
    ("gemini-3-flash-preview", "small"),
    ("deepseek-v4", "mid"),
    ("grok-4-1212", "frontier"),
]:
    tier, known = engine.tier_for(mid_id, lk)
    check("6 %s -> %s" % (mid_id, want), known and tier == want, "got %s known=%s" % (tier, known))

# ---------------------------------------------------------------------------
# 7. End-to-end CLI (subprocess, FOOTPRINT_LIVE isolation, no network)
# ---------------------------------------------------------------------------
env = dict(os.environ, FOOTPRINT_LIVE="0", PYTHONPATH=HERE)
r = subprocess.run([sys.executable, "-m", "modelfootprint", "whatif", "opus", "500k"],
                   capture_output=True, text=True, env=env, cwd=TMP)
check("7a whatif exit 0", r.returncode == 0, r.stderr)
check("7b whatif shows range + basis label", "low" in r.stdout and "location-based" in r.stdout, r.stdout[:200])
check("7c whatif tier table present", "← this estimate" in r.stdout)
r = subprocess.run([sys.executable, "-m", "modelfootprint", "whatif", "opus", "junk"],
                   capture_output=True, text=True, env=env, cwd=TMP)
check("7d unparseable tokens -> warning, exit 1, no invented numbers",
      r.returncode == 1 and "could not parse" in r.stdout and "Wh" not in r.stdout, r.stdout)
# report with no transcript for that cwd
r = subprocess.run([sys.executable, "-m", "modelfootprint", "report"],
                   capture_output=True, text=True, env=env, cwd=TMP)
check("7e no transcript -> honest empty report", "No transcript found" in r.stdout, r.stdout[:200])

# statusline with fresh synthetic cache -> live tag; env cache is isolated
write_cache({"fetched_at": time.time(), "ci_g_per_kwh": 250.0, "zone": "TEST"})
tpath = os.path.join(TMP, "t.jsonl")
with open(tpath, "w") as f:
    f.write(json.dumps({"type": "assistant", "message": {"id": "m1", "model": "claude-sonnet-5",
            "usage": {"input_tokens": 10000, "cache_creation_input_tokens": 0,
                      "cache_read_input_tokens": 50000, "output_tokens": 2000}}}) + "\n")
stdin = json.dumps({"transcript_path": tpath, "session_id": "x"})
r = subprocess.run([sys.executable, os.path.join(HERE, "footprint_statusline.py")],
                   input=stdin, capture_output=True, text=True, env=dict(os.environ))
check("7f statusline uses fresh cache: live-grid tag + 1.2 g (4.68Wh*250g/kWh)",
      "(live-grid 250g)" in r.stdout and "🌫 1.2 g" in r.stdout, r.stdout)
write_cache({"fetched_at": time.time() - 2 * 3600, "ci_g_per_kwh": 250.0, "zone": "TEST"})
r = subprocess.run([sys.executable, os.path.join(HERE, "footprint_statusline.py")],
                   input=stdin, capture_output=True, text=True, env=dict(os.environ))
check("7g statusline ignores stale cache -> loc-based", "(loc-based)" in r.stdout, r.stdout)

# ---------------------------------------------------------------------------
# 8. Altman anchor (NON-BLOCKING sanity check, per CLOUD_CONTEXT.md):
#    a cache-light, assistant-scale 1k-token chat on the mid tier should land
#    within ~5x of the public ~0.34 Wh/query anchor. Directional only.
# ---------------------------------------------------------------------------
e = report.whatif_entries("claude-sonnet-5", 1000, "chat")
res = engine.compute(e, COEFFS, "temperate")
wh = res["energy_Wh"][0]
if 0.34 / 5 <= wh <= 0.34 * 5:
    check("8 Altman 0.34 Wh/query anchor (non-blocking)", True, "mid-tier 1k chat = %.3f Wh" % wh)
else:
    print("NOTE (non-blocking): mid-tier 1k-token chat = %.3f Wh vs 0.34 Wh anchor — review tiers" % wh)

print("\nALL %d CHECKS PASSED" % passed)
