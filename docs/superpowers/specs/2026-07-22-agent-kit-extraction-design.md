# Agent Kit Extraction + Research Domain Live Agents — Design

Status: accepted (brainstormed with Ty, 2026-07-22; full-autonomy execution authorized)
Date: 2026-07-22

## Problem

`research_domain/` is a complete but inert second domain: four claim/source
event types, three projections, a working `ResearchRuntime` + CLI (see
`docs/superpowers/specs/2026-07-22-research-domain-runtime-design.md`) — and
six roles in `roles.py` that are all `tool_grant=None` stubs. Everything that
makes agents *alive* lives in `novelizer/` and is not extracted: `BaseAgent`'s
readiness/poll/work/commit loop (`novelizer/agents/base.py`), the readiness-
sorted dispatch-pool `Scheduler` (`novelizer/scheduler.py`), LLM runner
construction (`novelizer/agents/llm.py` + the deepagents pattern in
`novelizer/research/runner.py`), and the machinery-telemetry vocabulary
(`novelizer/telemetry/events.py`).

This is the third extraction, after `substrate/` (event sourcing) and
`tui_kit/` (TUI). The overall direction, decided in brainstorm:

- **C-sequencing**: proving-ground first (this campaign), "actually useful
  research tool" as a later phase.
- **Novelizer stays untouched** (option B from brainstorm): the kit is
  extracted *from* novelizer's shape, but novelizer keeps running on its own
  copies. The novelizer cutover is a separate future campaign. A temporary
  second copy of the loop machinery is the accepted cost; behavioral parity
  tests keep the copies honest.
- **New top-level package `agent_kit/`** (not growing `substrate/`): the
  langchain/deepagents dependency stays out of substrate; tui_kit-style
  import-linter enforcement.
- **Scope C**: loop + scheduler + LLM runner construction + the agent-run
  telemetry vocabulary, landed as separate mergeable steps.
- **Local document corpus** for round one (a directory of .md/.txt files);
  web sources are phase-A tooling, explicitly deferred.
- **Role trio**: extractor, verifier, retractor become real LLM agents —
  they cover all four existing event types with zero new event vocabulary.
  Scout folds into the extractor's `poll()` (filesystem diff); synthesizer
  and coverage_analyst stay stubs until phase A.
- **Approach 3 — verbatim mechanics, trimmed seams**: the copied logic is
  faithful line-for-line where it matters (backoff arithmetic, watermark
  gating, dispatch-pool semantics, crash-consumes-interval), but the
  dependency surface is corrected at exactly three seams (below).

## Section 1: Package layout and boundaries

```
agent_kit/
  __init__.py      # explicit public API with __all__, substrate-style
  base.py          # BaseAgent — the generic loop half
  scheduler.py     # Scheduler — dispatch pool
  telemetry.py     # TelemetryEmitter protocol + agent-run/scheduler payloads
  run_context.py   # current_run_id / current_agent_name contextvars
  llm.py           # build_chat_model + build_agent_runner (langchain/deepagents live here only)
  middleware.py    # ExcludeToolsMiddleware (generic langchain middleware)
```

Dependency rules, enforced by new import-linter contracts:

- `agent_kit` imports **nothing** from `novelizer`, `research_domain`,
  `tui_kit`, or `substrate`. The loop/scheduler/runner machinery needs none
  of them — `AgentSpec` etc. stay a substrate concern. agent_kit's only
  heavyweight deps (langchain-openai, langchain, deepagents) are confined to
  `llm.py`/`middleware.py`.
- `research_domain` may import `substrate` and `agent_kit` — it is the
  composition point where the two kits meet.
- `novelizer` is untouched: no import changes; its own `BaseAgent`/
  `Scheduler`/`llm.py` copies keep running the app.
- Consumers import from `agent_kit` top-level only (same `__all__`
  discipline as substrate), enforced by the same submodule-forbidden
  contract style. (Exception consistent with existing convention: `tests/`
  may import submodules directly.)

`pyproject.toml` gains `agent_kit` in `[tool.importlinter].root_packages`
plus contracts: agent_kit independence (forbidden: novelizer, substrate,
research_domain, tui_kit) and the consumer submodule rule for
`research_domain` (may import `agent_kit` but not `agent_kit.*` submodules).

## Section 2: agent_kit.base — BaseAgent

Extracted from `novelizer/agents/base.py` lines ~56–168 (the generic half).
The fiction half — `_consume_signals`, `_remark`, `_commit_flag_drafts`, and
the ten `_commit_*_intents` helpers — stays in novelizer; none of it enters
the kit.

Copied verbatim in behavior:

