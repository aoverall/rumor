#!/usr/bin/env python3
"""Rumor U1 — Claude Code reaction-candidate extractor.

Reads Claude Code session transcripts (`~/.claude/projects/**/*.jsonl`) and emits
one normalized JSON record per *human reaction candidate*: a user turn that plausibly
reacts to something the agent produced, paired with the preceding agent turn.

This adapter does NOT judge whether a turn is a genuine reaction — it only finds
candidates and normalizes them. The miner Workflow (U2) does the judging.

Record shape (one JSON object per line):
    {
      "source": "claude",
      "session": "<file stem>",
      "idx": <int turn index within session>,
      "human_text": "<the user's message>",
      "prev_agent_text": "<concatenated text of the preceding assistant turn, tail-capped>",
      "prev_had_tool": <bool: did the preceding assistant turn use tools>,
      "prev_truncated": <bool>
    }

Usage:
    python3 extract_claude.py PATH [PATH ...] [--limit N] [--min-agent-chars N]
    # PATH may be a .jsonl file or a directory (searched recursively).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

AGENT_TAIL_CAP = 2500   # chars of preceding agent text to keep (from the tail)
HUMAN_CAP = 4000        # chars of human text to keep

# User strings that are harness/system artifacts, not real human turns.
SYSTEM_PREFIXES = (
    "<command-name>",
    "<command-message>",
    "<local-command",
    "Caveat: The messages below",
    "[Request interrupted",
    "<bash-input>",
    "<bash-stdout>",
    "<system-reminder>",
)


def _iter_jsonl(path: str):
    """Yield parsed JSON objects from a jsonl file, skipping malformed lines."""
    bad = 0
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
    if bad:
        print(f"  warn: {bad} malformed lines skipped in {os.path.basename(path)}",
              file=sys.stderr)


def _assistant_text(entry: dict) -> tuple[str, bool]:
    """Return (concatenated text, had_tool_use) for an assistant entry."""
    msg = entry.get("message") or {}
    content = msg.get("content")
    if isinstance(content, str):
        return content, False
    if not isinstance(content, list):
        return "", False
    parts: list[str] = []
    had_tool = False
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            parts.append(block.get("text", ""))
        elif btype == "tool_use":
            had_tool = True
    return "\n".join(p for p in parts if p), had_tool


def _is_human_turn(entry: dict) -> bool:
    """A genuine human turn: type=user, role=user, content is a plain string."""
    if entry.get("type") != "user":
        return False
    msg = entry.get("message") or {}
    if msg.get("role") != "user":
        return False
    return isinstance(msg.get("content"), str)


def _looks_systemic(text: str) -> bool:
    stripped = text.lstrip()
    return any(stripped.startswith(p) for p in SYSTEM_PREFIXES)


def extract_session(path: str, min_agent_chars: int = 1):
    """Yield reaction-candidate records for one session file.

    A reaction candidate needs something to react to, so the default requires at
    least one char of preceding agent text. This keeps the function-level contract
    aligned with both the CLI default (--min-agent-chars=1) and the Codex adapter,
    which drops human turns with no preceding agent turn. A candidate with empty
    prev_agent_text is noise, not signal.
    """
    session = os.path.splitext(os.path.basename(path))[0]
    last_agent_text = ""
    last_agent_had_tool = False
    idx = 0
    for entry in _iter_jsonl(path):
        etype = entry.get("type")
        if etype == "assistant":
            text, had_tool = _assistant_text(entry)
            if text.strip():
                last_agent_text = text
                last_agent_had_tool = had_tool
            elif had_tool:
                last_agent_had_tool = True
            continue
        if not _is_human_turn(entry):
            continue
        human = entry.get("message", {}).get("content", "")
        if not human.strip() or _looks_systemic(human):
            continue
        if len(last_agent_text) < min_agent_chars:
            continue
        prev = last_agent_text
        truncated = len(prev) > AGENT_TAIL_CAP
        if truncated:
            prev = prev[-AGENT_TAIL_CAP:]
        idx += 1
        yield {
            "source": "claude",
            "session": session,
            "idx": idx,
            "human_text": human[:HUMAN_CAP],
            "prev_agent_text": prev,
            "prev_had_tool": last_agent_had_tool,
            "prev_truncated": truncated,
        }


def _walk(paths: list[str]):
    for p in paths:
        if os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                for f in sorted(files):
                    if f.endswith(".jsonl"):
                        yield os.path.join(root, f)
        elif p.endswith(".jsonl"):
            yield p


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Claude Code reaction-candidate extractor")
    ap.add_argument("paths", nargs="+", help=".jsonl file(s) or director(ies)")
    ap.add_argument("--limit", type=int, default=0, help="max records to emit (0 = all)")
    ap.add_argument("--min-agent-chars", type=int, default=1,
                    help="skip human turns with no/short preceding agent text")
    args = ap.parse_args(argv)

    emitted = 0
    for session_path in _walk(args.paths):
        for rec in extract_session(session_path, args.min_agent_chars):
            sys.stdout.write(json.dumps(rec, ensure_ascii=False) + "\n")
            emitted += 1
            if args.limit and emitted >= args.limit:
                print(f"reached --limit {args.limit}", file=sys.stderr)
                return 0
    print(f"emitted {emitted} reaction-candidate records", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
