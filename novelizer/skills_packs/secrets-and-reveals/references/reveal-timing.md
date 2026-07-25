# Reveal timing: obligation checklist and irony-ledger decoder

Companion to `SKILL.md`. The checklist is the judgement pass to run before
planting a secret or committing a reveal; the decoder is for reading
`/secrets/_dramatic-irony.md` without guessing at its fields.

## Checklist

1. **Asymmetry exists.** At least one character knows and at least one
   doesn't. "Everyone knows" is world-building (a world entry); "nobody
   knows" is backstory you have not written. Recording either as a secret
   creates a row that never closes.
2. **Someone can act on it.** A secret nobody's choices depend on is inert.
   Name the character who acts wrongly for not knowing before you plant.
3. **A plant is not a reveal-in-waiting until it reaches the page.** The
   reader's clock starts at the first `reference` in prose, not at the
   plant. A secret planted and never referenced is invisible to the reader
   and to the irony ledger alike.
4. **Weight the reveal to the gap it closes.** A gap played across many
   live chapters has accumulated expectation and needs a scene; one played
   across a single chapter does not, and revealing it there wastes the
   plant. This mirrors `promise-payoff`'s progress-weight rule.
5. **Reveal before the ignorance stops being credible.** When the character
   in the dark can only stay there because nobody raises the obvious
   question, the reveal is overdue and the withholding has become
   authorial protection.
6. **Declare the window early.** `ResolutionPlanIntent(kind="secret")` with
   `window_lo`/`window_hi` (1-based chapter ordinals; `0/0` clears) lets
   structural review flag reveal drift before the window is missed. This is
   the Plotter's slot; `0` means unset, not chapter zero.
7. **Respect the structural commitments the reveal serves.** A reveal an arc
   pivot or an obligatory scene depends on cannot land after it — see
   `character-arcs/references/arc-invariants.md` and
   `outlining/references/obligatory-scenes.md`.
8. **A reveal is public, not personal.** `reveal` names no character,
   because it makes the secret public rather than recording one person's
   act of knowing; that is `learn`.

## Decoding the ledger

Each entry is one secret. Its header line gives the reader's onset chapter
and whether the secret is marked revealed; then one bullet per character who
was behind the reader.

| Element | Meaning |
|---|---|
| `reader knows from chapter N` | First chapter, in story order, containing a `reference` to this secret. Ordinals are 1-based story positions, so `N` is `/chapters/00N-*.md`. |
| `revealed: yes/no` | Story-world public status. It is a secret-level flag with no chapter attached, so it can close a gap but never place one. |
| `in the dark chapters A-B, learns in chapter C (n ch)` | The character learned on the page in chapter C; the gap ran n chapters. |
| `closed by the reveal, whose chapter is not on record` | The gap ended at the public reveal. Its length is deliberately left unmeasured rather than guessed — the read model does not retain the reveal's chapter. |
| `in the dark from chapter N onward, never learns (n ch)` | Still open through the drafted story. |
| `on page in 4, 7, 9` | The gap's `live_chapters`: where this character appears **and** is still ignorant. The playable scenes. Gaps with none are omitted entirely. |

Notes replace the bullet list when there is no measurable gap, and each says
which case it is: the secret was never referenced in prose; it is marked
revealed but never referenced, so no on-page reader knowledge exists; it was
referenced but with no chapter on record, so the onset cannot be placed; or
every character sharing a scene with it already knew when the reader did.

Characters who never share a scene with a gap are absent by design — listing
the whole cast against every secret would bury the handful of gaps that can
actually be played.

## The inverse: leaks

The ledger's mirror image is the leak detector: a `reference` whose
character has neither learned the secret nor seen it revealed. Ignorance the
ledger reports is an opportunity; a leak is a continuity error, filed for the
Continuity Checker. Repairing one means adding the `learn` that the prose
implies, or changing the prose — never inventing a new secret to cover it.
