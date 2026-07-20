---
name: outlining
description: Adopting a story-structure framework, drafting rolling-wave chapter briefs, and targeting beats to their ideal position. Activate when proposing a BlueprintPlan, drafting/superseding a BriefIntent, or fulfilling a BeatIntent.
---

# Outlining

This system treats story structure as data, not prose: a `BlueprintRecord`
(the adopted framework), a set of `BeatRecord`s (structural anchors indexed
to an ideal manuscript position), and a rolling wave of `ChapterBriefRecord`s
(near-term chapter intentions, revised as the story develops). Read
`/outline/blueprint.md` and `/outline/beats.md` via the outline backend
before proposing changes — they render the live view.

## Adopting a framework

Propose a `BlueprintPlan`: `framework`, `target_chapter_count`, `genre`, and
`obligatory_scenes`. `framework` must be one of exactly two values — the
only keys in `novelizer.canon.beat_templates.BEAT_TEMPLATES`:

- `"six-position"` — the default conflict-driven shape (catalyst →
  threshold → **midpoint flip** → low point → final turn → climax).
- `"kishotenketsu"` — the conflict-optional shape (ki → shō → **ten**
  ~75% → ketsu); beats and briefs under it must not require
  `expected_polarity` or `value_shift` to be non-empty.

Any other string — including a lowercase-hyphenated guess at one of the
named frameworks below — is not a real key: committing a `BlueprintPlan`
with one silently mints no beats. Committing mints the blueprint id and
the full beat set from the chosen template in one gated action — this is
a structural decision, not something to redo lightly.

Save the Cat, Seven-Point, Three-Act, and Story Circle are **not**
adoptable `framework` values here — they're reference material in
`beat-frameworks.md` for *interpreting* structure and *choosing beats to
hit* within the two shipped templates, showing how the same convergent
skeleton (baseline → disturbance → commitment → pressure → **midpoint
flip** → pressure → **low point** → revelation → **climax** → settled
value) reads at different granularities. Use them to decide, e.g., which
Save the Cat beat a `six-position` chapter brief is functionally hitting —
not to name a `framework` the system doesn't recognize.

`obligatory_scenes` comes from Story Grid's per-genre lists in
`obligatory-scenes.md` (e.g. Love needs lovers-meet/break-up/proof-of-love).
Set these alongside the framework's beats — they're existence checks that
stack on top of, not instead of, the beat positions.

## Rolling-wave briefs

Don't front-load every chapter's brief at adoption time. Draft
`BriefIntent`s (`action="draft"`) a handful of chapters ahead, each carrying
`target_ordinal`, `goal`, `pov_character_id`, `threads_to_touch`,
`beats_to_hit`, `promises_to_progress`, `value_shift`, and
`planned_outcome` (see `scene-sequel/references/outcome-taxonomy.md` for the
outcome literal). As drafting reveals what the story actually needs, use
`action="supersede"` citing the prior brief's `id` rather than mutating it —
the read model keeps the superseded brief as history.

A brief's `beats_to_hit` and `planned_outcome` should be chosen with the
chapter's position on the tension target curve in mind (see
`pacing/references/curve-exemplars.md`) — a chapter landing in a trough
after the midpoint shouldn't be forced into a maximal-tension beat just
because the previous chapter was one.

## Beat targeting

Each `BeatRecord` carries `ideal_pct` and `tolerance_pct` — the beat is
"on target" if fulfilled within that window of the story's actual chapter
count, not at an exact chapter. Use `BeatIntent(action="fulfill", beat_id=,
chapter_id=)` to record which chapter satisfies a beat; passing
`chapter_id=""` clears a stale fulfillment (useful when a supersession or a
chapter reorder invalidates the prior claim).

When reviewing a beat set for drift, check the cross-beat invariants in
`beat-frameworks.md`, not just individual positions: Theme Stated must be
answered by the Finale; the B Story supplies the lesson that resolves the A
story; Midpoint and All Is Lost carry opposite tension polarity. A beat
fulfilled within tolerance but violating one of these relationships is
still a structural problem the Brain should surface.

## Working with the read model

`/outline/blueprint.md`, `/outline/beats.md`, `/outline/threads-plan.md`,
and `/outline/ledger.md` are always readable (even pre-adoption, where they
render "No blueprint adopted."). Read them before drafting new intents so
proposals build on the actual current state rather than a stale mental
model — this matters especially after a supersession, since the backend
always reflects the latest projector state.

## References

- `references/beat-frameworks.md` — the convergent beat map, Save the
  Cat's 15 beats with % positions, Seven-Point, Story Circle, and
  kishōtenketsu, all as tables.
- `references/obligatory-scenes.md` — per-genre obligatory scene lists
  (love, thriller, crime, action).
