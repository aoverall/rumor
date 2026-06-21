#!/usr/bin/env python3
"""Tests for the remine orchestrator (scripts/remine.py).

The honesty contract is the thing under test: remine must never reach COMPLETE with an
agentic stage skipped. These tests drive the state machine with the deterministic stage
runner stubbed (so the real scripts never run, and the real docs/eval-set.jsonl is never
touched), and assert each gate REFUSES when its handed-off artifact did not really change.

Run with:  python3 -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import remine  # noqa: E402


def _rec(**over):
    base = dict(source="claude", session="s", is_reaction=True, human_text="x",
                artifact_summary="a", verdict="acceptable", why="w",
                question_behind="q", mode="neutral")
    base.update(over)
    return base


class RemineHarness(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="rumor-remine-")
        os.makedirs(os.path.join(self.repo, "docs"))
        os.makedirs(os.path.join(self.repo, "skill", "references"))
        self.eval = os.path.join(self.repo, "docs", "eval-set.jsonl")
        self.cart = os.path.join(self.repo, "skill", "references", "cartridge.md")
        self._write(self.eval, [_rec(idx=1)])
        open(self.cart, "w").write("# cartridge v1\n")
        # Stub the deterministic stage runner: record calls, simulate stage-1's output.
        self.calls = []
        def fake_run(repo, argv):
            self.calls.append(argv[0])
            if argv[0] == "scripts/coverage_check.py" and "run" in argv:
                open(os.path.join(repo, "docs", "candidates-all.jsonl"), "w").write('{"x":1}\n')
            return 0
        self._orig = remine.run_stage
        remine.run_stage = fake_run
        # default: judge produced real paired predictions with a positive delta
        self._orig_score = remine.score_paired
        remine.score_paired = lambda repo, preds: {"paired_scored": 5, "delta": 0.2}

    def tearDown(self):
        remine.run_stage = self._orig
        remine.score_paired = self._orig_score

    def _write(self, path, recs):
        with open(path, "w", encoding="utf-8") as fh:
            for r in recs:
                fh.write(json.dumps(r) + "\n")

    def _advance(self):
        return remine.advance(self.repo)

    def _stage(self):
        return remine.load_state(self.repo)["stage"]

    # --- begin -> classify handoff -------------------------------------------------------

    def test_begin_runs_stage1_and_halts_for_classify(self):
        self.assertEqual(self._advance(), 0)
        self.assertEqual(self._stage(), remine.AWAIT_CLASSIFY)
        self.assertIn("scripts/coverage_check.py", self.calls)

    # --- the core honesty gate: cannot pass classify without a real change ---------------

    def test_refuses_when_eval_unchanged_after_classify_handoff(self):
        self._advance()                                   # -> await_classify
        # agent did NOT run the Workflow: eval-set is byte-identical
        code = self._advance()
        self.assertEqual(code, 2)
        self.assertEqual(self._stage(), remine.AWAIT_CLASSIFY)  # did not advance

    def test_refuses_malformed_mined_eval(self):
        self._advance()
        self._write(self.eval, [_rec(idx=1, verdict="garbage")])  # changed but invalid
        self.assertEqual(self._advance(), 2)
        self.assertEqual(self._stage(), remine.AWAIT_CLASSIFY)

    def test_refuses_empty_mine(self):
        # a Workflow that truncates the eval-set (changed fingerprint, zero reactions) is a
        # failure, not a valid mine
        self._advance()
        open(self.eval, "w").close()                      # empty, changed fingerprint
        self.assertEqual(self._advance(), 2)
        self.assertEqual(self._stage(), remine.AWAIT_CLASSIFY)

    def test_backfills_idx_and_advances(self):
        self._advance()
        # simulate a Workflow-written eval-set: changed content, rows missing idx
        self._write(self.eval, [_rec(human_text="new1"), _rec(human_text="new2", session="t")])
        self.assertEqual(self._advance(), 0)
        self.assertEqual(self._stage(), remine.AWAIT_DISTILL)
        recs = [json.loads(l) for l in open(self.eval)]
        self.assertTrue(all(isinstance(r["idx"], int) for r in recs))  # every row has idx now

    # --- distill gate --------------------------------------------------------------------

    def test_refuses_when_cartridge_unchanged_after_distill_handoff(self):
        self._advance()
        self._write(self.eval, [_rec(idx=1, human_text="changed")])
        self._advance()                                   # -> await_distill
        code = self._advance()                            # cartridge unchanged
        self.assertEqual(code, 2)
        self.assertEqual(self._stage(), remine.AWAIT_DISTILL)

    def test_distill_advances_and_runs_deterministic_stages(self):
        self._advance()
        self._write(self.eval, [_rec(idx=1, human_text="changed")])
        self._advance()
        open(self.cart, "w").write("# cartridge v2 distilled\n")  # real distill
        self.assertEqual(self._advance(), 0)
        self.assertEqual(self._stage(), remine.AWAIT_JUDGE)
        self.assertIn("scripts/render_field_manual.py", self.calls)
        self.assertIn("scripts/eval_judge.py", self.calls)

    # --- judge gate + complete -----------------------------------------------------------

    def _drive_to_judge(self):
        self._advance()
        self._write(self.eval, [_rec(idx=1, human_text="changed")])
        self._advance()
        open(self.cart, "w").write("# cartridge v2\n")
        self._advance()

    def test_refuses_complete_without_predictions(self):
        self._drive_to_judge()
        self.assertEqual(self._stage(), remine.AWAIT_JUDGE)
        code = self._advance()                            # no predictions.jsonl
        self.assertEqual(code, 2)
        self.assertNotEqual(self._stage(), remine.COMPLETE)

    def test_stale_predictions_are_cleared_at_handoff(self):
        # a leftover predictions.jsonl from a prior cycle must NOT satisfy the judge gate:
        # entering AWAIT_JUDGE deletes it, so existence later means a fresh judge.
        self._advance()
        self._write(self.eval, [_rec(idx=1, human_text="changed")])
        self._advance()
        stale = os.path.join(self.repo, "docs", "predictions.jsonl")
        open(stale, "w").write('{"old":1}\n')             # stale, present before distill
        open(self.cart, "w").write("# v2\n")
        self._advance()                                   # -> AWAIT_JUDGE, should clear stale
        self.assertFalse(os.path.exists(stale))
        self.assertEqual(self._advance(), 2)              # now refuses: no fresh predictions

    def test_refuses_when_judge_scored_zero_pairs(self):
        self._drive_to_judge()
        open(os.path.join(self.repo, "docs", "predictions.jsonl"), "w").write('{"p":1}\n')
        remine.score_paired = lambda repo, preds: {"paired_scored": 0, "delta": 0.0}  # nothing scorable
        self.assertEqual(self._advance(), 2)
        self.assertNotEqual(self._stage(), remine.COMPLETE)

    def test_completes_only_after_real_paired_judge(self):
        self._drive_to_judge()
        open(os.path.join(self.repo, "docs", "predictions.jsonl"), "w").write('{"p":1}\n')
        self.assertEqual(self._advance(), 0)              # score_paired stubbed to positive delta
        self.assertEqual(self._stage(), remine.COMPLETE)

    def test_complete_surfaces_a_zero_delta_honestly(self):
        # COMPLETE must not read as "the cartridge helped" when the delta is 0 (the issue-3 trap)
        import io, contextlib
        self._drive_to_judge()
        open(os.path.join(self.repo, "docs", "predictions.jsonl"), "w").write('{"p":1}\n')
        remine.score_paired = lambda repo, preds: {"paired_scored": 4, "delta": 0.0}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(self._advance(), 0)
        self.assertEqual(self._stage(), remine.COMPLETE)
        self.assertIn("no measurable effect", buf.getvalue())

    def test_complete_reports_a_positive_delta(self):
        import io, contextlib
        self._drive_to_judge()
        open(os.path.join(self.repo, "docs", "predictions.jsonl"), "w").write('{"p":1}\n')
        remine.score_paired = lambda repo, preds: {"paired_scored": 6, "delta": 0.25}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(self._advance(), 0)
        self.assertIn("the cartridge helped", buf.getvalue())

    # --- abort ---------------------------------------------------------------------------

    def test_abort_clears_state(self):
        self._advance()
        self.assertEqual(remine.main(["--repo", self.repo, "--abort"]), 0)
        self.assertEqual(self._stage(), remine.BEGIN)  # no state file -> default begin


class IdxBackfillUnit(unittest.TestCase):
    def test_keeps_existing_idx_and_fills_per_session(self):
        recs = [_rec(idx=5, session="s"), _rec(session="s"), _rec(session="t")]
        filled = remine.backfill_idx(recs)
        self.assertEqual(filled, 2)
        self.assertEqual(recs[0]["idx"], 5)            # kept
        self.assertEqual(recs[1]["idx"], 6)            # continues past max in session s
        self.assertEqual(recs[2]["idx"], 1)            # fresh session t

    def test_preserves_string_ledger_idx(self):
        # learnings rows carry idx as a string ledger id; it must not be clobbered
        recs = [_rec(source="learnings", idx="LRN-20260525-001"), _rec()]
        remine.backfill_idx(recs)
        self.assertEqual(recs[0]["idx"], "LRN-20260525-001")
        self.assertEqual(recs[1]["idx"], 1)


class FingerprintUnit(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="rumor-fp-")
        self.p = os.path.join(self.dir, "f.txt")

    def _fp(self, text):
        open(self.p, "w").write(text)
        return remine.fingerprint(self.p)

    def test_reorder_changes_fingerprint(self):
        # order is content for prose; a re-distill that reorders must register as a change
        self.assertNotEqual(self._fp("alpha\nbeta\n"), self._fp("beta\nalpha\n"))

    def test_trailing_whitespace_is_ignored(self):
        # a stray-space no-op must NOT read as real work
        self.assertEqual(self._fp("line one\nline two\n"), self._fp("line one  \nline two\t\n"))


if __name__ == "__main__":
    unittest.main()
