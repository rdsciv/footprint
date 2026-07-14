#!/usr/bin/env python3
"""Verification suite for the statusline accuracy contract — stdlib unittest,
runnable directly (`python3 test_footprint_statusline.py`) or via pytest.

Covers: hand-computed fixture, dedupe by message.id, cache-write attribution,
mixed-model tiering, unknown-model envelope + marker, scenario-envelope
propagation, malformed-input robustness, and timing against a synthetic 10 MB
transcript (hermetic — no personal data; set FOOTPRINT_BENCH_REAL=1 to also
benchmark your largest real transcript locally).

Hermeticity: all FOOTPRINT_* env vars are scrubbed for the test process and
every subprocess; temp dirs are cleaned up; subprocesses carry timeouts.
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

BENCH_REAL = os.environ.get("FOOTPRINT_BENCH_REAL") == "1"
for _k in [k for k in os.environ if k.startswith("FOOTPRINT_")]:
    del os.environ[_k]

import footprint_statusline as fs  # noqa: E402
from modelfootprint import engine  # noqa: E402

COEFFS = engine.load_coefficients()
CI = (345.0, 150.0, 600.0)  # static location-based central + hourly/regional envelope


def scrubbed_env(extra=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("FOOTPRINT_")}
    env["FOOTPRINT_LIVE"] = "0"  # static expectations: no live cache may leak in
    if extra:
        env.update(extra)
    return env


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


class StatuslineTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="footprint_test_")
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)

    @classmethod
    def write_transcript(cls, name, lines):
        path = os.path.join(cls.tmp, name)
        with open(path, "wb") as f:
            for line in lines:
                if isinstance(line, bytes):
                    f.write(line)
                else:
                    f.write(json.dumps(line).encode() + b"\n")
        return path

    def run_statusline(self, path, env_extra=None):
        stdin = json.dumps({"transcript_path": path, "session_id": "test"})
        r = subprocess.run(
            [sys.executable, os.path.join(HERE, "footprint_statusline.py")],
            input=stdin, capture_output=True, text=True,
            env=scrubbed_env(env_extra), timeout=30,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.splitlines()[0]


class Test1HandCheckedFixture(StatuslineTestBase):
    """10,000 in / 50,000 cache-read / 2,000 out on sonnet (mid tier),
    region temperate. By hand from coefficients.json (v0.3.0 boundaries):

      IT Wh      = (10000*0.15 + 50000*0.03 + 2000*0.45) / 1000 = 3.9
      energy     = 3.9 * PUE 1.2                                = 4.68 Wh
      water_mL   = IT*WUE + facility*EWIF
                 = 3.9*0.27 + 4.68*1.0    = 1.053 + 4.68        = 5.733
      carbon_g   = 4.68/1000 * 345 (loc-based central)          = 1.6146

      low : IT = (10000*0.075 + 50000*0.01 + 2000*0.225)/1000 = 1.7
            energy = 1.7*1.08 = 1.836
            water  = 1.7*0.2 + 1.836*0.5 = 1.258
            carbon = 1.836/1000 * 150 = 0.2754
      high: IT = (10000*0.3 + 50000*0.09 + 2000*0.9)/1000 = 9.3
            energy = 9.3*1.4 = 13.02
            water  = 9.3*0.45 + 13.02*1.8 = 27.621
            carbon = 13.02/1000 * 600 = 7.812
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.path = cls.write_transcript(
            "hand.jsonl", [asst("m1", "claude-sonnet-5", 10000, 0, 50000, 2000)]
        )
        cls.res = engine.compute(engine.parse_transcript(cls.path), COEFFS, "temperate")

    def test_energy_exact(self):
        for i, want in enumerate((4.68, 1.836, 13.02)):
            self.assertAlmostEqual(self.res["energy_Wh"][i], want, places=12)

    def test_it_energy_exact(self):
        for i, want in enumerate((3.9, 1.7, 9.3)):
            self.assertAlmostEqual(self.res["energy_IT_Wh"][i], want, places=12)

    def test_water_boundary_correct(self):
        for i, want in enumerate((5.733, 1.258, 27.621)):
            self.assertAlmostEqual(self.res["water_mL"][i], want, places=12)

    def test_carbon_with_ci_envelope(self):
        for i, want in enumerate((1.6146, 0.2754, 7.812)):
            self.assertAlmostEqual(self.res["carbon_g"][i], want, places=12)

    def test_display_rules(self):
        line = self.run_statusline(self.path)
        self.assertIn("⚡ 4.7 Wh", line)
        self.assertIn("[1.8–13]", line)
        self.assertIn("💧 ~5.7 mL", line)
        self.assertIn("🌫 1.6 gCO2e (loc-based)", line)
        self.assertIn("10k in · 50k cache · 2.0k out", line)


