# Character arc invariants (Weiland)

Internal architecture: ghost/wound → lie believed → want (external,
expresses the lie) vs need (accept the truth = ¬lie). These fields map
directly onto `ArcIntent`'s `ghost`, `lie`, `truth`, `want`, `need`.

## Arc type → start/end belief table

| Arc | Start | End | Rule |
|---|---|---|---|
| `positive` | Lie | Truth | Want sacrificed for Need |
| `flat` | Truth | Truth | Changes the world, not self |
| `disillusionment` | Lie | bleak Truth | Truth won, tragically |
| `fall` | Lie | worse Lie | Rejects available Truth |
| `corruption` | Truth | Lie | Abandons held Truth |

## Arc type → outcome table

`ArcIntent.outcome` (set via `resolve`) must match the arc's `arc_type`:

| `arc_type` | Valid `outcome` |
|---|---|
| `positive` | `truth_embraced` |
| `flat` | `world_changed` |
| `disillusionment` | `truth_tragic` |
| `fall` | `lie_embraced` |
| `corruption` | `lie_embraced` |

An arc resolved with an outcome that doesn't match its declared type is a
adjudication error — supersession (see below) is the fix, not silently
changing the outcome to fit.

## Pivot-to-beat mapping

Arc pivots (`ArcIntent.action == "plan_pivot"`, citing a `beat_id`)
co-locate with structural beats:

| Structural position | Arc pivot |
|---|---|
| Midpoint (~50%) | First truth-glimpse — the character sees the truth but isn't ready to act on it |
| ~75% (All Is Lost / Pinch 2) | The lie's maximal cost — the want, pursued at the truth's expense, costs the most here |
| Climax (~88–99%) | The final lie/truth decision — the moment that fixes the arc's outcome |

A `plan_pivot` whose `beat_id` doesn't land near one of these ideal
positions (see `outlining/references/beat-frameworks.md`) is a candidate
for `advance` (a real but minor movement) rather than a load-bearing pivot.

## Supersession-as-adjudication

Arcs are declared once (`declare` mints; `id` is stable). When later
narrative decisions contradict an earlier `plan_pivot` or the arc's
trajectory no longer supports its declared `outcome`, the correct move is
a new `plan_pivot`/`advance` sequence that supersedes the stale plan in
read-model terms — never mutate history. `resolve` is terminal: once an
arc is resolved, only a new arc (a fresh `declare`, e.g. for a sequel or a
retcon) can revisit that character's internal architecture.

Stagnation check: an arc with a `declare` and no `advance`/`plan_pivot`
activity across a large span of chapters is stalled — the Brain should be
able to flag arcs that haven't moved in N chapters the same way it flags
threads gone dark.
