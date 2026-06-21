#!/usr/bin/env python3
"""Rumor: the canonical verdict and mode vocabularies, defined once.

capture.py, remine.py, and extract_learnings.py import these so every validator agrees, and
the discovery classifier (workflows/rumor-discovery.cc.js) and SKILL.md document the same set.
Before this module they had drifted to four different sets (the classifier emitted 4, capture
allowed 5, remine and extract_learnings allowed 6), so remine accepted a `neutral` verdict
that capture rejected and the classifier could never produce the `confused` the validators
expected.

A VERDICT is the judgment of a real reaction. `neutral` is a MODE, not a verdict: in the data
it sits on non-reaction rows, which the classifier does not verdict and the merge step drops,
so a well-formed eval-set carries only the five reaction verdicts.
"""

VALID_VERDICTS = frozenset({"amazing", "acceptable", "rejected", "redirected", "confused"})
VALID_MODES = frozenset({"push", "interrogate", "neutral"})

# The two verdicts the SC3 metric scores (amazing vs acceptable). Kept here so eval_judge and
# the split stay in step with the canonical set.
METRIC_VERDICTS = frozenset({"amazing", "acceptable"})
