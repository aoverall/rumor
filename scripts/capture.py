#!/usr/bin/env python3
"""Rumor capture: the real `rumor capture` command (fronted by scripts/rumor).

The agent has already DONE the judgment in-flow (SPINE/GATE produced a verdict and the
prose). This command never decides anything. It takes the agent-supplied fields, enforces
shape so malformed signal can never enter the ground truth, and performs the dual-sink write
ATOMICALLY: both `docs/eval-set.jsonl` (Rumor's canonical record) and a `.learnings` entry
(the heartbeat/steering copy) land, or neither does.

It has no content-generating path. Every taste field (verdict, why, human_text, mode,
artifact_summary, question_behind) must be supplied; absent or malformed input fails closed.
That is the structural reason it cannot be used to fabricate.

Paths are REQUIRED args with no real-path defaults, so a test (or a stray run) cannot touch
the real sinks by accident; the `rumor` dispatcher supplies the real paths.

Usage:
    python3 scripts/capture.py --eval docs/eval-set.jsonl --learnings .learnings/LEARNINGS.md \\
        --verdict redirected --mode neutral \\
        --human "<what you actually said>" \\
        --artifact "<one-line summary of the work they reacted to>" \\
        --why "<the taste signal>" \\
        --question "<the question behind it>" \\
        [--session voice-2026-06-15] [--idx N] [--category correction] [--dry-run]
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verdicts import VALID_VERDICTS, VALID_MODES  # the canonical sets, shared by all validators

VALID_CATEGORIES = {"correction", "best_practice", "knowledge_gap"}

# Canonical eval-record key order (matches the existing source=capture row byte-for-byte).
EVAL_KEYS = ["source", "session", "idx", "is_reaction", "human_text",
             "artifact_summary", "verdict", "why", "question_behind", "mode"]

# Sensible .learnings category per verdict. The category is no longer load-bearing for the
# verdict (the entry carries an explicit **Verdict** that extract_learnings honors), so this
# only sets the human-facing header label.
CATEGORY_FOR_VERDICT = {
    "amazing": "best_practice", "acceptable": "best_practice",
    "rejected": "correction", "redirected": "correction", "confused": "correction",
}


class CaptureError(Exception):
    """Validation/atomicity failure. Carries the exit code to return."""
    def __init__(self, message: str, code: int):
        super().__init__(message)
        self.code = code


def _today() -> str:
    return datetime.date.today().strftime("%Y-%m-%d")


def _read_records(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _next_idx(records: list[dict], session: str) -> int:
    """1 + max int idx sharing this session; first capture in a fresh session is 1."""
    same = [r.get("idx") for r in records
            if r.get("session") == session and isinstance(r.get("idx"), int)]
    return (max(same) + 1) if same else 1


def _next_lrn_seq(learnings_path: str, datestamp: str) -> int:
    """Next zero-padded counter among today's LRN-<date>-NNN ids in the file."""
    if not os.path.exists(learnings_path):
        return 1
    import re
    pat = re.compile(rf"\[LRN-{re.escape(datestamp)}-(\d+)\]")
    seqs = [int(m.group(1)) for m in pat.finditer(open(learnings_path, encoding="utf-8").read())]
    return (max(seqs) + 1) if seqs else 1


def build_eval_record(args, records: list[dict]) -> dict:
    session = (args.session or "").strip() or f"voice-{_today()}"
    idx = args.idx if args.idx is not None else _next_idx(records, session)
    # Duplicate (source, session, idx) would collide in eval_judge's hash bucket and could
    # shadow a holdout record. Refuse rather than overwrite.
    for r in records:
        if (r.get("source") == "capture" and r.get("session") == session
                and r.get("idx") == idx):
            raise CaptureError(
                f"duplicate capture (session={session}, idx={idx}) already in eval-set", 2)
    return {
        "source": "capture",
        "session": session,
        "idx": idx,
        "is_reaction": True,
        "human_text": args.human,
        "artifact_summary": args.artifact,
        "verdict": args.verdict,
        "why": args.why,
        "question_behind": args.question,
        "mode": args.mode,
    }


