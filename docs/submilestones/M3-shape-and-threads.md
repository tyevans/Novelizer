# M3 · Shape & Threads — Sub-Milestone Breakdown

M3 gives the room its first faculty of story understanding: the Story Brain, a bounded
context that derives narrative intelligence from canon events and feeds it back in two
directions — into agent prompts (stale threads, pacing flags) and into the TUI (Story
Shape, Thread Board). Like M1 and M2, it's decomposed into just-in-time sub-milestones,
each independently shippable and testable, planned one at a time and executed via
subagent-driven development (spec-informed plan → fresh-subagent-per-task → two-stage
review → whole-branch review → merge).

Parent milestone in [`../MILESTONES.md`](../MILESTONES.md); end-state design (Story Brain
faculties, event domains, view priority) in
[`../superpowers/specs/2026-07-17-novelizer-vision-design.md`](../superpowers/specs/2026-07-17-novelizer-vision-design.md).

Phase 1 covers the **Structure** and **Threads** faculties only. Knowledge (`secret.*`,
Who-Knows-What) and Causality (`Causeway`) are M4. Theme/motif tracking is M5 and lives in
the Story Browser, not as a fifth Brain view.

## Sub-milestones

| # | Name | Delivers | Done when | Status |
|---|------|----------|-----------|--------|
| M3.1 | **Thread ledger** | New `thread.*` event domain (`planted`, `touched`, `paid_off`, `abandoned`) in `novelizer/canon/events.py`; Author/Editor structured output gains an optional `thread_intents` field (deterministic, agent-declared) that `work()` turns into `committer.commit(...)` calls alongside the existing `chapter.created`/remark commits; a `ThreadsProjection` (new table via the Projector) rebuilding thread state (`planted → touched* → paid_off\|abandoned`) from the log; `ReadStore.list_threads()` / `get_thread()`. **Thread identity**: a thread id is minted only at `planted` time — the Author names the thread freeform in prose and the system slugs that name into the aggregate_id; `touched`/`paid_off`/`abandoned` intents must reference an id drawn from the active-thread list already provided in the agent's context (the Author never invents an id when touching an existing thread) — an intent naming an unknown id is dropped with a logged warning and no event is committed. **Autonomy**: `thread.*` event types are classified as analysis/feed-flavor, added to `AutonomyPolicy._NEVER_GATED` alongside `agent.remarked`, so thread commits never enter the proposal queue. | Author declaring a thread intent in its structured output results in a `thread.touched` event in the log (never gated, confirmed by a test asserting `AutonomyPolicy.is_gated` is `False` for every `thread.*` type) and an updated row in the threads read table after `catch_up()`; a Hypothesis property test asserts the state machine holds under any valid event sequence replay, including the absorbing-terminal-state contract (see Load-bearing design decisions) | ✅ complete |
| M3.2 | **Staleness & pacing analysis** | Deterministic `StalenessAnalyzer` (pure function over `ReadStore` thread + chapter data: a thread is stale once **3 chapters** have elapsed since its last `planted`/`touched` event with no terminal event in between; the threshold is a named constant, not yet user-configurable) — no LLM involved, fully unit-testable. `annotation.*` event domain, single event type `annotation.structure_scored`, with a bounded numeric payload (`tension: float 0.0–1.0`, `pacing_label: str`) emitted by a new lightweight **Structure Analyst** scheduled agent that reads recent chapters and asks the LLM for a score/label per chapter; sag/spike detection is a pure function over the emitted scores, not an LLM call. **Autonomy**: `annotation.structure_scored` is likewise added to `AutonomyPolicy._NEVER_GATED` — it's an analysis artifact, not a canon-changing proposal. Analyst wired into `novelizer/scheduler.py` / `novelizer/runtime.py` alongside the six existing agents; its `readiness()` is proportional to the count of unscored recent chapters and returns `0.0` when there are none, so it never wins the readiness race with nothing to do, and it has its own `ready_for_interval` cadence like the other agents. M3.2's tests drive `analyst.run_once()` directly (the established agent-test pattern) rather than relying on `Scheduler.tick()` to pick it. | Seeding a fixture with 4 chapters and no `thread.touched`/`thread.planted` in the last 3 makes `StalenessAnalyzer` report the thread stale (unit test, no LLM, no scheduler involved); calling `analyst.run_once()` against a fixture with an artificially flat chapter produces an `annotation.structure_scored` event and the sag-detection pure function flags it; a test confirms `annotation.structure_scored` is never gated | ✅ complete |
| M3.3 | **Story Shape & Thread Board views + brain context injection** | `novelizer/tui/widgets/story_shape.py` (renders per-chapter tension/pacing scores + flagged sag/spike, reading `annotation.structure_scored` rows) and `novelizer/tui/widgets/thread_board.py` (renders threads by state; stale ones highlighted by calling the *same* `StalenessAnalyzer` function from M3.2 at render time via a small `ReadStore`-backed helper — staleness is never persisted as a projection field or recomputed with separate logic, so the Thread Board and the brain context injected into prompts can never disagree) wired into `NovelizerApp.compose()`; a small `BrainContext` provider (analogous to the M2 voice provider) that Runtime builds from `ReadStore` queries and hands to Author/Editor as an additional optional constructor param, following the exact M2 pattern in `novelizer/agents/author.py`/`editor.py` (conditional string appended in `work()`/`_summarize()` only when non-empty, byte-identical output when the brain has nothing to report) — Author sees a "stale threads" note (including the thread ids it's allowed to reference per M3.1's identity rule), Editor sees pacing flags. | Two-part done-when (see below): (a) a CI-verifiable mechanical chain, and (b) a live-LLM smoke check that is the milestone's *true* observation, per M1/M2 precedent. | ⬜ not started |

