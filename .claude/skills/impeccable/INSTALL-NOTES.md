# Impeccable — installed as guidance (scripts omitted)

This skill was installed into the Growth Operator repo as **craft guidance only**: its
`SKILL.md` + the `reference/*.md` playbooks. The upstream skill also ships a large
executable toolchain (`scripts/` — a browser-automation "live" server, image generation,
edit hooks, anti-pattern detectors). We intentionally **did not** copy that machinery into
this product repo — it would add a big JS/Python executable surface unrelated to the app.

**What this means when you invoke `impeccable`:**

- **Skip the `## Setup` step** that runs `node <skill-base-dir>/scripts/context.mjs` — that
  script is not installed. Instead, read the relevant `reference/*.md` playbook directly
  (e.g. [reference/bolder.md](reference/bolder.md), [reference/craft.md](reference/craft.md),
  [reference/craft-floor.md](reference/craft-floor.md), [reference/polish.md](reference/polish.md),
  [reference/delight.md](reference/delight.md), [reference/layout.md](reference/layout.md),
  [reference/colorize.md](reference/colorize.md), [reference/critique.md](reference/critique.md)).
- Ignore the `allowed-tools` Bash entries pointing at `scripts/` — those tools aren't present.
- The **live browser-iteration mode** and **image generation** are unavailable here. If you
  ever want the full toolchain, use the upstream package via `npx impeccable …` in a scratch
  workspace rather than vendoring its scripts into this repo.

All craft/reference content is upstream-verbatim (Apache-2.0). Source:
https://github.com/pbakaus/impeccable
