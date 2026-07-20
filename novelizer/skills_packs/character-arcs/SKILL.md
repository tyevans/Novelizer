---
name: character-arcs
description: Weiland's ghost/lie/want-vs-need arc architecture via ArcIntent — the five arc types, pivot-to-beat mapping, and supersession as the fix for stale plans. Activate when declaring, pivoting, advancing, or resolving a character arc.
---

# Character arcs

A character arc is internal architecture, not a vibe: a `ghost` (wound),
the `lie` it produced, the external `want` that expresses the lie, and the
internal `need` — accepting the `truth` — that the story's events press
the character toward or away from. `ArcIntent` carries all five fields
plus `arc_type`, `beat_id` (for pivots), and `outcome` (for resolution).

## Declaring an arc

`ArcIntent(action="declare", character_id=, arc_type=, ghost=, lie=, truth=,
want=, need=)` mints the arc id. Fill `ghost`/`lie`/`truth`/`want`/`need`
concretely — "wants approval" is a want, not a lie; the lie is the false
belief the want serves (e.g. "I'm only safe if everyone likes me"). Vague
or missing fields here are the most common reason later pivots feel
unmotivated.

## The five arc types

Every arc's `arc_type` fixes its start/end belief trajectory and, later,
which `outcome` values are valid on `resolve` — see the full table in
`arc-invariants.md`. Summary:

| `arc_type` | Trajectory | Valid `outcome` |
|---|---|---|
| `positive` | Lie → Truth, want sacrificed for need | `truth_embraced` |
| `flat` | Truth → Truth, changes the world not the self | `world_changed` or `truth_embraced` |
| `disillusionment` | Lie → bleak Truth, truth wins tragically | `truth_tragic` |
| `fall` | Lie → worse Lie, rejects available truth | `lie_embraced` |
| `corruption` | Truth → Lie, abandons a held truth | `lie_embraced` |

Choose the type at declaration time based on what the story is actually
built to deliver — a `flat` arc declared for a character who's clearly
meant to change internally will make every later `plan_pivot` feel like a
category error.

## Pivot-to-beat mapping

`plan_pivot` (citing an existing `beat_id`) should land near one of three
structural positions, cross-referenced against
`outlining/references/beat-frameworks.md`:

- **Midpoint (~50%)** — first truth-glimpse; the character sees it but
  isn't ready to act.
- **~75% (All Is Lost / Pinch 2)** — the lie's maximal cost; the want,
  pursued at the truth's expense, costs the most here.
- **Climax (~88–99%)** — the final lie/truth decision, which fixes the
  arc's eventual `outcome`.

A pivot whose beat doesn't land near one of these is probably better
logged as `advance` — a real but minor movement — rather than a
load-bearing `plan_pivot`. Reserve `plan_pivot` for movements that should
be checkable against the beat map.

## Resolving, and supersession as adjudication

`resolve` sets the terminal `outcome`, which must match the arc's
`arc_type` per the table above — an arc resolved with a mismatched outcome
is an adjudication error, not a matter of picking whichever outcome sounds
better. `resolve` is terminal: after it, only a new `declare` (a fresh arc,
e.g. for a sequel) can revisit that character's internal architecture.

Arcs are declared once and never mutated in place. When a later narrative
decision contradicts an earlier `plan_pivot`, or an arc's trajectory no
longer supports its declared outcome, the fix is a new
`plan_pivot`/`advance` that supersedes the stale plan in the read model —
never retcon the earlier intent itself. This is the same
supersession-not-mutation discipline the rest of the system uses for
briefs and blueprints.

Watch for stagnation: an arc with a `declare` and no `advance`/`plan_pivot`
across a long span of chapters has stalled, the same way a thread gone
dark is a Brain-flaggable condition.

## References

- `references/arc-invariants.md` — the arc type → outcome table, the
  start/end belief table, and the full pivot-to-beat mapping.
