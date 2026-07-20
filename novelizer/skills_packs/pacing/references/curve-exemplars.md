# Tension target curve reference

Kept consistent with `novelizer/brain/tension_target.py` — read that module
directly if these numbers ever look stale; it is the source of truth, this
file explains what the numbers mean.

## Anchor semantics

`target_curve()` builds a per-chapter target tension curve by interpolating
between anchors. Anchors come from two sources:

1. Every beat with a non-empty `expected_polarity` contributes an anchor at
   its ideal chapter ordinal (`round(ideal_pct * n)`, clamped to `1..n`):

   | `expected_polarity` | Target tension |
   |---|---|
   | `"up"` | 0.75 |
   | `"down"` | 0.35 |
   | `"flip"` | 0.85 |

2. Implicit start/end anchors, used only where no beat already anchors that
   ordinal:

   | Ordinal | Implicit target |
   |---|---|
   | 1 (first chapter) | 0.3 (gentle open) |
   | n (last chapter) | 0.5 (resolution isn't zero-tension) |

Between anchors, the curve is linear interpolation. A single-anchor curve
(degenerate: only one polarity-bearing beat, or a one-chapter blueprint) is
flat at that one value.

## Reading the curve

- **Rising sawtooth, not a monotonic ramp.** Individual chapters oscillate
  around the interpolated line — the check (`tension_deviations`) is
  against the *target*, not a requirement that tension only ever increases.
- **Global max at the midpoint.** In the shipped `six-position` template,
  Midpoint is a `"flip"` beat (0.85) at ~50% — the reactive→proactive turn
  is the highest point on the curve, matching the convergent beat map's
  midpoint flip (see `outlining/references/beat-frameworks.md`).
- **Climax rides lower, at 0.75.** The shipped template's Climax beat is
  `"up"` (0.75) at ~90% — a real peak, but below the midpoint's 0.85, not
  above it. Don't assume the climax must out-tense the midpoint; this
  template deliberately doesn't ask for that.
- **Deliberate troughs after peaks.** Low Point is a `"down"` beat (0.35)
  at ~75% — shortly after the midpoint's high, before the climb back up
  through Final Turn to Climax. That dip is breathing room, not a mistake
  — don't chase the curve upward everywhere.
- **Escalating try-fail stakes**, not escalating raw tension every chapter —
  the curve tolerates dips; `tension_deviations(delta=0.25)` only flags
  chapters whose *scored* tension diverges from the target by more than the
  delta, in either direction (too flat during a rising stretch, or too
  spiky during a trough).

## Practical use when drafting a brief or reviewing pacing

- Before drafting a brief for a chapter, check where its ordinal sits on
  the target curve (gentle open near 0.3, cresting at 0.85 at the
  midpoint, dipping to 0.35 at the low point, a lower 0.75 peak at the
  climax, settling to 0.5 at the end) and let that inform the chapter's
  `value_shift`/`planned_outcome` choice — a chapter sitting in a trough
  shouldn't be forced into a `no_and` disaster just because the last
  chapter was one.
- POV cadence and congestion (braiding multiple threads) interact with
  this curve: don't stack every thread's crisis beat in the same trough or
  peak chapter unless the blueprint's beats genuinely call for
  convergence there.
