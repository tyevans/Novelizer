# M1 · The Room Assembles — Sub-Milestone Breakdown

M1 is large (all six agents, the scheduler, the autonomy dial + approval queue, and
the full Mission Control TUI). Rather than one monolithic plan, M1 is decomposed into
three sub-milestones, each independently shippable and testable, planned just-in-time.
Each gets its own spec-informed plan in `docs/superpowers/plans/`, executed via
subagent-driven development, reviewed, and merged before the next begins — the same
loop that delivered M0.

Parent milestone in [`../MILESTONES.md`](../MILESTONES.md); end-state design in
[`../superpowers/specs/2026-07-17-novelizer-vision-design.md`](../superpowers/specs/2026-07-17-novelizer-vision-design.md).

## Sub-milestones

| # | Name | Delivers | Done when | Status |
|---|------|----------|-----------|--------|
| M1.1 | **The Room Runs** | Canon retcon events/projection/reads; an append-only `Committer` write-seam; shared `BaseAgent`; the five ported agents (WorldArchitect, CharacterKeeper, Editor, ContinuityChecker, Retconner) on deepagents; the readiness-scored scheduler; Runtime wiring all six; CLI `retcons` | The full pipeline (world → chapter → edit → continuity → retcon) runs unattended in full-auto, proven by a driver test + CLI | ✅ complete |
| M1.2 | **Mission Control** | The multi-pane TUI — activity feed + story browser (chapters/characters/world/retcons) + agent roster strip + status bar — and the command palette | Browse the whole live world/story state from the dashboard | ✅ complete |
| M1.3 | **Autonomy & Approvals** | Proposal + autonomy events/projections/reads; the *gating* `Committer` implementation + `AutonomyPolicy`; approve/reject service; the dial + approval-queue UI; CLI/TUI `autonomy`/`approve`/`reject` | Gate an agent → its output queues as a proposal → approve/reject from the TUI (M1's done-criterion) | ✅ complete |

## Load-bearing design decision

Agents write canon through a **`Committer`** abstraction from M1.1 — a single injected
collaborator, not direct `EventStore.append` calls. In M1.1 the `Committer` simply
appends the real event (full-auto). In M1.3 a gating implementation is swapped in that
either appends the real event or a `proposal.created` event, depending on the
`AutonomyPolicy` — **without touching any agent** (open/closed principle). This is why
the proposal/autonomy plumbing is deferred to M1.3: it is built only where it is used
(YAGNI), and the seam that lets it drop in cleanly is established up front.

## Standing principles (unchanged from M0)

Event sourcing (log is sole source of truth; projections disposable), DDD bounded
contexts (canon / agents / direction speak only via events + read queries), SOLID
(DI at boundaries, extension over modification), red/green TDD black-box-first with
property-based tests for canon invariants, spec + code review as gates.
