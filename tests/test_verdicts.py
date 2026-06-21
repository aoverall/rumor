#!/usr/bin/env python3
"""The verdict/mode vocabulary is canonical and consistent (issue 2).

Before scripts/verdicts.py the four locations had drifted (classifier 4, capture 5, remine 6,
extract_learnings 6), so remine accepted a `neutral` verdict capture rejected. These tests
keep them from drifting again.

Run with:  python3 -m unittest discover -s tests -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import verdicts            # noqa: E402
import capture            # noqa: E402
import remine             # noqa: E402
import extract_learnings  # noqa: E402
import eval_judge         # noqa: E402


class VerdictCanon(unittest.TestCase):
    def test_all_validators_share_the_canonical_set(self):
        self.assertEqual(capture.VALID_VERDICTS, verdicts.VALID_VERDICTS)
        self.assertEqual(remine.VALID_VERDICTS, verdicts.VALID_VERDICTS)
        self.assertEqual(extract_learnings.VALID_VERDICTS, verdicts.VALID_VERDICTS)
        self.assertEqual(capture.VALID_MODES, verdicts.VALID_MODES)
        self.assertEqual(remine.VALID_MODES, verdicts.VALID_MODES)
        self.assertEqual(extract_learnings.VALID_MODES, verdicts.VALID_MODES)

    def test_the_five_reaction_verdicts(self):
        self.assertEqual(
            verdicts.VALID_VERDICTS,
            frozenset({"amazing", "acceptable", "rejected", "redirected", "confused"}))

    def test_neutral_is_a_mode_not_a_verdict(self):
        self.assertNotIn("neutral", verdicts.VALID_VERDICTS)
        self.assertIn("neutral", verdicts.VALID_MODES)

    def test_eval_judge_metric_labels_track_the_canonical_set(self):
        self.assertEqual(set(eval_judge.METRIC_LABELS), set(verdicts.METRIC_VERDICTS))
        self.assertTrue(verdicts.METRIC_VERDICTS <= verdicts.VALID_VERDICTS)


if __name__ == "__main__":
    unittest.main()
