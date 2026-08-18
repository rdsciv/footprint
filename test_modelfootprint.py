#!/usr/bin/env python3
"""Verification suite for the modelfootprint package — stdlib unittest,
runnable directly (`python3 test_modelfootprint.py`) or via pytest.

Covers: what-if parsing, live-cache handling (fingerprint, staleness,
corruption, carry-forward, atomic writes), the two-signal weather model,
live overrides in compute, recommendations, tier lookup, coefficient
validation, CLI end-to-end behavior, the JS/Python golden cross-check, and
the Altman 0.34 Wh/query sanity anchor (non-blocking).

No test here touches the network. All FOOTPRINT_* env vars are scrubbed and
temp state is cleaned up.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

for _k in [k for k in os.environ if k.startswith("FOOTPRINT_")]:
    del os.environ[_k]

TMP = tempfile.mkdtemp(prefix="modelfootprint_test_")
os.environ["FOOTPRINT_CACHE"] = os.path.join(TMP, "live.json")  # isolate before imports

from modelfootprint import (  # noqa: E402
    counterfactuals,
    engine,
    insights,
    live,
    recommend,
    report,
)

COEFFS = engine.load_coefficients()
FP = live.config_fingerprint()  # fingerprint of the scrubbed test configuration


def write_cache(snap, path=None):
    snap.setdefault("config_fp", FP)
    with open(path or os.environ["FOOTPRINT_CACHE"], "w") as f:
        json.dump(snap, f)


def scrubbed_env(extra=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("FOOTPRINT_")}
    env["FOOTPRINT_LIVE"] = "0"
    env["PYTHONPATH"] = HERE
    if extra:
        env.update(extra)
    return env


import atexit

atexit.register(shutil.rmtree, TMP, ignore_errors=True)


class Test1WhatIfParsing(unittest.TestCase):
    def test_token_counts(self):
        self.assertEqual(report.parse_token_count("500k"), 500000)
        self.assertEqual(report.parse_token_count("2M"), 2000000)
        self.assertEqual(report.parse_token_count("1200"), 1200)
        self.assertEqual(report.parse_token_count("1.5m"), 1500000)
        self.assertIsNone(report.parse_token_count("lots"))

    def test_profiles_and_explicit_splits(self):
        e = report.whatif_entries("opus", 100000, "chat")
        self.assertEqual(e["whatif"][:3], (80000, 0, 20000))
        e = report.whatif_entries("haiku", 0, explicit={"in": 10000, "cache": 50000, "out": 2000})
        self.assertEqual(e["whatif"][:3], (10000, 50000, 2000))
        e = report.whatif_entries("sonnet", 1000000, "agent")
        self.assertEqual(sum(e["whatif"][:3]), 1000000)


class Test2LiveCache(unittest.TestCase):
    def setUp(self):
        try:
            os.unlink(os.environ["FOOTPRINT_CACHE"])
        except OSError:
            pass

    def test_missing_cache(self):
        self.assertIsNone(live.read_cached())

    def test_fresh_cache(self):
        write_cache({"fetched_at": time.time(), "ci_g_per_kwh": 100.0, "zone": "TEST"})
        snap = live.read_cached()
        self.assertIsNotNone(snap)
        self.assertEqual(snap["ci_g_per_kwh"], 100.0)
        self.assertFalse(snap["stale"])

    def test_stale_flag_and_max_age(self):
        write_cache({"fetched_at": time.time() - 2 * 3600, "ci_g_per_kwh": 100.0})
        snap = live.read_cached()
        self.assertIsNotNone(snap)
        self.assertTrue(snap["stale"])
        write_cache({"fetched_at": time.time() - 4 * 3600, "ci_g_per_kwh": 100.0})
        self.assertIsNone(live.read_cached())

    def test_corruption(self):
        write_cache({"fetched_at": "corrupt"})
        self.assertIsNone(live.read_cached())
        with open(os.environ["FOOTPRINT_CACHE"], "w") as f:
            f.write("{not json")
        self.assertIsNone(live.read_cached())

    def test_config_fingerprint_invalidates(self):
        """A cache written under one configuration is never served under
        another (the Virginia-cache-after-switching-to-Oregon bug)."""
        write_cache({"fetched_at": time.time(), "ci_g_per_kwh": 100.0})
        self.assertIsNotNone(live.read_cached())
        os.environ["FOOTPRINT_SITE"] = "oregon"
        try:
            self.assertIsNone(live.read_cached(),
                              "cache from a different config must be invisible")
        finally:
            del os.environ["FOOTPRINT_SITE"]
        self.assertIsNotNone(live.read_cached(), "restored config sees it again")

    def test_nonfinite_values_scrubbed(self):
        write_cache({"fetched_at": time.time(), "ci_g_per_kwh": float("nan"),
                     "wue_site_L_per_kWh": "0.4", "moer_percentile": float("inf")})
        snap = live.read_cached()
        self.assertIsNotNone(snap)
        self.assertNotIn("ci_g_per_kwh", snap)
        self.assertNotIn("wue_site_L_per_kWh", snap)
        self.assertNotIn("moer_percentile", snap)

    def test_atomic_write_bare_filename(self):
        """FOOTPRINT_CACHE with no directory component must not crash on
        os.makedirs('')."""
        old_cwd = os.getcwd()
        os.chdir(TMP)
        try:
            errors = []
            live._write_cache("bare_cache.json", {"fetched_at": time.time(), "config_fp": FP}, errors)
            self.assertEqual(errors, [])
            self.assertTrue(os.path.isfile("bare_cache.json"))
        finally:
            os.chdir(old_cwd)

    def test_refresh_unconfigured_records_errors_and_ok(self):
        snap = live.refresh(COEFFS, force=True)
        self.assertTrue(snap["ok"], "nothing configured -> nothing failed")
        self.assertTrue(any("FOOTPRINT_EM_TOKEN" in e for e in snap["errors"]))

    def test_watttime_requires_region(self):
        os.environ["FOOTPRINT_WT_USER"] = "u"
        os.environ["FOOTPRINT_WT_PASS"] = "p"
        try:
            snap = live.refresh(COEFFS, force=True)
            self.assertTrue(any("FOOTPRINT_WT_REGION" in e for e in snap["errors"]),
                            snap["errors"])
            self.assertNotIn("moer_percentile", snap)
        finally:
            del os.environ["FOOTPRINT_WT_USER"]
            del os.environ["FOOTPRINT_WT_PASS"]

    def test_carry_forward_preserves_signal(self):
        """A refresh that fails a signal keeps the previous snapshot's value
        instead of destroying it."""
        prev_ts = time.time() - 90 * 60  # 1.5h old: expired TTL, inside MAX_AGE
        write_cache({"fetched_at": prev_ts, "ci_g_per_kwh": 222.0,
                     "ci_source": "electricitymaps", "ci_fetched_at": prev_ts})
        snap = live.refresh(COEFFS, force=True)  # no network signals configured
        self.assertEqual(snap.get("ci_g_per_kwh"), 222.0)
        self.assertTrue(any("carried forward" in e for e in snap["errors"]))
        self.assertEqual(snap.get("ci_fetched_at"), prev_ts)


class Test3LiveOverridesInCompute(unittest.TestCase):
    ENTRIES = {"m": (10000, 50000, 2000, "claude-sonnet-5")}

    def test_ci_substitution_exact(self):
        static = engine.compute(self.ENTRIES, COEFFS, "temperate")
        lively = engine.compute(self.ENTRIES, COEFFS, "temperate", live={"ci_g_per_kwh": 100.0})
        self.assertEqual(lively["energy_Wh"], static["energy_Wh"])
        # 4.68 Wh * 100 g/kWh / 1000 = 0.468 g; live band is +/-25%
        self.assertAlmostEqual(lively["carbon_g"][0], 0.468, places=12)
        self.assertAlmostEqual(lively["carbon_g"][1], static["energy_Wh"][1] / 1000 * 75.0, places=12)
        self.assertAlmostEqual(lively["carbon_g"][2], static["energy_Wh"][2] / 1000 * 125.0, places=12)
        self.assertEqual(lively["ci_basis"], "live-grid")
        self.assertEqual(static["ci_basis"], "loc-based")

    def test_nonfinite_live_values_ignored(self):
        res = engine.compute(self.ENTRIES, COEFFS, "temperate",
                             live={"ci_g_per_kwh": float("nan"),
                                   "wue_site_L_per_kWh": float("inf")})
        self.assertEqual(res["ci_basis"], "loc-based")
        self.assertEqual(res["wue_basis"], "preset")

    def test_live_wue_boundary_correct(self):
        wet = engine.compute(self.ENTRIES, COEFFS, "temperate", live={"wue_site_L_per_kWh": 0.40})
        # central water = IT 3.9 * 0.40 + facility 4.68 * 1.0 = 6.24
        self.assertAlmostEqual(wet["water_mL"][0], 6.24, places=12)
        static = engine.compute(self.ENTRIES, COEFFS, "temperate")
        self.assertLessEqual(wet["water_mL"][1], static["water_mL"][1] + 1e-12)
        self.assertGreaterEqual(wet["water_mL"][2], static["water_mL"][2] - 1e-12)

    def test_render_basis_tags(self):
        static = engine.compute(self.ENTRIES, COEFFS, "temperate")
        lively = engine.compute(self.ENTRIES, COEFFS, "temperate", live={"ci_g_per_kwh": 100.0})
        self.assertIn("(live-grid 100g)", engine.render(lively))
        self.assertIn("(loc-based)", engine.render(static))


class Test4WeatherModel(unittest.TestCase):
    WUE = COEFFS["region_presets"]["temperate"]["WUE_site_L_per_kWh"]

    def test_stull_sanity(self):
        self.assertAlmostEqual(live.wet_bulb_stull(20.0, 50.0), 13.7, delta=0.3)

    def test_cool_day_preset_low(self):
        self.assertEqual(live.wue_from_weather(10.0, 50.0, self.WUE), self.WUE["low"])

    def test_hot_humid_day_preset_high(self):
        self.assertEqual(live.wue_from_weather(35.0, 90.0, self.WUE), self.WUE["high"])

    def test_humidity_changes_water_draw(self):
        """The wet-bulb term: 30C at 10% RH must draw less water than 30C at
        90% RH (this was the dry-bulb-only bug)."""
        dry = live.wue_from_weather(30.0, 10.0, self.WUE)
        humid = live.wue_from_weather(30.0, 90.0, self.WUE)
        self.assertLess(dry, humid)
        self.assertGreater(dry, self.WUE["low"])
        self.assertLessEqual(humid, self.WUE["high"])

    def test_bounded_by_preset(self):
        for t in (0, 15, 25, 29, 33, 45):
            for rh in (5, 40, 95):
                v = live.wue_from_weather(t, rh, self.WUE)
                self.assertGreaterEqual(v, self.WUE["low"])
                self.assertLessEqual(v, self.WUE["high"])


class Test5Recommendations(unittest.TestCase):
    FC = [{"t": "T%02d" % h, "ci": ci} for h, ci in enumerate([400, 300, 200, 100, 50, 80, 300, 450])]

    def test_best_window(self):
        win = recommend.best_window({"ci_g_per_kwh": 400.0, "ci_forecast": self.FC})
        self.assertEqual((win["best_ci"], win["best_t"]), (50, "T04"))
        self.assertAlmostEqual(win["ratio"], 8.0)

    def test_defer_advice(self):
        lines = recommend.when_advice({"ci_g_per_kwh": 400.0, "ci_forecast": self.FC}, COEFFS)
        self.assertTrue(any("deferring" in s for s in lines), lines)

    def test_static_fallback_labeled_speculative(self):
        lines = recommend.when_advice(None, COEFFS)
        self.assertTrue(any("SPECULATIVE" in s for s in lines), lines)

    def test_watttime_percentile_labeled_with_region_not_gkwh(self):
        lines = recommend.when_advice(
            {"moer_percentile": 90.0, "moer_region": "CAISO_NORTH"}, COEFFS)
        self.assertTrue(any("not g/kWh" in s and "CAISO_NORTH" in s for s in lines), lines)

    def test_when_advice_silent_when_diesel_none(self):
        lines = recommend.when_advice({"diesel_risk": "none"}, COEFFS)
        self.assertFalse(any("diesel" in x.lower() for x in lines), lines)

    def test_when_advice_emergency_is_modeled_not_a_measurement(self):
        lines = recommend.when_advice(
            {"diesel_risk": "emergency_alert", "diesel_risk_source": "test"},
            COEFFS,
        )
        blob = " ".join(lines).lower()
        self.assertIn("diesel", blob)
        self.assertIn("modeled", blob)
        self.assertIn("did not observe", blob)

    def test_which_advice(self):
        res = engine.compute({"m": (10000, 200000, 5000, "claude-fable-5")}, COEFFS, "temperate")
        lines = recommend.which_advice(res, COEFFS)
        self.assertTrue(any("small-tier" in s and "%" in s for s in lines), lines)
        self.assertTrue(any("cache reads" in s for s in lines), lines)


class Test6Config(unittest.TestCase):
    def test_tier_lookup_current_models(self):
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
            self.assertTrue(known, mid_id)
            self.assertEqual(tier, want, mid_id)

    def test_site_selects_region(self):
        """FOOTPRINT_SITE=phoenix must select hot_arid without a separate
        FOOTPRINT_REGION (the Phoenix-WUE-with-temperate-EWIF bug)."""
        os.environ["FOOTPRINT_SITE"] = "phoenix"
        try:
            cfg = live.site_config()
            self.assertEqual(engine.resolve_region(COEFFS, site_region=cfg["region"]), "hot_arid")
        finally:
            del os.environ["FOOTPRINT_SITE"]

    def test_explicit_region_overrides_site(self):
        self.assertEqual(
            engine.resolve_region(COEFFS, region="cool_humid", site_region="hot_arid"),
            "cool_humid")
        self.assertEqual(engine.resolve_region(COEFFS, region="bogus"), "temperate")

    def test_invalid_coordinates_unconfigured_not_fatal(self):
        os.environ["FOOTPRINT_LAT"] = "999"
        os.environ["FOOTPRINT_LON"] = "not-a-number"
        try:
            cfg = live.site_config()
            self.assertIsNone(cfg["lat"])
            self.assertIsNone(cfg["lon"])
        finally:
            del os.environ["FOOTPRINT_LAT"]
            del os.environ["FOOTPRINT_LON"]

    def test_validate_coefficients_rejects_garbage(self):
        broken = json.loads(json.dumps(COEFFS))
        broken["model_tiers"]["mid"]["e_in_Wh_per_1k_tok"]["low"] = 99  # low > central
        with self.assertRaises(ValueError):
            engine.validate_coefficients(broken)
        broken2 = json.loads(json.dumps(COEFFS))
        broken2["$schema_version"] = "9.0.0"
        with self.assertRaises(ValueError):
            engine.validate_coefficients(broken2)
        with self.assertRaises(ValueError):
            engine.validate_coefficients("not a dict")


class Test7CliEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="modelfootprint_cli_")
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)

    def cli(self, *args, env_extra=None, cwd=None):
        return subprocess.run(
            [sys.executable, "-m", "modelfootprint", *args],
            capture_output=True, text=True, env=scrubbed_env(env_extra),
            cwd=cwd or self.tmp, timeout=30,
        )

    def test_whatif_happy_path(self):
        r = self.cli("whatif", "opus", "500k")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("location-based", r.stdout)
        self.assertIn("← this estimate", r.stdout)
        self.assertIn("scenario envelope", r.stdout.lower())

    def test_whatif_unparseable_tokens(self):
        r = self.cli("whatif", "opus", "junk")
        self.assertEqual(r.returncode, 1)
        self.assertIn("could not parse", r.stdout)
        self.assertNotIn("Wh", r.stdout)

    def test_report_no_transcript(self):
        r = self.cli("report")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("No transcript found", r.stdout)

    def test_statusline_uses_fresh_cache_and_ignores_stale(self):
        cache = os.path.join(self.tmp, "live_cache.json")
        tpath = os.path.join(self.tmp, "t.jsonl")
        with open(tpath, "w") as f:
            f.write(json.dumps({"type": "assistant", "message": {
                "id": "m1", "model": "claude-sonnet-5",
                "usage": {"input_tokens": 10000, "cache_creation_input_tokens": 0,
                          "cache_read_input_tokens": 50000, "output_tokens": 2000}}}) + "\n")
        stdin = json.dumps({"transcript_path": tpath, "session_id": "x"})
        env = scrubbed_env({"FOOTPRINT_CACHE": cache})
        del env["FOOTPRINT_LIVE"]

        # fingerprint must match the subprocess's scrubbed config
        old_cache = os.environ["FOOTPRINT_CACHE"]
        os.environ["FOOTPRINT_CACHE"] = cache
        try:
            fp = live.config_fingerprint()
        finally:
            os.environ["FOOTPRINT_CACHE"] = old_cache

        write_cache({"fetched_at": time.time(), "ci_g_per_kwh": 250.0,
                     "zone": "TEST", "config_fp": fp}, path=cache)
        r = subprocess.run([sys.executable, os.path.join(HERE, "footprint_statusline.py")],
                           input=stdin, capture_output=True, text=True, env=env, timeout=30)
        self.assertIn("(live-grid 250g)", r.stdout)
        self.assertIn("🌫 1.2 gCO2e", r.stdout)  # 4.68 Wh * 250 g/kWh

        write_cache({"fetched_at": time.time() - 2 * 3600, "ci_g_per_kwh": 250.0,
                     "zone": "TEST", "config_fp": fp}, path=cache)
        r = subprocess.run([sys.executable, os.path.join(HERE, "footprint_statusline.py")],
                           input=stdin, capture_output=True, text=True, env=env, timeout=30)
        self.assertIn("(loc-based)", r.stdout, "stale cache must not be used")


class Test8GoldenCrossRuntime(unittest.TestCase):
    """The site's JavaScript engine must produce byte-identical math to the
    Python engine. Skipped (with a visible notice) when node is missing."""

    def test_js_matches_python(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not available — JS golden check skipped")
        tier_models = {"small": "claude-haiku-4-5", "mid": "claude-sonnet-5",
                       "frontier": "claude-opus-4-8"}
        fixtures = {"compute": [], "fmt_sig": []}
        for tier, model in tier_models.items():
            for region in ("temperate", "hot_arid", "cool_humid"):
                tokens = {"in": 12345, "cache": 456789, "out": 23456}
                res = engine.compute(
                    {"m": (tokens["in"], tokens["cache"], tokens["out"], model)},
                    COEFFS, region)
                fixtures["compute"].append({
                    "tier": tier, "region": region, "tokens": tokens,
                    "expected": {"energy": list(res["energy_Wh"]),
                                 "water": list(res["water_mL"]),
                                 "carbon": list(res["carbon_g"])},
                })
        for x in (0.0999, 4.6812, 13.02, 0.234, 1234.0, 99.9, 0.001234, 567890.0):
            fixtures["fmt_sig"].append({"x": x, "n": 2, "expected": engine.fmt_sig(x)})
        fixtures["diesel"] = []
        for energy in ([1000.0, 500.0, 2000.0], [240.0, 72.0, 850.0]):
            sc = counterfactuals.scenario_diesel({"energy_Wh": tuple(energy)}, COEFFS)
            fixtures["diesel"].append({
                "energy": energy,
                "expected": list(sc["carbon_g"]),
            })
        fx_path = os.path.join(TMP, "golden.json")
        with open(fx_path, "w") as f:
            json.dump(fixtures, f)
        r = subprocess.run([node, os.path.join(HERE, "site", "golden_check.mjs"), fx_path],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        print("\n   " + r.stdout.strip())


class Test9SanityAnchor(unittest.TestCase):
    def test_altman_anchor_nonblocking(self):
        """NON-BLOCKING sanity check (CLOUD_CONTEXT.md): a cache-light 1k-token
        chat on the mid tier should land within ~5x of the public ~0.34
        Wh/query anchor. Prints a notice instead of failing when outside."""
        e = report.whatif_entries("claude-sonnet-5", 1000, "chat")
        res = engine.compute(e, COEFFS, "temperate")
        wh = res["energy_Wh"][0]
        if not (0.34 / 5 <= wh <= 0.34 * 5):
            print("\nNOTE (non-blocking): mid-tier 1k-token chat = %.3f Wh vs 0.34 Wh anchor" % wh)
        else:
            print("\n   Altman anchor: mid-tier 1k chat = %.3f Wh (within 5x of 0.34)" % wh)


class Test10InsightsAndCounterfactuals(unittest.TestCase):
    """Usage-style composition + modeled savings (Phase 1)."""

    def setUp(self):
        # Mixed session: frontier + mid, cache-heavy agent shape
        self.entries = {
            "a": (5000, 80000, 3000, "claude-opus-4"),
            "b": (2000, 20000, 1000, "claude-sonnet-4"),
        }
        self.res = engine.compute(self.entries, COEFFS, "temperate")

    def test_composition_parts_sum_to_total(self):
        parts = insights.energy_by_token_class(self.entries, COEFFS, "temperate")
        total = sum(parts.values())
        self.assertAlmostEqual(total, self.res["energy_Wh"][0], places=6)
        self.assertGreater(parts["cache"], 0)
        self.assertGreater(parts["out"], 0)

    def test_composition_lines_include_bar(self):
        parts = insights.energy_by_token_class(self.entries, COEFFS, "temperate")
        text = "\n".join(insights.composition_lines(parts, self.res["energy_Wh"][0]))
        self.assertIn("█", text)
        self.assertIn("cache reads", text)
        self.assertIn("not a usage quota", text.lower())

    def test_frontier_insight_present(self):
        ins = insights.contribution_insights(
            self.entries, COEFFS, "temperate", self.res
        )
        kinds = [i["kind"] for i in ins]
        self.assertIn("tier", kinds)
        self.assertTrue(any("frontier" in i["headline"] for i in ins))

    def test_tier_down_saves_energy(self):
        row = counterfactuals.scenario_one_tier_down(
            self.entries, COEFFS, "temperate", self.res
        )
        self.assertIsNotNone(row)
        self.assertGreater(row["save_energy_Wh"], 0)
        self.assertGreater(row["save_energy_pct"], 0)

    def test_all_small_saves_more_than_tier_down(self):
        td = counterfactuals.scenario_one_tier_down(
            self.entries, COEFFS, "temperate", self.res
        )
        sm = counterfactuals.scenario_all_tier(
            self.entries, COEFFS, "temperate", self.res, "small"
        )
        self.assertIsNotNone(sm)
        self.assertGreater(sm["save_energy_Wh"], td["save_energy_Wh"])

    def test_timing_scales_carbon_only(self):
        live = {
            "ci_g_per_kwh": 400.0,
            "ci_forecast": [
                {"t": "2026-07-28T13:00", "ci": 100.0},
                {"t": "2026-07-28T20:00", "ci": 500.0},
            ],
        }
        # Baseline must use same live CI for apples-to-apples ratio
        res = engine.compute(self.entries, COEFFS, "temperate", live=live)
        best, worst = counterfactuals.scenario_timing(res, live)
        self.assertIsNotNone(best)
        self.assertTrue(best["energy_same"] or best["kind"] == "timing_best")
        self.assertAlmostEqual(best["energy_Wh"], res["energy_Wh"][0], places=6)
        self.assertAlmostEqual(best["carbon_g"], res["carbon_g"][0] * (100.0 / 400.0), places=5)
        self.assertGreater(best["save_carbon_g"], 0)
        self.assertIsNotNone(worst)
        self.assertLess(worst["save_carbon_g"], 0)

    def test_session_report_usage_sections(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            for mid, (tin, tc, to, model) in self.entries.items():
                f.write(json.dumps({
                    "type": "assistant",
                    "message": {
                        "id": mid,
                        "model": model,
                        "usage": {
                            "input_tokens": tin,
                            "cache_creation_input_tokens": 0,
                            "cache_read_input_tokens": tc,
                            "output_tokens": to,
                        },
                    },
                }) + "\n")
            path = f.name
        try:
            md = report.session_report(COEFFS, "temperate", transcript=path, live=None)
        finally:
            os.unlink(path)
        self.assertIn("### Session", md)
        self.assertIn("### Composition of energy", md)
        self.assertIn("### What's contributing to your footprint?", md)
        self.assertIn("### What you could have saved", md)
        self.assertIn("### By model", md)
        self.assertIn("scenario envelope", md.lower())
        self.assertIn("modeled counterfactual", md.lower())

    def test_whatif_keeps_tier_marker(self):
        md = report.whatif_report(COEFFS, "temperate", "opus", 500000)
        self.assertIn("← this estimate", md)
        self.assertIn("location-based", md)
        self.assertIn("### What you could have saved", md)


class Test12DieselCoefficients(unittest.TestCase):
    def test_diesel_coefficient_is_genset_not_ar5_oil(self):
        c = engine.load_coefficients()
        d = c["diesel_backup"]["direct_g_per_kWh"]
        self.assertEqual(d["central"], 780)
        self.assertEqual(d["low"], 680)
        self.assertEqual(d["high"], 890)
        self.assertEqual(d["label"], "modeled")
        self.assertNotIn("AR5", d["source"])
        self.assertIn("EPA", d["source"])
        self.assertIn("gal", d["source"])
        self.assertEqual(
            c["diesel_backup"]["risk_vocab"],
            ["none", "elevated_oil_share", "emergency_alert"],
        )
        life = c["diesel_backup"]["lifecycle_g_per_kWh"]
        self.assertEqual(life["central"], 920)
        self.assertLessEqual(life["low"], life["central"])
        self.assertLessEqual(life["central"], life["high"])


class Test13DieselScenario(unittest.TestCase):
    def test_diesel_scenario_does_not_change_compute_carbon(self):
        entries = {"m": (1000, 0, 100, "claude-sonnet-4")}
        r = engine.compute(entries, COEFFS, "temperate")
        before = r["carbon_g"]
        sc = counterfactuals.scenario_diesel(r, COEFFS)
        r2 = engine.compute(entries, COEFFS, "temperate")
        self.assertEqual(r2["carbon_g"], before)
        self.assertGreater(sc["carbon_g"][0], before[0])
        self.assertEqual(sc["label"], "modeled-diesel")
        self.assertIsNone(sc["replaces"])

    def test_diesel_scenario_scales_only_with_energy(self):
        a = {"energy_Wh": (1000.0, 500.0, 2000.0)}
        b = {"energy_Wh": (2000.0, 1000.0, 4000.0)}
        sa = counterfactuals.scenario_diesel(a, COEFFS)
        sb = counterfactuals.scenario_diesel(b, COEFFS)
        self.assertAlmostEqual(sb["carbon_g"][0], 2 * sa["carbon_g"][0])
        self.assertAlmostEqual(sa["carbon_g"][0], 1000.0 / 1000.0 * 780)

    def test_compute_ignores_diesel_risk_in_live(self):
        entries = {"m": (1000, 0, 100, "claude-sonnet-4")}
        r0 = engine.compute(entries, COEFFS, "temperate")
        r1 = engine.compute(
            entries, COEFFS, "temperate",
            live={"diesel_risk": "emergency_alert"},
        )
        self.assertEqual(r0["carbon_g"], r1["carbon_g"])


def _write_jsonl(rows):
    f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
    for row in rows:
        f.write(json.dumps(row) + "\n")
    f.close()
    return f.name


def _assistant(mid, geo=None, model="claude-sonnet-4"):
    usage = {
        "input_tokens": 10,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": 5,
    }
    if geo is not None:
        usage["inference_geo"] = geo
    return {
        "type": "assistant",
        "message": {"id": mid, "model": model, "usage": usage},
    }


class Test14InferenceGeo(unittest.TestCase):
    def test_inference_geo_all_us(self):
        path = _write_jsonl([_assistant("a", "us"), _assistant("b", "us")])
        try:
            meta = engine.parse_transcript_meta(path)
        finally:
            os.unlink(path)
        self.assertEqual(meta["inference_geo"], "us")
        self.assertEqual(meta["inference_geo_counts"]["us"], 2)

    def test_inference_geo_global_wins_if_any_global(self):
        path = _write_jsonl([_assistant("a", "us"), _assistant("b", "global")])
        try:
            meta = engine.parse_transcript_meta(path)
        finally:
            os.unlink(path)
        self.assertEqual(meta["inference_geo"], "global")

    def test_inference_geo_absent(self):
        path = _write_jsonl([_assistant("a"), _assistant("b")])
        try:
            meta = engine.parse_transcript_meta(path)
        finally:
            os.unlink(path)
        self.assertIsNone(meta["inference_geo"])

    def test_report_names_undisclosed_location(self):
        path = _write_jsonl([_assistant("a", "global")])
        try:
            md = report.session_report(COEFFS, "temperate", transcript=path)
        finally:
            os.unlink(path)
        self.assertIn("not disclosed", md.lower())
        self.assertIn("inference_geo", md.lower())

    def test_report_names_us_facility_unknown(self):
        path = _write_jsonl([_assistant("a", "us")])
        try:
            md = report.session_report(COEFFS, "temperate", transcript=path)
        finally:
            os.unlink(path)
        self.assertIn("US infrastructure", md)
        self.assertIn("facility unknown", md.lower())


class Test11SitePublish(unittest.TestCase):
    """GitHub Pages publishes site/ as root — coefficients must live there
    as a byte-identical copy of the engine file."""

    def test_site_coefficients_copy_matches(self):
        src = os.path.join(HERE, "modelfootprint", "coefficients.json")
        dst = os.path.join(HERE, "site", "coefficients.json")
        self.assertTrue(
            os.path.isfile(dst),
            "site/coefficients.json missing — copy from modelfootprint/",
        )
        with open(src, encoding="utf-8") as a, open(dst, encoding="utf-8") as b:
            self.assertEqual(json.load(a), json.load(b))

    def test_site_fetches_local_coefficients(self):
        with open(os.path.join(HERE, "site", "index.html"), encoding="utf-8") as f:
            html = f.read()
        self.assertIn('fetch("./coefficients.json")', html)
        self.assertNotIn("../modelfootprint/coefficients.json", html)


def load_tests(loader, tests, pattern):  # keep class order deterministic
    return tests


if __name__ == "__main__":
    unittest.main(verbosity=2)
