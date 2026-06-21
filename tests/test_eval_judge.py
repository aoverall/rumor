#!/usr/bin/env python3
"""Tests for the eval-judge calibration harness (eval_judge.py).

This harness owns the deterministic parts of the SC3 validation: the held-out
split and the with/without-cartridge scoring. SC3 is the project's central claim
(does the rumor actually help?), so a silent bug here produces a confident,
fake-favorable number — the exact failure the cartridge calls fraud. The split
must be reproducible and the delta must be measured over a comparable set; both
are guarded directly here.

Run with:
    python3 -m unittest discover -s tests -v
"""
import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import eval_judge  # noqa: E402


def _write_jsonl(records):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return path


def _score(preds):
    """Run cmd_score over preds, returning (parsed stdout json, stderr text)."""
    path = _write_jsonl(preds)
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            eval_judge.cmd_score(argparse.Namespace(predictions=path))
    finally:
        os.unlink(path)
    return json.loads(out.getvalue()), err.getvalue()


class SplitTest(unittest.TestCase):
    def _split(self, records, frac=0.25):
        eval_path = _write_jsonl(records)
        d = _write_jsonl([])
        h = _write_jsonl([])
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                eval_judge.cmd_split(argparse.Namespace(
                    eval=eval_path, holdout_frac=frac, out_distill=d, out_holdout=h))
            with open(d) as fh:
                distill = [json.loads(x) for x in fh if x.strip()]
            with open(h) as fh:
                holdout = [json.loads(x) for x in fh if x.strip()]
        finally:
            for p in (eval_path, d, h):
                os.unlink(p)
        return distill, holdout, err.getvalue()

    def _recs(self, n):
        return [{"source": "claude", "session": f"s{i}", "idx": i,
                 "verdict": "amazing" if i % 2 else "acceptable"} for i in range(n)]

    def test_split_is_deterministic_across_runs(self):
        recs = self._recs(40)
        d1, h1, _ = self._split(recs)
        d2, h2, _ = self._split(recs)
        self.assertEqual([r["idx"] for r in h1], [r["idx"] for r in h2])
        self.assertEqual([r["idx"] for r in d1], [r["idx"] for r in d2])

    def test_split_partitions_every_record_exactly_once(self):
        recs = self._recs(40)
        distill, holdout, _ = self._split(recs)
        self.assertEqual(len(distill) + len(holdout), len(recs))
        ids = {r["idx"] for r in distill} | {r["idx"] for r in holdout}
        self.assertEqual(ids, {r["idx"] for r in recs})

    def test_bucket_stable_for_identical_identity(self):
        rec = {"source": "codex", "session": "abc", "idx": 7}
        self.assertEqual(eval_judge._stable_bucket(rec), eval_judge._stable_bucket(dict(rec)))

    def test_split_warns_when_holdout_has_few_scorable(self):
        # All neutral verdicts => zero amazing/acceptable in holdout => warn.
        recs = [{"source": "claude", "session": f"s{i}", "idx": i, "verdict": "neutral"}
                for i in range(8)]
        _, _, err = self._split(recs, frac=0.5)
        self.assertIn("warn", err)

    def test_split_stratifies_each_metric_class_into_the_holdout(self):
        # A plain hash bucket can leave the holdout with zero `amazing` on a small set, which
        # makes the SC3 delta meaningless. Stratifying must put each metric class in holdout.
        recs = ([{"source": "claude", "session": f"a{i}", "idx": i, "verdict": "amazing"} for i in range(4)]
                + [{"source": "claude", "session": f"b{i}", "idx": 100 + i, "verdict": "acceptable"} for i in range(12)]
                + [{"source": "claude", "session": f"c{i}", "idx": 200 + i, "verdict": "rejected"} for i in range(10)])
        _, holdout, _ = self._split(recs, frac=0.25)
        hv = [r["verdict"] for r in holdout]
        self.assertIn("amazing", hv)       # the rare class reached the holdout
        self.assertIn("acceptable", hv)
        self.assertTrue(len(holdout) < len(recs))  # distill keeps the rest

    def test_split_warns_by_name_when_a_metric_class_cannot_reach_holdout(self):
        # A singleton `amazing` can't be split into the holdout; warn naming the class.
        recs = ([{"source": "claude", "session": "a", "idx": 1, "verdict": "amazing"}]
                + [{"source": "claude", "session": f"b{i}", "idx": 100 + i, "verdict": "acceptable"} for i in range(8)])
        _, holdout, err = self._split(recs, frac=0.25)
        self.assertNotIn("amazing", [r["verdict"] for r in holdout])
        self.assertIn("amazing", err)


