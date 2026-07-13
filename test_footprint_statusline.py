#!/usr/bin/env python3
"""Verification suite for footprint_statusline.py — stdlib only, run directly.

Covers the eight required checks: hand-computed fixture, dedupe by message.id,
cache-write attribution, mixed-model tiering, unknown-model marker, range
propagation, malformed-input robustness, and timing on the largest real
transcript.
"""
import glob
import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import footprint_statusline as fs

COEFFS = fs.load_coefficients()
HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="footprint_test_")


def write_transcript(name, lines):
    path = os.path.join(TMP, name)
    with open(path, "wb") as f:
        for line in lines:
            if isinstance(line, bytes):
                f.write(line)
            else:
                f.write(json.dumps(line).encode() + b"\n")
    return path


def asst(mid, model, tin, ccw, cr, out):
    return {
        "type": "assistant",
        "isSidechain": False,
        "uuid": "u-" + mid,
        "message": {
            "id": mid,
            "model": model,
            "usage": {
                "input_tokens": tin,
                "cache_creation_input_tokens": ccw,
                "cache_read_input_tokens": cr,
                "output_tokens": out,
            },
        },
    }


def run(path, env_extra=None):
    env = dict(os.environ)
    env.pop("FOOTPRINT_REGION", None)
    env.pop("FOOTPRINT_VERBOSE", None)
    env["FOOTPRINT_LIVE"] = "0"  # static expectations: a real live cache must not leak in
    if env_extra:
        env.update(env_extra)
    stdin = json.dumps({"transcript_path": path, "session_id": "test"})
    r = subprocess.run(
        [sys.executable, os.path.join(HERE, "footprint_statusline.py")],
        input=stdin, capture_output=True, text=True, env=env,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout.splitlines()[0]


passed = 0


def check(name, cond, detail=""):
    global passed
    assert cond, "FAIL %s %s" % (name, detail)
    passed += 1
    print("PASS %s %s" % (name, detail))


# ---------------------------------------------------------------------------
# 1. Hand-check fixture: 10,000 in / 50,000 cache-read / 2,000 out on sonnet
#    (mid tier), region temperate.
#
#    Central, by hand from coefficients.json:
#      pre-PUE Wh = (10000*0.15 + 50000*0.03 + 2000*0.45) / 1000
#                 = (1500 + 1500 + 900) / 1000 = 3.9
#      energy     = 3.9 * PUE 1.2                      = 4.68 Wh
#      water_mL   = 4.68 * (WUE 0.27 + EWIF 1.0)       = 4.68 * 1.27 = 5.9436
#      carbon_g   = 4.68 / 1000 * 345 (loc-based)      = 1.6146
#    Low  = (10000*0.075 + 50000*0.01 + 2000*0.225)/1000 * 1.08
#         = (750 + 500 + 450)/1000 * 1.08 = 1.7 * 1.08 = 1.836 Wh
#    High = (10000*0.3 + 50000*0.09 + 2000*0.9)/1000 * 1.4
#         = (3000 + 4500 + 1800)/1000 * 1.4 = 9.3 * 1.4 = 13.02 Wh
# ---------------------------------------------------------------------------
p = write_transcript("hand.jsonl", [asst("m1", "claude-sonnet-5", 10000, 0, 50000, 2000)])
res = fs.compute(fs.parse_transcript(p), COEFFS, "temperate")
check("1a internal energy central == 4.68 Wh exactly", abs(res["energy_Wh"][0] - 4.68) < 1e-12, "got %r" % (res["energy_Wh"][0],))
check("1b internal energy low == 1.836 Wh exactly", abs(res["energy_Wh"][1] - 1.836) < 1e-12, "got %r" % (res["energy_Wh"][1],))
check("1c internal energy high == 13.02 Wh exactly", abs(res["energy_Wh"][2] - 13.02) < 1e-12, "got %r" % (res["energy_Wh"][2],))
check("1d internal water central == 5.9436 mL exactly", abs(res["water_mL"][0] - 5.9436) < 1e-12, "got %r" % (res["water_mL"][0],))
check("1e internal carbon central == 1.6146 g exactly", abs(res["carbon_g"][0] - 1.6146) < 1e-12, "got %r" % (res["carbon_g"][0],))
line = run(p)
check("1f displayed energy is 2 sig figs '4.7'", "⚡ 4.7 Wh" in line, line)
check("1g displayed range [1.8–13]", "[1.8–13]" in line, "")
check("1h displayed water '~5.9 mL'", "💧 ~5.9 mL" in line, "")
check("1i displayed carbon '1.6 gCO2e (loc-based)'", "🌫 1.6 gCO2e (loc-based)" in line, "")
check("1j token counts '10k in · 50k cache · 2.0k out'", "10k in · 50k cache · 2.0k out" in line, "")

# ---------------------------------------------------------------------------
# 2. Dedupe: same message.id 3x with growing usage -> counted once, final value.
# ---------------------------------------------------------------------------
p = write_transcript("dedupe.jsonl", [
    asst("mdup", "claude-sonnet-5", 100, 0, 0, 10),
    asst("mdup", "claude-sonnet-5", 100, 0, 0, 200),
    asst("mdup", "claude-sonnet-5", 100, 0, 0, 555),
])
ent = fs.parse_transcript(p)
check("2a one entry survives", len(ent) == 1, "got %d" % len(ent))
check("2b last-seen usage wins (out=555)", ent["mdup"][2] == 555, "got %r" % (ent["mdup"],))
res = fs.compute(ent, COEFFS, "temperate")
# (100*0.15 + 555*0.45)/1000 * 1.2 = (15 + 249.75)/1000 * 1.2 = 0.3177
check("2c energy counts it once == 0.3177 Wh", abs(res["energy_Wh"][0] - 0.3177) < 1e-12, "got %r" % (res["energy_Wh"][0],))

# ---------------------------------------------------------------------------
# 3. Cache-write attribution: cache_creation_input_tokens -> tok_in at e_in,
#    not tok_cache at e_cache.
# ---------------------------------------------------------------------------
p = write_transcript("cachewrite.jsonl", [asst("m1", "claude-sonnet-5", 0, 10000, 0, 0)])
ent = fs.parse_transcript(p)
check("3a cache writes land in tok_in", ent["m1"][0] == 10000 and ent["m1"][1] == 0, "got %r" % (ent["m1"],))
res = fs.compute(ent, COEFFS, "temperate")
# priced at e_in: 10000*0.15/1000*1.2 = 1.8 (at e_cache it would be 0.36)
check("3b priced at e_in (1.8 Wh, not 0.36)", abs(res["energy_Wh"][0] - 1.8) < 1e-12, "got %r" % (res["energy_Wh"][0],))

# ---------------------------------------------------------------------------
# 4. Mixed models: Haiku sidechain + Opus main -> each priced at its own tier.
# ---------------------------------------------------------------------------
haiku = asst("mh", "claude-haiku-4-5-20251001", 0, 0, 0, 1000)
haiku["isSidechain"] = True
opus = asst("mo", "claude-opus-4-8", 0, 0, 0, 1000)
p = write_transcript("mixed.jsonl", [haiku, opus])
res = fs.compute(fs.parse_transcript(p), COEFFS, "temperate")
# per-tier: (1000*0.15 + 1000*0.9)/1000 * 1.2 = 1.26 Wh
# all-opus would be 2.16, all-haiku 0.36
check("4a mixed == 1.26 Wh (per-entry tiering)", abs(res["energy_Wh"][0] - 1.26) < 1e-12, "got %r" % (res["energy_Wh"][0],))
check("4b differs from single-tier pricing (2.16 / 0.36)",
      abs(res["energy_Wh"][0] - 2.16) > 0.1 and abs(res["energy_Wh"][0] - 0.36) > 0.1)
check("4c sidechain entries included", res["tokens"]["out"] == 2000, "got %r" % (res["tokens"],))

# ---------------------------------------------------------------------------
# 5. Unknown model -> mid tier + visible "?" marker.
# ---------------------------------------------------------------------------
p = write_transcript("unknown.jsonl", [asst("m1", "totally-new-model-x9", 1000, 0, 0, 100)])
res = fs.compute(fs.parse_transcript(p), COEFFS, "temperate")
# mid tier: (1000*0.15 + 100*0.45)/1000*1.2 = 0.234
check("5a unknown model priced at mid tier (0.234 Wh)", abs(res["energy_Wh"][0] - 0.234) < 1e-12, "got %r" % (res["energy_Wh"][0],))
line = run(p)
check("5b '?' marker visible in output", "0.23? Wh" in line, line)
tier, known = fs.tier_for("claude-haiku-4-5", COEFFS["model_tier_lookup"])
check("5c known model no marker + longest-substring match", tier == "small" and known)
tier, known = fs.tier_for("gpt-5-nano-2027", COEFFS["model_tier_lookup"])
check("5d 'gpt-5-nano' wins over 'gpt-5' substring", tier == "small" and known, "got %s" % tier)

# ---------------------------------------------------------------------------
# 6. Range propagation: low = product of lows, high = product of highs (§5.1).
# ---------------------------------------------------------------------------
p = write_transcript("range.jsonl", [asst("m1", "claude-opus-4-8", 3000, 1000, 20000, 5000)])
res = fs.compute(fs.parse_transcript(p), COEFFS, "hot_arid")
t = COEFFS["model_tiers"]["frontier"]
pue = COEFFS["infrastructure_overhead"]["PUE_typical_hyperscale"]
reg = COEFFS["region_presets"]["hot_arid"]
ci = COEFFS["carbon_intensity_accounting_note"]["google_2024_example_gCO2e_per_kWh"]["location_based"]
for i, b in enumerate(("central", "low", "high")):
    e = (4000 * t["e_in_Wh_per_1k_tok"][b] + 20000 * t["e_cache_Wh_per_1k_tok"][b]
         + 5000 * t["e_out_Wh_per_1k_tok"][b]) / 1000.0 * pue[b]
    w = e * (reg["WUE_site_L_per_kWh"][b] + reg["EWIF_offsite_L_per_kWh_seasonal_fallback"][b])
    c = e / 1000.0 * ci
    check("6%s energy %s == product of %s bounds" % ("abc"[i], b, b), abs(res["energy_Wh"][i] - e) < 1e-12)
    check("6%s water %s bound" % ("abc"[i], b), abs(res["water_mL"][i] - w) < 1e-12)
    check("6%s carbon %s bound" % ("abc"[i], b), abs(res["carbon_g"][i] - c) < 1e-12)
check("6d low < central < high",
      res["energy_Wh"][1] < res["energy_Wh"][0] < res["energy_Wh"][2])

# ---------------------------------------------------------------------------
# 7. Robustness: truncated file, malformed lines, missing usage -> no crash,
#    no invented numbers; failure modes render placeholders/error glyphs.
# ---------------------------------------------------------------------------
good = json.dumps(asst("mok", "claude-sonnet-5", 1000, 0, 0, 100)).encode() + b"\n"
p = write_transcript("mangled.jsonl", [
    b"this is not json at all\n",
    b'{"type":"assistant","message":{"id":"nouse","model":"claude-sonnet-5"}}\n',   # no usage
    b'{"type":"assistant","message":{"id":"badusage","model":"x","usage":"usage-not-a-dict"}}\n',
    b'{"type":"user","message":{"content":"decoy \\"usage\\" string"}}\n',
    good,
    b'{"type":"assistant","message":{"id":"trunc","usage":{"input_tokens":999999,"cache_read',  # truncated mid-line
])
ent = fs.parse_transcript(p)
check("7a only the valid entry counted", list(ent) == ["mok"], "got %r" % list(ent))
res = fs.compute(ent, COEFFS, "temperate")
check("7b totals not polluted by malformed lines", res["tokens"] == {"in": 1000, "cache": 0, "out": 100}, "got %r" % (res["tokens"],))
line = run("/nonexistent/path/transcript.jsonl")
check("7c missing transcript -> '–' placeholders", line == fs.PLACEHOLDER_LINE, line)
line = run(p, {"FOOTPRINT_COEFFS": "/nonexistent/coeffs.json"})
check("7d unreadable coefficients -> error glyph, tokens still shown",
      line.startswith("⚠ footprint: coefficients unreadable") and "1.0k in" in line, line)
r = subprocess.run([sys.executable, os.path.join(HERE, "footprint_statusline.py")],
                   input="not json", capture_output=True, text=True,
                   env=dict(os.environ, FOOTPRINT_LIVE="0"))
check("7e garbage stdin -> placeholder line, exit 0",
      r.returncode == 0 and r.stdout.splitlines()[0] == fs.PLACEHOLDER_LINE, r.stdout)

# ---------------------------------------------------------------------------
# 8. Timing: largest real transcript on this machine, end-to-end < 300 ms.
# ---------------------------------------------------------------------------
real = sorted(glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl")),
              key=os.path.getsize, reverse=True)
assert real, "no real transcripts found"
big = real[0]
size_mb = os.path.getsize(big) / 1e6
env = dict(os.environ, FOOTPRINT_LIVE="0")
stdin = json.dumps({"transcript_path": big, "session_id": "timing"})
t0 = time.perf_counter()
r = subprocess.run([sys.executable, os.path.join(HERE, "footprint_statusline.py")],
                   input=stdin, capture_output=True, text=True, env=env)
elapsed = time.perf_counter() - t0
print("   timing: %.1f MB transcript -> %.0f ms end-to-end (incl. interpreter startup)" % (size_mb, elapsed * 1000))
print("   output: %s" % r.stdout.splitlines()[0])
check("8a returncode 0 on largest real transcript", r.returncode == 0)
check("8b elapsed < 300 ms", elapsed < 0.3, "%.0f ms" % (elapsed * 1000))

print("\nALL %d CHECKS PASSED" % passed)
