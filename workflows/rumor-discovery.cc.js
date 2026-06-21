export const meta = {
  name: 'rumor-discovery',
  description: 'Mine reaction candidates into a labeled eval-set: fan out classifier agents over docs/candidates-all.jsonl, drop non-reactions, dedup, and return the labeled records. Pre-labeled gold (learnings/feedback) bypasses the classifier.',
  phases: [{ title: 'Classify' }],
}

// Runtime contract: stock Claude Code Workflow tool. The script has NO filesystem access, so
// the classifier agents Read their slice of candidates-all.jsonl with the Read tool, this
// script merges the labels in memory, and it RETURNS the eval-set records. The caller (the
// agent that invoked the workflow) writes them to docs/eval-set.jsonl with the Write tool.
// (The companion rumor-discovery.js targets a different host that injects a `ctx` object with
// shell + filesystem; use this .cc.js on a clean Claude Code install.)
//
// Build candidates-all.jsonl first (`bash scripts/onboard.sh`, which prints the count), then:
//   Workflow({ scriptPath: 'workflows/rumor-discovery.cc.js', args: { count: <that count>, batchSize: 12 } })
// Invoke by scriptPath, not name: the Workflow tool resolves names only from .claude/workflows/.
// args: { count (required, lines in candidates-all.jsonl), path?, batchSize? }

// args may arrive as an object or, defensively, as a JSON string.
const A = (typeof args === 'string')
  ? (() => { try { return JSON.parse(args) } catch (_e) { return {} } })()
  : (args || {})
const path = A.path || 'docs/candidates-all.jsonl'
// Bigger batches + a light model: classification is a simple rubric call, so each agent
// handles ~24 candidates on haiku at low effort. Fewer, faster, cheaper calls than one heavy
// agent per dozen records, which is what made a real mine sprawl.
const batchSize = Number(A.batchSize) || 24
const count = Number(A.count)
if (!Number.isFinite(count) || count <= 0) {
  throw new Error(
    'rumor-discovery needs args.count = the number of lines in ' + path +
    '. Run `bash scripts/onboard.sh` (or `python3 scripts/coverage_check.py run`) first; it ' +
    'prints the candidate count, then pass it as args.count.')
}

const VERDICTS = 'amazing | acceptable | rejected | redirected | confused'
const CLASSIFIER = [
  'You are labeling reaction candidates mined from agent transcripts. Each non-blank line of',
  'the file is one JSON candidate: a human turn (human_text) and usually the preceding agent',
  'turn (prev_agent_text), plus source and session.',
  '',
  'ALWAYS copy `source`, `session`, and `human_text` from each input candidate into your',
  'output record, verbatim. Never omit human_text - it is the evidence; a record without it',
  'is useless.',
  '',
  'GOLD BYPASS: some candidates already carry a "verdict" field (pre-labeled corrections from',
  'learnings/feedback). Do NOT re-judge those. Return them unchanged (keep source, session,',
  'verdict, mode, is_reaction, and any other fields verbatim).',
  '',
  'For each candidate, fill the fields IN THIS ORDER (reasoning FIRST, decision after):',
  '  1. reasoning: think it through in one or two sentences before you label. Does this human',
  '     turn respond to something the agent produced, and how? Decide is_reaction and verdict',
  '     only AFTER writing this. Do not skip it.',
  '  2. is_reaction: is the human turn a genuine reaction to work the agent produced?',
  '     A reaction is ANY turn that evaluates, approves, corrects, redirects, re-specs,',
  '     conditionally accepts, reports a bug or regression, complains about form/readability,',
  '     or pushes back on a produced artifact - in ANY style. (All example phrases here are',
  '     invented, not quotes.) A terse "looks right, merge it", a precise re-spec with exact',
  '     values ("make the sidebar 280 wide, not 320"), a conditional yes ("ok, but only if it',
  '     still works offline"), a scope-down ("skip the bulk case for now, just the single one"),',
  '     a regression report ("the export button vanished, it was there before"), and a format',
  '     complaint ("I cannot read this dump, put it on a page") are ALL reactions. Reaction style',
  '     varies by person; do not expect one fixed phrasing or only loud praise. Pure logistics,',
  '     off-topic chatter, fresh task handoffs, and next-step turns with no evaluation of produced',
  '     work ("ok, next let us wire the export", "when is the standup") are NOT reactions.',
  '     If false, still include human_text and your reasoning.',
  '  3. verdict: one of ' + VERDICTS + '. Grade each against THIS person\'s OWN range, not an',
  '     absolute scale and not any one person\'s style. Calibrate to their baseline:',
  '       amazing    = their rarest, strongest positive signal. They do not just approve, they',
  '                    EXTEND: pour in more detail, raise the ambition, commit beyond the ask, or',
  '                    react with unusual intensity FOR THEM. This can be calm (an invented',
  '                    example: "this is the one, let us use it everywhere") for a reserved person',
  '                    or loud for an effusive one. Judge the delight relative to how they',
  '                    normally react, NOT by volume or profanity. Some people have no amazing',
  '                    tier at all; do not manufacture one.',
  '       acceptable = approval that trusts and moves on at their ordinary register: the bulk of',
  '                    reactions, warm or terse. It means they saw nothing to fix, not that they',
  '                    are delighted.',
  '       rejected   = the work was wrong, regressed, or is unusable as delivered.',
  '       redirected = do it differently: a re-spec, a scope change, or a different approach.',
  '       confused   = lost the thread / cannot tell what happened.',
  '  4. artifact_summary: one line naming what the agent had just produced (from',
  '     prev_agent_text), i.e. the thing being reacted to. Empty string if unknown.',
  '  5. why: the taste signal in one or two sentences.',
  '  6. question_behind: the underlying intent beneath the literal words.',
  '  7. mode: push | interrogate | neutral.',
  '',
  'If you cannot confidently decide, set uncertain:true and still return your best guess.',
  'Never silently drop a candidate; every input line must produce exactly one output record,',
  'and every record must include human_text verbatim.',
].join('\n')

const SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['records'],
  properties: {
    records: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['reasoning', 'source', 'session', 'is_reaction', 'human_text'],
        properties: {
          reasoning: { type: 'string' },
          source: { type: 'string' },
          session: { type: 'string' },
          is_reaction: { type: 'boolean' },
          human_text: { type: 'string' },
          artifact_summary: { type: 'string' },
          verdict: { type: 'string', enum: ['amazing', 'acceptable', 'rejected', 'redirected', 'confused'] },
          why: { type: 'string' },
          question_behind: { type: 'string' },
          mode: { type: 'string', enum: ['push', 'interrogate', 'neutral'] },
          uncertain: { type: 'boolean' },
          corpus: { type: 'string' },
        },
      },
    },
  },
}

phase('Classify')

// Line ranges (1-based, for the Read tool's offset/limit). One classifier agent per batch.
const batches = []
for (let start = 1; start <= count; start += batchSize) {
  batches.push([start, Math.min(start + batchSize - 1, count)])
}
log(`fan out: ${batches.length} batches of <=${batchSize} over ${count} candidates`)

const labeledBatches = await parallel(batches.map(([start, end]) => () =>
  agent(
    `${CLASSIFIER}\n\nUse the Read tool to read ${path} with offset ${start} and limit ` +
    `${end - start + 1} (lines ${start} to ${end}). Classify each candidate line and return ` +
    `one record per line, in order.`,
    { label: `classify:${start}-${end}`, phase: 'Classify', schema: SCHEMA,
      model: 'haiku', effort: 'medium' })))

// MERGE in memory: drop non-reactions, dedup by (source, session, human_text), shape the row.
const labeled = labeledBatches.filter(Boolean).flatMap((b) => b.records || [])
const seen = new Set()
const kept = []
let dropped = 0
let uncertain = 0
for (const r of labeled) {
  if (r.is_reaction === false) { dropped += 1; continue }
  if (r.uncertain) uncertain += 1
  const key = `${r.source || ''} ${r.session || ''} ${(r.human_text || '').slice(0, 200)}`
  if (seen.has(key)) { dropped += 1; continue }
  seen.add(key)
  kept.push({
    source: r.source,
    session: r.session,
    is_reaction: true,
    human_text: r.human_text,
    artifact_summary: r.artifact_summary || '',
    verdict: r.verdict,
    why: r.why || '',
    question_behind: r.question_behind || '',
    mode: r.mode || 'neutral',
    ...(r.corpus ? { corpus: r.corpus } : {}),
  })
}

log(`merge: ${count} candidates -> ${labeled.length} labeled -> ${kept.length} kept ` +
    `(${dropped} dropped: non-reaction + duplicate; ${uncertain} kept-but-flagged)`)
if (labeled.length < count) {
  log(`WARN: ${count - labeled.length} candidates were never labeled (agent shortfall)`)
}

// The script can't write files. Return the eval-set; the caller writes one JSON object per
// line to docs/eval-set.jsonl with the Write tool.
return {
  eval_set: kept,
  kept: kept.length,
  dropped,
  uncertain,
  candidates: count,
  write_to: 'docs/eval-set.jsonl',
}