def build_learnings_entry(args, rec: dict, lrn_id: str, category: str) -> str:
    """A .learnings block that parses back through extract_learnings.py, with explicit
    Verdict/Mode so the captured label round-trips faithfully (not via the lossy category
    map). The eval-set holds the verbatim human_text; this heartbeat copy carries the why
    as its Summary, the convention the correction log already uses."""
    return (
        f"\n## [LRN-{lrn_id}] {category}\n"
        f"**Area**: {args.artifact}\n"
        f"**Priority**: {(args.priority or 'normal').strip()}\n"
        f"**Verdict**: {rec['verdict']}\n"
        f"**Mode**: {rec['mode']}\n"
        f"**Source**: rumor capture {rec['session']}#{rec['idx']}\n"
        f"### Summary\n"
        f"{args.why}\n"
    )


def validate(args) -> None:
    required = {"human": args.human, "artifact": args.artifact, "why": args.why,
                "question": args.question, "verdict": args.verdict, "mode": args.mode}
    for name, val in required.items():
        if not val or not val.strip():
            raise CaptureError(f"--{name} is required and must be non-empty "
                               f"(capture never invents content)", 2)
    if args.verdict not in VALID_VERDICTS:
        raise CaptureError(f"verdict '{args.verdict}' not in {sorted(VALID_VERDICTS)}", 2)
    if args.mode not in VALID_MODES:
        raise CaptureError(f"mode '{args.mode}' not in {sorted(VALID_MODES)}", 2)
    if args.category and args.category not in VALID_CATEGORIES:
        raise CaptureError(f"category '{args.category}' not in {sorted(VALID_CATEGORIES)}", 2)


def _atomic_dual_write(eval_path: str, eval_line: str,
                       learnings_path: str, learnings_block: str) -> None:
    """Append to both sinks or neither. Both are append-only single-writer logs, so the
    rollback is a truncate back to the pre-write length: if the .learnings append fails
    after the eval-set append landed, the eval-set is truncated back, leaving no half-write."""
    os.makedirs(os.path.dirname(eval_path) or ".", exist_ok=True)
    eval_len = os.path.getsize(eval_path) if os.path.exists(eval_path) else 0
    with open(eval_path, "a", encoding="utf-8") as fh:
        fh.write(eval_line)
        fh.flush()
        os.fsync(fh.fileno())
    try:
        os.makedirs(os.path.dirname(learnings_path) or ".", exist_ok=True)
        with open(learnings_path, "a", encoding="utf-8") as fh:
            fh.write(learnings_block)
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as exc:
        with open(eval_path, "a", encoding="utf-8") as fh:
            fh.truncate(eval_len)
        raise CaptureError(f"learnings write failed ({exc}); rolled the eval-set append "
                           f"back, nothing written", 3)


def run(args) -> int:
    validate(args)
    records = _read_records(args.eval)
    rec = build_eval_record(args, records)
    eval_line = json.dumps({k: rec[k] for k in EVAL_KEYS},
                           ensure_ascii=False, separators=(",", ":")) + "\n"
    category = args.category or CATEGORY_FOR_VERDICT[rec["verdict"]]
    lrn_id = f"{_today().replace('-', '')}-{_next_lrn_seq(args.learnings, _today().replace('-', '')):03d}"
    learnings_block = build_learnings_entry(args, rec, lrn_id, category)

    if args.dry_run:
        sys.stdout.write("--- would append to eval-set ---\n" + eval_line)
        sys.stdout.write("--- would append to .learnings ---\n" + learnings_block)
        return 0

    _atomic_dual_write(args.eval, eval_line, args.learnings, learnings_block)
    print(f"captured: eval idx {rec['idx']} (session {rec['session']}) + LRN-{lrn_id}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="rumor capture", description=__doc__)
    ap.add_argument("--eval", required=True, help="path to eval-set.jsonl (no default, so tests stay isolated)")
    ap.add_argument("--learnings", required=True, help="path to the .learnings log")
    ap.add_argument("--verdict", required=True)
    ap.add_argument("--mode", required=True)
    ap.add_argument("--human", required=True, help="what you actually said or did")
    ap.add_argument("--artifact", required=True, help="one-line summary of the work they reacted to")
    ap.add_argument("--why", required=True, help="the taste signal")
    ap.add_argument("--question", required=True, help="the question behind it")
    ap.add_argument("--session", default=None)
    ap.add_argument("--idx", type=int, default=None)
    ap.add_argument("--category", default=None)
    ap.add_argument("--priority", default=None)
    ap.add_argument("--dry-run", action="store_true")
    return ap


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except CaptureError as exc:
        print(f"capture: {exc}", file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
