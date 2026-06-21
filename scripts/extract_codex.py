#!/usr/bin/env python3
"""Rumor U1 — Codex reaction-candidate extractor (fan-out source).

Codex stores sessions at `~/.codex/sessions/**/rollout-*.jsonl`. Schema differs from
Claude Code: each line is `{type, payload, timestamp}`. Human turns are
`type=response_item` with `payload.type=message`, `payload.role=user`, and
`payload.content=[{type:input_text, text:...}]`.

Caveat (why Codex is a fan-out source, not the proving source): the `user` role also
carries injected environment context — AGENTS.md dumps, permission instructions, tool
output framing — so the systemic-prefix filter has to work harder here than for Claude.
Emits the SAME record shape as extract_claude.py so the miner consumes both unchanged.

Usage:
    python3 extract_codex.py PATH [PATH ...] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

AGENT_TAIL_CAP = 2500
HUMAN_CAP = 4000

# Codex user-role turns that are environment injection, not you typing.
SYSTEM_MARKERS = (
    "<permissions instructions>",
    "# AGENTS.md instructions",
    "<INSTRUCTIONS>",
    "<environment_context>",
    "<user_instructions>",
    "# Escalation Requests",
    "<command-name>",
    "<turn_aborted>",
    "Warning: apply_patch",
    "<turn-context>",
)


def _iter_jsonl(path: str):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _message_text(payload: dict) -> str:
    content = payload.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict) and "text" in block:
            parts.append(block.get("text", ""))
    return "\n".join(p for p in parts if p)


def _looks_systemic(text: str) -> bool:
    head = text.lstrip()[:400]
    return any(marker in head for marker in SYSTEM_MARKERS)


def extract_session(path: str):
    session = os.path.splitext(os.path.basename(path))[0]
    last_agent_text = ""
    last_agent_had_tool = False
    idx = 0
    for entry in _iter_jsonl(path):
        if entry.get("type") != "response_item":
            continue
        payload = entry.get("payload") or {}
        ptype = payload.get("type")
        if ptype in ("function_call", "custom_tool_call", "web_search_call"):
            last_agent_had_tool = True
            continue
        if ptype != "message":
            continue
        role = payload.get("role")
        if role == "assistant":
            text = _message_text(payload)
            if text.strip():
                last_agent_text = text
            continue
        if role != "user":
            continue
        human = _message_text(payload)
        if not human.strip() or _looks_systemic(human):
            continue
        if not last_agent_text:
            continue
        prev = last_agent_text
        truncated = len(prev) > AGENT_TAIL_CAP
        if truncated:
            prev = prev[-AGENT_TAIL_CAP:]
        idx += 1
        yield {
            "source": "codex",
            "session": session,
            "idx": idx,
            "human_text": human[:HUMAN_CAP],
            "prev_agent_text": prev,
            "prev_had_tool": last_agent_had_tool,
            "prev_truncated": truncated,
        }
        last_agent_had_tool = False


def _walk(paths):
    for p in paths:
        if os.path.isdir(p):
            for root, _d, files in os.walk(p):
                for f in sorted(files):
                    if f.endswith(".jsonl"):
                        yield os.path.join(root, f)
        elif p.endswith(".jsonl"):
            yield p


def main(argv) -> int:
    ap = argparse.ArgumentParser(description="Codex reaction-candidate extractor")
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)
    emitted = 0
    for sp in _walk(args.paths):
        for rec in extract_session(sp):
            sys.stdout.write(json.dumps(rec, ensure_ascii=False) + "\n")
            emitted += 1
            if args.limit and emitted >= args.limit:
                return 0
    print(f"emitted {emitted} codex reaction-candidate records", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
