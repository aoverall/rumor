#!/usr/bin/env bash
# Rumor onboarding. Point it at YOUR machine and go.
#
# Discovers your local agent-conversation history (Claude Code, Codex, OpenClaw) and your
# correction logs, extracts reaction-to-artifact candidates, and tells you the next steps to
# mine them into your own taste cartridge.
#
# The avenue list lives in ONE place, scripts/sources.jsonl, walked by coverage_check.py.
# This script no longer keeps its own copy of the source list (the drift that used to extract
# OpenClaw and then drop it from the mine). A source absent on your machine prints an explicit
# SKIP; it is never silently missing.
#
# Usage:  bash scripts/onboard.sh
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> Rumor onboarding"
echo "    Building a rumor of YOUR taste from how you react to produced work."
echo

# Coverage is mechanical: walk every avenue in the manifest, fold the combine lanes into
# docs/candidates-all.jsonl. Relevance stays agentic (the mining + distillation below).
python3 "$REPO/scripts/coverage_check.py" run --repo "$REPO"

ALL="$REPO/docs/candidates-all.jsonl"
if [ ! -s "$ALL" ]; then
  echo
  echo "No reaction candidates found. Rumor needs at least one of:"
  echo "  ~/.claude/projects  |  ~/.codex/sessions  |  ~/.openclaw  |  a correction log"
  exit 1
fi

echo
cat <<'NEXT'
Next steps (the mining + distillation are agentic, run them in an agent):

  1. MINE   Point the discovery Workflow (workflows/rumor-discovery.cc.js) at
            docs/candidates-all.jsonl. It classifies each candidate into a labeled
            eval-set: verdict (amazing/acceptable/rejected/...), why, question-behind, mode.
            -> writes docs/eval-set.jsonl

  2. DISTILL  Ask an agent to read docs/eval-set.jsonl and write your taste cartridge
              to skill/references/cartridge.md (see ONBOARDING.md for the prompt).

  3. RENDER   python3 scripts/render_field_manual.py   ->  docs/field-manual.html

  4. VALIDATE python3 scripts/eval_judge.py split ...  (prove the cartridge helps)

  5. ADOPT    Wire skill/ into your agent setup and load the cartridge every session.
              See ONBOARDING.md (Adopt).
NEXT
