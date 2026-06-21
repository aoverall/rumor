#!/usr/bin/env python3
"""Tests for the reaction-candidate extractors (extract_claude, extract_codex).

These extractors are the provenance floor of the whole system: they decide what
counts as a real human reaction. A silent regression here lets systemic noise
(AGENTS.md dumps, harness markers, tool framing) leak into the eval-set labeled as
if you typed it. That is the exact failure the cartridge calls fraud: fake signal
dressed as real. So the filtering and pairing logic is guarded here directly.

Fixtures are synthetic *transcript* lines written only to exercise parsing. They are
never written to docs/eval-set.jsonl and never become eval records. Run with:

    python3 -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import extract_claude  # noqa: E402
import extract_codex  # noqa: E402


def _write_jsonl(lines):
    """Write a list of dicts (or raw strings) as a temp .jsonl, return its path."""
    fh = tempfile.NamedTemporaryFile(
        "w", suffix=".jsonl", delete=False, encoding="utf-8"
    )
    for ln in lines:
        fh.write(ln if isinstance(ln, str) else json.dumps(ln))
        fh.write("\n")
    fh.close()
    return fh.name


def _claude_user(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def _claude_assistant(text=None, tool=False):
    content = []
    if text is not None:
        content.append({"type": "text", "text": text})
    if tool:
        content.append({"type": "tool_use", "name": "Bash", "input": {}})
    return {"type": "assistant", "message": {"role": "assistant", "content": content}}


def _codex_user(text):
    return {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": text}],
        },
    }


def _codex_assistant(text):
    return {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text}],
        },
    }


def _codex_tool_call():
    return {"type": "response_item", "payload": {"type": "function_call"}}


class ClaudeExtractorTest(unittest.TestCase):
    def _extract(self, lines, **kw):
        path = _write_jsonl(lines)
        try:
            return list(extract_claude.extract_session(path, **kw))
        finally:
            os.unlink(path)

    def test_pairs_human_reaction_with_preceding_agent(self):
        recs = self._extract([
            _claude_assistant("Here is the plan, proceed?"),
            _claude_user("proceed"),
        ])
        self.assertEqual(len(recs), 1)
        rec = recs[0]
        self.assertEqual(rec["source"], "claude")
        self.assertEqual(rec["human_text"], "proceed")
        self.assertEqual(rec["prev_agent_text"], "Here is the plan, proceed?")
        self.assertEqual(rec["idx"], 1)

    def test_skips_human_turn_with_no_preceding_agent(self):
        # min_agent_chars default is 1, so a leading human with no agent is dropped.
        recs = self._extract([_claude_user("first message")])
        self.assertEqual(recs, [])

    def test_filters_systemic_user_turns(self):
        for marker in extract_claude.SYSTEM_PREFIXES:
            recs = self._extract([
                _claude_assistant("agent output"),
                _claude_user(marker + " injected harness content"),
            ])
            self.assertEqual(recs, [], f"marker {marker!r} should be filtered")

    def test_systemic_marker_after_leading_whitespace_filtered(self):
        recs = self._extract([
            _claude_assistant("agent output"),
            _claude_user("\n  <system-reminder> stuff"),
        ])
        self.assertEqual(recs, [])

    def test_real_human_turn_containing_marker_mid_text_kept(self):
        # The filter is anchored at the start; a marker word mid-sentence is real input.
        recs = self._extract([
            _claude_assistant("agent output"),
            _claude_user("why is the <system-reminder> showing up in output?"),
        ])
        self.assertEqual(len(recs), 1)

    def test_tool_only_assistant_turn_sets_had_tool(self):
        recs = self._extract([
            _claude_assistant("running it now", tool=True),
            _claude_user("y"),
        ])
        self.assertEqual(len(recs), 1)
        self.assertTrue(recs[0]["prev_had_tool"])

    def test_structured_user_content_is_not_a_human_turn(self):
        # tool_result turns arrive as content=list; only plain-string user content counts.
        tool_result = {
            "type": "user",
            "message": {"role": "user", "content": [{"type": "tool_result"}]},
        }
        recs = self._extract([_claude_assistant("ran tool"), tool_result])
        self.assertEqual(recs, [])

    def test_truncation_flag_and_tail_cap(self):
        long_text = "x" * (extract_claude.AGENT_TAIL_CAP + 500)
        recs = self._extract([_claude_assistant(long_text), _claude_user("ok")])
        self.assertEqual(len(recs), 1)
        self.assertTrue(recs[0]["prev_truncated"])
        self.assertEqual(len(recs[0]["prev_agent_text"]), extract_claude.AGENT_TAIL_CAP)

    def test_human_text_capped(self):
        long_human = "a" * (extract_claude.HUMAN_CAP + 100)
        recs = self._extract([_claude_assistant("out"), _claude_user(long_human)])
        self.assertEqual(len(recs[0]["human_text"]), extract_claude.HUMAN_CAP)

    def test_malformed_lines_skipped(self):
        recs = self._extract([
            "{not valid json",
            _claude_assistant("recovered output"),
            _claude_user("nice"),
        ])
        self.assertEqual(len(recs), 1)

    def test_idx_increments_across_multiple_reactions(self):
        recs = self._extract([
            _claude_assistant("a"),
            _claude_user("one"),
            _claude_assistant("b"),
            _claude_user("two"),
        ])
        self.assertEqual([r["idx"] for r in recs], [1, 2])


class CodexExtractorTest(unittest.TestCase):
    def _extract(self, lines):
        path = _write_jsonl(lines)
        try:
            return list(extract_codex.extract_session(path))
        finally:
            os.unlink(path)

    def test_pairs_human_reaction_with_preceding_agent(self):
        recs = self._extract([
            _codex_assistant("done, look good?"),
            _codex_user("y"),
        ])
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["source"], "codex")
        self.assertEqual(recs[0]["human_text"], "y")
        self.assertEqual(recs[0]["prev_agent_text"], "done, look good?")

    def test_filters_injected_environment_context(self):
        for marker in extract_codex.SYSTEM_MARKERS:
            recs = self._extract([
                _codex_assistant("agent output"),
                _codex_user(marker + " injected"),
            ])
            self.assertEqual(recs, [], f"marker {marker!r} should be filtered")

    def test_tool_call_sets_had_tool_flag(self):
        recs = self._extract([
            _codex_assistant("about to run"),
            _codex_tool_call(),
            _codex_user("go"),
        ])
        self.assertEqual(len(recs), 1)
        self.assertTrue(recs[0]["prev_had_tool"])

    def test_skips_human_turn_with_no_preceding_agent(self):
        recs = self._extract([_codex_user("first message")])
        self.assertEqual(recs, [])

    def test_record_shape_matches_claude(self):
        # Both adapters MUST emit the same keys so the miner consumes them unchanged.
        cl_path = _write_jsonl([_claude_assistant("out"), _claude_user("ok")])
        cx_path = _write_jsonl([_codex_assistant("out"), _codex_user("ok")])
        try:
            cl = list(extract_claude.extract_session(cl_path))[0]
            cx = list(extract_codex.extract_session(cx_path))[0]
        finally:
            os.unlink(cl_path)
            os.unlink(cx_path)
        self.assertEqual(set(cl.keys()), set(cx.keys()))


if __name__ == "__main__":
    unittest.main()
