# Setup–payoff ledger checklist

Generalizes Chekhov's gun and Sanderson's promise → progress → payoff into
the `PromiseIntent` vocabulary (`make` / `progress` / `pay` / `release`).

## Kind taxonomy (`PromiseIntent.kind`)

| Kind | Meaning | Payoff obligation |
|---|---|---|
| `foreshadow` | A hint of something to come, not yet a concrete object/fact | Must eventually be paid (`pay`) |
| `plant` | A concrete object, fact, or ability seeded for later use | Must eventually be paid; the payoff scene should use exactly what was planted, no more, no less |
| `red_herring` | A deliberate false lead | May be `release`d instead of paid — this is the sanctioned subversion, not a bug |

## Checklist

1. Every non-`red_herring` promise (`foreshadow`, `plant`) has a later
   `pay`. An unpaid `foreshadow`/`plant` past the story's end (or past its
   `window_hi` if set) is a dangling promise.
2. Every `pay` cites a promise that was actually `make`d earlier in story
   order — no payoff appears without a prior seed (no deus ex machina).
   This is symmetric with #1: setups need payoffs, payoffs need setups.
3. `release` is the only terminal action that isn't a `pay` — reserve it
   for `red_herring`-kind promises whose subversion is the point. Releasing
   a `plant` or `foreshadow` without payoff is a broken promise, not a
   red herring, unless the story explicitly means to abandon it (rare;
   flag for review).
4. Heavily-`progress`ed promises (several `progress` actions logged) read
   as more important to the reader — they need proportionally weightier
   payoffs. A promise progressed five times and paid off in one throwaway
   line under-delivers.
5. Use `window_lo`/`window_hi` (1-based chapter ordinals) to declare an
   intended payoff window when the payoff chapter isn't chosen yet — this
   lets the Brain flag drift before the payoff is late, not just after.
6. Reveal sequencing: a payoff should not fire before its promise has had
   at least one `progress` beat for foreshadow/plant kinds with a long
   runway — pure surprise with no groundwork reads as arbitrary. Red
   herrings are the exception: their whole function is an unearned near-miss.
7. Window discipline: don't let a promise's `window_hi` slide past the
   story's obligatory-scene or arc-resolution beats it's meant to support
   (see `outlining/references/obligatory-scenes.md`,
   `character-arcs/references/arc-invariants.md`) — a promise tied to a
   character's arc resolution should pay no later than that arc's climax
   pivot.

Read `/outline/ledger.md` (via the outline backend) for the live view of
open, progressed, and paid promises before declaring new ones.
