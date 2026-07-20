---
name: promise-payoff
description: Managing setups and payoffs across the story via PromiseIntent — Chekhov's gun, red herrings, reveal sequencing, and payoff-window discipline. Activate when declaring, progressing, paying off, or releasing a promise.
---

# Promise / progress / payoff

Sanderson's promise → progress → payoff model, generalized into a ledger of
setups and payoffs (Chekhov's gun, made systematic): every setup that isn't
a deliberate red herring must be paid, and every payoff must have been
seeded earlier. `PromiseIntent` is the vocabulary — `make` mints a promise,
`progress`/`pay`/`release` cite an existing `id`. Read `/outline/ledger.md`
for the live view before declaring or resolving anything.

## The three actions past `make`

- `progress` — the promise is developed further without resolving. Use
  this to signal "this still matters" between the seed and the payoff;
  heavily-progressed promises read as more important and need a
  proportionally weightier payoff scene, not a throwaway line.
- `pay` — the promise resolves. This is the default terminal action for
  `foreshadow` and `plant` kinds.
- `release` — the promise is dropped without payoff. Reserved for
  `red_herring`-kind promises; releasing a `plant` or `foreshadow` this
  way is a broken promise unless the story is deliberately abandoning it
  (rare, worth flagging).

## Chekhov's gun, generalized: the kind taxonomy

`PromiseIntent.kind` is `foreshadow | plant | red_herring`.
`foreshadow` is a hint without a concrete object yet; `plant` is a
concrete object, fact, or ability seeded for exact later use — the payoff
scene should use precisely what was planted, no more; `red_herring` is a
deliberate false lead, sanctioned to end in `release` instead of `pay`.
This taxonomy is what makes an unpaid setup a bug (`foreshadow`/`plant`)
versus a feature (`red_herring`) — see `ledger-checklist.md` for the full
checklist this skill runs against.

## Reveal sequencing and window discipline

A payoff shouldn't fire before its promise has had at least one `progress`
beat, for `foreshadow`/`plant` kinds with a long runway — pure surprise
with no groundwork reads as arbitrary, and pure red herrings are the
deliberate exception. Use `window_lo`/`window_hi` (1-based chapter
ordinals, 0 = unset) to declare an intended payoff window as soon as you
know roughly when a promise should resolve, even before you know exactly
which chapter — this lets structural review flag drift before the window
is actually missed, not after.

Windows should respect the story's other structural commitments: a promise
tied to a character's arc resolution shouldn't have a `window_hi` past
that arc's climax pivot (see
`character-arcs/references/arc-invariants.md`); a promise underpinning an
obligatory scene (see `outlining/references/obligatory-scenes.md`)
shouldn't outlive the chapter where that scene needs to land.

## Working the ledger during drafting

Before drafting a `BriefIntent`, check `promises_to_progress` against
`/outline/ledger.md` — cite existing promise ids rather than letting a
chapter touch a thread's promises implicitly. When a chapter's outcome
(`planned_outcome`, see `scene-sequel/references/outcome-taxonomy.md`) is
`yes` or `yes_but`, that's often the natural moment for a `pay` or
`progress` on the promise driving that goal.

## References

- `references/ledger-checklist.md` — the full setup/payoff checklist:
  kind taxonomy, symmetric setup↔payoff requirement, red-herring handling,
  progress-weight guidance, and window discipline.
