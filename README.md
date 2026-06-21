# Rumor

Your coding agent already has something like taste. It just doesn't know it's yours. Rumor
learns what you find good from how you've reacted to an agent's work, and keeps the agent
moving the way you would instead of stopping to ask or shipping the generic version.

## Just run it

Paste this to your coding agent (Claude Code) and let it set itself up:

> Go to https://github.com/aoverall/rumor, read the README and ONBOARDING.md, and set Rumor
> up on this machine. Install the `skill/` directory where you load skills so the cartridge
> loads each session. Run `bash scripts/onboard.sh` to pull my reaction history, then drive
> `python3 scripts/rumor remine` to build my taste cartridge. The remine driver pauses between
> stages by design: each time it stops and asks you to classify, distill, or judge, do that
> stage and run it again. Keep going through all the stages yourself; run the discovery
> Workflow when it asks you to classify. Only come back to me if you hit a real decision, and
> at the end show me what you found about my taste.

That runs mostly on its own and takes a while, since it reads your whole history. It will
check in if it hits something only you can decide.

## What it is

Two things grind about working with an AI agent: it stops to ask you to choose when you'd
rather it made the call, and it hands you competent, forgettable work and calls it done.

Rumor reads your own history with these agents, finds where you reacted to their output, and
distills a short write-up of your taste from those reactions. The agent loads it every
session: at a fork it infers what you'd want and keeps going; before it calls work done it
grades the result against your bar and pushes when it's merely fine.

No training, no magic. A cheat sheet about you, assembled from things you actually said.

> About the name. I was trying to figure out what made Anthropic's Fable seem really good. My
> guess is it had something to do with modeling the kind of feedback it already knew I'd give.
> So this is my homemade version. Not a Fable, just a rumor.

## The pieces

- eval-set (`docs/eval-set.jsonl`): your reactions, mined from your history and labeled
  (delight, plain approval, rejection, redirect). The ground truth.
- cartridge (`skill/references/cartridge.md`): a dense write-up of your taste, distilled from
  the eval-set, loaded into the agent each session. Ships blank; you generate your own.
- field manual (`docs/field-manual.html`): the same model rendered readable, to look over and
  correct.

## The loop

```
the agent hits a fork (about to ask "how should I proceed?")
   |
   v  CONTINUE   read the cartridge, infer what you'd want, and keep going
   |             (only stop for the irreversible, costly, or constraint-breaking forks)
   v  produce the work
   |
   v  CHECK      grade it against your bar: actually good by your standard,
   |             or just competent and generic?
   |               good --------> done
   |               just fine ---> make one sideways move and loop back
```

## Set it up by hand

```bash
bash scripts/onboard.sh        # pull your reaction history into docs/candidates-all.jsonl
python3 scripts/rumor remine   # build the model; it pauses at each stage an agent must do
python3 scripts/rumor remine --status   # where am I, what is pending
```

Run it inside an agent that can write files and run the discovery Workflow (the labeling step
fans out across helper agents). `ONBOARDING.md` has the full walkthrough: the classify step,
the distiller prompt, the validation recipe, and how to install the skill. Once you have a
cartridge, record fresh signals as you work:

```bash
python3 scripts/rumor capture --verdict redirected --mode neutral \
  --human "<what they said>" --artifact "<what they reacted to>" \
  --why "<the taste signal>" --question "<the intent behind it>"
```

## Optional: put Rumor in charge of a build loop

If you already use the `/loop`, `/lfg`, and `ultracode` tools (an autonomous pipeline that
plans, builds, and ships a task across subagents), you can let Rumor decide when that loop is
allowed to stop: `/loop ultracode /lfg <your task> until it satisfies /rumor`. Each pass,
Rumor grades the result against your taste; if it is merely competent it goes around again,
and it stops when the work is good by your standard, not just done. Skip this if you do not
already run those tools; it is not needed to use Rumor.

## What you need

Claude Code (so the labeling step can fan out across helper agents), one of
`~/.claude/projects`, `~/.codex/sessions`, or `~/.openclaw` to mine, and that is it. Nothing
here is anyone's taste but the blank template; you build yours from your own machine.