class Test2Dedupe(StatuslineTestBase):
    def test_same_message_id_counted_once_last_wins(self):
        path = self.write_transcript("dedupe.jsonl", [
            asst("mdup", "claude-sonnet-5", 100, 0, 0, 10),
            asst("mdup", "claude-sonnet-5", 100, 0, 0, 200),
            asst("mdup", "claude-sonnet-5", 100, 0, 0, 555),
        ])
        ent = engine.parse_transcript(path)
        self.assertEqual(len(ent), 1)
        self.assertEqual(ent["mdup"][2], 555)
        res = engine.compute(ent, COEFFS, "temperate")
        # (100*0.15 + 555*0.45)/1000 * 1.2 = 0.3177
        self.assertAlmostEqual(res["energy_Wh"][0], 0.3177, places=12)


class Test3CacheWriteAttribution(StatuslineTestBase):
    def test_cache_writes_priced_as_fresh_input(self):
        path = self.write_transcript(
            "cachewrite.jsonl", [asst("m1", "claude-sonnet-5", 0, 10000, 0, 0)]
        )
        ent = engine.parse_transcript(path)
        self.assertEqual((ent["m1"][0], ent["m1"][1]), (10000, 0))
        res = engine.compute(ent, COEFFS, "temperate")
        # priced at e_in: 10000*0.15/1000*1.2 = 1.8 (at e_cache it would be 0.36)
        self.assertAlmostEqual(res["energy_Wh"][0], 1.8, places=12)


class Test4MixedModels(StatuslineTestBase):
    def test_per_entry_tiering(self):
        haiku = asst("mh", "claude-haiku-4-5-20251001", 0, 0, 0, 1000)
        haiku["isSidechain"] = True
        opus = asst("mo", "claude-opus-4-8", 0, 0, 0, 1000)
        path = self.write_transcript("mixed.jsonl", [haiku, opus])
        res = engine.compute(engine.parse_transcript(path), COEFFS, "temperate")
        # per-tier: (1000*0.15 + 1000*0.9)/1000 * 1.2 = 1.26 Wh
        # all-opus would be 2.16, all-haiku 0.36
        self.assertAlmostEqual(res["energy_Wh"][0], 1.26, places=12)
        self.assertGreater(abs(res["energy_Wh"][0] - 2.16), 0.1)
        self.assertGreater(abs(res["energy_Wh"][0] - 0.36), 0.1)
        self.assertEqual(res["tokens"]["out"], 2000, "sidechain entries included")


class Test5UnknownModel(StatuslineTestBase):
    def test_unknown_model_mid_central_wide_envelope(self):
        path = self.write_transcript(
            "unknown.jsonl", [asst("m1", "totally-new-model-x9", 1000, 0, 0, 100)]
        )
        res = engine.compute(engine.parse_transcript(path), COEFFS, "temperate")
        # central at mid: (1000*0.15 + 100*0.45)/1000*1.2 = 0.234
        self.assertAlmostEqual(res["energy_Wh"][0], 0.234, places=12)
        # envelope spans small lows .. frontier highs:
        # low  = (1000*0.02 + 100*0.06)/1000 * 1.08 = 0.02808
        # high = (1000*0.9  + 100*2.7)/1000 * 1.4   = 1.638
        self.assertAlmostEqual(res["energy_Wh"][1], 0.02808, places=12)
        self.assertAlmostEqual(res["energy_Wh"][2], 1.638, places=12)
        line = self.run_statusline(path)
        self.assertIn("0.23? Wh", line, line)

    def test_tier_lookup_anchoring(self):
        lk = COEFFS["model_tier_lookup"]
        self.assertEqual(engine.tier_for("claude-haiku-4-5", lk), ("small", True))
        self.assertEqual(engine.tier_for("gpt-5-nano-2027", lk), ("small", True),
                         "'gpt-5-nano' must win over 'gpt-5'")
        self.assertEqual(engine.tier_for("gpt-4o-2024-11-20", lk), ("frontier", True),
                         "gpt-4o is Large-class per METHODOLOGY 2.2")
        self.assertEqual(engine.tier_for("gpt-4o-mini", lk), ("mid", True),
                         "Mini variants are Medium-class per METHODOLOGY 2.2")
        # anchored matching: 'o1' must not match inside another token
        self.assertEqual(engine.tier_for("model-no1x", lk)[1], False)
        self.assertEqual(engine.tier_for("o1-preview", lk), ("frontier", True))


