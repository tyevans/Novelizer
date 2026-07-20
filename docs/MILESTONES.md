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

## Phase 2 — The Room Authors (M6–M11)

Phase 1 built a room that *writes*: chapters emerge, and the Story Brain
checks what emerged. Phase 2 makes authorship the core of the system: the
room adopts a shape, plans the pace of its own payoffs, drafts against that
plan, measures the drift, and re-plans — until the book **lands**. Design:
[`2026-07-19-authoring-skills-blueprint-design.md`](superpowers/specs/2026-07-19-authoring-skills-blueprint-design.md)
(+ research notes in `specs/assets/`); M6 implements the already-approved
[`2026-07-19-canon-pull-tools-design.md`](superpowers/specs/2026-07-19-canon-pull-tools-design.md).

The final goal, and Phase 2's definition of done: **a seeded world becomes a
finished novel** — beats fulfilled, promises paid, arcs resolved, an ending
that was steered toward rather than stumbled into — with the Director
watching the whole descent from Mission Control.

Every milestone remains a vertical slice (events → projections → intents →
brain → TUI), ends runnable and watchable, and leaves the room writing a
measurably better book than the milestone before.

| # | Name | Delivers | Done when | Status |
|---|------|----------|-----------|--------|
| M6 | **Deep Read** | Canon pull tools wired (§4 of pull-tools spec): `CanonBackend` + `search_canon` in Author/Checker/chat runners, push diet (chapter maps replace excerpts), tool telemetry in the Engine Room | Watching the Engine Room, the Author greps chapter 3 while drafting chapter 40; a chat persona answers a continuity question by reading the actual chapter | ✅ complete (delivered via CPT-M4/M5/M6 ladder + write_todos Author-only scoping; see docs/superpowers/plans/2026-07-19-canon-pull-tools-milestones.md) |
| M7 | **The Ledger** | Promise ledger (`promise.*`), planned resolution windows (`thread.resolution_planned`, `secret.reveal_planned`), `ledger.py` + `resolution_pacing.py` faculties, ledger + window badges on the Thread Board, `PromiseIntent`/`ResolutionPlanIntent` for Author/Checker, windows settable via Director signals | A planted Chekhov's gun unpaid past its window raises an alarm and the Author pays it off unprompted; the Director sees three threads congesting into the same resolution window before it happens | ✅ complete (pending review; checker prose-mining of promises, canon_fs rendering, and search kind deferred to M8; windows set via `plan-resolution`/`plan-reveal` CLI commands) |
| M8 | **The Blueprint** | Blueprint/beat/brief aggregates, the Plotter agent (rolling briefs 1–3 ahead), Author drafts against briefs, `beat_drift.py` + `tension_target.py`, Outline board tab (threads × chapters grid with beat markers), `OutlineBackend` (`/outline/`) | The Director approves a proposed blueprint; briefs march ahead of the draft on the Outline board; a "midpoint late" alarm fires and the Plotter visibly re-plans the next briefs | ✅ complete (checker prose-mining of promises + `search_canon` promise/brief kinds remain deferred — tracked for M9/M10) |
| M9 | **Arcs** | Character arc aggregate (ghost/lie/want/need, five types), `ArcIntent` for the CharacterKeeper, `arc_alignment.py`, Arcs tab with pivots pinned to beats | A declared fall arc that resolves `truth_embraced` raises a contradiction alarm for the Director to adjudicate; a stagnant arc surfaces and the Plotter routes its character into the next brief | ✅ complete (relationship arcs + per-advance pivot history deferred; checker promise-mining, search kinds, and the orphaned-pivot re-pin finding all delivered in M10) |
| M10 | **Craft** | SKILL.md craft packs (outlining, promise-payoff, character-arcs, scene-sequel, pacing) with progressive disclosure, writable `/workspace/` (StateBackend) for think-in-files deliberation, chat personas get craft access | The Engine Room shows a skill activate and a beat-table reference pulled mid-plan; the Director asks a persona "how should I pace this reveal?" and gets an answer grounded in the ledger and the adopted framework | ✅ complete (pending review; live skill-activation + /workspace/ write smoke require a running agent graph — deferred to the acceptance run) |
| M11 | **Landing** | Endgame steering: completion criteria over the blueprint (beats fulfilled ∧ promises paid/released ∧ arcs resolved), `blueprint.retargeted` flow when the book runs long/short, finale-window convergence notes, `book.completed`, Frame step in the setup wizard (blueprint adoption at story start), Outline board as the home Brain tab | A seeded story runs to a **finished** novel and the system declares it done; a stranger reads a book with a beginning, a middle, and an *ending* | planned |

Dependencies: M6 before M8 (the Plotter is born with pull tools); M7 needs
only intents and can proceed in parallel with M6; M9/M10 build on M8; M11
caps the phase. A story with no blueprint must keep running exactly as in
Phase 1 at every step — bottom-up remains a supported mode, it just stops
being the only one.

## Standing principles (all milestones)

- Event sourcing: the log is the sole source of truth; projections are disposable.
- DDD: four bounded contexts (World Canon, Agent Roster, Story Brain, Direction) speak only through domain events and read-side queries.
- SOLID at module boundaries; extension over modification.
- Red/green TDD, black-box first; parametrized and property-based (Hypothesis) where invariants generalize.
- Spec and code review are gates, not suggestions.
