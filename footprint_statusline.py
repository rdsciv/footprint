#!/usr/bin/env python3
"""Claude Code statusline: live token counts + estimated energy (Wh), water (mL),
and carbon (gCO2e) for the current session.

Thin wrapper over modelfootprint.engine (see that module for the equations and
display rules; METHODOLOGY.md for derivations). This script must return in
<300 ms and NEVER touches the network — live grid/weather signals are only
read from the cache file that `python3 -m modelfootprint refresh` (or the
/footprint command) maintains.

Env vars:
  FOOTPRINT_COEFFS   path to coefficients.json
  FOOTPRINT_REGION   hot_arid | temperate | cool_humid | hot_humid (default temperate)
  FOOTPRINT_VERBOSE  =1 to also show water/carbon [low–high] ranges
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modelfootprint.engine import (  # noqa: E402,F401  (re-exported for tests)
    BOUNDS,
    PLACEHOLDER_LINE,
    PLACEHOLDER_TOKENS,
    compute,
    fmt_sig,
    fmt_tok,
    load_coefficients,
    parse_transcript,
    render,
    tier_for,
)


def main():
    try:
        stdin = json.load(sys.stdin)
        if not isinstance(stdin, dict):
            stdin = {}
    except ValueError:
        stdin = {}

    tpath = stdin.get("transcript_path")
    entries = None
    if tpath and os.path.isfile(tpath):
        try:
            entries = parse_transcript(tpath)
        except OSError:
            entries = None

    try:
        coeffs = load_coefficients()
    except (OSError, ValueError):
        # Explicit error glyph — never a fabricated 0.0.
        if entries is not None:
            t = {k: sum(e[i] for e in entries.values()) for i, k in enumerate(["in", "cache", "out"])}
            tokens = "%s in · %s cache · %s out" % (
                fmt_tok(t["in"]), fmt_tok(t["cache"]), fmt_tok(t["out"])
            )
        else:
            tokens = PLACEHOLDER_TOKENS
        print("⚠ footprint: coefficients unreadable | " + tokens)
        return

    if entries is None:
        print(PLACEHOLDER_LINE)
        return

    region = os.environ.get("FOOTPRINT_REGION", "temperate")
    presets = coeffs.get("region_presets", {})
    if region not in presets or region.startswith(("_", "$")):
        region = "temperate"

    live = None
    if os.environ.get("FOOTPRINT_LIVE") != "0":
        try:
            from modelfootprint.live import read_cached

            snap = read_cached()
            if snap and not snap.get("stale"):
                live = snap
        except Exception:
            live = None  # cache problems must never break the statusline

    res = compute(entries, coeffs, region, live=live)
    print(render(res, verbose=os.environ.get("FOOTPRINT_VERBOSE") == "1"))


if __name__ == "__main__":
    main()
