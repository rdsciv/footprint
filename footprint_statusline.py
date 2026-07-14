#!/usr/bin/env python3
"""Claude Code statusline: live token counts + estimated energy (Wh), water (mL),
and carbon (gCO2e) for the current session.

Thin wrapper over modelfootprint.engine (see that module for the equations and
display rules; METHODOLOGY.md for derivations). This script must return in
<300 ms, NEVER touches the network — live grid/weather signals are only read
from the cache file that `python3 -m modelfootprint refresh` (or the
/footprint command) maintains — and never crashes: any unexpected failure
prints an explicit error line and exits 0.

Env vars:
  FOOTPRINT_COEFFS   path to coefficients.json
  FOOTPRINT_SITE     datacenter site preset (also selects the climate region)
  FOOTPRINT_REGION   hot_arid | temperate | cool_humid | hot_humid (overrides site)
  FOOTPRINT_VERBOSE  =1 to also show water/carbon [low–high] ranges
  FOOTPRINT_LIVE     =0 to ignore the live-signal cache
"""
import json
import os
import sys

try:
    from modelfootprint.engine import (
        PLACEHOLDER_LINE,
        PLACEHOLDER_TOKENS,
        compute,
        fmt_tok,
        load_coefficients,
        parse_transcript,
        render,
        resolve_region,
    )
except ImportError:  # running from a source checkout without installation
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from modelfootprint.engine import (
        PLACEHOLDER_LINE,
        PLACEHOLDER_TOKENS,
        compute,
        fmt_tok,
        load_coefficients,
        parse_transcript,
        render,
        resolve_region,
    )


def _statusline():
    try:
        stdin = json.load(sys.stdin)
        if not isinstance(stdin, dict):
            stdin = {}
    except ValueError:
        stdin = {}

    tpath = stdin.get("transcript_path")
    entries = None
    if isinstance(tpath, str) and tpath and os.path.isfile(tpath):
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
        return "⚠ footprint: coefficients unreadable | " + tokens

    if entries is None:
        return PLACEHOLDER_LINE

    live = None
    site_region = None
    if os.environ.get("FOOTPRINT_LIVE") != "0":
        try:
            from modelfootprint.live import read_cached, site_config

            site_region = site_config().get("region")
            snap = read_cached()
            if snap and not snap.get("stale"):
                live = snap
        except Exception:
            live = None  # cache problems must never break the statusline

    region = resolve_region(coeffs, site_region=site_region)
    res = compute(entries, coeffs, region, live=live)
    return render(res, verbose=os.environ.get("FOOTPRINT_VERBOSE") == "1")


def main():
    try:
        line = _statusline()
    except Exception as e:  # fail closed: a broken statusline helps nobody
        line = "⚠ footprint: error (%s) | %s" % (type(e).__name__, PLACEHOLDER_TOKENS)
    print(line)


if __name__ == "__main__":
    main()
