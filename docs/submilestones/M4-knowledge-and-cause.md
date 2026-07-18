# M4 · Knowledge & Cause — Sub-Milestone Breakdown

M4 gives the Story Brain its second and third faculties: Knowledge (who knows what secret,
and when they learned it) and Causality (what caused what, and whether the causal graph is
internally consistent). Like M3, it's decomposed into just-in-time sub-milestones, each
independently shippable and testable, planned one at a time and executed via
subagent-driven development (spec-informed plan → fresh-subagent-per-task → two-stage
review → whole-branch review → merge).

Parent milestone in [`../MILESTONES.md`](../MILESTONES.md); end-state design (Story Brain
faculties, event domains, view priority) in
[`../superpowers/specs/2026-07-17-novelizer-vision-design.md`](../superpowers/specs/2026-07-17-novelizer-vision-design.md).

Phase 1 (M3) covered Structure and Threads. This phase covers **Knowledge** and
**Causality** only. Theme/motif tracking is M5 and lives in the Story Browser, not as a
fifth Brain view.

## Sub-milestones

| # | Name | Delivers | Done when | Status |
|---|------|----------|-----------|--------|
| M4.1 | **Secret & causal-edge ledgers** | New `secret.*` event domain (`created`, `learned`, `referenced`, `revealed`) and `causal_edge.*` event domain (`declared`) in `novelizer/canon/events.py`. Author/Editor/CharacterKeeper structured output gains an optional `knowledge_intents` field (deterministic, agent-declared, mirroring M3.1's `thread_intents`) and Author/Editor gain an optional `causal_intents` field; `work()` turns these into `committer.commit(...)` calls through the existing `Committer`/`GatingCommitter` seam, alongside existing commits. **Secret identity**: minted only at `secret.created` time from a freeform title, slugged the same way as threads (`novelizer.canon.secrets.slugify_secret_name`, new module mirroring `novelizer.canon.threads`); `learned`/`revealed` intents must cite an id drawn from the active-secret list already provided in the agent's context — an intent naming an unknown id is dropped with a logged warning and no event is committed, same as M3.1's thread rule. A `uses` intent commits a `secret.referenced` event carrying `(secret_id, character_id, chapter_id)` — the durable, replayable record M4.2's `LeakDetector` reads; leak detection never depends on transient `ChapterDraft` objects, which do not survive past the agent turn and would break rebuild-from-log. Any agent producing prose in a chapter (Author, Editor) may mint a secret at plant time; CharacterKeeper may also declare `learned` intents when it updates a character's knowledge state as part of its own work. **Causal edge identity**: an edge is `(cause_chapter_id, effect_chapter_id, note)` — no separate minted id; edges are declared, not referenced later, so there is no touch/pay-off lifecycle to protect. A `KnowledgeProjection` (new table via the Projector) folding `secret.*` events into a secret × character matrix — per-character `learned` cells plus a **secret-level `revealed` flag** (a `secret.revealed` event sets the flag once on the secret's own record; the matrix accessor derives `revealed` for every character, including characters created *after* the reveal, rather than fanning out per-cell writes) — and a `CausalGraphProjection` folding `causal_edge.declared` events into an adjacency list keyed by chapter id. `ReadStore.list_secrets()` / `get_secret()` / `knowledge_matrix()` and `ReadStore.list_causal_edges()`. **Autonomy**: `secret.created`/`secret.learned`/`secret.referenced` and `causal_edge.declared` are added to `AutonomyPolicy._NEVER_GATED` (see Locked decisions #6 for `secret.revealed`, which is not). | Author declaring a `learned` knowledge intent results in a `secret.learned` event in the log and an updated cell in the knowledge-matrix read table after `catch_up()`; a Hypothesis property test asserts the knowledge-matrix fold is monotonic per (secret, character) `learned` cell under any valid event sequence replay and that the secret-level `revealed` flag is set-once (see Locked decisions #2); a second property test asserts the causal-graph fold never drops or duplicates a declared edge across replay order. | complete |
| M4.2 | **Leak & paradox analyzers, Continuity Checker upgrade** | Deterministic `LeakDetector` (pure function over `ReadStore` knowledge-matrix + committed `secret.referenced` rows: a `secret.referenced` event naming character C using secret S in chapter H is a leak if the matrix shows C has not `learned` S — and S is not `revealed` — evaluated over the full log through and including chapter H's own commits, per Locked decisions #3) and deterministic `ParadoxDetector` (pure function over the causal-graph adjacency list: an edge is a paradox candidate if the effect chapter is ordered before or equal to the cause chapter, or if the edge closes a cycle) — no LLM involved in detection itself, fully unit-testable. `ContinuityChecker` is upgraded: in addition to its existing free-text LLM contradiction pass, `poll()`/`work()` now also run `LeakDetector` and `ParadoxDetector` over fresh `ReadStore` data every cycle (deterministic, cheap, no extra LLM call), and `commit()` turns each hit into a `retcon_request.created` event via the same `RetconRequest` model and `Committer` call the LLM-path already uses — leaks and paradoxes become first-class retcon requests indistinguishable in the queue from LLM-found contradictions, tagged for traceability by prefixing the request description with pinned module constants `LEAK_SOURCE_TAG = "[source: leak_detector]"` / `PARADOX_SOURCE_TAG = "[source: paradox_detector]"` — the exact strings are fixed here so M4.2's implementation and M4.3's done-when assertion, written in different sessions, cannot drift. **Autonomy**: no policy change needed — `retcon_request.created` already falls through `_CANON_EVENTS`/`_RETCON_EVENTS`/`_NEVER_GATED` ungated except under `gated_all` (see Locked decisions #5), so leak-triggered retcon requests reach the queue at every autonomy level except the most conservative, matching existing LLM-found retcon-request behavior exactly. | Seeding a fixture where a committed `secret.referenced` event has character C referencing secret S with no `secret.learned` event for that pair anywhere in the log makes `LeakDetector` report a leak (unit test, no LLM); seeding two causal edges forming a cycle makes `ParadoxDetector` report both as paradox candidates; calling `continuity_checker.run_once()` against the leak fixture (LLM call mocked/stubbed to return no findings of its own) produces a `retcon_request.created` event whose description starts with `LEAK_SOURCE_TAG`, landing in `list_retcon_requests(status=open)` — this is the mechanical half of the milestone done-when. | not started |
| M4.3 | **Who-Knows-What & Causeway views + brain context injection** | `novelizer/tui/widgets/who_knows_what.py` (renders the secret × character matrix as a grid, reading `KnowledgeProjection` rows) and `novelizer/tui/widgets/causeway.py` (renders the causal graph as cause→effect chains grouped by chapter, calling the *same* `ParadoxDetector` function from M4.2 at render time via a small `ReadStore`-backed helper — paradoxes are never persisted as a projection field or recomputed with separate logic, mirroring M3.3's Thread Board/staleness precedent) wired into `NovelizerApp.compose()`. `novelizer/brain/context.py` (from M3.3 — a module of pure functions, not a class) gains two more note builders — `known_secrets_note()` (Author-facing: a compact who-knows-what summary of non-revealed secrets, e.g. `Secrets and who knows them: 'the-heir-lives' — known only to Mara; 'the-map-is-forged' — known to no one.`, built from the knowledge matrix; it is deliberately **not** POV-scoped, because the note is injected *before* the chapter exists, when no POV has been chosen — and no POV field exists in the chapter schema; M4 does not add one) and `causal_flags_note()` (Editor-facing: paradox candidates) — following the exact M3.3 pattern (conditional string, empty when nothing to report, byte-identical output otherwise). | Two-part done-when (see below): (a) a CI-verifiable mechanical chain, and (b) a live-LLM smoke check that is the milestone's *true* observation, per M1/M2/M3 precedent. | not started |

### M4.3 done-when, in full (this is the milestone done-when)

**(a) CI-verifiable mechanical chain** — proves the plumbing, not LLM judgment: seed a
secret (`secret.created`), a character who has *not* learned it, and a committed
`secret.referenced` event naming that character using the secret in a chapter → assert
`LeakDetector` (M4.2) flags it → drive
`ContinuityChecker.run_once()` with a `FakeRunner` preset to return no LLM-found
contradictions → assert the resulting `retcon_request.created` event lands via the
`Committer`, its description starting with `LEAK_SOURCE_TAG` → assert it appears in
`list_retcon_requests(status=open)` → assert the Who-Knows-What widget's render-time helper
still shows the character as not-having-learned the secret (the leak is flagged, not
silently resolved). This runs in CI, with no live model call, and is a normal black-box
test in the existing agent-test style.

**(b) Live-LLM smoke check** — the actual claim ("a planted knowledge leak is auto-caught
and routed to the retcon queue"): a `live_llm`-marked test (or documented manual run),
following the M1–M3 precedent for behavior that depends on real model output, that seeds
the same leak fixture as (a) but drives the *real* Author to write a chapter where the
character's dialogue/action plausibly references the secret, then runs the real
`ContinuityChecker` and confirms a matching retcon request lands in the queue — with no
director signal and no manual prompt beyond what the room already injects. **CI cannot
prove this causality** — a `FakeRunner`-driven test only proves the pipe is connected, not
that the deterministic detector catches what a real agent's structured output actually
declares under live conditions. The live_llm-marked check is the true done-when observation
for M4; (a) is a necessary but not sufficient precondition for it.

## Locked decisions

1. **Secret identity: minted at `secret.created`, slugged, first-plant-wins, agent-side
   collision downgrade — same rule as threads (M3.1), reused rather than reinvented.**
   Any prose-producing agent (Author, Editor) may mint a secret; CharacterKeeper may declare
   `learned` against an existing id but never mints one itself, since minting secrets is a
   narrative-authoring act (deciding "this is now a secret in the story") rather than a
   bookkeeping act (deciding "this character now knows it"), and Author/Editor are the
   agents that actually write the prose where secrets get introduced. This keeps the
   identity story symmetric with M3's thread rule and lets a new
   `BaseAgent._commit_knowledge_intents` — a *sibling* of `_commit_thread_intents`
   (same pattern, separate method; event types and payload models differ, so it is not
   literal method reuse) — apply the exact same collision-avoidance rules (see M4.1 scope).

2. **Knowledge matrix: three states per (secret, character) cell — `unknown` (default,
   no row), `known` (a `secret.learned` event exists), `revealed` (a `secret.revealed`
   event exists, meaning publicly known, not just known-to-one-character) — and the fold
   is monotonic: `unknown → known → revealed` only, never backwards.** Knowledge cannot be
   un-learned by a plain event; if the story needs a character to un-know something (e.g. a
   false memory retconned away), that happens through the existing retcon machinery
   (`world_entry.superseded`-style supersession, not a new "unlearn" event type) — M4 does
   not introduce forgetting as a first-class knowledge-domain concept, keeping the fold a
   simple monotonic lattice per cell, which is what the M4.1 property test asserts.
   `revealed` is character-independent and is represented as **secret-level state, not
   per-cell writes**: a `secret.revealed` event sets a set-once `revealed` flag on the
   secret's own projected record, and the matrix *accessor* derives `revealed` for every
   character — including characters created *after* the reveal, who would be silently
   missed by a fan-out of per-cell writes at event-application time. `learned` remains
   scoped to the one character named in the event payload. The M4.1 property test
   therefore asserts two things: per-(secret, character) `learned` cells are monotonic,
   and the secret-level `revealed` flag is set-once.

3. **A leak is a precise, deterministic, testable condition: a committed
   `secret.referenced` event names a character C using secret S in chapter H (produced
   from an intent's `action: "uses"` variant, mirroring `ThreadIntent`'s `action` field)
   while the knowledge matrix shows C's cell for S is `unknown` — and S is not
   `revealed` — evaluated over the full log through and including chapter H's own
   commits.** The `uses` intent is durably recorded as a `secret.referenced` event
   precisely so the detector reads the log/projections, never transient `ChapterDraft`
   objects — the Continuity Checker runs on its own schedule, long after the Author's
   turn ended, and rebuild-from-log must reproduce detection inputs. Intra-chapter
   ordering is defined: matrix state is evaluated *after* all of the chapter's own
   commits are applied, so a chapter that both declares `learn`(C, S) and `uses`(C, S)
   in the same structured response is **not** a leak (self-consistent
   learn-then-use-in-one-chapter is normal prose); only a `uses` with no corresponding
   `learned`/`revealed` anywhere in the log through that chapter is a leak. Leak
   *detection* is deterministic (committed-event cross-check against the matrix —
   CI-testable, same split as M3's thread-intent self-declaration), not prose-mining. Following the M3 precedent exactly:
   agents self-declare knowledge intents (`plant`/`learn`/`reveal`/`uses`) in structured
   output; LLM prose extraction of *undeclared* leaks (an agent writing a leak into prose
   without declaring the intent) is out of scope for M4, deferred alongside M3's deferred
   undeclared-plant detection — both are the same category of future work (post-hoc prose
   mining to catch what self-reporting misses), intentionally deferred together.

4. **Causal graph: edges are `(cause_chapter_id, effect_chapter_id, note)` triples declared
   by Author/Editor in structured output (`causal_intents`), node identity is the chapter
   id (the only stable, already-ordered identity the room has — no new node type), and a
   paradox is deterministically either (a) an edge whose effect chapter is ordered at or
   before its cause chapter in `ReadStore.list_chapters()`'s chronological order, or (b) an
   edge that closes a cycle in the adjacency list.** Plain dicts/lists power the graph — no
   `networkx` dependency: M4's graph is small (chapter-count-bounded), the only operations
   needed are adjacency-list fold, ordering-violation check, and cycle detection (DFS over
   a dict-of-lists, ~20 lines), and adding a graph library for that would be scope
   disproportionate to the need, breaking the project's no-new-dependencies-without-strong-
   justification constraint. The LLM layer (out of scope for M4.1/M4.2's deterministic
   core, live in the Continuity Checker's judgment pass) adds *inferring* implicit causal
   links the agents didn't declare — deferred to the same prose-mining bucket as #3.

5. **Leak/paradox → retcon queue routes through the existing `retcon_request.created` →
   `RetconRequest`/`Committer` path unchanged — no new event type, no bypass of gating.**
   Investigating the current policy (`novelizer/canon/policy.py`) surfaced a load-bearing
   fact: `RETCON_REQUEST_CREATED` is *not* in `_NEVER_GATED`, `_CANON_EVENTS`, or
   `_RETCON_EVENTS` today — it falls through `AutonomyPolicy.is_gated`'s `_GATED_SETS`
   lookup ungated at every named level (`full_auto`, `gated_retcons`, `gated_canon`) and is
   only gated at `gated_all` (everything-gated fallback). Only `RETCON_REQUEST_RESOLVED`
   (the Retconner's amendment-application step) and `WORLD_ENTRY_SUPERSEDED` are in
   `_RETCON_EVENTS`/`gated_retcons`. This means *creating* a retcon request already reaches
   the queue at every autonomy level except the strictest — exactly the behavior M4's
   done-when needs ("auto-caught and routed to the retcon queue automatically") — so M4
   requires **no policy change** for request creation; leak/paradox-sourced requests get
   this behavior for free by reusing the existing event type. Resolving a leak/paradox
   retcon request still goes through the Retconner and remains gated under
   `gated_retcons`/`gated_canon`, same as today — M4 does not change resolution autonomy.

6. **`secret.created`, `secret.learned`, and `secret.referenced` are `_NEVER_GATED`
   (bookkeeping — `referenced` in particular is the leak-detection signal and must always
   flow, or a conservative autonomy setting would blind the Continuity Checker to the very
   leaks it exists to catch); `causal_edge.declared` is `_NEVER_GATED` (bookkeeping);
   `secret.revealed` is deliberately left OUT
   of `_NEVER_GATED`, falling into `_CANON_EVENTS` under `gated_canon`.** `created`/
   `learned` are narrative bookkeeping in the same sense as `thread.*` and `annotation.*`
   in M3 — they record a fact about the story's internal state, not a change to canon that
   readers experience directly, so the room's signal must flow at every autonomy level
   (same rationale as M3's Locked Decisions). `secret.revealed`, by contrast, is genuinely
   canon-changing in a way `thread.*` never was: it's the event that makes a secret public
   to the story, structurally closer to `chapter.created`/`character.updated` (both already
   in `_CANON_EVENTS`) than to feed-flavor bookkeeping — an agent unilaterally revealing a
   secret at a conservative autonomy setting is exactly the kind of consequential,
   plot-altering action the gating ladder exists to slow down. `causal_edge.declared` is
   pure annotation (recording a causal claim about existing chapters, not creating new
   canon), so it follows `thread.*`'s precedent into `_NEVER_GATED` without the caveat.

7. **Knowledge context injection: Author only, scoped to `known_secrets_note()` — a
   compact who-knows-what summary of every non-revealed secret and which characters know
   it; no causal-graph injection into agent prompts in M4.** The note is deliberately
   *not* POV-scoped: it is injected before the chapter exists, when no POV has been
   chosen (and no POV field exists in the chapter schema — M4 does not add one), so the
   only coherent Author-facing form is the full summary, which equips the Author to
   avoid leaks for *any* character it chooses to write. Author is the only agent that
   writes prose a leak could appear in, so it's the only agent that needs
   who-knows-what context — Editor gets
   `causal_flags_note()` (paradox candidates) instead, mirroring M3.3's split (Author got
   stale-thread context, Editor got pacing flags) rather than duplicating both notes onto
   both agents. Injecting the causal graph itself into Author's prompt (e.g. "chapter 3
   caused chapter 7") is deferred — M4's done-when only requires that leaks are caught and
   routed, not that Author writes more causally coherent prose unprompted; that stretch
   goal is a natural M5 follow-on once Causeway data exists to evaluate whether it helps.

8. **Done-when verification split mirrors M3 exactly**: (a) a CI-provable mechanical chain
   (seed a leak deterministically → `LeakDetector` flags it → `ContinuityChecker.run_once()`
   with a `FakeRunner` produces a `retcon_request.created` event → it's visible in the open
   retcon queue) proven in the M4.1/M4.2 sub-milestone done-whens and restated as part (a)
   of the M4.3 milestone done-when; (b) a `live_llm`-marked smoke test (not `ollama` — that
   dependency was removed in `7a01a23`) that seeds the same fixture, runs the *real* Author
   and Continuity Checker, and confirms the leak reaches the queue with no manual prompting
   beyond what the room already injects. (a) is necessary but not sufficient; (b) is the
   milestone's true observation, matching M1–M3 precedent for any claim depending on live
   model judgment.

## Non-goals / deferred to later milestones

- Prose-based (LLM) detection of undeclared secret leaks or undeclared causal links (agents
  writing a leak/link into prose without declaring the corresponding intent) — deferred
  alongside M3's deferred undeclared-plant/payoff detection, same future bucket.
- "Unlearning" knowledge as a first-class event (a character forgetting or having a false
  memory retconned away) — routes through existing retcon/supersession machinery instead of
  a new event type; no `secret.unlearned` in M4.
- Injecting the causal graph itself into Author's prompt (beyond the "does not know" note)
  — deferred to M5 as a possible enhancement once Causeway data exists to evaluate it.
- Configurable leak/paradox detection thresholds via TUI/settings — M4 ships fixed
  deterministic rules (matrix-cell lookup, chapter-order/cycle check), matching M3.2's
  precedent of shipping a fixed default first.
- Theme/motif tracking — M5, and lives in the Story Browser rather than a Brain view.
- Atomic multi-event commits for a single chapter's knowledge/causal intents — deferred,
  same non-transactional-appends precedent as M3.

## Standing principles (unchanged)

Event sourcing (log sole truth; only the Projector writes projections; state changes via
appended events), DDD bounded contexts (Story Brain derives from canon events and exposes
read-side queries only — it does not reach into agent internals, and agents depend only on
injected context, never Brain internals), SOLID (extension over modification — brain
context injected via the existing M3.3 provider, secret/causal-edge identity minting reuses
M3.1's slugify pattern rather than inventing a parallel one), red/green TDD black-box-first
with property-based tests where invariants generalize (knowledge-matrix monotonic fold,
causal-graph replay equivalence, cycle/ordering detection), spec + code review as gates.
