# The Rumor: YOUR Taste Cartridge (template, not yet generated)

> This file ships blank. It becomes the rumor of *your* taste once you run the pipeline.
> Until then, the loop has nothing to grade against.

Generate it:

1. `bash scripts/onboard.sh`: discovers your agent history (Claude Code, Codex, etc.) and
   extracts your reaction-to-artifact turns into `docs/candidates-all.jsonl`.
2. Mine those candidates into a labeled eval-set (`docs/eval-set.jsonl`) using the discovery
   Workflow in `workflows/rumor-discovery.cc.js`, run from an agent that can fan out subagents.
3. Ask an agent to read `docs/eval-set.jsonl` and write your cartridge here, replacing this
   template. The distiller prompt is in `ONBOARDING.md`.

A good cartridge has six sections, all grounded in YOUR real reactions (never invented):

1. **Who you are to an agent**: what you actually want from the interaction.
2. **THE BAR: acceptable vs amazing**: with real quoted exemplars from your reactions. The
   most important section. What separates a win from a competent-but-generic result for you.
3. **The question behind the question**: recurring intents under your literal asks.
4. **Push vs interrogate**: when you want momentum vs premises questioned.
5. **Standing dislikes**: what reliably lands badly.
6. **Oblique moves you make**: how you push when something is merely fine.

Once generated, this is the file the loop injects every session. Keep it dense and honest:
every claim should trace to an eval-set record, not a guess.
