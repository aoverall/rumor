# Rumor: Onboarding (point it at your own machine)

Rumor mines *your* conversation history into a *rumor of your taste* and hands it to a loop.
None of it is specific to one person. The cartridge you generate is yours.

## The short path

```bash
bash scripts/onboard.sh        # discover your history -> docs/candidates-all.jsonl
python3 scripts/rumor remine   # orchestrate the rebuild; it halts at each step you must do
```

`rumor remine` is the real driver. It runs the deterministic stages itself and STOPS at each
stage only an agent can do (classify, distill, judge), printing exactly what to do, then you
run `rumor remine` again to continue. It refuses to finish if you skip a stage. You can check
where you are with `python3 scripts/rumor remine --status` and reset with `--abort`.

Below is what each halt asks for. Run them inside an AI agent that can fan out a Workflow and
write files.

## 1. Mine (agentic)

remine halts and asks you to classify the candidates. On a clean Claude Code install, run the
discovery Workflow shipped at `workflows/rumor-discovery.cc.js`. Invoke it by `scriptPath`,
not `name`: the Workflow tool only resolves names from `.claude/workflows/`, and the kit ships
this one in `workflows/`.

```
Workflow({ scriptPath: 'workflows/rumor-discovery.cc.js', args: { count: <lines in docs/candidates-all.jsonl> } })
```

It fans out lightweight classifier agents over `docs/candidates-all.jsonl`, labels each reaction
`{verdict, why, question_behind, mode}`, drops non-reactions and duplicates, and RETURNS the
labeled records (it cannot write files itself). Write them, one JSON object per line, to
`docs/eval-set.jsonl`. Pre-labeled rows (your correction logs) pass through with their label
intact; raw candidates are judged. Then run `rumor remine` again.

(The companion `rumor-discovery.js` is for a different host that injects a `ctx` object; it
does not run on stock Claude Code.)

## 2. Distill (agentic)

remine halts and asks you to write your cartridge. Ask an agent to read `docs/eval-set.jsonl`
and write `skill/references/cartridge.md` with this prompt (works for any user, replace
nothing):

> Read `docs/eval-set.jsonl` (labeled reaction records: what I said, what I was reacting to,
> a verdict, and the taste signal in `why`). Distill a dense, runtime-injectable cartridge of
> MY taste: (1) who I am to an agent; (2) THE BAR, what separates "amazing" from merely
> "acceptable" in my reactions, with real quoted exemplars; (3) the question behind my
> questions; (4) when I want push (momentum) vs interrogate (premises questioned); (5)
> standing dislikes that reliably land badly; (6) the oblique moves I make when something is
> merely fine. Authoritative, concrete, quotable, 200-380 lines. Write it to
> `skill/references/cartridge.md`.

Then run `rumor remine` again. It renders the field manual (`docs/field-manual.html`) and runs
the SC3 split for you, then halts for the last step.

## 3. Judge (agentic, the validation gate)

remine halts and asks you to validate that the cartridge actually helps. It has split the
eval-set into `docs/eval-distill.jsonl` (used to distill) and a blind `docs/eval-holdout.jsonl`.
Have an agent label the holdout TWICE, once WITH `skill/references/cartridge.md` in context and
once WITHOUT, writing one row per held-out record to `docs/predictions.jsonl`:

```
{"idx": <holdout idx>, "gold": <the held-out verdict>, "pred_with": <verdict>, "pred_without": <verdict>}
```

Then run `rumor remine` again. It scores `predictions.jsonl` and reports the SC3 delta. A
positive delta (the cartridge improves the amazing-vs-acceptable call on records it never saw)
means the rumor actually captures you. remine refuses to report complete unless the judge
produced real scorable pairs.

## Capture as you go

When the person you are working for reacts in a way worth remembering, record it without
breaking flow:

```
python3 scripts/rumor capture --verdict redirected --mode neutral \
  --human "<what they said>" --artifact "<what they reacted to>" \
  --why "<the taste signal>" --question "<the intent behind it>"
```

It validates the shape and appends to your eval-set (and a `.learnings` log if you keep one),
both or neither. The cartridge is then stale; re-run `rumor remine` at a natural break.

## Adopt

The point is the loop firing automatically, not a file that rots. Install the skill so the
cartridge loads every session:

- **Claude Code / agents with a skills dir:** copy or symlink `skill/` to where your agent
  discovers skills (e.g. `~/.claude/skills/rumor`), so `SKILL.md` is registered and
  `references/cartridge.md` is read at a fork. Restart the agent so it picks the skill up.
- **Anywhere else:** load `skill/references/cartridge.md` into the agent's context each session
  (a session-start hook, or an always-loaded instructions file). The cartridge is the runtime
  layer; everything else is how it gets generated and refreshed.

## What you need

At least one of `~/.claude/projects`, `~/.codex/sessions`, `~/.openclaw`, plus an agent that
can run a fan-out Workflow (the mine) and write files (the distill). That is it.
