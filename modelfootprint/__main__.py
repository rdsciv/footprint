"""CLI: python3 -m modelfootprint <command>

Commands:
  report  [--transcript PATH] [--region R] [--refresh]
      Markdown footprint report for the current (or given) session transcript.
  whatif  MODEL TOKENS [chat|agent|out-heavy] [in=N] [cache=N] [out=N]
      Hypothetical estimate, e.g.:  whatif opus 500k   whatif sonnet 2M chat
      Explicit splits override the profile: whatif haiku 0 in=10k cache=50k out=2k
  refresh [--force]
      Fetch live grid/weather signals into the hourly cache (the only
      networked command; everything else reads the cache or static values).
"""
import argparse
import os
import sys

from .engine import load_coefficients
from .live import read_cached, refresh
from .report import (
    DEFAULT_PROFILE,
    PROFILES,
    parse_token_count,
    session_report,
    whatif_report,
)


def _region(coeffs, arg=None):
    region = arg or os.environ.get("FOOTPRINT_REGION", "temperate")
    presets = coeffs.get("region_presets", {})
    if region not in presets or region.startswith(("_", "$")):
        region = "temperate"
    return region


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="modelfootprint", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_report = sub.add_parser("report", help="session footprint report")
    p_report.add_argument("--transcript")
    p_report.add_argument("--region")
    p_report.add_argument("--refresh", action="store_true",
                          help="refresh live signals first (networked)")

    p_whatif = sub.add_parser("whatif", help="hypothetical estimate")
    p_whatif.add_argument("model")
    p_whatif.add_argument("tokens")
    p_whatif.add_argument("rest", nargs="*")
    p_whatif.add_argument("--region")

    p_refresh = sub.add_parser("refresh", help="fetch live signals into cache")
    p_refresh.add_argument("--force", action="store_true")

    args = parser.parse_args(argv)

    try:
        coeffs = load_coefficients()
    except (OSError, ValueError) as e:
        print("⚠ footprint: coefficients unreadable (%s) — no numbers will be invented." % e)
        return 1

    if args.cmd == "refresh":
        snap = refresh(coeffs, force=args.force)
        got = [k for k in ("ci_g_per_kwh", "ci_forecast", "moer_percentile", "wue_site_L_per_kWh") if snap.get(k) is not None]
        print("refreshed: %s" % (", ".join(got) if got else "nothing (no signals configured)"))
        for err in snap.get("errors", []):
            print("  ⚠ %s" % err)
        return 0

    live = None
    if os.environ.get("FOOTPRINT_LIVE") != "0":
        if getattr(args, "refresh", False):
            live = refresh(coeffs)
        else:
            live = read_cached()

    region = _region(coeffs, args.region)

    if args.cmd == "report":
        print(session_report(coeffs, region, transcript=args.transcript, live=live))
        return 0

    if args.cmd == "whatif":
        total = parse_token_count(args.tokens)
        profile = DEFAULT_PROFILE
        explicit = {}
        for tok in args.rest:
            if tok in PROFILES:
                profile = tok
            elif "=" in tok:
                k, _, v = tok.partition("=")
                n = parse_token_count(v)
                if k in ("in", "cache", "out") and n is not None:
                    explicit[k] = n
                else:
                    print("⚠ ignoring unrecognized argument %r" % tok)
            else:
                print("⚠ ignoring unrecognized argument %r" % tok)
        if total is None and not explicit:
            print("⚠ could not parse token count %r (use e.g. 500k, 2M, 120000)" % args.tokens)
            return 1
        print(whatif_report(coeffs, region, args.model, total or 0,
                            profile=profile, explicit=explicit or None, live=live))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