### M3.3 done-when, in full (this is the milestone done-when)

**(a) CI-verifiable mechanical chain** — proves the plumbing, not LLM judgment: seed a
thread and enough chapters that `StalenessAnalyzer` (M3.2) flags it stale → assert the
`BrainContext` string built for the Author contains that thread's name/id (asserted on the
literal prompt text, not agent behavior) → drive the Author with a `FakeRunner` preset to
return a draft whose structured output declares a `thread_intents` entry touching that
exact id → assert the resulting `thread.touched` event lands via the `Committer` → assert
the Thread Board's render-time helper no longer reports the thread stale. This runs in CI,
with no live model call, and is a normal black-box test in the existing agent-test style.

**(b) Live-LLM smoke check** — the actual claim ("the Author, unprompted, picks a stale
thread back up"): a `live_llm`-marked test (or documented manual run), following the M1/M2
precedent for behavior that depends on real model output, that seeds the same stale-thread
fixture, runs the real Author against the injected `BrainContext`, and confirms it reacts
by including a matching thread intent — with no director signal and no manual prompt
beyond what the room already injects. **CI cannot prove this causality** — a
`FakeRunner`-driven test only proves the pipe is connected, not that an LLM will act on
what flows through it. The live_llm-marked check is the true done-when observation for M3;
(a) is a necessary but not sufficient precondition for it.

## Load-bearing design decisions

- **Agents declare thread intents; analyzers only backfill and score.** The vision spec
  lists "detecting plants/payoffs in prose" as a Threads-faculty analyzer concern, but for
  M3 the simplest path to the done-when is: Author/Editor structured output *declares*
  thread intents directly (deterministic, cheap, testable), and `work()` turns those into
  `thread.*` commits through the existing `Committer`/`GatingCommitter` seam — no new
  narrative-parsing LLM call in the critical path. Post-hoc prose-based detection of
  plants/payoffs the agents *fail* to declare is explicitly deferred to M4+; M3's Threads
  faculty trusts agent self-reporting, which is sufficient to make the done-when
  observable and testable.
- **Thread ids are minted once, at plant time, and reused by reference thereafter.**
  Allowing the Author to freely invent an id on every intent would let "touches" silently
  fork into new threads. The rule — plant mints an id from a freeform name; touch/pay-off/
  abandon must cite an id from the active-thread list already in the agent's own context —
  keeps thread identity deterministic and keeps the M3.3 done-when's "referencing the
  *same* thread" claim checkable in a test rather than assumed.
- **`thread.*` and `annotation.structure_scored` are never gated.** Both domains are
  analysis/feed-flavor output — narrative bookkeeping and derived scores, not proposals to
  change canon — so they're classified in `AutonomyPolicy._NEVER_GATED` next to
  `agent.remarked`. This is a deliberate choice to keep the Story Brain's signal flowing
  regardless of the active autonomy level: a stale thread must be able to surface and be
  acted on even at the most conservative autonomy setting, or the done-when could get stuck
  behind an unapproved proposal that nobody asked for.
- **Tension/pacing scoring is a small dedicated LLM agent; staleness and sag/spike
  detection are pure functions.** Score *production* (`annotation.structure_scored`
  events) requires judgment and stays an LLM call, scheduled like the six existing agents
  via `novelizer/scheduler.py`'s readiness-scoring tick, gated locally by a
  count-of-unscored-chapters readiness score. Score *consumption* — staleness (3 chapters
  since last plant/touch with no terminal event) and sag/spike detection (thresholds over
  stored scores) — is pure, deterministic, and unit-testable without any LLM in the loop,
  keeping the milestone's CI-verifiable half honest.
- **Staleness has one implementation, called from two places.** `StalenessAnalyzer` is a
  pure function over `ReadStore` data, computed live — never persisted as a projection
  field. Both the `BrainContext` provider (M3.3) and the Thread Board widget (M3.3) call
  that same function through a small shared read-side helper at the moment they need an
  answer, rather than each re-deriving "is this thread stale" independently. This is what
  makes "the Thread Board shows it stale" and "the Author was told it's stale" the same
  fact, not two facts that happen to agree today.
- **Brain context reaches agents via the exact M2 injection seam.** Runtime builds a
  `BrainContext` from `ReadStore` queries (stale threads + their referenceable ids, pacing
  flags) and passes it as an additional optional constructor parameter to `Author`/
  `Editor`, mirroring `casting_note`/`personality`: appended as a conditional string inside
  `work()` only when non-empty, never touching the deepagents `system_prompt` at
  construction. This keeps Story Brain a read-only dependency of agents (DDD: agents
  depend only on injected context strings / read queries, never on Brain internals) and
  preserves byte-identical behavior when the Brain has nothing to report.
- **Projections stay disposable.** `ThreadsProjection` and the structure-scores table are
  built and rebuilt by the Projector from the `thread.*`/`annotation.structure_scored` log
  exactly like existing projections (`chapters`, `characters`, etc.) — no new persistence
  path bypasses the event log.
- **Analyzer scheduling reuses the existing room, not a bespoke loop.** The Structure
  Analyst is a seventh scheduled agent with its own `readiness()` (proportional to unscored
  recent chapters, `0.0` when none) and its own `ready_for_interval` cadence, participating
  in the same eligibility/readiness-scoring tick in `Scheduler.tick()` as the six existing
  agents — no parallel scheduling mechanism. Tests exercise it via direct `run_once()`
  calls, the same pattern already used for the six agents, rather than depending on it
  winning a scheduler race.
- **No cross-event transaction, by design.** A chapter that both advances the story and
  touches/pays-off threads results in `chapter.created` plus N separate `thread.*`
  `Committer.commit()` appends — N+1 independent appends, not one atomic write. This is
  accepted and consistent with existing precedent: the Author already performs its
  `chapter.created` commit, its `agent.remarked` commit, and signal consumption as separate
  appends with no cross-event transaction. A partial failure leaves a partial-but-valid
  log entry, same as today.
- **Terminal thread states are absorbing; late events are no-ops, not errors.** Once a
  thread reaches `paid_off` or `abandoned`, any further `thread.*` event for that id stays
  in the log as a fact (nothing is ever deleted or rejected) but the `ThreadsProjection`
  treats it as a no-op — the aggregate's state does not change. This is what the Hypothesis
  property test in M3.1 asserts: replaying any valid event sequence, including one with
  events after a terminal state, is idempotent and never resurrects a closed thread.

## Non-goals / deferred to later milestones

- Prose-based (LLM) detection of undeclared thread plants/payoffs — M4+.
- `secret.*` events, Knowledge faculty, Who-Knows-What view — M4.
- Causality faculty, cause→effect edges, Causeway view — M4.
- Theme/motif tracking — M5, and lives in the Story Browser rather than a Brain view.
- Configurable staleness threshold via TUI/settings (M3 ships a fixed default of 3
  chapters; making it user-configurable is a follow-up, not required for the done-when).
- Atomic multi-event commits for a single chapter's thread intents — deferred; current
  precedent (separate, non-transactional appends) is accepted as-is.

## Standing principles (unchanged)

Event sourcing (log sole truth; only the Projector writes projections; state changes via
appended events), DDD bounded contexts (Story Brain derives from canon events and exposes
read-side queries only — it does not reach into agent internals, and agents depend only on
injected context, never Brain internals), SOLID (extension over modification — brain
context injected via a small additive provider, following M2's precedent), red/green TDD
black-box-first with property-based tests where invariants generalize (thread state
machine including absorbing terminal states, projection rebuild equivalence, score
bounds), spec + code review as gates.
