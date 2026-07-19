# Novelizer Milestones

Roadmap to the end-state product described in
[`docs/superpowers/specs/2026-07-17-novelizer-vision-design.md`](superpowers/specs/2026-07-17-novelizer-vision-design.md).
Spine-first vertical slices: every milestone ends runnable and watchable. Each milestone
gets its own spec → plan → red/green TDD → code review cycle before the next begins.

| # | Name | Delivers | Done when | Status |
|---|------|----------|-----------|--------|
| M0 | **Heartbeat** | Event-sourced store (EventStore/Projector/ReadStore), Author on deepagents via OpenAI-compat endpoint, skeletal TUI feed tailing the log | Run `novelizer`, watch chapters appear live | ✅ complete |
| M1 | **The Room Assembles** | All six agents on deepagents, readiness scheduler, autonomy dial + approval queue, full Mission Control layout | Room runs unattended; gate/approve/seed/browse from TUI | ✅ complete |
| M2 | **Voices** | TOML voice packs (prose profiles + agent personalities), The Room drill-in view, character voice cards v1 | Recasting an agent visibly changes the feed; switching prose profile changes the next chapter | ✅ complete |
| M3 | **Shape & Threads** | Story brain phase 1: structure + thread faculties, Story Shape + Thread Board views, brain context in agent prompts | A stale thread surfaces and the Author picks it back up unprompted | ✅ complete |
| M4 | **Knowledge & Cause** | Story brain phase 2: who-knows-what matrix + causal graph, Who-Knows-What + Causeway views, upgraded Continuity Checker | A planted knowledge leak is auto-caught and routed to the retcon queue | ✅ complete (CI-proven; live end-to-end smoke deferred — see M4 doc) |
| M5 | **Finish** | Theme tracking, voice enforcement maturity, UX polish, performance, packaging, docs | A stranger installs it, casts a room, seeds a world, reads a coherent novella a day later | ✅ complete (walkthrough steps 1–4 executed live, incl. prose-mining + voice-drift live smokes; the day-long run + human coherence read handed to the user — see M5 doc closeout) |

## Standing principles (all milestones)

- Event sourcing: the log is the sole source of truth; projections are disposable.
- DDD: four bounded contexts (World Canon, Agent Roster, Story Brain, Direction) speak only through domain events and read-side queries.
- SOLID at module boundaries; extension over modification.
- Red/green TDD, black-box first; parametrized and property-based (Hypothesis) where invariants generalize.
- Spec and code review are gates, not suggestions.