class Test6EnvelopePropagation(StatuslineTestBase):
    def test_scenario_envelope_all_bounds(self):
        path = self.write_transcript(
            "range.jsonl", [asst("m1", "claude-opus-4-8", 3000, 1000, 20000, 5000)]
        )
        res = engine.compute(engine.parse_transcript(path), COEFFS, "hot_arid")
        t = COEFFS["model_tiers"]["frontier"]
        pue = COEFFS["infrastructure_overhead"]["PUE_typical_hyperscale"]
        reg = COEFFS["region_presets"]["hot_arid"]
        for i, b in enumerate(("central", "low", "high")):
            it = (4000 * t["e_in_Wh_per_1k_tok"][b] + 20000 * t["e_cache_Wh_per_1k_tok"][b]
                  + 5000 * t["e_out_Wh_per_1k_tok"][b]) / 1000.0
            e = it * pue[b]
            w = it * reg["WUE_site_L_per_kWh"][b] + e * reg["EWIF_offsite_L_per_kWh_seasonal_fallback"][b]
            c = e / 1000.0 * CI[i]
            self.assertAlmostEqual(res["energy_Wh"][i], e, places=12)
            self.assertAlmostEqual(res["water_mL"][i], w, places=12)
            self.assertAlmostEqual(res["carbon_g"][i], c, places=12)
        self.assertLess(res["energy_Wh"][1], res["energy_Wh"][0])
        self.assertLess(res["energy_Wh"][0], res["energy_Wh"][2])


class Test7Robustness(StatuslineTestBase):
    def test_malformed_lines_skipped(self):
        good = json.dumps(asst("mok", "claude-sonnet-5", 1000, 0, 0, 100)).encode() + b"\n"
        path = self.write_transcript("mangled.jsonl", [
            b"this is not json at all\n",
            b'{"type":"assistant","message":{"id":"nouse","model":"claude-sonnet-5"}}\n',
            b'{"type":"assistant","message":{"id":"badusage","model":"x","usage":"usage-not-a-dict"}}\n',
            b'{"type":"user","message":{"content":"decoy \\"usage\\" string"}}\n',
            good,
            b'{"type":"assistant","message":{"id":"trunc","usage":{"input_tokens":999999,"cache_read',
        ])
        ent = engine.parse_transcript(path)
        self.assertEqual(list(ent), ["mok"])
        res = engine.compute(ent, COEFFS, "temperate")
        self.assertEqual(res["tokens"], {"in": 1000, "cache": 0, "out": 100})
        self.mangled_path = path

    def test_missing_transcript_placeholders(self):
        line = self.run_statusline("/nonexistent/path/transcript.jsonl")
        self.assertEqual(line, fs.PLACEHOLDER_LINE)

    def test_unreadable_coefficients_error_glyph(self):
        path = self.write_transcript("ok.jsonl", [asst("m", "claude-sonnet-5", 1000, 0, 0, 100)])
        line = self.run_statusline(path, {"FOOTPRINT_COEFFS": "/nonexistent/coeffs.json"})
        self.assertTrue(line.startswith("⚠ footprint: coefficients unreadable"), line)
        self.assertIn("1.0k in", line)

    def test_structurally_invalid_coefficients_fail_closed(self):
        bad = os.path.join(self.tmp, "bad_coeffs.json")
        broken = json.loads(json.dumps(COEFFS))
        broken["model_tiers"]["mid"]["e_out_Wh_per_1k_tok"]["low"] = "not-a-number"
        with open(bad, "w") as f:
            json.dump(broken, f)
        path = self.write_transcript("ok2.jsonl", [asst("m", "claude-sonnet-5", 1000, 0, 0, 100)])
        line = self.run_statusline(path, {"FOOTPRINT_COEFFS": bad})
        self.assertTrue(line.startswith("⚠ footprint: coefficients unreadable"), line)

    def test_garbage_stdin(self):
        r = subprocess.run(
            [sys.executable, os.path.join(HERE, "footprint_statusline.py")],
            input="not json", capture_output=True, text=True,
            env=scrubbed_env(), timeout=30,
        )
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.splitlines()[0], fs.PLACEHOLDER_LINE)

    def test_wrong_typed_stdin_fields_never_crash(self):
        for stdin in (
            json.dumps({"transcript_path": ["a", "list"]}),
            json.dumps({"transcript_path": 42}),
            json.dumps({"transcript_path": None}),
            json.dumps([1, 2, 3]),
        ):
            r = subprocess.run(
                [sys.executable, os.path.join(HERE, "footprint_statusline.py")],
                input=stdin, capture_output=True, text=True,
                env=scrubbed_env(), timeout=30,
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.splitlines()[0], fs.PLACEHOLDER_LINE, stdin)