- `Runner` protocol (`async def ainvoke(self, inputs: dict) -> dict`).
- `PASS_BACKOFF_MULTIPLIER = 3` module constant.
- `paused` flag + `pause()`/`resume()`.
- Interval machinery: `ready_for_interval(now)`, `mark_ran(now)`,
  `seconds_until_ready(now)`, `note_pass(now=None)` (backoff =
  `interval * PASS_BACKOFF_MULTIPLIER`, `time.monotonic` clock family).
- Watermarking: `_fingerprint()` (None default disables), `_gate_on_watermark
  (score)`, `_record_watermark()`, `_clear_watermark()`.
- `readiness() -> 0.0` default; `_run()` template method.
- `run_once()`: uuid run id, contextvar set/reset (`current_run_id`,
  `current_agent_name` from `agent_kit.run_context`), telemetry bracketing —
  AGENT_RUN_STARTED before `_run()`, AGENT_RUN_FAILED (with
  `phase = "llm_call" if telemetry.in_llm_call(run_id) else "agent"`) on
  exception + re-raise, AGENT_RUN_FINISHED on success.
- `_emit_telemetry()` no-op when `self.telemetry is None`; `telemetry`
  attribute defaults to None, injected post-construction (same convention
  novelizer's Runtime uses).
- `_guarded_line(label, value)` static helper.

The three corrected seams (the entire diff vs. novelizer's copy):

1. **Constructor**: `__init__(self, runner, interval, name=None,
   personality="")`. `read_store`/`committer` are dropped — the generic half
   never uses them (they only serve the fiction commit helpers). Subclasses
   own their storage dependencies.
2. **Telemetry protocol**: `agent_kit.telemetry.TelemetryEmitter` protocol —
   `async def emit(self, event_type: str, aggregate_id: str, payload) ->
   None` and `def in_llm_call(self, run_id: str) -> bool`. Novelizer's
   `TelemetryRecorder` already satisfies it structurally.
3. **No prompts import**: the `DEFAULT_PASS_REMARK`/`PASS_PROMPT_INSTRUCTION`
   re-exports (already flagged in novelizer as migrate-then-drop) are not
   carried over.

`GRAPH_RECURSION_LIMIT` moves to `agent_kit.llm` (it is a runner concern).

## Section 3: agent_kit.scheduler — Scheduler

Extracted from `novelizer/scheduler.py`, verbatim mechanics:

- Readiness-sorted dispatch pool: `tick()` fills free slots
  (`max_concurrent_agents`, default 2) from eligible agents (not paused, not
  in flight, interval elapsed), scored by `await agent.readiness()`, dispatch
  only for score > 0.0. Returns dispatched names without awaiting them.
- `pause_agent`/`resume_agent`/`pause_all`/`resume_agents`, `status()`
  (name/paused/running/last_error/last_completed/run_count/next_ready_in).
- `_run()` finally-block invariants: `mark_ran` on completion (crash consumes
  interval — no hot-looping), in-flight cleanup, run_count increment, sticky
  `_last_completed` marker. Done-callback exception retrieval for
  fire-and-forget tasks.
- `_emit_eligibility()`: one SCHEDULER_ELIGIBILITY_CHANGED per agent per
  state *change* (reasons: "paused" | "running" | "interval not elapsed" |
  "readiness 0" | "ready"), SCHEDULER_PICKED per dispatch.
- `drain_in_flight()`, `run()` loop with `tick_sleep`, `stop()`.
- Injectable `clock` (default `time.monotonic`) — the testing seam.

The one corrected seam: the constructor drops `read_store` and gains
`override_provider: Callable[[], Awaitable[str | None]] | None = None`.
`tick()` calls it (when set) instead of
`read_store.list_unconsumed_signals()` + `SignalKind.override` filtering; a
returned agent name is dispatched first, exactly as the override branch does
today. Default None means no override mechanism — research has no Director;
novelizer supplies its signal query at cutover time.

## Section 4: agent_kit.telemetry, run_context, llm, middleware

**telemetry.py**: `TelemetryEventType` constants for the five machinery
events the loop/scheduler emit — SCHEDULER_PICKED,
SCHEDULER_ELIGIBILITY_CHANGED, AGENT_RUN_STARTED, AGENT_RUN_FINISHED,
AGENT_RUN_FAILED — plus the matching pydantic payload models copied from
`novelizer/telemetry/events.py` (same field names/types, so novelizer's
recorder and tui_kit adapters already understand the shapes), and the
`TelemetryEmitter` protocol. The LLM/tool-call event vocabulary
(LLM_CALL_*/TOOL_CALL_*) is emitted by novelizer's recorder-side callback
handler, not by the loop — it stays in novelizer and is extracted later with
the recorder (future campaign; noted as a non-goal here).

**run_context.py**: `current_run_id` and `current_agent_name` contextvars,
copied from `novelizer/run_context.py`.

**llm.py**:

- `CONTEXT_WINDOW_TOKENS = 128_000` default; `build_chat_model(model,
  base_url, api_key, temperature=0.8, max_tokens=None, callbacks=None,
  streaming=None, context_window_tokens=CONTEXT_WINDOW_TOKENS)` — extracted
  from `novelizer/agents/llm.py` including `_ReasoningAwareChatOpenAI` (the
  reasoning-delta-surfacing ChatOpenAI subclass) and the
  callbacks-imply-streaming default. The only change is parameterizing the
  context window instead of a module constant baked into the call.
- `GRAPH_RECURSION_LIMIT = 100`.
- `build_agent_runner(*, model, system_prompt, response_format, tools=None,
  middleware=None, backend=None, callbacks=None,
  recursion_limit=GRAPH_RECURSION_LIMIT)` — the generic form of
  `novelizer/research/runner.py`'s `build_research_runner`: wraps
  `deepagents.create_deep_agent(...)` and applies
  `.with_config({"recursion_limit": ..., "callbacks": ...})`. Domain
  runners (research's per-role builders) call this with their prompt, their
  pydantic response_format, and their tools.

**middleware.py**: `ExcludeToolsMiddleware` copied from
`novelizer/agents/middleware.py` (fully generic langchain middleware; used
to strip deepagents built-ins like `write_todos`). `TodoContextMiddleware`
is not copied — it serves novelizer's Author workflow, not the kit.

## Section 5: research_domain live vertical

### Runtime extensions (`research_domain/runtime.py`)

`ResearchRuntime` grows the read-side state the agents and tools need, in
the same `_refresh_lookup_dicts()` pattern already there:

- `_claims_by_id: dict[str, dict]` — from `claim.proposed` payloads
  (claim_id → {claim_id, source_id, text}).
- `_corroborators_by_claim: dict[str, list[str]]` — from
  `source.corroborated` (claim_id → [source_id]).
- Existing `_counts_by_source`, `_refuters_by_target`,
  `_superseders_by_target` unchanged.
- Read accessors: `list_claims()`, `get_claim(claim_id)`,
  `corroborators_for(claim_id)`, `refuters_for(claim_id)`,
  `superseders_for(claim_id)` — plain dict/list reads of current state.
- An `asyncio.Lock` around `catch_up()` and `append_event()` bodies:
  multiple agents share one runtime instance under a concurrency-2
  scheduler, and `_refresh_lookup_dicts()` mutates shared dicts — the lock
  serializes refresh/append so no agent reads half-cleared state.

### Corpus (`research_domain/corpus.py`)

`CorpusReader(root: Path)`: `list_documents() -> list[str]` (sorted posix
relative paths of `*.md`/`*.txt` under root, skipping hidden directories;
the relative path *is* the `source_id`) and `read_document(source_id) ->
str`. Pure filesystem, no async needed.

### Structured outputs (`research_domain/schemas.py`, new)

- `ClaimDraft(text: str)` — extractor proposes claim texts for one
  document; `claim_id` is minted at commit time (uuid4 hex), never by the
  LLM.
- `ExtractorOutput(claims: list[ClaimDraft])`.
- `VerificationDraft(claim_id: str, corroborating_source_ids: list[str],
  refutation: RefutationDraft | None)` with `RefutationDraft(source_id:
  str, counter_text: str, reason: str)`.
- `VerifierOutput(verdicts: list[VerificationDraft])`.
- `CorrectionDraft(superseding_claim_id: str, target_claim_id: str,
  reason: str)`; `RetractorOutput(corrections: list[CorrectionDraft])`.

### Tools (`research_domain/tools.py`, new)

Read-only langchain `@tool` factories over `CorpusReader` + runtime state
(mirroring `novelizer/research/tools.py`'s factory shape): `list_documents`,
`read_document(source_id)`, `list_claims`, `get_claim(claim_id)`. Writes
never happen through tools — structured output carries proposals, commit
validates and appends (novelizer's intent pattern).

### The three agents (`research_domain/agents.py`, new)

All subclass `agent_kit.BaseAgent`; all follow poll/work/commit with
watermark gating and `note_pass()` when a run yields nothing actionable.
All appends batch via inherited `RuntimeBase.append()` then one
`catch_up()` at commit end (under the runtime lock).

**Extractor** (readiness 0.7 gated on watermark):
- `poll()`: pending = corpus docs whose source_id has no `claim.proposed`
  yet (`_counts_by_source` keys). Fingerprint = frozenset(pending).
- `work()`: one document per run (keeps runs short and scheduler traffic
  honest): runner reads the doc (prompt includes full text; tools available
  for cross-reference) → `ExtractorOutput`.
- `commit()`: dedup drafts against existing claims by (source_id,
  normalized text); append `claim.proposed` per surviving draft with minted
  claim_id. A doc yielding zero claims appends nothing — the recorded
  watermark keeps it from re-triggering until the corpus changes (this is
  exactly what watermark gating is for).

**Verifier** (readiness 0.6):
- `poll()`: pending = claims with no corroboration and no refutation
  targeting them. Fingerprint = frozenset(pending claim ids).
- `work()`: one claim per run: runner gets the claim + tools to read other
  documents → `VerifierOutput`.
- `commit()`: per verdict — `source.corroborated` per corroborating source
  (deduped against `corroborators_for`, and a claim's own source never
  corroborates it); a refutation mints a counter-claim
  (`claim.proposed` from the refuting source) then `claim.refuted`
  (claim_id=counter, target_claim_id=verified claim, reason).

**Retractor** (readiness 0.5):
- `poll()`: pending = contradiction targets (`_refuters_by_target` keys)
  with no superseder yet. Fingerprint = frozenset(pending).
- `work()`: one contradiction per run: runner sees target claim, its
  refuters, and tools → `RetractorOutput`.
- `commit()`: validate `superseding_claim_id` is an existing refuter of
  `target_claim_id` and target not already superseded; append
  `claim.corrected`. An empty corrections list (model judges the original
  stands) commits nothing; watermark prevents re-litigating until the
  contradiction set changes.

Gating enforcement (`claim.corrected` is tiered "reviewed") remains
unenforced, consistent with the runtime spec's non-goals — the human-review
gate is phase-A work.

### Runner builders + CLI (`research_domain/runners.py`, `cli.py`)

`runners.py`: per-role system prompts and
`build_extractor_runner/build_verifier_runner/build_retractor_runner(model_
settings, tools)` — thin calls to `agent_kit.llm.build_chat_model` +
`build_agent_runner` with the role's prompt and response_format, excluding
`write_todos` via `ExcludeToolsMiddleware`.

`cli.py` gains `run`:

```
research-domain run --corpus DIR [--dsn/-DATABASE_URL] [--stream ...]
  [--model $RESEARCH_MODEL] [--base-url $LLM_BASE_URL]
  [--api-key $LLM_API_KEY] [--interval 60] [--max-concurrent 2]
  [--max-ticks N]
```

Constructs `ResearchRuntime` + `CorpusReader` + the three agents +
`agent_kit.Scheduler`; `connect()`, `catch_up()`, then `scheduler.run()`
(or `--max-ticks` bounded ticking with `drain_in_flight()` for scripted
runs), `close()` in finally. Telemetry: none wired in round one (emitter
stays None); Engine Room wiring is the follow-on that the shared telemetry
vocabulary was extracted for.

`roles.py`: the trio's `AgentSpec.construct` entries become real
constructors; scout/synthesizer/coverage_analyst remain stubs (documented
as phase-A).

## Section 6: Error handling and edge behavior

- Agent crash → scheduler records `last_error`, interval consumed
  (`mark_ran` in finally — no hot-looping), AGENT_RUN_FAILED emitted,
  nothing committed (work-then-commit ordering; partial LLM output is
  discarded, retried next interval).
- Structured-output failure (deepagents response_format miss) raises →
  same crash path; unstamped work is naturally retried.
- Doc deleted between poll and work → `read_document` raises
  FileNotFoundError → clean run failure; next poll recomputes pending
  without the missing doc.
- Empty corpus / drained backlog → readiness 0.0 (or watermark-gated 0.0);
  scheduler idles at tick cadence with the eligibility trace showing why.
- Duplicate protection is commit-time, mirroring novelizer's guards:
  extractor dedups by (source_id, normalized claim text); verifier dedups
  corroborations by (claim_id, source_id) and skips self-corroboration;
  retractor re-validates supersession preconditions at commit.
- Concurrency: the runtime lock serializes catch_up/append across the
  dispatch pool; agents read runtime state only in poll/commit (both under
  or adjacent to a fresh catch_up), tools read the in-memory dicts (a
  benign-staleness read during work is acceptable and disclosed in code
  comments).

## Section 7: Testing

Per repo standard: red/green + property-based TDD; all suites run in this
worktree, never the main checkout (standing DB-lock rule).

**agent_kit unit tests** (`tests/agent_kit/`):
- Property-based (hypothesis) interval/backoff math: for arbitrary
  interval/clock sequences, `ready_for_interval`/`seconds_until_ready`/
  `note_pass` invariants hold (never ready before interval; note_pass
  triples the wait; seconds_until_ready is 0 exactly when ready).
- Watermark gating: fingerprint None disables; unchanged fingerprint gates
  to 0.0; changed fingerprint restores score; `_clear_watermark` re-arms.
- `run_once` bracketing with a fake emitter: started/finished on success;
  started/failed + re-raise on crash; phase "llm_call" iff
  `in_llm_call(run_id)`; contextvars set during `_run` and reset after.
- Scheduler with fake clock + stub agents (adapting
  `tests/test_scheduler.py` patterns): readiness-sorted dispatch,
  concurrency cap, override_provider priority, crash-consumes-interval,
  run_count/last_error/last_completed bookkeeping, eligibility-changed
  emitted only on state transitions, drain_in_flight.
- Behavioral parity spot-check vs novelizer: identical scripted scenario
  (fake clock, scripted readiness) through `agent_kit.Scheduler` and
  `novelizer.scheduler.Scheduler` asserting identical dispatch traces —
  the honesty check that keeps the temporary copies aligned until cutover.
- llm: `build_chat_model` constructor-level assertions (profile stamping,
  callbacks-imply-streaming, explicit streaming decouple) with no network;
  `build_agent_runner` construction smoke (no invocation).

**research_domain tests** (`tests/research_domain/`, extending existing):
- `corpus.py`: tmp-dir listing/reading, hidden-dir and extension filtering,
  source_id stability.
- Runtime extensions: claims/corroborators registries refresh correctly;
  accessors; lock serialization (two concurrent append_event calls don't
  interleave refresh).
- Agents with FakeRunner (canned `Runner.ainvoke` returning scripted
  structured outputs — no LLM anywhere in CI): each agent's
  poll/work/commit against the real Postgres fixture (`postgres_dsn`,
  docker-skip), including: extractor dedup + zero-claim watermark
  behavior; verifier corroboration dedup + refutation minting counter-claim
  then refuted event; retractor validation + supersession; watermark
  re-arm on corpus/claim-set change.
- Pipeline integration: fixture corpus with a planted contradiction
  (brainstorm decision: synthetic-contradiction trick lives in tests);
  drive Scheduler with fake clock + fake runners through enough ticks that
  extractor→verifier→retractor converge; assert `contradiction_map` and
  `claim_dependency_graph` projections end in the expected state.
- CLI `run --max-ticks` smoke against the fixture: `cli.py` factors
  component construction into a `build_run_components(dsn, stream, corpus,
  model_settings, ...) -> (runtime, scheduler)` helper the command calls;
  tests call the helper with fake runners substituted, then tick.
- Import boundary: existing `test_import_boundary.py` covers the new
  contracts automatically once pyproject gains them; `test_public_api.py`
  pattern repeated for `agent_kit.__all__`.

**Live acceptance** (post-merge, user-run per repo convention): point
`research-domain run` at a real corpus (e.g. `docs/superpowers/specs/`)
with the local endpoint and watch the trio drain it. Same convention as
prior milestones: green suite merges; live run is the user's acceptance
step.

## Non-goals

- No novelizer changes of any kind (imports, behavior, tests). The
  novelizer cutover onto agent_kit is a separate future campaign.
- No TUI/Engine Room wiring for research this round; the extracted
  telemetry vocabulary is the enabler, the wiring is follow-on.
- No LLM/tool-call telemetry extraction (recorder-side; goes with the
  future recorder extraction).
- No web fetching, no scout/synthesizer/coverage_analyst implementations,
  no new event types, no gating enforcement, no auth/multi-tenancy — all
  phase A or later.
- No PyPI packaging for agent_kit (same posture as substrate/tui_kit).

## Sequencing (mergeable steps, each red/green)

1. `agent_kit` core: `base.py`, `telemetry.py`, `run_context.py` + unit/
   property tests; pyproject root_packages + independence contract.
2. `agent_kit/scheduler.py` + tests incl. parity spot-check.
3. `agent_kit/llm.py` + `middleware.py` + tests; `__init__.py` public API
   + public-api test.
4. `research_domain` runtime extensions (claims/corroborators registries,
   accessors, lock) + `corpus.py` + tests.
5. `schemas.py` + `tools.py` + `runners.py` + Extractor + tests.
6. Verifier + Retractor + tests; `roles.py` trio de-stubbed.
7. CLI `run` + pipeline integration test + docs (`agent_kit/README.md`,
   substrate README cross-link, memory update).
