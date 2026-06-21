#!/usr/bin/env python3
"""Rumor U1b — OpenClaw transcript discovery + extraction (fan-out, Phase 3).

Per the plan (KTD1, prove-then-fan), OpenClaw is the uncertain source: its transcripts are
NOT in `openclaw-agent.sqlite` (auth/cache only). They scatter across:
  - ~/.openclaw/agents/*/sessions/          (per-agent session stores, format TBD)
  - ~/.openclaw/memory/*.sqlite             (per-model memory)
  - ~/.openclaw/agents/*/agent/codex-home/logs_2.sqlite  (codex-under-openclaw logs)

So this adapter starts with DISCOVERY: probe each store, report which ones actually hold
human reaction turns, and only then commit to a parser. Run `discover` first; wire up the
matching extractor before trusting `extract`.

Usage:
  python3 extract_openclaw.py discover            # report candidate stores + shapes
  python3 extract_openclaw.py extract PATH ...     # (implemented after discovery confirms shape)
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sqlite3
import sys

OPENCLAW = os.path.expanduser("~/.openclaw")


def _sqlite_tables(path: str):
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        cur = con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]
        counts = {}
        for t in tables:
            try:
                counts[t] = con.execute(f"SELECT count(*) FROM '{t}'").fetchone()[0]
            except sqlite3.Error:
                counts[t] = "?"
        con.close()
        return counts
    except sqlite3.Error as e:
        return {"_error": str(e)}


def cmd_discover(_args) -> int:
    print("# OpenClaw transcript discovery\n")
    sess_dirs = sorted(glob.glob(os.path.join(OPENCLAW, "agents", "*", "sessions")))
    print(f"## per-agent session dirs ({len(sess_dirs)})")
    for d in sess_dirs:
        files = glob.glob(os.path.join(d, "**", "*"), recursive=True)
        files = [f for f in files if os.path.isfile(f)]
        exts = {}
        for f in files:
            exts[os.path.splitext(f)[1]] = exts.get(os.path.splitext(f)[1], 0) + 1
        agent = d.split(os.sep)[-2]
        print(f"  {agent:16} files={len(files):4}  exts={exts}")

    mem = sorted(glob.glob(os.path.join(OPENCLAW, "memory", "*.sqlite")))
    print(f"\n## memory sqlite stores ({len(mem)})")
    for m in mem:
        tables = _sqlite_tables(m)
        sz = os.path.getsize(m) // 1024
        print(f"  {os.path.basename(m):20} {sz:6}KB  tables={tables}")

    logs = sorted(glob.glob(os.path.join(OPENCLAW, "agents", "*", "agent", "codex-home", "logs_2.sqlite")))
    print(f"\n## codex-home logs ({len(logs)})")
    for lg in logs:
        print(f"  {lg}  tables={_sqlite_tables(lg)}")

    print("\nNext: pick the store(s) whose tables/files contain human reaction turns, "
          "then implement `extract` to emit the U1 record shape "
          "(source='openclaw', session, idx, human_text, prev_agent_text, prev_had_tool).")
    return 0


import json as _json

OC_AGENT_TAIL_CAP = 2500
OC_HUMAN_CAP = 4000

# OpenClaw "user" turns are dominated by system/heartbeat injection, not you typing.
OC_SYSTEM_MARKERS = (
    "System:", "Read HEARTBEAT", "Read SOUL", "Read USER", "Read MEMORY",
    "<system-reminder>", "following the heartbeat", "[heartbeat", "HEARTBEAT.md",
    "Before doing anything else", "Every Session", "<turn_", "Node:", "auto-injected",
    "Continue where you left off", "[OpenClaw heartbeat", "Sender (untrusted metadata",
    "previous model attempt failed", "OpenClaw runtime context", "[heartbeat poll",
    "runtime-generated", "[cron]", "scheduled task", "untrusted metadata",
)


def _oc_text(payload: dict) -> str:
    content = payload.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for b in content:
        if isinstance(b, dict) and b.get("type") == "text":
            parts.append(b.get("text", ""))
        elif isinstance(b, str):
            parts.append(b)
    return "\n".join(p for p in parts if p)


# Cron/sub-agent task dispatches arrive as "user" turns: "[Wed 2026-03-09 02:33 EDT] You are ..."
_OC_TASK_RE = re.compile(r"^\s*\[\w{3}\s+\d{4}-\d{2}-\d{2}[^\]]*\]\s*(You are|OpenClaw|System|Research|Write|Read|Following)")
_OC_AGENT_INSTR_RE = re.compile(r"^\s*(\[[^\]]*\]\s*)?You are (a |an |researching|the )")


def _oc_looks_systemic(text: str) -> bool:
    head = text.lstrip()[:300]
    if any(m in head for m in OC_SYSTEM_MARKERS):
        return True
    if _OC_TASK_RE.match(text) or _OC_AGENT_INSTR_RE.match(text):
        return True
    return False


def _oc_extract_file(path: str):
    session = os.path.basename(path).split(".")[0]
    last_agent = ""
    idx = 0
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                e = _json.loads(line)
            except _json.JSONDecodeError:
                continue
            if e.get("type") != "message":
                continue
            p = e.get("payload") or e.get("message") or e
            role = p.get("role") or e.get("role")
            if role == "assistant":
                t = _oc_text(p)
                if t.strip():
                    last_agent = t
                continue
            if role != "user":
                continue
            human = _oc_text(p)
            if not human.strip() or _oc_looks_systemic(human) or not last_agent:
                continue
            prev = last_agent
            trunc = len(prev) > OC_AGENT_TAIL_CAP
            if trunc:
                prev = prev[-OC_AGENT_TAIL_CAP:]
            idx += 1
            yield {
                "source": "openclaw",
                "session": session,
                "idx": idx,
                "human_text": human[:OC_HUMAN_CAP],
                "prev_agent_text": prev,
                "prev_had_tool": False,
                "prev_truncated": trunc,
            }


def cmd_extract(args) -> int:
    paths = args.paths or [os.path.join(OPENCLAW, "agents", a, "sessions")
                           for a in ("main", "cron", "codex")]
    files = []
    for p in paths:
        if os.path.isdir(p):
            for root, _d, fs in os.walk(p):
                files += [os.path.join(root, f) for f in fs
                          if f.endswith(".jsonl") and "checkpoint" not in f]
        elif p.endswith(".jsonl"):
            files.append(p)
    emitted = 0
    for fp in sorted(files):
        for rec in _oc_extract_file(fp):
            sys.stdout.write(_json.dumps(rec, ensure_ascii=False) + "\n")
            emitted += 1
            if args.limit and emitted >= args.limit:
                print(f"reached --limit {args.limit}", file=sys.stderr)
                return 0
    print(f"emitted {emitted} openclaw reaction-candidate records from {len(files)} files",
          file=sys.stderr)
    return 0


def main(argv) -> int:
    ap = argparse.ArgumentParser(description="OpenClaw transcript discovery/extraction")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("discover").set_defaults(func=cmd_discover)
    ex = sub.add_parser("extract")
    ex.add_argument("paths", nargs="*")
    ex.add_argument("--limit", type=int, default=0)
    ex.set_defaults(func=cmd_extract)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