class Test8Timing(StatuslineTestBase):
    """Timing on a checked-in-shape synthetic transcript (~10 MB), so the
    budget is verified hermetically on any machine/CI. Set
    FOOTPRINT_BENCH_REAL=1 to additionally benchmark your own largest real
    transcript (local-only, never required)."""

    def _time_statusline(self, path):
        stdin = json.dumps({"transcript_path": path, "session_id": "timing"})
        t0 = time.perf_counter()
        r = subprocess.run(
            [sys.executable, os.path.join(HERE, "footprint_statusline.py")],
            input=stdin, capture_output=True, text=True,
            env=scrubbed_env(), timeout=30,
        )
        elapsed = time.perf_counter() - t0
        self.assertEqual(r.returncode, 0, r.stderr)
        return elapsed, r.stdout.splitlines()[0]

    def test_synthetic_10mb_under_300ms(self):
        """Shape matches real transcripts: ~1 assistant-usage entry per 10
        lines (tool results, user turns, and progress lines carry no usage
        and take the fast-path skip)."""
        path = os.path.join(self.tmp, "big_synthetic.jsonl")
        filler = json.dumps({"type": "user", "message": {"content": "x" * 400}}).encode() + b"\n"
        entries = 0
        with open(path, "wb") as f:
            while f.tell() < 10 * 1024 * 1024:
                entries += 1
                f.write(json.dumps(
                    asst("m%07d" % entries, "claude-sonnet-5", 1200, 300, 45000, 800)
                ).encode() + b"\n")
                for _ in range(9):
                    f.write(filler)
        size_mb = os.path.getsize(path) / 1e6
        self._time_statusline(path)  # warm the file cache; measure the second run
        elapsed, line = self._time_statusline(path)
        print("\n   timing: %.1f MB synthetic (%d usage entries) -> %.0f ms end-to-end" % (size_mb, entries, elapsed * 1000))
        print("   output: %s" % line)
        self.assertLess(elapsed, 0.3, "%.0f ms" % (elapsed * 1000))

    @unittest.skipUnless(BENCH_REAL, "opt-in local benchmark (FOOTPRINT_BENCH_REAL=1)")
    def test_largest_real_transcript(self):
        import glob
        real = sorted(glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl")),
                      key=os.path.getsize, reverse=True)
        self.assertTrue(real, "no real transcripts found")
        elapsed, line = self._time_statusline(real[0])
        print("\n   real: %.1f MB -> %.0f ms | %s" % (os.path.getsize(real[0]) / 1e6, elapsed * 1000, line))
        self.assertLess(elapsed, 0.3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
