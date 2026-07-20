---
name: pacing
description: Tension curves, breathing room, POV cadence, escalation, and thread congestion, anchored to brain/tension_target.py's polarity targets. Activate when reviewing pacing, choosing a chapter's value_shift, or investigating a tension-deviation flag.
---

# Pacing

Pacing in this system is checkable, not impressionistic: `novelizer.brain
.tension_target` builds a per-chapter target tension curve from beat
polarities and compares each chapter's scored tension against it. This
skill is about drafting and reviewing chapters with that curve in mind —
not fighting it, but understanding what it's telling you when a chapter
deviates.

## The tension target curve

Every beat with a non-empty `expected_polarity` (`"up"`, `"down"`,
`"flip"`) anchors the curve at its ideal chapter ordinal, at 0.75, 0.35,
and 0.85 tension respectively. Implicit anchors bracket the story: chapter
1 defaults to 0.3 (gentle open), the last chapter to 0.5 (resolution isn't
zero-tension) — unless a beat already claims that ordinal. Between
anchors, the curve is linear interpolation; see `curve-exemplars.md` for
the full table and how to read it.

Practically: when choosing a chapter's `beats_to_hit` and
`planned_outcome` (see `scene-sequel/references/outcome-taxonomy.md`),
check where that chapter's ordinal sits on the curve. A chapter in a
trough right after a `"down"`-anchored beat doesn't need to escalate —
forcing a `no_and` disaster into a chapter the curve expects to be calm
produces a spiky, exhausting stretch rather than real pacing.

## Breathing room

Troughs are deliberate, not gaps to fill. A `"down"` beat placed shortly
after a peak (e.g. right after Midpoint, or through a Dark Night of the
Soul before the climb to Break into Three) is where the reader and the
characters process what just happened. Don't chase the curve upward on
every chapter — `tension_deviations` only flags chapters that diverge from
the *target*, in either direction, so an under-tense chapter during a
trough is correct, not a deficiency.

## Escalation shape

The curve is a rising sawtooth, not a monotonic ramp: a local peak at the
midpoint (0.75, `"up"`), then a climb to the global max near the climax
(0.85, `"flip"`, typically around Break into Three / Plot Turn 2 at
~80%). Escalating try-fail stakes across Scene/Sequel chains (see
`scene-sequel` pack) should track this shape — each `no_and` or
`yes_but` should generally cost more than the last one as the story
climbs toward that global max, then release toward the 0.5 implicit end
anchor.

## POV cadence and congestion

POV thread and plotline are separate axes — balancing POV cadence
(alternating or intentionally weighting POV chapters) is not the same
check as keeping every thread's resolution windows honored (see
`promise-payoff/references/ledger-checklist.md` for window discipline).
Two congestion failure modes to watch for when reviewing a stretch of
chapters:

- **Drought** — a thread or POV character goes dark beyond a reasonable
  chapter gap. Check `/outline/threads-plan.md` for last-touched
  chapters.
- **Congestion** — too many threads' crisis or resolution beats stacked
  into the same chapter or the same tension-curve trough/peak, unless the
  blueprint's beats genuinely call for convergence there (e.g. a real
  midpoint convergence of subplots is fine; an accidental pileup during
  what should be a breathing-room chapter is not).

## When a chapter deviates from the target

A `tension_deviations` flag (actual vs. target beyond `delta`) is a
prompt to check three things in order: is the chapter's `value_shift`
doing real work (see `scene-sequel` pack's "every scene turns"); is a
beat's `expected_polarity` actually anchoring the ordinal you think it is
(check `/outline/beats.md`); and is the deviation actually wrong, or is
this a legitimate exception (e.g. an early character-establishing chapter
that's meant to run hotter than the gentle-open default). Not every
deviation is a bug — but an unexplained one usually means the brief's
outcome and the beat map have drifted apart.

## References

- `references/curve-exemplars.md` — the anchor semantics table (kept
  consistent with `tension_target.py`'s 0.75/0.35/0.85 values) and how to
  read the curve when drafting or reviewing.