class ScoreTest(unittest.TestCase):
    def test_only_gold_metric_labels_are_scorable(self):
        res, _ = _score([
            {"gold": "amazing", "pred_with": "amazing", "pred_without": "amazing"},
            {"gold": "redirected", "pred_with": "amazing", "pred_without": "acceptable"},
            {"gold": "neutral", "pred_with": "acceptable", "pred_without": "amazing"},
        ])
        self.assertEqual(res["scorable_gold"], 1)
        self.assertEqual(res["paired_scored"], 1)

    def test_delta_measured_over_paired_set_only(self):
        # idx2: judge declined WITHOUT the cartridge. The old harness scored
        # 'with' over 3 and 'without' over 2, inflating the baseline. The paired
        # fix must drop idx2 from BOTH arms so the comparison stays honest.
        res, _ = _score([
            {"gold": "amazing", "pred_with": "amazing", "pred_without": "acceptable"},
            {"gold": "acceptable", "pred_with": "acceptable", "pred_without": None},
            {"gold": "amazing", "pred_with": "amazing", "pred_without": "amazing"},
        ])
        self.assertEqual(res["paired_scored"], 2)
        self.assertEqual(res["accuracy_with_cartridge"], 1.0)
        # paired baseline: idx1 wrong, idx3 right => 0.5, not the inflated 0.5-from-2.
        self.assertEqual(res["accuracy_without_cartridge"], 0.5)
        self.assertEqual(res["delta"], 0.5)
        self.assertEqual(res["dropped_on_label_only_with"], 1)

    def test_off_label_prediction_does_not_silently_vanish(self):
        # A garbage / off-label token in one arm must be dropped from the pair and
        # counted, not treated as a wrong-but-scored prediction.
        res, _ = _score([
            {"gold": "amazing", "pred_with": "amazing", "pred_without": "garbage"},
            {"gold": "acceptable", "pred_with": "acceptable", "pred_without": "acceptable"},
        ])
        self.assertEqual(res["paired_scored"], 1)
        self.assertEqual(res["dropped_on_label_only_with"], 1)

    def test_no_paired_predictions_warns_and_zeroes(self):
        res, err = _score([
            {"gold": "amazing", "pred_with": "amazing", "pred_without": None},
        ])
        self.assertEqual(res["paired_scored"], 0)
        self.assertEqual(res["delta"], 0.0)
        self.assertIn("warn", err)

    def test_verdict_reflects_sign_of_delta(self):
        helps, _ = _score([
            {"gold": "amazing", "pred_with": "amazing", "pred_without": "acceptable"},
        ])
        self.assertEqual(helps["verdict"], "cartridge helps")
        hurts, _ = _score([
            {"gold": "amazing", "pred_with": "acceptable", "pred_without": "amazing"},
        ])
        self.assertEqual(hurts["verdict"], "cartridge hurts")
        same, _ = _score([
            {"gold": "amazing", "pred_with": "amazing", "pred_without": "amazing"},
        ])
        self.assertEqual(same["verdict"], "no effect")

    def test_thin_pairing_emits_provisional_warning(self):
        # 1 paired, 3 dropped for missing/off-label => more dropped than scored.
        res, err = _score([
            {"gold": "amazing", "pred_with": "amazing", "pred_without": "amazing"},
            {"gold": "amazing", "pred_with": "amazing", "pred_without": None},
            {"gold": "acceptable", "pred_with": None, "pred_without": "acceptable"},
            {"gold": "acceptable", "pred_with": "amazing", "pred_without": None},
        ])
        self.assertEqual(res["paired_scored"], 1)
        self.assertIn("provisional", err)


if __name__ == "__main__":
    unittest.main()
