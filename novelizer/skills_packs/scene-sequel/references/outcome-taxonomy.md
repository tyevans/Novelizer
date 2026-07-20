# Outcome taxonomy: yes / yes_but / no_and / no

This is `BriefIntent.planned_outcome`'s literal set (`""`, `yes`,
`yes_but`, `no_and`, `no`) — the resolution of a scene's proactive goal
(Swain's Scene: goal → conflict → disaster) or its reactive counterpart
(Sequel: reaction → dilemma → decision).

## Taxonomy

| Outcome | Meaning | Typical use |
|---|---|---|
| `yes` | The POV character's goal is achieved cleanly | Rare mid-book; a clean `yes` before ~90% manuscript position is a premature tension release — reserve it for minor sub-goals or the climax/resolution |
| `yes_but` | The goal is achieved, at a cost or with a complication attached | Workhorse outcome — progress that still generates the next problem |
| `no_and` | The goal fails, and things get worse besides | Workhorse outcome — Swain's "disaster"; the engine of a scene chain |
| `no` | The goal simply fails, no additional complication | Use sparingly — a flat `no` reads as a dead end unless it's deliberately quiet (e.g. right before a Sequel's reflection beat) |

## Usage guidance

- The body of the book should run predominantly on `yes_but` and
  `no_and`. These are the two outcomes that both resolve the immediate
  goal-conflict and seed the next scene's goal (Swain's chain check: each
  Sequel's `decision` becomes the next Scene's `goal`).
- A clean `yes` is a tension release. Reserve it for: (a) minor
  instrumental goals that aren't the scene's central conflict, or (b) the
  finale/resolution, where the story is intentionally letting go of
  tension. A `yes` at, say, the 30% mark on the story's central conflict
  is very likely wrong — check what promise or arc pivot it's supposedly
  resolving; it's probably too early per
  `promise-payoff/references/ledger-checklist.md`'s window discipline.
- `no` (flat failure) is the rarest outcome. It underdelivers on Swain's
  disaster principle unless it's paired with a Sequel scene right after
  that does the reflective work — otherwise the story just stalls.
- Cadence check: a long run of `yes_but`/`no_and` Scene-outcomes with no
  Sequel (reaction-dilemma-decision) beats between them reads breathless;
  a long run of Sequels with nothing but internal `decision`s and no
  Scene resolving them reads saggy. Alternate.
- Every scene should also carry a value-shift/polarity flip
  (`BriefIntent.value_shift`) independent of its outcome literal — a
  scene that resolves `yes_but` but never turns its tracked value is an
  exposition flag (Story Grid's "every scene turns").
