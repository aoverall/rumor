#!/usr/bin/env python3
"""Rumor U7 — offline eval-judge calibration harness.

Validates the central claim (SC3): does the taste cartridge actually help an agent
tell AMAZING from ACCEPTABLE on held-out examples, vs. a no-cartridge baseline?

This harness owns the deterministic parts — the held-out split and the scoring — so the
result is reproducible. The judging itself is an LLM step (run by an agent or a model
CLI) that happens between `split` and `score`, keeping the loop's runtime gate (which
self-grades against the injected cartridge, no extra call — see plan KTD3) separate from
this offline check.

Workflow:
  1. split   — deterministically partition eval-set.jsonl into distill/ and holdout/ sets.
               The cartridge is distilled from the distill set ONLY; holdout is unseen.
  2. (judge) — out of band: an agent labels each holdout example's verdict twice —
               once WITH references/cartridge.md in context, once WITHOUT — writing
               predictions JSONL: {idx, gold, pred_with, pred_without}.
  3. score   — compute accuracy(with) vs accuracy(without) on the amazing-vs-acceptable
               distinction and report the delta. The delta is measured over the PAIRED
               set only — examples where BOTH arms emitted an on-label call — so a judge
               that declines or goes off-label in one arm can't shift the denominators and
               manufacture a delta. Dropped examples are reported, not hidden. Positive
               delta over a non-trivial paired set => the rumor works.

Usage:
  python3 eval_judge.py split  --eval docs/eval-set.jsonl --holdout-frac 0.25 \
                               --out-distill docs/eval-distill.jsonl --out-holdout docs/eval-holdout.jsonl
  python3 eval_judge.py score  --predictions docs/predictions.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# The distinction the gate must make (amazing vs acceptable), from the canonical vocabulary.
from verdicts import METRIC_VERDICTS as METRIC_LABELS


def _stable_bucket(rec: dict) -> float:
    """Deterministic [0,1) hash of a record's identity — stable across runs, no RNG."""
    key = f"{rec.get('source')}|{rec.get('session')}|{rec.get('idx')}"
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def cmd_split(args) -> int:
    records = []
    with open(args.eval, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    # STRATIFY by verdict so each class lands in the holdout when its count allows. A plain
    # hash bucket can leave the holdout with zero `amazing` on a small eval-set, which makes
    # the amazing-vs-acceptable delta meaningless (0.0) while remine still reports COMPLETE.
    by_verdict: dict = {}
    for r in records:
        by_verdict.setdefault(r.get("verdict"), []).append(r)

    distill, holdout = [], []
    for verdict, recs in by_verdict.items():
        ordered = sorted(recs, key=_stable_bucket)  # deterministic, no RNG
        n = len(ordered)
        k = round(args.holdout_frac * n)
        if n >= 2:
            k = max(1, min(k, n - 1))  # at least one each side so a class is never split away
        else:
            k = 0  # a singleton class can't be split; keep it on the distill side
        holdout.extend(ordered[:k])
        distill.extend(ordered[k:])

    with open(args.out_distill, "w", encoding="utf-8") as fh:
        for r in distill:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(args.out_holdout, "w", encoding="utf-8") as fh:
        for r in holdout:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    metric_holdout = sum(1 for r in holdout if r.get("verdict") in METRIC_LABELS)
    print(f"distill={len(distill)} holdout={len(holdout)} "
          f"(holdout amazing/acceptable scorable={metric_holdout})", file=sys.stderr)
    # Explicit warning when a metric class exists in the eval-set but never reached the holdout
    # (a singleton class, or none at all): the delta can't measure what it can't see.
    for label in sorted(METRIC_LABELS):
        total = sum(1 for r in records if r.get("verdict") == label)
        in_holdout = sum(1 for r in holdout if r.get("verdict") == label)
        if total > 0 and in_holdout == 0:
            print(f"warn: holdout has 0 '{label}' examples (eval-set has {total}); the "
                  f"amazing-vs-acceptable delta cannot measure this class", file=sys.stderr)
    if metric_holdout < 4:
        print("warn: few scorable holdout examples — fold in more signal before trusting the delta",
              file=sys.stderr)
    return 0


def _on_label(p, field) -> bool:
    """A prediction counts only if the judge emitted one of the metric labels.
    A null (judge declined) or an off-label token (e.g. 'redirected', garbage)
    is not a usable amazing-vs-acceptable call and must not silently vanish."""
    return p.get(field) in METRIC_LABELS


def cmd_score(args) -> int:
    preds = []
    with open(args.predictions, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                preds.append(json.loads(line))

    # Only gold amazing/acceptable examples are scorable at all.
    scorable = [p for p in preds if p.get("gold") in METRIC_LABELS]

    # The delta is only honest over the PAIRED set: examples where BOTH arms
    # produced an on-label call. Scoring each arm over its own surviving subset
    # lets a judge that declines (or emits an off-label token) more often in one
    # arm shift the denominators and report a delta that compares different
    # example sets. That is a fake PASS — the exact thing the harness exists to
    # catch. So pair first, then measure on identical ground.
    paired = [p for p in scorable
              if _on_label(p, "pred_with") and _on_label(p, "pred_without")]

    cw = sum(1 for p in paired if p["pred_with"] == p["gold"])
    cb = sum(1 for p in paired if p["pred_without"] == p["gold"])
    n = len(paired)
    acc_with = cw / n if n else 0.0
    acc_without = cb / n if n else 0.0
    delta = acc_with - acc_without

    # Surface the dropped examples so a thin or lopsided comparison can't hide.
    only_with = sum(1 for p in scorable
                    if _on_label(p, "pred_with") and not _on_label(p, "pred_without"))
    only_without = sum(1 for p in scorable
                       if _on_label(p, "pred_without") and not _on_label(p, "pred_with"))
    unusable = sum(1 for p in scorable
                   if not _on_label(p, "pred_with") and not _on_label(p, "pred_without"))

    result = {
        "scorable_gold": len(scorable),
        "paired_scored": n,
        "accuracy_with_cartridge": round(acc_with, 3),
        "accuracy_without_cartridge": round(acc_without, 3),
        "delta": round(delta, 3),
        "verdict": "cartridge helps" if delta > 0 else ("no effect" if delta == 0 else "cartridge hurts"),
        "dropped_on_label_only_with": only_with,
        "dropped_on_label_only_without": only_without,
        "dropped_off_label_both": unusable,
    }
    print(json.dumps(result, indent=2))

    if n == 0:
        print("warn: no paired on-label predictions — the delta is meaningless; "
              "check that the judge emitted amazing/acceptable in both arms", file=sys.stderr)
    elif (only_with + only_without) > n:
        print("warn: more examples dropped for missing/off-label predictions than were "
              "paired and scored — treat the delta as provisional", file=sys.stderr)
    return 0


def main(argv) -> int:
    ap = argparse.ArgumentParser(description="Rumor eval-judge calibration harness")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("split")
    sp.add_argument("--eval", required=True)
    sp.add_argument("--holdout-frac", type=float, default=0.25)
    sp.add_argument("--out-distill", required=True)
    sp.add_argument("--out-holdout", required=True)
    sp.set_defaults(func=cmd_split)

    sc = sub.add_parser("score")
    sc.add_argument("--predictions", required=True)
    sc.set_defaults(func=cmd_score)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
