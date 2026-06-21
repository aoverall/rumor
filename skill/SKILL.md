---
name: rumor
description: "A rumor of YOUR taste: a simulated-taste loop that keeps an agent moving at a fork instead of stopping to ask, built from how you actually react to produced work. Use (1) automatically mid-task at a fork where you'd normally stop and ask how to proceed; (2) before declaring work done, to gate it against your bar; (3) on demand via 'rumor capture' to record a fresh taste signal; (4) 'rumor remine' to rebuild the taste model from your history. Run scripts/onboard.sh then 'rumor remine' (see ONBOARDING.md) to generate your cartridge."
metadata:
  project: rumor
---

# Rumor

> A *rumor* of Fable. Not the real thing, a plausible fabrication of it: a taste model
> stitched from how one specific person reacts to produced work, stated with confidence,
> good enough to act on.

The cartridge at `references/cartridge.md` is the rumor: a distilled model of what YOU find
**acceptable vs amazing**, the **question behind your questions**, and where you want
**force vs interrogation**. This skill puts that rumor to work.

**You start with no cartridge.** `references/cartridge.md` ships as a template. Generate
your real one by running the pipeline in `README.md` / `ONBOARDING.md`: extract your
reaction turns, mine them into an eval-set, distill the cartridge. Until then the loop has
nothing to run on.

## The Loop (primary, automatic)

Fire this at any **fork** where you would otherwise stop and ask how to proceed. The whole
point is to not break the spell.

```
fork reached (you're about to ask "how should I proceed?")
   │
   ▼  SPINE   read references/cartridge.md, infer "what would they want here", CONTINUE.
   │          Escalate only the irreversible/costly/constraint-breaking forks.
   ▼  produce
   │
   ▼  GATE    grade your own output against the cartridge bar: AMAZING or merely ACCEPTABLE?
   │            amazing ───────► done
   │            acceptable ──┐
   │                         ▼
   ▼  MOVE    apply ONE oblique move (references/loop-mechanics.md), re-enter at SPINE.
```

See `references/loop-mechanics.md` for the gate rubric and the oblique-moves library.

## Capture (`rumor capture`)

When the person reacts to your work in a way worth remembering (approval, correction, an
"that's the one I want" or a "not like that", all invented examples), record it. You decide the verdict; `rumor capture`
validates the shape and appends to both sinks at once, both or neither.

```
python3 scripts/rumor capture \
  --verdict redirected --mode neutral \
  --human "<what they actually said>" \
  --artifact "<one-line summary of the work they reacted to>" \
  --why "<the taste signal>" \
  --question "<the question behind it>"
```

It appends the labeled record to `docs/eval-set.jsonl` (your ground truth) in the shape
`{source:"capture", session, idx, is_reaction, human_text, artifact_summary, verdict, why,
question_behind, mode}`, and a faithful `.learnings/LEARNINGS.md` entry (carrying an explicit
`**Verdict**`/`**Mode**` so it round-trips with its true label, if you keep a self-improvement
log). `verdict ∈ {amazing, acceptable, rejected, redirected, confused}`, `mode ∈ {push,
interrogate, neutral}`. It refuses malformed input rather than write partial ground truth, and
never invents a field: every taste value is yours.

## Remine (`rumor remine`)

Rebuild the taste model when enough new signal has accrued. `rumor remine` orchestrates it.
Three stages are judgment a script can't do (classify the candidates, distill the cartridge,
judge the holdout), so it runs the deterministic stages itself and stops at each of those with
a handoff, picking back up once you've done it.

```
python3 scripts/rumor remine            begin, or advance to the next stage
python3 scripts/rumor remine --status   the cursor and what is pending
python3 scripts/rumor remine --abort    clear the run state
```

In order: it rebuilds candidates from the sources manifest and halts; you run the discovery
Workflow to classify into `docs/eval-set.jsonl`, and remine validates the mine and backfills
any missing `idx`; you re-distill `references/cartridge.md`, and remine renders the field
manual and runs the SC3 split; you judge the blind holdout into `docs/predictions.jsonl`, and
remine scores it and reports the delta. It will not report complete with an agentic stage
skipped. See `README.md`.

## Files

- `references/cartridge.md`: the rumor (your runtime-injected taste model). Generated; ships blank.
- `references/loop-mechanics.md`: gate rubric + the oblique-moves library.
- `docs/eval-set.jsonl`: your labeled reaction ground truth. Generated.
- `scripts/rumor`: the dispatcher that makes the vocabulary runnable (`capture`, `remine`, `coverage`, `onboard`).
- `scripts/`, `workflows/`: extraction, mining, distillation, and the offline judge.
