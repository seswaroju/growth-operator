# Project skills — UI/UX toolkit

Curated, **markdown-only** UI/UX design skills vendored from four open-source collections so
Claude Code has strong design taste + craft knowledge when working on `web/` and `web-ops/`.
Installed 2026-08-10 at the founder's request.

## Install policy

- **Guidance only — no executable surface.** Only each skill's `SKILL.md` + `reference/`,
  `references/`, and supporting `*.md`/`*.txt` were copied. All `scripts/` (`.mjs`/`.cjs`/`.py`/
  `.js`), `agents/*.toml`, `data/*.csv`, image assets, and per-repo test suites were **excluded**
  so nothing runs on install and the product repo stays free of an unrelated toolchain.
- Content is upstream-verbatim (aside from this README and `impeccable/INSTALL-NOTES.md`).
- Full toolchains remain available upstream / via `npx impeccable …` if ever needed.

## Installed skills (11)

| Skill | Source | What it's for |
|---|---|---|
| `impeccable` | pbakaus/impeccable (Apache-2.0) | Flagship premium-craft skill — covers dashboards, app shells, product UI; `bolder`/`delight`/`overdrive`/`polish` playbooks. See `impeccable/INSTALL-NOTES.md`. |
| `apple-design` | emilkowalski/skills (MIT) | Apple's fluid, physical motion + interface foundations, for the web. |
| `animate` | emilkowalski/skills (MIT) | Build an animation from scratch, decisions in the right order. |
| `animation-vocabulary` | emilkowalski/skills (MIT) | Shared vocabulary for motion. |
| `review-animations` | emilkowalski/skills (MIT) | Critique existing motion against a standard. |
| `emil-design-eng` | emilkowalski/skills (MIT) | Design-engineering craft (the big one). |
| `design-system` | nextlevelbuilder/ui-ux-pro-max (MIT) | Three-layer token architecture (primitive→semantic→component), CSS vars, scales. |
| `ui-styling` | nextlevelbuilder/ui-ux-pro-max (MIT) | Tailwind + accessible component patterns, dark mode, theming. |
| `brand` | nextlevelbuilder/ui-ux-pro-max (MIT) | Brand system: palette, typography, voice, consistency checklists. |
| `design-taste-frontend` | leonxlnx/taste-skill (MIT) | Anti-slop taste; landing-page oriented but good general craft. |
| `redesign-existing-projects` | leonxlnx/taste-skill (MIT) | Audit-first redesign of an existing UI. |

## Held back (available on request — say the word and I'll add any of these)

- **emilkowalski**: `improve-animations`, `find-animation-opportunities`, `pick-ui-library`,
  `prototype`, `ask-sonner`.
- **nextlevelbuilder**: `design`, `ui-ux-pro-max`, `slides`, `banner-design` (deck/marketing-asset
  focused), plus their `scripts/` toolchains.
- **leonxlnx**: `brandkit`, `minimalist-skill`, `soft-skill`, `brutalist-skill` (aesthetic packs),
  `stitch-skill`; and the image-generation skills (`imagegen-frontend-web/mobile`,
  `image-to-code`) — omitted because they need image-generation tooling we don't have.
- **pbakaus/impeccable**: the full `scripts/` live-browser + image-gen + hooks toolchain.

## Sources

- https://github.com/pbakaus/impeccable — Apache-2.0
- https://github.com/emilkowalski/skills — MIT
- https://github.com/nextlevelbuilder/ui-ux-pro-max-skill — MIT
- https://github.com/leonxlnx/taste-skill — MIT
