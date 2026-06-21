#!/usr/bin/env python3
"""Tests for the source-coverage walker (coverage_check.py).

The walker is the deterministic half of the loop: it must make a silently-skipped taste
avenue impossible. These tests prove the guards actually BITE, by reconstructing the two
real defects this change fixes inside a temp repo and asserting the walker catches them:

  - Defect 1 (OpenClaw extracted then dropped from the mine): coverage_check run folds in
    EVERY combine:true avenue that produced records. A produced avenue cannot vanish.
  - Defect 2 (an extractor orphaned from the manifest, reaching the eval-set only via a
    one-off sync-back): coverage_check verify fails when an extract_*.py on disk is named
    by no manifest row.

Plus the share-safety contract: a source absent on this machine is an explicit SKIP, never
a failure, so a friend with only one history source still runs clean.

Run with:  python3 -m unittest discover -s tests -v
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
COVERAGE = os.path.join(REPO, "scripts", "coverage_check.py")

# A fake extractor: prints one JSON line tagged with its avenue id, so we can tell which
# avenue's records landed in candidates-all. Real reactions vs noise is not this layer's job.
FAKE_EXTRACTOR = (
    "import json, sys\n"
    "print(json.dumps({'source': %(id)r, 'human_text': 'x'}))\n"
)


class CoverageCheckHarness(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="rumor-cov-")
        os.makedirs(os.path.join(self.repo, "scripts"))
        os.makedirs(os.path.join(self.repo, "docs"))
        shutil.copy(COVERAGE, os.path.join(self.repo, "scripts", "coverage_check.py"))

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def _extractor(self, avenue_id):
        rel = f"scripts/extract_{avenue_id}.py"
        with open(os.path.join(self.repo, rel), "w") as fh:
            fh.write(FAKE_EXTRACTOR % {"id": avenue_id})
        return rel

    def _write_manifest(self, rows, comments=True):
        path = os.path.join(self.repo, "scripts", "sources.jsonl")
        with open(path, "w") as fh:
            if comments:
                fh.write("# test manifest\n\n")
            for r in rows:
                fh.write(json.dumps(r) + "\n")

    def _read_all(self):
        with open(os.path.join(self.repo, "docs", "candidates-all.jsonl")) as fh:
            return fh.read()

    def _run(self, cmd):
        return subprocess.run(
            [sys.executable, os.path.join(self.repo, "scripts", "coverage_check.py"),
             cmd, "--repo", self.repo],
            capture_output=True, text=True,
        )

    def _avenue(self, avenue_id, probe, combine=True):
        self._extractor(avenue_id)
        return {
            "id": avenue_id, "extractor": f"scripts/extract_{avenue_id}.py",
            "argv": ["{probe}"], "probe": probe,
            "sink": f"docs/candidates-{avenue_id}.jsonl", "combine": combine,
            "private": False,
        }

    def _capture_row(self, sinks=("docs/eval-set.jsonl", ".learnings/LEARNINGS.md")):
        return {"id": "capture", "extractor": None, "argv": [], "probe": "on-demand",
                "sink": "docs/eval-set.jsonl", "sinks": list(sinks),
                "combine": False, "private": False}

    # --- Defect 1: a produced combine avenue can never be dropped from the mine ----------

    def test_run_folds_in_every_produced_combine_avenue(self):
        # Two present sources (like claude + openclaw). The old onboard.sh extracted openclaw
        # then dropped it; here the combine is derived from the manifest, so both must land.
        probe = os.path.join(self.repo, "probe_a")
        os.makedirs(probe)
        rows = [self._avenue("alpha", probe), self._avenue("beta", probe),
                self._capture_row()]
        self._write_manifest(rows)
        res = self._run("run")
        self.assertEqual(res.returncode, 0, res.stderr)
        allrecs = self._read_all()
        self.assertIn('"source": "alpha"', allrecs)
        self.assertIn('"source": "beta"', allrecs,
                      "a produced combine:true avenue was dropped from candidates-all "
                      "(this is the defect-1 regression)")

    # --- Share-safety: an absent source is an explicit SKIP, not a failure ---------------

    def test_absent_probe_skips_loudly_without_failing(self):
        present = os.path.join(self.repo, "present")
        os.makedirs(present)
        rows = [self._avenue("here", present),
                self._avenue("missing", os.path.join(self.repo, "does-not-exist")),
                self._capture_row()]
        self._write_manifest(rows)
        res = self._run("run")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("SKIP", res.stdout)
        self.assertIn("missing", res.stdout)
        allrecs = self._read_all()
        self.assertIn('"source": "here"', allrecs)
        self.assertNotIn('"source": "missing"', allrecs)

    # --- Defect 2: an orphaned extractor fails verify ------------------------------------

    def test_verify_catches_orphaned_extractor(self):
        probe = os.path.join(self.repo, "p")
        os.makedirs(probe)
        # extractor on disk for 'ghost', but no row names it -> the defect-2 shape.
        self._extractor("ghost")
        rows = [self._avenue("kept", probe), self._capture_row()]
        self._write_manifest(rows)
        res = self._run("verify")
        self.assertEqual(res.returncode, 1)
        self.assertIn("orphaned extractor", res.stderr)
        self.assertIn("ghost", res.stderr)

    def test_verify_passes_when_every_extractor_is_named(self):
        probe = os.path.join(self.repo, "p")
        os.makedirs(probe)
        rows = [self._avenue("kept", probe), self._capture_row()]
        self._write_manifest(rows)
        res = self._run("verify")
        self.assertEqual(res.returncode, 0, res.stderr)

    # --- Capture dual-sink invariant -----------------------------------------------------

    def test_verify_requires_capture_both_sinks(self):
        probe = os.path.join(self.repo, "p")
        os.makedirs(probe)
        rows = [self._avenue("kept", probe),
                self._capture_row(sinks=("docs/eval-set.jsonl",))]  # only one sink
        self._write_manifest(rows)
        res = self._run("verify")
        self.assertEqual(res.returncode, 1)
        self.assertIn("capture", res.stderr.lower())

    def test_verify_fails_when_manifest_names_missing_extractor(self):
        rows = [{"id": "phantom", "extractor": "scripts/extract_phantom.py",
                 "argv": [], "probe": "~/nope", "sink": "docs/c.jsonl",
                 "combine": True, "private": False},
                self._capture_row()]
        self._write_manifest(rows)
        res = self._run("verify")
        self.assertEqual(res.returncode, 1)
        self.assertIn("missing extractor", res.stderr)


# --- the REAL repo manifest, not a fixture: it must verify clean ------------------------

class RealManifest(unittest.TestCase):
    def test_repo_manifest_verifies(self):
        res = subprocess.run(
            [sys.executable, COVERAGE, "verify", "--repo", REPO],
            capture_output=True, text=True,
        )
        self.assertEqual(res.returncode, 0,
                         f"the shipped scripts/sources.jsonl does not verify:\n{res.stderr}")


if __name__ == "__main__":
    unittest.main()
