#!/usr/bin/env python3
"""Rumor: learnings/corrections ingest.

A correction log (a human already decided "this was wrong" or "this is the good way") is
pre-labeled taste gold. This adapter folds such a log into the same eval-record shape the
conversation miner produces, so the cartridge distills from corrections and reactions
together. By default it reads the repo-local `.learnings/LEARNINGS.md`, where `rumor capture`
writes; pass other paths to fold in correction logs you keep elsewhere.

Mapping:
  correction / knowledge_gap  -> verdict "rejected"  (the thing being corrected away from)
  best_practice               -> verdict "amazing"   (the thing being moved toward)
  default                     -> verdict "redirected"
An entry may set an explicit `**Verdict**` to override the lossy category map.

Usage:
    python3 extract_learnings.py [LEARNINGS.md ...] > learnings-eval.jsonl
    # defaults to .learnings/LEARNINGS.md if no paths given.
"""
from __future__ import annotations

import json
import os
import re
import sys

# Repo-local by default (where `rumor capture` writes). Point at an external correction log
# with an explicit path arg if you keep one elsewhere.
DEFAULT_PATHS = [".learnings/LEARNINGS.md"]

VERDICT_BY_CATEGORY = {
    "correction": "rejected",
    "knowledge_gap": "rejected",
    "best_practice": "amazing",
}

# The category map is lossy: it can only emit rejected/amazing/redirected, so a logged
# reaction whose true verdict is acceptable/redirected/confused would be relabeled on
# re-mine. An entry may therefore carry an explicit `**Verdict**:` (and `**Mode**:`) field,
# which is honored verbatim so a faithfully-logged reaction round-trips with its real label.
# `rumor capture` writes these fields; hand-written entries can too.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verdicts import VALID_VERDICTS, VALID_MODES  # the canonical sets

# A learnings entry header looks like:  ## [LRN-20260525-001] correction
ENTRY_RE = re.compile(r"^##\s+\[(?P<id>[^\]]+)\]\s*(?P<category>\S+)?\s*$")
FIELD_RE = re.compile(r"^\*\*(?P<key>[A-Za-z ]+)\*\*\s*:\s*(?P<val>.*)$")


def _parse_markdown_log(path: str):
    """Yield eval records from a LEARNINGS.md-style log."""
    if not os.path.exists(path):
        return
    origin = os.path.basename(path)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()

    entries = []
    cur = None
    section = None
    for raw in lines:
        line = raw.rstrip("\n")
        m = ENTRY_RE.match(line)
        if m:
            if cur:
                entries.append(cur)
            cur = {"id": m.group("id"), "category": (m.group("category") or "").strip(),
                   "fields": {}, "summary": [], "_section": None}
            section = None
            continue
        if cur is None:
            continue
        fm = FIELD_RE.match(line)
        if fm:
            cur["fields"][fm.group("key").strip().lower()] = fm.group("val").strip()
            continue
        if line.startswith("### "):
            section = line[4:].strip().lower()
            continue
        if section == "summary" and line.strip():
            cur["summary"].append(line.strip())
    if cur:
        entries.append(cur)

    for e in entries:
        category = e["category"] or e["fields"].get("category", "")
        # Honor an explicit, valid verdict over the lossy category map (faithful round-trip).
        explicit_verdict = e["fields"].get("verdict", "").strip().lower()
        verdict = (explicit_verdict if explicit_verdict in VALID_VERDICTS
                   else VERDICT_BY_CATEGORY.get(category, "redirected"))
        explicit_mode = e["fields"].get("mode", "").strip().lower()
        mode = (explicit_mode if explicit_mode in VALID_MODES
                else ("interrogate" if verdict == "rejected" else "neutral"))
        summary = " ".join(e["summary"]).strip() or e["fields"].get("summary", "")
        if not summary:
            continue
        yield {
            "source": "learnings",
            "session": origin,
            "idx": e["id"],
            "is_reaction": True,
            "human_text": summary,
            "artifact_summary": e["fields"].get("area", "") or category,
            "verdict": verdict,
            "why": summary,
            "question_behind": f"Logged as a {category or 'correction'}; it encodes a standing preference.",
            "mode": mode,
            "priority": e["fields"].get("priority", ""),
        }


def main(argv: list[str]) -> int:
    paths = argv or DEFAULT_PATHS
    n = 0
    for p in paths:
        for rec in _parse_markdown_log(p):
            sys.stdout.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
    print(f"emitted {n} learnings-derived eval records", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
