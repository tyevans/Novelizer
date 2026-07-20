---
name: scene-sequel
description: Swain's Scene (goal-conflict-disaster) / Sequel (reaction-dilemma-decision) chain, MRUs, and chapter hooks, mapped to BriefIntent.planned_outcome. Activate when drafting a chapter brief's outcome or reviewing scene-level pacing.
---

# Scene / Sequel

Swain's unit-level craft model underneath a chapter brief: a proactive
**Scene** runs goal → conflict → disaster; a reactive **Sequel** runs
reaction → dilemma → decision. The chain check is what makes a sequence of
chapters cohere rather than read as disconnected incidents: each Sequel's
`decision` should seed the next Scene's `goal`. `BriefIntent.goal` and
`BriefIntent.planned_outcome` are where this shows up in the system.

## The outcome literal

`BriefIntent.planned_outcome` is `"" | yes | yes_but | no_and | no` — this
is Swain's "disaster" (or its absence) made explicit and checkable. See
`outcome-taxonomy.md` for the full table; in short: `yes_but` and `no_and`
are the workhorse outcomes that both resolve a goal and generate the next
problem, `yes` is a rare tension release best saved for minor goals or the
finale, and `no` is the sparest outcome — a flat failure needs a Sequel
right after to do the reflective work or the story just stalls.

## MRUs (motivation-reaction units)

Below the Scene/Sequel level, prose alternates stimulus (motivation, what
happens) and response (reaction — feeling, reflex, speech/action, in that
order). This is a prose-craft discipline more than a data-modeled one in
this system, but it's the reason a Sequel's `reaction → dilemma →
decision` should be drafted in that order, not compressed: skipping
straight to `decision` without the character processing the `reaction`
first is what makes reactions read as mechanical.

## Chapter hooks and cadence

A chapter ending on a Scene's `disaster` (a `no_and` or a hard `no`) is a
natural hook — the reader wants to see the fallout. A chapter ending mid-
Sequel, on the `decision`, is a softer hook — the reader wants to see the
new goal enacted. Neither is wrong, but a book that hooks the same way
every chapter (all disaster-cliffhangers, or all decision-into-next-goal)
reads mechanically; vary it.

Cadence: a long run of Scene-outcomes (`yes_but`/`no_and`/`no`) with no
Sequel in between is breathless — nothing but escalating conflict with no
character processing. A long run of Sequels with no Scene resolving them
is saggy — reflection without forward motion. Use `beats_to_hit` and
`value_shift` on consecutive briefs to check this isn't happening across a
run of chapters, not just within one.

## Value shift ("every scene turns")

Independent of the outcome literal, `BriefIntent.value_shift` should
record a polarity flip on some tracked value across the chapter (Story
Grid's "every scene turns" — the single most automatable pacing check). A
chapter that resolves `yes_but` but leaves `value_shift` blank or static
is very likely exposition rather than a Scene or Sequel — worth a second
look before committing the brief.

## Drafting order

When drafting a `BriefIntent`, decide the outcome first (what kind of
Scene/Sequel is this, and does it chain from the prior chapter's
`decision`), then the `value_shift` (what actually turns), then fill
`beats_to_hit`/`promises_to_progress` against that shape rather than the
reverse — starting from beats-to-hit and backfilling an outcome tends to
produce a `planned_outcome` that doesn't match what the beat actually
needs.

## References

- `references/outcome-taxonomy.md` — the yes / yes_but / no_and / no
  table with full usage guidance.
