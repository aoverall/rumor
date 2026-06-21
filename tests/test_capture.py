#!/usr/bin/env python3
"""Tests for `rumor capture` (scripts/capture.py).

capture writes the sacred ground truth, so these tests are paranoid and HERMETIC: every test
points --eval and --learnings at a tempdir and NEVER the real docs/eval-set.jsonl or the real
.learnings. The proofs that matter:

  - the dual write is atomic (a .learnings failure rolls the eval-set append back, so a
    half-capture is impossible)
  - the eval line is byte-compatible with the existing source=capture row
  - shape is enforced, so no malformed record can enter ground truth
  - the .learnings entry round-trips through extract_learnings.py with its TRUE verdict for
    ALL FIVE capture verdicts (not just the two the lossy category map happens to preserve)

Run with:  python3 -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import capture          # noqa: E402
import extract_learnings  # noqa: E402

BASE = dict(human="rad fuck yes keep going", artifact="served everything at one URL",
            why="honesty plus reachability plus a true systemic read", question="is it real",
            verdict="amazing", mode="push")


def _args(eval_path, learnings_path, **over):
    a = ["--eval", eval_path, "--learnings", learnings_path]
    fields = {**BASE, **over}
    for k, v in fields.items():
        if v is not None:
            a += [f"--{k}", str(v)]
    return a


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="rumor-cap-")
        self.eval = os.path.join(self.dir, "eval-set.jsonl")
        self.learn = os.path.join(self.dir, ".learnings", "LEARNINGS.md")

    def _records(self):
        with open(self.eval, encoding="utf-8") as fh:
            return [json.loads(l) for l in fh if l.strip()]

    # --- happy path + byte shape ---------------------------------------------------------

    def test_happy_writes_both_sinks(self):
        code = capture.main(_args(self.eval, self.learn, session="voice-2026-06-15"))
        self.assertEqual(code, 0)
        recs = self._records()
        self.assertEqual(len(recs), 1)
        self.assertEqual(list(recs[0].keys()), capture.EVAL_KEYS)  # exact key order
        self.assertEqual(recs[0]["source"], "capture")
        self.assertEqual(recs[0]["verdict"], "amazing")
        self.assertNotIn("ts", recs[0])  # the real shape has no ts
        self.assertTrue(os.path.exists(self.learn))

    def test_eval_line_is_compact_and_unicode_literal(self):
        capture.main(_args(self.eval, self.learn, human="just you and me — café",
                           session="s", idx=1))
        raw = open(self.eval, encoding="utf-8").read()
        self.assertIn('"source":"capture"', raw)   # compact separators, no spaces
        self.assertIn("café", raw)                  # ensure_ascii=False, literal unicode
        self.assertNotIn("\\u", raw)

    # --- round-trip ALL FIVE verdicts through extract_learnings --------------------------

    def test_all_five_verdicts_roundtrip_faithfully(self):
        cases = [("amazing", "push"), ("acceptable", "neutral"), ("rejected", "interrogate"),
                 ("redirected", "neutral"), ("confused", "interrogate")]
        for i, (verdict, mode) in enumerate(cases, 1):
            with self.subTest(verdict=verdict):
                d = tempfile.mkdtemp(prefix="rt-")
                ev, lr = os.path.join(d, "e.jsonl"), os.path.join(d, "L.md")
                self.assertEqual(capture.main(_args(ev, lr, verdict=verdict, mode=mode,
                                                    session="s", idx=1)), 0)
                mined = list(extract_learnings._parse_markdown_log(lr))
                self.assertEqual(len(mined), 1)
                self.assertEqual(mined[0]["verdict"], verdict,
                                 f"{verdict} did not round-trip through .learnings")
                self.assertEqual(mined[0]["mode"], mode)

    # --- validation rejects, nothing written --------------------------------------------

    def _assert_reject(self, args, code=2):
        before_eval = os.path.exists(self.eval)
        self.assertEqual(capture.main(args), code)
        # nothing written on reject
        self.assertEqual(os.path.exists(self.eval), before_eval)
        if os.path.exists(self.eval):
            self.assertEqual(self._records(), [])

    def test_rejects_bad_verdict(self):
        self._assert_reject(_args(self.eval, self.learn, verdict="great"))

    def test_rejects_bad_mode(self):
        self._assert_reject(_args(self.eval, self.learn, mode="vibes"))

    def test_rejects_empty_human(self):
        self._assert_reject(_args(self.eval, self.learn, human="   "))

    def test_rejects_empty_why(self):
        self._assert_reject(_args(self.eval, self.learn, why=""))

    def test_rejects_bad_category(self):
        self._assert_reject(_args(self.eval, self.learn, category="misc"))

    def test_rejects_duplicate_triple(self):
        self.assertEqual(capture.main(_args(self.eval, self.learn, session="s", idx=7)), 0)
        self._assert_reject_nonempty(_args(self.eval, self.learn, session="s", idx=7))

    def _assert_reject_nonempty(self, args):
        before = open(self.eval, encoding="utf-8").read()
        self.assertEqual(capture.main(args), 2)
        self.assertEqual(open(self.eval, encoding="utf-8").read(), before)  # unchanged

    # --- atomicity: a .learnings failure rolls the eval-set append back ------------------

    def test_atomic_rollback_on_learnings_failure(self):
        # seed one good capture, then force the second write to fail and prove rollback
        self.assertEqual(capture.main(_args(self.eval, self.learn, session="s", idx=1)), 0)
        before = open(self.eval, encoding="utf-8").read()
        blocker = os.path.join(self.dir, "blocker")
        open(blocker, "w").close()                       # a FILE where a dir is needed
        bad_learn = os.path.join(blocker, "sub", "L.md")  # makedirs will fail on it
        code = capture.main(_args(self.eval, bad_learn, session="s", idx=2))
        self.assertEqual(code, 3)
        self.assertEqual(open(self.eval, encoding="utf-8").read(), before,
                         "eval-set was not rolled back after the .learnings write failed")

    # --- idx auto-assign + dry-run -------------------------------------------------------

    def test_idx_auto_increments_within_session(self):
        capture.main(_args(self.eval, self.learn, session="s"))         # idx 1
        capture.main(_args(self.eval, self.learn, session="s"))         # idx 2
        capture.main(_args(self.eval, self.learn, session="other"))     # idx 1 (fresh)
        recs = self._records()
        self.assertEqual([r["idx"] for r in recs], [1, 2, 1])

    def test_dry_run_writes_nothing(self):
        code = capture.main(_args(self.eval, self.learn, dry_run=None) + ["--dry-run"])
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(self.eval))
        self.assertFalse(os.path.exists(self.learn))


class NoRealPathDefault(unittest.TestCase):
    def test_capture_source_has_no_real_eval_default(self):
        src = open(os.path.join(os.path.dirname(__file__), "..", "scripts", "capture.py"),
                   encoding="utf-8").read()
        # --eval/--learnings are required with no default, so a forgotten override in a test
        # can never ride onto the real sinks.
        self.assertNotIn('"docs/eval-set.jsonl"', src)
        self.assertIn('required=True', src)


if __name__ == "__main__":
    unittest.main()
