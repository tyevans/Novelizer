# Beat frameworks reference

Normalized to manuscript-percent position. Use these tables to populate a
`BlueprintPlan` (`framework`, `target_chapter_count`, `genre`,
`obligatory_scenes`) and the beat set the Plotter mints from
`novelizer.canon.beat_templates.BEAT_TEMPLATES`.

## The convergent beat map

The major frameworks are the same skeleton at different resolutions:

| Position | Save the Cat | 7-Point (Wells) | Three-Act | Story Circle | Function |
|---|---|---|---|---|---|
| 0% | Opening Image | Hook | — | You (comfort) | Baseline value / status quo |
| ~10% | Catalyst | Plot Turn 1 | Inciting Incident | Need | Outside disturbance |
| ~20–25% | Break into Two | (Plot Turn 1) | End Act 1 | Go | Commitment; new world |
| ~25–37% | Pinch 1 | Pinch 1 | — | Search | Antagonist pressure |
| **50%** | **Midpoint** | **Midpoint** | Midpoint | Find | **Reactive→proactive flip; false victory/defeat** |
| ~62–75% | Pinch 2 | Pinch 2 | — | Take | Crushing pressure; loss |
| ~75% | All Is Lost | — | Crisis | — | Lowest point |
| ~80% | Break into Three | Plot Turn 2 | End Act 2 | Return | Revelation → fightback |
| ~88–99% | Finale | Resolution | Climax | Change | Value settled |
| 100% | Final Image | — | Denouement | (changed) | Mirror of opening |

## Save the Cat — 15 beats with canonical %

| Beat | Ideal % |
|---|---|
| Opening Image | 0–1 |
| Theme Stated | ~5 |
| Set-Up | 1–10 |
| Catalyst | ~10 |
| Debate | 10–20 |
| Break into Two | ~20 |
| B Story | ~22 |
| Fun and Games | 20–50 |
| Midpoint | ~50 |
| Bad Guys Close In | 50–75 |
| All Is Lost | ~75 |
| Dark Night of the Soul | 75–80 |
| Break into Three | ~80 |
| Finale | 80–99 |
| Final Image | 100 |

Cross-beat invariants (check these when reviewing a beat set, not just
individual positions):

- Theme Stated must be answered by the Finale.
- The B Story supplies the lesson that resolves the A story.
- Midpoint and All Is Lost carry opposite tension polarity (see
  `pacing/references/curve-exemplars.md` — Midpoint anchors "flip"/high
  tension, All Is Lost is a trough before the climb to climax).

## Seven-Point (Wells)

| Beat | Ideal % |
|---|---|
| Hook | 0 |
| Plot Turn 1 | ~10 |
| Pinch 1 | ~25–37 |
| Midpoint | 50 |
| Pinch 2 | ~62–75 |
| Plot Turn 2 | ~80 |
| Resolution | ~90–100 |

## Story Circle (Dan Harmon, 8 steps)

| Step | Ideal % | Function |
|---|---|---|
| 1. You | 0 | A character in a zone of comfort |
| 2. Need | ~12 | But they want something |
| 3. Go | ~25 | They enter an unfamiliar situation |
| 4. Search | ~37 | Adapt to it |
| 5. Find | 50 | Get what they wanted |
| 6. Take | ~62 | Pay a heavy price for it |
| 7. Return | ~87 | Return to their familiar situation |
| 8. Change | 100 | Having changed |

## Kishōtenketsu

Four-act, conflict-optional. The counterexample to keep in mind when a
beat template's obligatory-conflict assumption doesn't fit: tension comes
from recontextualization at the turn, not from an antagonist.

| Act | Ideal % | Function |
|---|---|---|
| Ki (introduction) | 0–25 | Establish characters, setting, situation |
| Shō (development) | 25–50 | Develop without major conflict |
| Ten (twist) | ~75 | Unexpected element recontextualizes what came before |
| Ketsu (conclusion) | 75–100 | Resolution informed by the twist |

`expected_polarity` and conflict-shaped fields (e.g. `value_shift` on a
`BriefIntent`) must stay optional for this framework — a kishōtenketsu
blueprint's beats can legitimately leave them blank.
