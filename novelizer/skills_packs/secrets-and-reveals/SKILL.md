---
name: secrets-and-reveals
description: When withheld knowledge earns its place and how a reveal should be paced — asymmetry, actionability, mystery versus dramatic irony, and reading the irony ledger. Activate when planting a secret, citing one in prose, or planning a reveal window.
---

# Secrets and reveals

A secret here is not "something the reader doesn't know" — it is a
**knowledge asymmetry between characters that someone can act on**.
`SecretPlant` mints one, `SecretCitation` acts on one that exists, and
`ResolutionPlanIntent` plans the window a reveal lands in; the fields each
owes are in `output-conventions/references/schema-conventions.md`. This pack
is about whether a secret deserves to exist and when it is owed a reveal.
Read `/secrets/_dramatic-irony.md` before deciding either.

## Two tests a secret has to pass

**Asymmetry.** Someone knows and someone doesn't. A fact everyone on the
page knows is world-building; a fact nobody knows is unwritten backstory.
Neither is a secret, and recording either as one adds a row that never
closes. The asymmetry *is* the content: "the regent poisoned the king" is a
secret only because of who is standing next to whom when it comes up.

**Actionability.** Someone must behave differently for knowing or not
knowing. If no character's choices change either way the secret is inert —
true, hidden, and dramatically worthless. Ask not "is this interesting" but
"who acts wrongly because they don't know, and who is protected because they
do".

## Mystery and dramatic irony are opposite instruments

In a **mystery** the reader is behind: they know something is withheld and
want it. That works only if the withholding is *felt* — the reader has to
sense a shape they cannot see. Withholding the reader cannot detect is not
suspense, it is a fact you have not written yet, and its reveal lands as
arbitrary: `promise-payoff`'s payoff with no seed.

In **dramatic irony** the reader is ahead: they know, a character doesn't,
and the tension is watching that character walk into it. It is the more
reliable of the two, and the only one the system can measure.

A secret is often both in sequence, and it flips at the first `reference` to
it in prose. Nothing pays before that.

## The irony ledger is an instrument, not a report

`/secrets/_dramatic-irony.md` is derived read-only from canon on every read;
there is nothing to write back. Per secret it gives the chapter where the
READER learned it — the FIRST chapter holding a `reference` on it, since
merely existing counts for nothing — then one line per character still in
the dark from that point. Chapter numbers are story positions.

The field to act on is `live_chapters`: the chapters where that character is
on the page **and** still ignorant, the only scenes where the irony can be
played. A gap with no live chapters is not listed at all — irony that never
shares a scene with its character is not an effect you can use. Each line
also says how the gap closes; `reveal-timing.md` decodes the line grammar
and the notes that stand in for an absent gap.

Use it twice. **Before drafting:** if this chapter is in some gap's
`live_chapters`, you have irony for free — that character can say the
confident wrong thing. **Before revealing:** a gap played across many live
chapters has earned a scene; one played across a single chapter has barely
been played, and revealing it there wastes the plant.

## When a reveal is owed

Once the asymmetry stops generating pressure. The symptoms are in the prose,
not the schema: the character in the dark keeps not acting because acting
would force the reveal; the same near-miss beat repeats; the ignorance has
run long enough to read as authorial protection rather than character
blindness. Held past that, a reveal turns from payoff into anticlimax — the
reader assembled it chapters ago and is waiting for the book to catch up.

The Plotter acts on this in advance through one slot:
`resolution_plan_intents` with `kind: "secret"`, a `window_lo` / `window_hi`
pair declaring roughly when the reveal should land long before the chapter
is chosen. The discipline is `promise-payoff`'s — a reveal an arc turn
depends on must not have a `window_hi` past that pivot
(`character-arcs/references/arc-invariants.md`).

## Capability: who plants, who cites, who paces

Deliberately unequal, and settled by which fields each output schema
carries rather than by a rule to remember. **Author and Editor** carry both
`secret_plants` and `secret_citations` — the only agents that can mint,
because minting belongs to whoever puts the asymmetry on the page. **The
Character Keeper** carries citations only, in practice `learn`: it records
who came to know what, never what there is to know. **The Plotter** carries
neither, and reasons about reveal *timing* only. A field you do not have is
an answer, not an obstacle — hand the observation to the agent whose slot
it is.

## Leaks are the opposite error

A character who `uses` a secret with no prior `learn` or `reveal` covering
them is a leak, reported to the Continuity Checker. The ledger measures
ignorance you meant; a leak is knowledge you did not. Never repair one by
back-dating a plant: add the `learn` for the scene where they actually found
out, or fix the prose.

## References

- `references/reveal-timing.md` — the reveal-obligation checklist and a
  decoder for the irony ledger's gap lines.
