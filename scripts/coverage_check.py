#!/usr/bin/env python3
"""Rumor source-coverage walker.

The deterministic half of "check everything, apply what fits." It walks the one source
manifest (scripts/sources.jsonl) so no taste-signal avenue is silently skipped. It does
NOT touch relevance: whether a candidate is a real reaction, what verdict it earns, which
exemplar applies at a fork. Those stay agentic (the discovery Workflow, the classifiers,
SPINE/GATE reading the cartridge).

Two subcommands:

  run     Walk every avenue: run its extractor if the probe is present (else print an
          EXPLICIT skip, never a silent one), then build candidates-all.jsonl by folding
          in exactly the combine:true sinks. This REPLACES onboard.sh's hand-maintained
          second list, the drift that dropped OpenClaw from the mine.

  verify  Assert the manifest and the loop agree, with no machine access:
            - every scripts/extract_*.py on disk is named by a row (no orphaned avenue;
              this is the guard that would have caught the feedback extractor reaching the
              eval-set only via a one-off sync-back)
            - capture names BOTH sinks (the dual-write invariant)
            - candidates-all would fold in every combine:true avenue (no second list)

Usage:
    python3 scripts/coverage_check.py run [--repo DIR]
    python3 scripts/coverage_check.py verify [--repo DIR]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MANIFEST = "scripts/sources.jsonl"


def load_manifest(repo: str) -> list[dict]:
    """Parse the source manifest, tolerating # comment and blank lines."""
    path = os.path.join(repo, MANIFEST)
    rows = []
    with open(path, encoding="utf-8") as fh:
        for n, raw in enumerate(fh, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{MANIFEST}:{n}: bad JSON: {exc}")
    return rows


def _nonempty(path: str) -> bool:
    return os.path.exists(path) and os.path.getsize(path) > 0


def _linecount(path: str) -> int:
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        return sum(1 for _ in fh)


def cmd_run(args) -> int:
    repo = args.repo
    rows = load_manifest(repo)
    ledger: list[str] = []

    for row in rows:
        if not row.get("extractor"):
            continue  # capture and other live-written avenues have no extractor to run
        rid = row["id"]
        probe = os.path.expanduser(row["probe"])
        if not os.path.isabs(probe):
            probe = os.path.join(repo, probe)  # relative probes resolve against the repo, like sinks
        sink = os.path.join(repo, row["sink"])
        if not os.path.exists(probe):
            # No silent skip. If a prior run left a sink, say it is being folded as stale,
            # so a SKIP can never quietly masquerade as current coverage.
            if row.get("combine") and _nonempty(sink):
                ledger.append(f"  [{rid:9}] SKIP  probe absent: {row['probe']} "
                              f"(folding {_linecount(sink)} stale candidates from a prior run)")
            else:
                ledger.append(f"  [{rid:9}] SKIP  probe absent: {row['probe']}")
            continue
        argv = [a.replace("{probe}", probe) for a in row.get("argv", [])]
        os.makedirs(os.path.dirname(sink), exist_ok=True)
        with open(sink, "w", encoding="utf-8") as out:
            proc = subprocess.run(
                [sys.executable, os.path.join(repo, row["extractor"]), *argv],
                stdout=out, stderr=subprocess.PIPE, text=True,
            )
        if proc.returncode != 0:
            # an extractor that errors is a real failure, not a silent skip
            print(f"FAIL: extractor for '{rid}' exited {proc.returncode}", file=sys.stderr)
            if proc.stderr:
                print(proc.stderr.strip(), file=sys.stderr)
            return 1
        ledger.append(f"  [{rid:9}] {_linecount(sink):4d} candidates  ({row['sink']})")

    # Build candidates-all from EXACTLY the combine:true sinks. One list, derived from the
    # manifest. There is no second hand-written list to drift, which is the defect-1 fix.
    combine_rows = [r for r in rows if r.get("combine")]
    folded, folded_ids = [], []
    out_all = os.path.join(repo, "docs", "candidates-all.jsonl")
    os.makedirs(os.path.dirname(out_all), exist_ok=True)
    with open(out_all, "w", encoding="utf-8") as out:
        for row in combine_rows:
            sink = os.path.join(repo, row["sink"])
            if not _nonempty(sink):
                continue
            with open(sink, encoding="utf-8") as fh:
                data = fh.read()
            if not data.endswith("\n"):
                data += "\n"
            out.write(data)
            folded.append(row["sink"])
            folded_ids.append(row["id"])

    # Defect-1 assertion: every combine:true avenue that produced a nonempty sink is in
    # candidates-all. By construction it is, but assert it so a future regression is loud.
    for row in combine_rows:
        sink = os.path.join(repo, row["sink"])
        if _nonempty(sink) and row["sink"] not in folded:
            print(f"FAIL: '{row['id']}' produced {row['sink']} but it was dropped from "
                  f"the candidates-all combine", file=sys.stderr)
            return 1

    print("==> Rumor coverage ledger")
    for line in ledger:
        print(line)
    print(f"==> candidates-all.jsonl folds in [{', '.join(folded_ids) or 'none'}] "
          f"= {_linecount(out_all)} candidates")
    return 0


def cmd_verify(args) -> int:
    repo = args.repo
    rows = load_manifest(repo)
    by_extractor = {r["extractor"] for r in rows if r.get("extractor")}
    problems: list[str] = []

    # Guard 1 (the defect-2 class): no orphaned extractor. Every extract_*.py on disk must
    # be claimed by a row, so a working source can never be invisible to the manifest.
    on_disk = sorted(
        os.path.relpath(p, repo)
        for p in glob.glob(os.path.join(repo, "scripts", "extract_*.py"))
    )
    for rel in on_disk:
        if rel not in by_extractor:
            problems.append(
                f"orphaned extractor {rel}: on disk but named by no manifest row "
                f"(a remine cannot walk what is not in the manifest)")

    # Guard 2: the reverse, so the manifest cannot name a vanished extractor.
    for r in rows:
        ext = r.get("extractor")
        if ext and not os.path.exists(os.path.join(repo, ext)):
            problems.append(f"avenue '{r['id']}' names missing extractor {ext}")

    # Guard 3: capture is the one runtime invariant that is deterministic. Its DECISION is
    # judgment; its WRITE must hit both sinks. Assert the row names both.
    cap = next((r for r in rows if r["id"] == "capture"), None)
    if cap is None:
        problems.append("no 'capture' row: the dual-sink write invariant is unguarded")
    else:
        sinks = cap.get("sinks", [])
        if len(sinks) < 2:
            problems.append(
                f"capture row names {len(sinks)} sink(s); the contract is BOTH the "
                f"eval-set and .learnings (a half-written capture is a silent partial)")

    # Guard 4: at least one avenue folds into the mine (a manifest that combines nothing
    # would silently starve the cartridge).
    if not any(r.get("combine") for r in rows):
        problems.append("no combine:true avenue: candidates-all would be empty")

    if problems:
        print(f"coverage_check verify: {len(problems)} problem(s)", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    combine_ids = [r["id"] for r in rows if r.get("combine")]
    print(f"coverage_check verify: OK ({len(rows)} avenues, "
          f"combine=[{', '.join(combine_ids)}], capture dual-sink named)")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Rumor source-coverage walker")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "verify"):
        sp = sub.add_parser(name)
        sp.add_argument("--repo", default=REPO)
    args = ap.parse_args(argv)
    args.repo = os.path.abspath(args.repo)
    return {"run": cmd_run, "verify": cmd_verify}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
