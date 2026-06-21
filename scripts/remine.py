#!/usr/bin/env python3
"""Rumor remine: the real `rumor remine` orchestrator (fronted by scripts/rumor).

Remine is part deterministic, part judgment. Three stages are scripts (regenerate
candidates, render the field manual, validate SC3). Three are agentic and a script CANNOT
do them: classify the candidates (the discovery Workflow, agent-harness only), distill the
cartridge (an agent writes taste), and judge the holdout (an agent labels it with and
without the cartridge). A print-only checklist would be cosplay; faking the agentic stages
would be fraud.

So remine is a resumable state machine. It runs the deterministic stages itself and HALTS at
each agentic stage with an explicit handoff. On resume it REFUSES to advance unless the
handed-off stage left a real, changed, shape-valid artifact, computed from file content, not
asserted. There is no path where it prints "remine complete" with a judgment stage skipped.

It also owns one deterministic integrity fix: after classify it backfills a per-session `idx`
on any row missing one, so eval_judge's holdout split cannot silently degenerate.

Usage:
    python3 scripts/rumor remine            begin, or advance to the next stage
    python3 scripts/rumor remine --status   print the cursor and what is pending
    python3 scripts/rumor remine --abort     clear the run state
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verdicts import VALID_VERDICTS, VALID_MODES  # canonical (drops the stray `neutral` verdict)

# Stage cursor. Each AWAIT_* is a hard halt on an agentic stage.
BEGIN, AWAIT_CLASSIFY, AWAIT_DISTILL, AWAIT_JUDGE, COMPLETE = (
    "begin", "await_classify", "await_distill", "await_judge", "complete")


def _p(repo: str, *parts: str) -> str:
    return os.path.join(repo, *parts)


def fingerprint(path: str) -> str:
    """Content fingerprint: sha256 of the non-blank lines IN ORDER (order is content for
    prose, so reordering must register as a change), with trailing whitespace normalized so a
    stray-space edit is not mistaken for real work. Survives mtime touches."""
    if not os.path.exists(path):
        return "absent"
    lines = [l.rstrip() for l in open(path, encoding="utf-8").read().splitlines() if l.strip()]
    h = hashlib.sha256(("\n".join(lines)).encode("utf-8")).hexdigest()
    return f"{len(lines)}:{h[:16]}"


def _state_path(repo: str) -> str:
    return _p(repo, "docs", ".remine-state.json")


def load_state(repo: str) -> dict:
    p = _state_path(repo)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {"stage": BEGIN, "fp": {}}


def save_state(repo: str, state: dict) -> None:
    os.makedirs(_p(repo, "docs"), exist_ok=True)
    with open(_state_path(repo), "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)


# Tests monkeypatch this to avoid running the real scripts; in production it runs them.
def run_stage(repo: str, argv: list[str]) -> int:
    return subprocess.call([sys.executable, _p(repo, *argv[0].split("/")), *argv[1:]])


# Run eval_judge score and return its result dict (or None on failure). Separate from
# run_stage because the judge gate needs the paired count AND the delta, not just an exit
# code: eval_judge score exits 0 even with zero scorable pairs or a zero delta, so
# existence/exit-code alone cannot prove the judge produced anything meaningful. Tests
# monkeypatch this.
def score_paired(repo: str, preds: str) -> dict | None:
    proc = subprocess.run(
        [sys.executable, _p(repo, "scripts", "eval_judge.py"), "score", "--predictions", preds],
        capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def _read_records(path: str) -> list[dict]:
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def validate_eval(records: list[dict]) -> list[str]:
    """Reasons the mined eval-set is not shippable. Empty list means valid."""
    problems = []
    reactions = 0
    for i, r in enumerate(records, 1):
        if not r.get("is_reaction"):
            continue
        reactions += 1
        if r.get("verdict") not in VALID_VERDICTS:
            problems.append(f"row {i}: verdict {r.get('verdict')!r} not valid")
        if r.get("mode") not in VALID_MODES:
            problems.append(f"row {i}: mode {r.get('mode')!r} not valid")
    if reactions == 0:
        problems.append("zero reaction rows: an empty or all-dropped mine is a failure, "
                        "not a valid eval-set")
    return problems


def backfill_idx(records: list[dict]) -> int:
    """Give every row a per-(source, session) integer idx. Existing valid int idx are kept;
    the discovery Workflow drops idx, which silently degrades eval_judge's split, so remine
    guarantees it here deterministically. Returns how many were filled."""
    counters: dict[tuple, int] = {}
    # seed counters from existing valid idx so backfilled values never collide
    for r in records:
        if isinstance(r.get("idx"), int):
            key = (r.get("source"), r.get("session"))
            counters[key] = max(counters.get(key, 0), r["idx"])
    filled = 0
    for r in records:
        idx = r.get("idx")
        # Present if it is an int OR a non-empty string ledger id (e.g. "LRN-20260525-001",
        # which the learnings adapter uses). Only a truly absent idx gets backfilled, so a
        # meaningful provenance id is never clobbered.
        if isinstance(idx, int) or (isinstance(idx, str) and idx.strip()):
            continue
        key = (r.get("source"), r.get("session"))
        counters[key] = counters.get(key, 0) + 1
        r["idx"] = counters[key]
        filled += 1
    return filled


def _write_records(path: str, records: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def _handoff(msg: str) -> None:
    print("\n  HANDOFF (agentic stage, a script cannot do this):")
    print(f"  {msg}")
    print("  Then run `rumor remine` again to continue.\n")


def advance(repo: str) -> int:
    eval_path = _p(repo, "docs", "eval-set.jsonl")
    cart_path = _p(repo, "skill", "references", "cartridge.md")
    cands = _p(repo, "docs", "candidates-all.jsonl")
    state = load_state(repo)
    stage = state["stage"]

    if stage in (BEGIN, COMPLETE):
        # Stage 1 (deterministic): rebuild candidates from the manifest.
        if run_stage(repo, ["scripts/coverage_check.py", "run", "--repo", repo]) != 0:
            print("remine: stage 1 (coverage_check run) failed", file=sys.stderr)
            return 1
        if fingerprint(cands) == "absent":
            print("remine: no candidates produced; nothing to mine", file=sys.stderr)
            return 1
        state = {"stage": AWAIT_CLASSIFY, "fp": {"eval": fingerprint(eval_path)}}
        save_state(repo, state)
        n = sum(1 for line in open(cands, encoding="utf-8") if line.strip())
        _handoff(f"Mine the candidates. Run the discovery Workflow by scriptPath: "
                 f"Workflow(scriptPath='workflows/rumor-discovery.cc.js', args={{count: {n}}}). "
                 f"Use scriptPath, not name: the Workflow tool only resolves names from "
                 f".claude/workflows/, and the kit ships this one in workflows/. It fans out "
                 f"classifier agents over docs/candidates-all.jsonl and RETURNS the labeled "
                 f"records; write them, one JSON object per line, to docs/eval-set.jsonl.")
        return 0

    if stage == AWAIT_CLASSIFY:
        if fingerprint(eval_path) == state["fp"].get("eval"):
            print("remine: REFUSED. docs/eval-set.jsonl is unchanged since the classify "
                  "handoff. Run the discovery Workflow first.", file=sys.stderr)
            return 2
        records = _read_records(eval_path)
        problems = validate_eval(records)
        if problems:
            print("remine: REFUSED. the mined eval-set is malformed:", file=sys.stderr)
            for p in problems[:10]:
                print(f"  - {p}", file=sys.stderr)
            return 2
        filled = backfill_idx(records)
        if filled:
            _write_records(eval_path, records)
        state = {"stage": AWAIT_DISTILL,
                 "fp": {"cart": fingerprint(cart_path), "eval": fingerprint(eval_path)}}
        save_state(repo, state)
        print(f"remine: classify accepted ({len(records)} records"
              f"{f', backfilled idx on {filled}' if filled else ''}).")
        _handoff("Distill skill/references/cartridge.md from docs/eval-set.jsonl "
                 "(see ONBOARDING.md for the distiller prompt).")
        return 0

    if stage == AWAIT_DISTILL:
        if fingerprint(cart_path) == state["fp"].get("cart"):
            print("remine: REFUSED. skill/references/cartridge.md is unchanged since the "
                  "distill handoff. Re-distill the cartridge first.", file=sys.stderr)
            return 2
        # Stages 4 + 5a + 5b (deterministic): render, verify coverage, split for SC3.
        for label, argv in [
            ("render", ["scripts/render_field_manual.py"]),
            ("coverage verify", ["scripts/coverage_check.py", "verify", "--repo", repo]),
            ("eval_judge split", ["scripts/eval_judge.py", "split",
                                   "--eval", eval_path, "--holdout-frac", "0.25",
                                   "--out-distill", _p(repo, "docs", "eval-distill.jsonl"),
                                   "--out-holdout", _p(repo, "docs", "eval-holdout.jsonl")])]:
            if run_stage(repo, argv) != 0:
                print(f"remine: stage '{label}' failed", file=sys.stderr)
                return 1
        # Invalidate any stale predictions.jsonl so the judge gate cannot pass on a leftover
        # file from a prior cycle: after this, predictions existing means the agent judged the
        # freshly split holdout.
        preds = _p(repo, "docs", "predictions.jsonl")
        if os.path.exists(preds):
            os.remove(preds)
        save_state(repo, {"stage": AWAIT_JUDGE, "fp": {}})
        _handoff("Judge docs/eval-holdout.jsonl twice (with and without "
                 "skill/references/cartridge.md in context) and write docs/predictions.jsonl "
                 "(see ONBOARDING.md step 3 for the recipe). This is the SC3 gate, do not skip it.")
        return 0

    if stage == AWAIT_JUDGE:
        preds = _p(repo, "docs", "predictions.jsonl")
        if not os.path.exists(preds):
            print("remine: REFUSED. docs/predictions.jsonl does not exist (it was cleared at "
                  "the judge handoff). Judge the holdout first.", file=sys.stderr)
            return 2
        result = score_paired(repo, preds)
        if result is None:
            print("remine: REFUSED. eval_judge score failed on predictions.jsonl.", file=sys.stderr)
            return 2
        paired = result.get("paired_scored") or 0
        if paired <= 0:
            print("remine: REFUSED. eval_judge scored 0 paired on-label predictions; the "
                  "judge produced nothing usable for the amazing-vs-acceptable delta. Re-judge "
                  "the holdout emitting amazing/acceptable in both arms.", file=sys.stderr)
            return 2
        save_state(repo, {"stage": COMPLETE, "fp": {}})
        # COMPLETE means every stage ran, NOT that the cartridge helped. Surface the SC3 delta
        # so the two are not confused (a zero delta on a thin holdout still reaches here).
        delta = result.get("delta", 0.0)
        if delta > 0:
            sense = f"the cartridge helped (delta +{delta:.3f})"
        elif delta == 0:
            sense = (f"the cartridge showed no measurable effect (delta {delta:.3f}); check the "
                     f"split warnings, the holdout may lack amazing/acceptable spread")
        else:
            sense = f"the cartridge HURT on this holdout (delta {delta:.3f}); investigate before trusting it"
        print(f"remine: COMPLETE. every stage ran ({paired} paired predictions scored). SC3: {sense}.")
        return 0

    print(f"remine: unknown stage {stage!r}", file=sys.stderr)
    return 1


def status(repo: str) -> int:
    state = load_state(repo)
    pending = {
        BEGIN: "not started. `rumor remine` runs coverage_check and hands off classify.",
        AWAIT_CLASSIFY: "waiting on the discovery Workflow to rewrite docs/eval-set.jsonl.",
        AWAIT_DISTILL: "waiting on the cartridge re-distill.",
        AWAIT_JUDGE: "waiting on the holdout judge (docs/predictions.jsonl).",
        COMPLETE: "complete.",
    }.get(state["stage"], "unknown")
    print(f"remine cursor: {state['stage']}  ({pending})")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="rumor remine", description=__doc__)
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--abort", action="store_true")
    args = ap.parse_args(argv)
    repo = os.path.abspath(args.repo)
    if args.abort:
        p = _state_path(repo)
        if os.path.exists(p):
            os.remove(p)
        print("remine: run state cleared.")
        return 0
    if args.status:
        return status(repo)
    return advance(repo)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
