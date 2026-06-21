#!/usr/bin/env python3
"""Tests for the OpenClaw reaction-candidate extractor (extract_openclaw).

This is the last untested adapter on the provenance floor, and the riskiest of the
three real extractors. Unlike Claude/Codex transcripts (mostly you typing at an
agent), OpenClaw "user" turns are dominated by autonomous machinery: heartbeat
injections, cron dispatches, and sub-agent task briefings ("[Wed 2026-03-09 ...]
You are a ..."). `extract_openclaw extract` is wired live into onboard.sh, which
writes its output to candidates-openclaw.jsonl in the U1 record shape the miner
consumes. A silent regression in its filtering leaks autonomous agent-loop noise
into the candidate set labeled as if you reacted to work. That is precisely the
fraud the cartridge names: fake signal dressed as real.

So the unique logic this adapter carries and the others don't is pinned directly:
  - the regex task-dispatch / agent-instruction filters (_OC_TASK_RE / _OC_AGENT_INSTR_RE)
  - the OC_SYSTEM_MARKERS head-anchored filter
  - the divergent envelope unwrap (payload | message | bare event)
  - the hardcoded prev_had_tool=False contract
  - the U1 record-shape contract shared with the Claude/Codex adapters

Fixtures are synthetic transcript lines written only to exercise parsing. They are
never written to docs/eval-set.jsonl and never become eval records. Run with:

    python3 -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import extract_openclaw  # noqa: E402


def _write_jsonl(lines):
    fh = tempfile.NamedTemporaryFile(
        "w", suffix=".jsonl", delete=False, encoding="utf-8"
    )
    for ln in lines:
        fh.write(ln if isinstance(ln, str) else json.dumps(ln))
        fh.write("\n")
    fh.close()
    return fh.name


def _oc_msg(role, text, envelope="payload"):
    """An OpenClaw transcript message line.

    envelope controls where the role/content live: 'payload' (the documented
    shape), 'message' (the secondary unwrap the adapter also accepts), or 'bare'
    (role/content on the event itself).
    """
    body = {"role": role, "content": [{"type": "text", "text": text}]}
    if envelope == "payload":
        return {"type": "message", "payload": body}
    if envelope == "message":
        return {"type": "message", "message": body}
    # bare: role/content directly on the event
    out = {"type": "message"}
    out.update(body)
    return out


class OpenClawExtractorTest(unittest.TestCase):
    def _extract(self, lines):
        path = _write_jsonl(lines)
        try:
            return list(extract_openclaw._oc_extract_file(path))
        finally:
            os.unlink(path)

    # --- core pairing ---

    def test_pairs_human_reaction_with_preceding_agent(self):
        recs = self._extract([
            _oc_msg("assistant", "done, look good?"),
            _oc_msg("user", "rad fuck yes keep going"),
        ])
        self.assertEqual(len(recs), 1)
        rec = recs[0]
        self.assertEqual(rec["source"], "openclaw")
        self.assertEqual(rec["human_text"], "rad fuck yes keep going")
        self.assertEqual(rec["prev_agent_text"], "done, look good?")
        self.assertEqual(rec["idx"], 1)

    def test_skips_human_turn_with_no_preceding_agent(self):
        # A leading user turn with no agent output before it is not a reaction.
        recs = self._extract([_oc_msg("user", "first message")])
        self.assertEqual(recs, [])

    def test_idx_increments_across_multiple_reactions(self):
        recs = self._extract([
            _oc_msg("assistant", "a"),
            _oc_msg("user", "one"),
            _oc_msg("assistant", "b"),
            _oc_msg("user", "two"),
        ])
        self.assertEqual([r["idx"] for r in recs], [1, 2])

    def test_latest_agent_turn_is_the_paired_one(self):
        # When two agent turns precede a human turn, the most recent one pairs.
        recs = self._extract([
            _oc_msg("assistant", "stale"),
            _oc_msg("assistant", "fresh"),
            _oc_msg("user", "ok"),
        ])
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["prev_agent_text"], "fresh")

    def test_empty_agent_turn_does_not_overwrite_last_agent(self):
        # A whitespace-only assistant turn must not clobber the real prior agent text.
        recs = self._extract([
            _oc_msg("assistant", "real output"),
            _oc_msg("assistant", "   "),
            _oc_msg("user", "ok"),
        ])
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["prev_agent_text"], "real output")

    # --- the unique systemic filters (the fraud guard) ---

    def test_marker_systemic_user_turns_filtered(self):
        for marker in extract_openclaw.OC_SYSTEM_MARKERS:
            recs = self._extract([
                _oc_msg("assistant", "agent output"),
                _oc_msg("user", marker + " injected machinery"),
            ])
            self.assertEqual(recs, [], f"marker {marker!r} should be filtered")

    def test_cron_task_dispatch_filtered(self):
        # Sub-agent task dispatches arrive as user turns and are NOT you reacting.
        recs = self._extract([
            _oc_msg("assistant", "agent output"),
            _oc_msg("user", "[Wed 2026-03-09 02:33 EDT] You are researching X"),
        ])
        self.assertEqual(recs, [])

    def test_agent_instruction_briefing_filtered(self):
        recs = self._extract([
            _oc_msg("assistant", "agent output"),
            _oc_msg("user", "You are a research agent. Begin."),
        ])
        self.assertEqual(recs, [])

    def test_real_human_turn_kept_despite_marker_word_mid_text(self):
        # The marker filter is head-anchored (first 300 chars, lstripped); a marker
        # word deep in real input must not nuke a genuine reaction.
        head = "this is a real reaction from you that runs on for a while. " * 6
        recs = self._extract([
            _oc_msg("assistant", "agent output"),
            _oc_msg("user", head + "System: was mentioned here"),
        ])
        self.assertEqual(len(recs), 1)

    def test_real_human_turn_starting_with_you_not_filtered(self):
        # _OC_AGENT_INSTR_RE only matches "You are a/an/researching/the ...",
        # not arbitrary sentences that happen to start with "You".
        recs = self._extract([
            _oc_msg("assistant", "agent output"),
            _oc_msg("user", "you broke the vault registration, fix it"),
        ])
        self.assertEqual(len(recs), 1)

    # --- envelope handling ---

    def test_message_envelope_unwrap(self):
        recs = self._extract([
            _oc_msg("assistant", "out", envelope="message"),
            _oc_msg("user", "ok", envelope="message"),
        ])
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["prev_agent_text"], "out")

    def test_bare_event_envelope_unwrap(self):
        recs = self._extract([
            _oc_msg("assistant", "out", envelope="bare"),
            _oc_msg("user", "ok", envelope="bare"),
        ])
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["human_text"], "ok")

    def test_string_content_supported(self):
        # content may be a bare string rather than a block list.
        recs = self._extract([
            {"type": "message", "payload": {"role": "assistant", "content": "out"}},
            {"type": "message", "payload": {"role": "user", "content": "go"}},
        ])
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["prev_agent_text"], "out")
        self.assertEqual(recs[0]["human_text"], "go")

    def test_non_message_events_skipped(self):
        recs = self._extract([
            {"type": "reasoning", "payload": {"role": "assistant", "content": "noise"}},
            _oc_msg("assistant", "real output"),
            _oc_msg("user", "ok"),
        ])
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["prev_agent_text"], "real output")

    def test_malformed_lines_skipped(self):
        recs = self._extract([
            "{not valid json",
            _oc_msg("assistant", "recovered output"),
            _oc_msg("user", "nice"),
        ])
        self.assertEqual(len(recs), 1)

    # --- caps and contract ---

    def test_agent_tail_cap_and_truncation_flag(self):
        long_text = "x" * (extract_openclaw.OC_AGENT_TAIL_CAP + 500)
        recs = self._extract([
            _oc_msg("assistant", long_text),
            _oc_msg("user", "ok"),
        ])
        self.assertEqual(len(recs), 1)
        self.assertTrue(recs[0]["prev_truncated"])
        self.assertEqual(
            len(recs[0]["prev_agent_text"]), extract_openclaw.OC_AGENT_TAIL_CAP
        )

    def test_human_text_capped(self):
        long_human = "a" * (extract_openclaw.OC_HUMAN_CAP + 100)
        recs = self._extract([
            _oc_msg("assistant", "out"),
            _oc_msg("user", long_human),
        ])
        self.assertEqual(len(recs[0]["human_text"]), extract_openclaw.OC_HUMAN_CAP)

    def test_prev_had_tool_is_always_false(self):
        # OpenClaw transcripts don't carry the tool signal here; the adapter pins it
        # to False. This is a real contract: a future change must be deliberate.
        recs = self._extract([
            _oc_msg("assistant", "out"),
            _oc_msg("user", "ok"),
        ])
        self.assertIs(recs[0]["prev_had_tool"], False)

    def test_record_shape_matches_other_adapters(self):
        # All adapters MUST emit the same keys so the miner consumes them unchanged.
        import extract_claude
        import extract_codex

        cl_path = _write_jsonl([
            {"type": "assistant", "message": {"role": "assistant",
                "content": [{"type": "text", "text": "out"}]}},
            {"type": "user", "message": {"role": "user", "content": "ok"}},
        ])
        cx_path = _write_jsonl([
            {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "out"}]}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "ok"}]}},
        ])
        oc_path = _write_jsonl([
            _oc_msg("assistant", "out"),
            _oc_msg("user", "ok"),
        ])
        try:
            cl = list(extract_claude.extract_session(cl_path))[0]
            cx = list(extract_codex.extract_session(cx_path))[0]
            oc = list(extract_openclaw._oc_extract_file(oc_path))[0]
        finally:
            os.unlink(cl_path)
            os.unlink(cx_path)
            os.unlink(oc_path)
        self.assertEqual(set(oc.keys()), set(cl.keys()))
        self.assertEqual(set(oc.keys()), set(cx.keys()))


if __name__ == "__main__":
    unittest.main()
