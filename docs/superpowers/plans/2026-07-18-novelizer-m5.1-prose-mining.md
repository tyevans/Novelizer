# M5.1 · Prose Mining in the Continuity Checker — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **NEVER create a `.env` file at any point in this plan.** A past subagent broke the
> suite doing this. Any scratch/temp file goes in the job's tmp dir, never the repo.
> Live-LLM settings load through `load_effective_settings()` only — never bare
> `EffectiveSettings()`.

**Branch:** `m5.1-prose-mining` (already checked out in this worktree; this plan does
not create it).

**Goal:** Give the Continuity Checker a second, structured LLM pass — prose mining —
that reads recent chapter prose plus the current knowledge matrix/thread list/causal
edges and reports facts the prose *shows* but the log has no covering event for. Mined
facts commit through the exact same `Committer` seam self-declared intents already use,
tagged `source="mined"`, but only for `_NEVER_GATED` types; mined reveals and ambiguous
facts always escalate to a `retcon_request.created` tagged `MINED_SOURCE_TAG`. This
closes the M4 reliability gap: 20+ live runs never produced a leak because the Author
never failed to declare — mining gives the Checker an independent way to notice what
agents don't self-report.

**Architecture:**
- `MinedFactsOutput` — a new structured-response schema (`novelizer/agents/schemas.py`),
  parallel to `ContinuityOutput`, returned by a *second* `Runner.ainvoke()` call inside
  `ContinuityChecker.work()`.
- `novelizer/agents/continuity_checker.py`: `poll()` gains a `mined_chapters` key (list
  of `Chapter` rows lacking a `chapter.mined` marker — computed via a raw log scan, see
  Task 3); `work()` runs the mining pass per un-mined chapter (skips entirely if none);
  `commit()` gains a mining-commit branch that reuses `BaseAgent._commit_knowledge_intents`
  / `_commit_thread_intents` / `_commit_causal_intents` with `source="mined"` intents,
  routes ambiguous/reveal facts to `retcon_request.created` tagged `MINED_SOURCE_TAG`, and
  always commits one `chapter.mined` marker per mined chapter (idempotency).
- `source: str = "declared"` field added to the five `_NEVER_GATED` fact payload models
  in `novelizer/canon/events.py` (`SecretCreated` unaffected — plant is never mined per
  the milestone's scope; only `SecretLearned`, `SecretReferenced`, `ThreadPlanted`,
  `ThreadTouched`, `ThreadPaidOff`, `CausalEdgeDeclared` carry the field — see Task 1 for
  the exact list and rationale).
- `chapter.mined` — new `_NEVER_GATED`, unprojected event type (payload: chapter id
  only), same class as `agent.remarked` (no `_apply` branch in `Projector`).
- `MINED_SOURCE_TAG = "[source: prose_miner]"` constant, defined in
  `novelizer/agents/continuity_checker.py` (this module owns mining, so it owns the tag —
  parallel to `leaks.py` owning `LEAK_SOURCE_TAG`).
- Dedup is **log-only, same-poll snapshot**, no new persisted "seen" cache: secrets via
  `list_secret_references` + `knowledge_matrix`; causal edges via exact `(cause, effect)`
  triple match against `list_causal_edges`; threads via a raw event-log scan for
  `thread.*` events citing `(thread_id, chapter_id)` — a mining-only log read, not a new
  projection.
- Deterministic analyzers (`find_leaks`, `find_paradoxes`, `StalenessAnalyzer`) are
  **not touched** — they already read `ReadStore` accessors regardless of `source`.

**Tech Stack:** Python 3.13, `pydantic` v2, `aiosqlite`, `pytest`+`pytest-asyncio`
(`asyncio_mode=auto`), `hypothesis>=6.156.6`.

## Global Constraints

- Event sourcing: mining never bypasses `Committer`; every mined fact is an ordinary
  event with `source="mined"`, never a side channel. `chapter.mined` is bookkeeping,
  never projected — same precedent as `agent.remarked` (`novelizer/canon/projector.py`
  has no `_apply` branch for `AGENT_REMARKED`; `chapter.mined` gets the same treatment).
- No new autonomy-policy gating rules beyond registering `chapter.mined` in
  `_NEVER_GATED` alongside the six existing mined-eligible types (already all in
  `_NEVER_GATED` — see `novelizer/canon/policy.py`). `secret.revealed` stays in
  `_CANON_EVENTS` and mining **never** calls `Committer.commit` with it — mined reveals
  are always converted to a `retcon_request.created`, unconditionally, before any
  autonomy check is even relevant.
- Payload models: adding `source: str = "declared"` is replay-compatible because no
  payload model in `novelizer/canon/events.py` sets `model_config`/`extra="forbid"`
  (verified) — old events without the field parse fine with the default.
- Mining reuses `BaseAgent._commit_knowledge_intents` / `_commit_thread_intents` /
  `_commit_causal_intents` (`novelizer/agents/base.py`) for the actual commit — **not**
  reimplemented in `continuity_checker.py`. Those methods currently build payloads with
  no `source` param — Task 1 threads a `source` argument through them (default
  `"declared"`, so every existing caller/test is unaffected) before the miner is wired
  up in Task 5, so at every point in this plan the suite stays green.
- TDD, black-box-first: every task starts with a failing test on `ReadStore`/`Committer`-
  visible output, not internals. Hypothesis property tests generalize mining-dedup
  idempotency and `source`-field replay compatibility.
- Do **not** create any `.env` file. Every task ends by running the **full** suite
  (`uv run pytest`) and reporting real failures.

## Sequencing hazards flagged up front

1. **`source` param must land in `BaseAgent`'s three `_commit_*_intents` methods before
   any test asserts a mined event's `source` field** — Task 1 does this in isolation
   (default-valued, non-breaking) so Tasks 2-4 (schema/event-type/policy) can proceed
   without the miner existing yet, and Task 5 (the miner itself) is the first caller to
   pass `source="mined"`. This avoids the M3.1 lesson (property test sequenced before its
   accessors exist) by doing the additive plumbing change first, self-testing it in
   place, then building the feature that exercises it.
2. **`chapter.mined` event type must be registered in `events.py` + `policy.py` before
   Task 5's miner references `EventType.CHAPTER_MINED`** — Tasks 2-3 land it first.
3. **The raw event-log scan for thread dedup and for `mined_chapters` (chapters lacking
   `chapter.mined`) both read `EventStore.events_since(0, event_types=[...])` directly**,
   bypassing `ReadStore` — this is intentional (Locked decision 4/2: no new projection).
   Task 5 introduces a small `novelizer/brain/mining.py` helper module for these two log
   scans so `continuity_checker.py` doesn't hand-roll SQL-adjacent logic inline, and so
   the helper is independently unit-testable (Task 4 builds it before Task 5 wires it
   into the agent — ordered so the accessor exists before the property test that uses
   it, again avoiding the M3.1 lesson).
4. **The decomposition's `MinedFactsOutput` needs to look like real prose-derived
   facts, not just re-declare `KnowledgeIntent`/`ThreadIntent`/`CausalIntent`** — those
   existing schemas assume agent self-knowledge (`id` cites a *known* id, confidence is
   implicit). Mining needs an explicit confidence signal to route ambiguous facts to
   retcon. Task 1 (schema task, actually Task 2 below) defines new mined-fact-specific
   models (`MinedSecretFact`, `MinedThreadFact`, `MinedCausalFact`) rather than reusing
   `KnowledgeIntent`/`ThreadIntent`/`CausalIntent` verbatim, each carrying a
   `known_id: bool` (or equivalent ambiguity signal) the miner sets, and Task 5's commit
   logic branches on it. **Flagging this as a deliberate reinterpretation**: the
   decomposition doesn't spell out the exact mined-fact schema shape, only the outcome
   (auto-commit clean facts, escalate ambiguous/reveal ones) — this plan fills that gap
   with schemas designed for that outcome rather than force-fitting the declared-intent
   schemas, which have no ambiguity field today.
5. **Author truncation note for the live_llm task (Task 11)**: `author.py`'s
   `_summarize` truncates each previous chapter's prose to `c.prose[:200]` when building
   the next chapter's context (`novelizer/agents/author.py:19`). This only matters for
   the fixture chapter the live smoke *withholds* `known_secrets_note()` from — that
   withholding happens in the test's own call construction, not through `_summarize`, so
   truncation doesn't interact with the leak-engineering mechanism directly. It **does**
   matter if the smoke later chains a second chapter off the leak chapter's prose (it
   does not, per this plan — the smoke runs the Author once, then mining once, then
   asserts). Noted here per the decomposition's explicit warning; no plan step depends on
   prose past character 200 surviving into a later Author call.

---

### Task 1: Thread a `source` parameter through `BaseAgent`'s intent-commit helpers

**Files:**
- Modify: `novelizer/agents/base.py`
- Test: `tests/agents/test_base.py`

**Interfaces:**
- `BaseAgent._commit_thread_intents(intents, active_thread_ids, chapter_id="", source="declared")`
- `BaseAgent._commit_knowledge_intents(intents, active_secret_ids, chapter_id="", allowed_actions=..., source="declared")`
- `BaseAgent._commit_causal_intents(intents, valid_chapter_ids, source="declared")`
- Each payload construction (`ThreadPlanted`, `ThreadTouched`, `ThreadPaidOff`,
  `SecretLearned`, `SecretReferenced`, `CausalEdgeDeclared`) passes `source=source`
  through to the (not-yet-existing) payload field — **this task only changes the method
  signatures and call sites; Task 2 adds the `source` field to the payload models.**
  Order matters: land this task's signature change first is fine because pydantic
  models ignore unknown kwargs only if `extra` allows it — **actually land Task 2's
  payload-model change first if running these two tasks out of order causes a
  `TypeError: unexpected keyword argument`.** To keep every task independently green,
  this plan sequences Task 2 (payload `source` field) **before** Task 1's call-site
  changes take effect — see the reordering note below.

**Reordering note (read before starting):** Task 2 (payload models gain `source` field)
must land before this task's call sites pass `source=` into payload constructors, or the
constructor calls raise `TypeError`. Do Task 2 first, then this task. The numbering in
this plan reflects narrative order (plumbing-before-feature) but the **execution order
is: Task 2, then Task 1's method-signature changes, then Task 3+.** Renumber your local
tracking as Task 1 = "payload `source` fields" and Task 2 = "`BaseAgent` param
threading" if that's less confusing — the content below is written so either label
works, but implement payload models first.

- [ ] **Step 1: Write the failing tests**

Append to `tests/agents/test_base.py` (read the existing file first for fixture/import
style — it already constructs a bare `BaseAgent` subclass or uses `Author`/`Editor`
fixtures; match whichever pattern is already there):

```python
async def test_commit_thread_intents_defaults_source_to_declared(stack_or_fixture):
    # Arrange a ThreadIntent(action="plant", name="A Thread"), call
    # agent._commit_thread_intents([intent], set()), catch up the projector,
    # read the raw event via events_since(0, event_types=[EventType.THREAD_PLANTED]),
    # assert event.payload["source"] == "declared".
    ...

async def test_commit_thread_intents_accepts_explicit_source(stack_or_fixture):
    # Same, but call with source="mined"; assert event.payload["source"] == "mined".
    ...

async def test_commit_knowledge_intents_accepts_explicit_source(stack_or_fixture):
    # KnowledgeIntent(action="learn", id=<existing secret>, character_id="mara"),
    # call with source="mined", assert the resulting SECRET_LEARNED event's
    # payload["source"] == "mined".
    ...

async def test_commit_causal_intents_accepts_explicit_source(stack_or_fixture):
    # CausalIntent(cause_chapter_id="c1", effect_chapter_id="c2"), call with
    # source="mined", assert CAUSAL_EDGE_DECLARED payload["source"] == "mined".
    ...
```

Use the exact fixture/stack pattern already present in `tests/agents/test_base.py` —
read that file in full before writing these tests so imports and the test-double agent
class match existing style exactly.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_base.py -v -k source`
Expected: FAIL — `TypeError: _commit_thread_intents() got an unexpected keyword argument 'source'` (method doesn't accept the param yet), or `KeyError: 'source'` once the param is added but the payload model doesn't carry the field. Implement both this task and Task 2 together if your test runner requires both to compile — but write the payload-model change (Task 2) FIRST so this task's constructor calls don't `TypeError`.

- [ ] **Step 3: Implement**

In `novelizer/canon/events.py`, add `source: str = "declared"` to these six payload
models only (the `_NEVER_GATED` fact types mining is scoped to — NOT `SecretCreated`,
which mining never emits per the milestone's non-goals: mining never mints new secret
identity, only cites existing ids):
`ThreadPlanted`, `ThreadTouched`, `ThreadPaidOff`, `SecretLearned`, `SecretReferenced`,
`CausalEdgeDeclared`. Add a one-line docstring note to each: `"source distinguishes
agent-declared facts ('declared', default) from Continuity Checker prose-mined facts
('mined') -- see M5.1."`

Then in `novelizer/agents/base.py`, add `source: str = "declared"` as the last parameter
of `_commit_thread_intents`, `_commit_knowledge_intents`, `_commit_causal_intents`, and
pass `source=source` into every `ThreadPlanted(...)`, `ThreadTouched(...)`,
`ThreadPaidOff(...)`, `SecretLearned(...)`, `SecretReferenced(...)`,
`CausalEdgeDeclared(...)` construction inside those three methods. Do **not** add
`source` to `SecretCreated(...)` (plant path) — leave that constructor call unchanged;
`SecretCreated` gets no `source` field in this task, matching the payload-model list
above.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_base.py -v`
Expected: PASS (all prior tests + 4 new, including every existing caller of
`_commit_*_intents` across `tests/agents/test_author.py`, `test_editor.py`,
`test_character_keeper.py` still passing unmodified — they never pass `source`, so they
get the `"declared"` default). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/events.py novelizer/agents/base.py tests/agents/test_base.py
git commit -m "feat: source field on declared-fact payloads, threaded through BaseAgent commit helpers"
```

---

### Task 2: `chapter.mined` event type — registration only, no behavior yet

**Files:**
- Modify: `novelizer/canon/events.py`, `novelizer/canon/policy.py`
- Test: `tests/canon/test_committer.py` (or `tests/canon/test_policy.py` if that file
  exists — check first; use whichever the codebase already has for policy assertions)

**Interfaces:**
- `EventType.CHAPTER_MINED = "chapter.mined"`
- `ChapterMined(BaseModel)` with a single field `chapter_id: str` in `events.py`.
- `EventType.CHAPTER_MINED` added to `AutonomyPolicy._NEVER_GATED` in `policy.py`.

- [ ] **Step 1: Write the failing test**

Check for an existing policy test file first:
```bash
ls tests/canon/ | grep -i polic
```
If `tests/canon/test_policy.py` exists, append there; otherwise append to
`tests/canon/test_committer.py` next to any existing `_NEVER_GATED` assertions (search
for `is_gated` in that file to find the pattern). Add:

```python
async def test_chapter_mined_is_never_gated(stack):  # match existing fixture name
    from novelizer.canon.events import EventType
    from novelizer.canon.policy import AutonomyPolicy
    events, proj, read, committer = stack  # or whatever the local fixture unpacks to
    policy = AutonomyPolicy(read)
    assert await policy.is_gated("continuity_checker", EventType.CHAPTER_MINED) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/canon/ -v -k chapter_mined`
Expected: FAIL — `AttributeError: type object 'EventType' has no attribute 'CHAPTER_MINED'`.

- [ ] **Step 3: Implement**

In `novelizer/canon/events.py`, add `CHAPTER_MINED = "chapter.mined"` to `EventType`
(alongside `ANNOTATION_STRUCTURE_SCORED`, same section). Add:

```python
class ChapterMined(BaseModel):
    """Payload for chapter.mined -- bookkeeping marker that the prose-mining
    pass has run for this chapter. Never projected (no _apply branch in
    Projector), same class as AgentRemark. Gives mining idempotency without
    a new persisted 'already mined' flag or a re-scan of the full log's
    prose every cycle (M5.1 Locked decision 2).
    """

    chapter_id: str
```

In `novelizer/canon/policy.py`, add `EventType.CHAPTER_MINED` to the `_NEVER_GATED` set.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/canon/ -v`
Expected: PASS. Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/events.py novelizer/canon/policy.py tests/canon/
git commit -m "feat: register chapter.mined as a never-gated, unprojected bookkeeping event"
```

---

### Task 3: `MinedFactsOutput` and mined-fact schemas

**Files:**
- Modify: `novelizer/agents/schemas.py`
- Test: `tests/agents/test_schemas.py` if it exists, else a new
  `tests/agents/test_mined_schemas.py`

**Interfaces:**
- `MinedSecretFact(BaseModel)`: `action: Literal["learn", "uses"]`, `id: str`,
  `character_id: str`, `chapter_id: str`, `known_id: bool = True`, `note: str = ""`.
  (No `"reveal"` action here — mined reveals are modeled separately below because their
  handling is unconditional-escalate, not confidence-gated.)
- `MinedRevealFact(BaseModel)`: `id: str`, `chapter_id: str`, `note: str = ""`,
  `known_id: bool = True`. Always escalates regardless of `known_id` (Locked decision 3)
  — the field is kept for a consistent shape and for the retcon description to say
  whether the id was even recognized, not to gate behavior.
- `MinedThreadFact(BaseModel)`: `action: Literal["touch", "planted", "paid_off"]`,
  `id: str`, `chapter_id: str`, `known_id: bool = True`, `note: str = ""`. (Mining
  reports thread facts against *existing* ids only — mining never mints new thread
  identity via `plant`; if the prose implies a brand-new thread the miner doesn't
  recognize, that's exactly the `known_id=False` ambiguous case, escalated to retcon,
  matching Locked decision 3's "never invents an id" rule. So `action` here is really
  "touch" or "paid_off" in the common case, with `known_id=False` covering the plant-like
  ambiguous case — do not add a `"plant"` action to this model.)
- `MinedCausalFact(BaseModel)`: `cause_chapter_id: str`, `effect_chapter_id: str`,
  `note: str = ""`. No `known_id` field — causal facts cite chapter ids, which the miner
  always has from `ctx["chapters"]`/`chapter_order` (no ambiguity axis for chapters the
  way there is for secret/thread ids); dedup for these is exact-triple-match (Task 5),
  not an escalate-on-ambiguity path.
- `MinedFactsOutput(BaseModel)`: `secret_facts: list[MinedSecretFact] = []`,
  `reveal_facts: list[MinedRevealFact] = []`, `thread_facts: list[MinedThreadFact] = []`,
  `causal_facts: list[MinedCausalFact] = []`, `feed_note: str = ""`.

- [ ] **Step 1: Write the failing tests**

Create (or append to) the test file:

```python
def test_mined_facts_output_defaults_to_empty():
    from novelizer.agents.schemas import MinedFactsOutput
    out = MinedFactsOutput()
    assert out.secret_facts == [] and out.reveal_facts == [] and out.thread_facts == [] and out.causal_facts == []


def test_mined_secret_fact_defaults_known_id_true():
    from novelizer.agents.schemas import MinedSecretFact
    f = MinedSecretFact(action="uses", id="s1", character_id="mara", chapter_id="c1")
    assert f.known_id is True


def test_mined_secret_fact_can_declare_unknown_id():
    from novelizer.agents.schemas import MinedSecretFact
    f = MinedSecretFact(action="uses", id="s-guessed", character_id="mara", chapter_id="c1", known_id=False)
    assert f.known_id is False


def test_mined_reveal_fact_shape():
    from novelizer.agents.schemas import MinedRevealFact
    f = MinedRevealFact(id="s1", chapter_id="c1")
    assert f.note == "" and f.known_id is True


def test_mined_thread_fact_action_is_restricted():
    from novelizer.agents.schemas import MinedThreadFact
    import pydantic
    with pytest.raises(pydantic.ValidationError):
        MinedThreadFact(action="plant", id="t1", chapter_id="c1")


def test_mined_causal_fact_has_no_known_id_field():
    from novelizer.agents.schemas import MinedCausalFact
    f = MinedCausalFact(cause_chapter_id="c1", effect_chapter_id="c2")
    assert not hasattr(f, "known_id")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_mined_schemas.py -v` (or wherever you placed it)
Expected: FAIL — `ImportError: cannot import name 'MinedFactsOutput'`.

- [ ] **Step 3: Implement**

Append to `novelizer/agents/schemas.py` the five classes exactly as specified in this
task's Interfaces section above, each with a short docstring citing the M5.1 plan and
Locked decision 3 for the `known_id` escalation semantics.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_mined_schemas.py -v` (or your chosen path)
Expected: PASS. Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/schemas.py tests/agents/
git commit -m "feat: MinedFactsOutput and mined-fact schemas for prose mining"
```

---

### Task 4: `novelizer/brain/mining.py` — pure log-scan dedup helpers

**Files:**
- Create: `novelizer/brain/mining.py`
- Test: `tests/brain/test_mining.py`

**Interfaces:**
- `MINED_SOURCE_TAG = "[source: prose_miner]"` (module constant — owned here since this
  module is the mining-support module; `continuity_checker.py` imports it).
- `already_mined_chapter_ids(mined_events: list[StoredEvent]) -> set[str]` — given raw
  `chapter.mined` events (caller fetches via `EventStore.events_since(0,
  event_types=[EventType.CHAPTER_MINED])`), returns the set of chapter ids already
  marked mined. Pure function over `StoredEvent.payload["chapter_id"]`.
- `thread_touch_log(thread_events: list[StoredEvent]) -> set[tuple[str, str]]` — given
  raw `thread.*` events (caller fetches via `events_since(0, event_types=[THREAD_PLANTED,
  THREAD_TOUCHED, THREAD_PAID_OFF, THREAD_ABANDONED])`), returns the set of
  `(thread_id, chapter_id)` pairs already present in the log, reading `payload["id"]` and
  `payload["chapter_id"]` off each event. This is the "mining-only log read" Locked
  decision 4 names — `ThreadsProjection` holds aggregate state, not this per-chapter
  history, so this helper reads the raw log directly rather than a `ReadStore` accessor.
  Skips events with an empty `chapter_id` (declared intents before M3.1 always set
  `chapter_id`, but be defensive since the field defaults to `""`).

Both functions take pre-fetched `StoredEvent` lists (not an `EventStore`), keeping them
pure and unit-testable without a DB fixture — `continuity_checker.py` does the fetch in
`poll()` and passes results in, matching the plan's black-box-first + pure-function
precedent (`novelizer/brain/leaks.py`, `paradoxes.py`).

- [ ] **Step 1: Write the failing tests**

Create `tests/brain/test_mining.py`:

```python
from novelizer.brain.mining import MINED_SOURCE_TAG, already_mined_chapter_ids, thread_touch_log
from novelizer.canon.events import StoredEvent


def _ev(event_type, payload):
    return StoredEvent(sequence=1, id="e1", event_type=event_type, aggregate_id="a", payload=payload, created_at="2026-01-01T00:00:00Z")


def test_already_mined_chapter_ids_reads_chapter_id_field():
    events = [_ev("chapter.mined", {"chapter_id": "c1"}), _ev("chapter.mined", {"chapter_id": "c2"})]
    assert already_mined_chapter_ids(events) == {"c1", "c2"}


def test_already_mined_chapter_ids_empty_on_no_events():
    assert already_mined_chapter_ids([]) == set()


def test_thread_touch_log_pairs_id_and_chapter_id():
    events = [
        _ev("thread.planted", {"id": "t1", "chapter_id": "c1"}),
        _ev("thread.touched", {"id": "t1", "chapter_id": "c2"}),
    ]
    assert thread_touch_log(events) == {("t1", "c1"), ("t1", "c2")}


def test_thread_touch_log_skips_blank_chapter_id():
    events = [_ev("thread.touched", {"id": "t1", "chapter_id": ""})]
    assert thread_touch_log(events) == set()


def test_mined_source_tag_is_pinned():
    assert MINED_SOURCE_TAG == "[source: prose_miner]"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/brain/test_mining.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'novelizer.brain.mining'`.

- [ ] **Step 3: Implement**

Create `novelizer/brain/mining.py`:

```python
from __future__ import annotations
from novelizer.canon.events import StoredEvent

MINED_SOURCE_TAG = "[source: prose_miner]"


def already_mined_chapter_ids(mined_events: list[StoredEvent]) -> set[str]:
    """Chapter ids that already have a chapter.mined marker -- the caller
    fetches these via EventStore.events_since(0, event_types=[CHAPTER_MINED])
    and passes the raw events in. Pure function, no DB access (M5.1 Locked
    decision 2's idempotency mechanism).
    """
    return {e.payload["chapter_id"] for e in mined_events}


def thread_touch_log(thread_events: list[StoredEvent]) -> set[tuple[str, str]]:
    """(thread_id, chapter_id) pairs already present in the raw thread.*
    event log -- a mining-only log read (M5.1 Locked decision 4).
    ThreadsProjection holds aggregate state, not per-chapter touch history,
    so mining dedups against the log directly rather than a new
    projection. Events with an empty chapter_id are skipped (nothing to
    dedup against).
    """
    pairs: set[tuple[str, str]] = set()
    for e in thread_events:
        chapter_id = e.payload.get("chapter_id", "")
        if not chapter_id:
            continue
        pairs.add((e.payload["id"], chapter_id))
    return pairs
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/brain/test_mining.py -v`
Expected: PASS (5 new). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/brain/mining.py tests/brain/test_mining.py
git commit -m "feat: pure log-scan dedup helpers for prose mining (chapter.mined marker, thread touch log)"
```

---

### Task 5: `ContinuityChecker` — poll gains `mined_chapters`; wire the second LLM call and commit branch

**Files:**
- Modify: `novelizer/agents/continuity_checker.py`
- Test: `tests/agents/test_continuity_checker.py`

**Interfaces:**
- `ContinuityChecker.poll()` gains `"mined_chapters": list[Chapter]` — chapters from
  `ctx["chapters"]` (the already-fetched `list_chapters()` result, not a new query)
  filtered to exclude ids in `already_mined_chapter_ids(await
  self._read... )` — wait, `already_mined_chapter_ids` takes raw `StoredEvent`s, so
  `poll()` must also fetch those via a new small helper on `ContinuityChecker` itself:
  `self._events` is **not** currently injected into `ContinuityChecker` (only
  `ReadStore`/`Committer` are) — this is the one constructor change this task makes:
  `ContinuityChecker.__init__` gains an `event_store: EventStore` parameter (positional,
  after `committer`, before `interval`, matching the existing parameter order convention
  in this codebase — check `interval`'s position in the current signature and insert
  before it) so `poll()` can call `self._events.events_since(0,
  event_types=[EventType.CHAPTER_MINED])` and `self._events.events_since(0,
  event_types=[THREAD_PLANTED, THREAD_TOUCHED, THREAD_PAID_OFF, THREAD_ABANDONED])`
  directly. **This changes every call site that constructs `ContinuityChecker`** —
  search `rg "ContinuityChecker\("` across `novelizer/` and `tests/` before writing this
  task's implementation and update every constructor call (runtime wiring module,
  existing tests) to pass the event store. This is a breaking constructor-signature
  change; flag it loudly in the commit message.
- `ContinuityChecker.work()`: after the existing LLM contradiction-pass call, if
  `ctx["mined_chapters"]` is non-empty, makes a **second** `self._runner.ainvoke(...)`
  call per mined chapter (or one call covering all mined chapters in the batch — pick
  whichever keeps the prompt-construction code simplest; recommend one call per chapter
  since `chapter.mined` markers are per-chapter and a single failed/malformed response
  for one chapter shouldn't block marking others) requesting `MinedFactsOutput`, using a
  prompt built from that chapter's prose plus the current `knowledge_matrix`,
  `secret_references`, thread list (`ctx.get("threads")` — **note**: `ContinuityChecker.poll()`
  does not currently fetch `list_threads()`; add `"threads": await
  self._read.list_threads()` to `poll()`'s returned dict in this task, since mining needs
  the active-thread-id list the same way Author/Editor's `_commit_thread_intents` calls
  need it), and `causal_edges`. Returns a `dict[chapter_id, MinedFactsOutput]` (or a list
  of `(chapter_id, MinedFactsOutput)` pairs) alongside the existing `ContinuityOutput` —
  **`work()`'s return type changes** from `ContinuityOutput | None` to a small container;
  simplest: return a `tuple[ContinuityOutput | None, dict[str, MinedFactsOutput]]` and
  update `run_once()`/`commit()`'s signature to match.
- `ContinuityChecker.commit()`: for each `(chapter_id, mined_out)` pair —
  - For each `MinedSecretFact` in `mined_out.secret_facts`: if `known_id` is `False` or
    `fact.id` not in the active-secret-id set (`{s.id for s in ctx["secrets"]}` —
    **`poll()` also needs `"secrets": await self._read.list_secrets()`**, add it), file a
    `retcon_request.created` tagged `MINED_SOURCE_TAG` describing the ambiguous fact, do
    not commit a secret event. Otherwise, dedup: skip if a matching committed reference/
    learn already exists at the same-poll snapshot (secret+character pair present in
    `ctx["secret_references"]` for `action="uses"`, or in `knowledge_matrix[id]["known_by"]`
    for `action="learn"`) — else call `self._commit_knowledge_intents([KnowledgeIntent(
    action=fact.action, id=fact.id, character_id=fact.character_id, note=fact.note)],
    active_secret_ids, chapter_id=chapter_id, allowed_actions=frozenset({"learn",
    "uses"}), source="mined")`.
  - For each `MinedRevealFact`: **always** file a `retcon_request.created` tagged
    `MINED_SOURCE_TAG`, never call `_commit_knowledge_intents` with `action="reveal"` —
    per Locked decision 3, unconditional, regardless of `known_id`.
  - For each `MinedThreadFact`: if `known_id` is `False` or `fact.id` not in the active-
    thread-id set, file a `retcon_request.created` tagged `MINED_SOURCE_TAG`. Otherwise
    dedup against `thread_touch_log(...)` for `(fact.id, chapter_id)` — skip if present —
    else call `self._commit_thread_intents([ThreadIntent(action=fact.action, id=fact.id,
    note=fact.note)], active_thread_ids, chapter_id=chapter_id, source="mined")`.
  - For each `MinedCausalFact`: dedup by exact `(cause_chapter_id, effect_chapter_id)`
    triple match against `ctx["causal_edges"]` — skip if present — else call
    `self._commit_causal_intents([CausalIntent(cause_chapter_id=..., effect_chapter_id=...,
    note=...)], valid_chapter_ids, source="mined")`.
  - Finally, commit `ChapterMined(chapter_id=chapter_id)` via
    `self._committer.commit(self.name, EventType.CHAPTER_MINED, chapter_id,
    ChapterMined(chapter_id=chapter_id))` — **always**, even if the chapter produced zero
    mined facts (an empty `MinedFactsOutput` still means "mining ran for this chapter,"
    per Locked decision 2).
- `run_once()` updated to unpack `work()`'s new tuple return and pass both halves to
  `commit()`.

**Decision Note (ambiguity check ordering):** for secret/thread facts, check `known_id`
first (LLM's own confidence signal), then separately verify the cited id is actually in
the active-id set fetched this poll — a `known_id=True` fact citing a stale/wrong id
still routes to retcon, it doesn't get committed on the strength of the LLM's
self-reported confidence alone. This mirrors `_commit_knowledge_intents`'s existing
drop-on-unknown-id behavior, except mining escalates instead of silently dropping,
because Locked decision 3 requires that.

- [ ] **Step 1: Write the failing tests**

Read `tests/agents/test_continuity_checker.py` in full first (already read during
planning — see this plan's context) to match its fixture/`FakeRunner` style exactly.
`FakeRunner` currently returns one fixed `out` for every `ainvoke` call — for this
task's tests, you need a `FakeRunner` variant that returns *different* structured
responses on successive calls (first the `ContinuityOutput`, then per-chapter
`MinedFactsOutput`). Add a small `SequencedFakeRunner` (or extend `FakeRunner` to accept
a list and pop from it) local to this test file — do not change the shared `FakeRunner`
class signature in a way that breaks its other call sites in this same file.

Append tests covering (write these as real pytest functions, not pseudocode, following
the file's existing `stack` fixture and event-seeding helper conventions):

1. `test_mining_commits_a_secret_referenced_event_tagged_mined` — seed a chapter with no
   `secret.referenced` for a known secret+character, a `FakeRunner` sequence
   `[ContinuityOutput(), MinedFactsOutput(secret_facts=[MinedSecretFact(action="uses",
   id=<secret>, character_id=<char>, chapter_id=<chapter>)])]`, run `run_once()`, assert
   the resulting `secret.referenced` event's `payload["source"] == "mined"` and
   `find_leaks` (import from `novelizer.brain.leaks`) now flags it against the current
   matrix.
2. `test_mining_does_not_recommit_on_a_second_run_once` — run `run_once()` twice on the
   same seeded state (with the mock sequence set up so the second call's mining response
   would be ignored anyway); assert only one `secret.referenced` mined event exists and
   assert a `chapter.mined` event for that chapter id exists exactly once (idempotency
   via marker, per Task 4).
3. `test_mining_ambiguous_secret_fact_files_a_tagged_retcon_not_an_event` — `FakeRunner`
   mining response with `MinedSecretFact(..., known_id=False)`; assert no
   `secret.referenced`/`secret.learned` event lands, and a `retcon_request.created`
   event's `payload["description"]` starts with `MINED_SOURCE_TAG` (import from
   `novelizer.brain.mining`).
4. `test_mining_reveal_fact_always_escalates_never_auto_commits` — `MinedRevealFact` in
   the mining response; assert no `secret.revealed` event exists anywhere in the log
   (`events_since(0, event_types=[EventType.SECRET_REVEALED])` is empty) and a
   `MINED_SOURCE_TAG`-tagged retcon request exists.
5. `test_mining_causal_fact_dedups_against_exact_triple_match` — seed an existing
   `causal_edge.declared` for `(c1, c2)`, mining response repeats the same
   `MinedCausalFact(cause_chapter_id="c1", effect_chapter_id="c2")`; assert no second
   `causal_edge.declared` event is committed (only the originally-seeded one).
6. `test_mining_thread_fact_dedups_against_raw_log_scan` — seed a `thread.touched` event
   for `(thread_id, chapter_id)`, mining response repeats it; assert no second
   `thread.touched` event for that pair.
7. `test_mining_runs_only_for_chapters_without_a_mined_marker` — two chapters, one
   already has a `chapter.mined` event, `FakeRunner` sequence sized for only the
   un-marked chapter (assert `runner.calls` length matches: 1 contradiction call + 1
   mining call, not 2 mining calls) — asserting `poll()`'s `mined_chapters` excludes the
   marked one.
8. `test_poll_includes_threads_secrets_and_mined_chapters` — direct `poll()` assertion
   matching the new dict keys.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_continuity_checker.py -v`
Expected: FAIL — `TypeError: ContinuityChecker.__init__() missing 1 required positional
argument: 'event_store'` (or similar), since the constructor signature hasn't changed
yet, plus `KeyError`/`AttributeError` for the new poll keys and mining commit logic.

- [ ] **Step 3: Implement**

Modify `novelizer/agents/continuity_checker.py`:
- Add `event_store` param to `__init__` (store as `self._events`), update
  `super().__init__` call — **`BaseAgent.__init__` itself is unchanged**, `self._events`
  is a `ContinuityChecker`-only attribute, not passed to the base class.
- Update every other constructor call site: search and update
  `tests/agents/test_leak_live_llm.py` (`ContinuityChecker(build_continuity_checker_runner(settings),
  read, committer)` → add `events` as the third positional arg — check that fixture's
  `stack` already yields `events`, it does), and any runtime-wiring module under
  `novelizer/` (search `rg "ContinuityChecker\(" novelizer/` — likely a `runtime.py` or
  `main.py`/`tui/app.py` construction site) plus every test in
  `tests/agents/test_continuity_checker.py` itself (already updated as part of this
  task's test-writing step, but the pre-existing tests earlier in that file — e.g.
  `test_files_retcons_for_contradictions` — also construct `ContinuityChecker(...)` and
  need the new arg added, or `poll()`/`commit()` breaks for them too since `self._events`
  would be undefined).
- Extend `poll()`: add `"threads": await self._read.list_threads()`, `"secrets": await
  self._read.list_secrets()`, and `"mined_chapters"` computed via: fetch
  `mined_events = await self._events.events_since(0, event_types=[EventType.CHAPTER_MINED])`,
  `already_mined = already_mined_chapter_ids(mined_events)`, then filter
  `ctx["chapters"]` (the full `chapters` list already fetched, not the `[-10:]` slice) to
  those whose `id not in already_mined`.
- Extend `work()` to return the tuple described in this task's Interfaces, making the
  second `ainvoke` call per chapter in `ctx["mined_chapters"]`, building a prompt with
  that chapter's `prose`, the knowledge matrix, secret references, active thread list,
  and causal edges (mirror `_summarize`'s truncation-and-join style from `author.py` for
  consistency, but this is a fresh prompt — no code reuse required across files for a
  single string-building function).
- Extend `commit()` per the branching logic in this task's Interfaces section, importing
  `MINED_SOURCE_TAG`, `already_mined_chapter_ids`, `thread_touch_log` from
  `novelizer.brain.mining`, and `ChapterMined`, `EventType.CHAPTER_MINED` from
  `novelizer.canon.events`.
- Update `run_once()` to unpack `work()`'s new tuple.
- Update `build_continuity_checker_runner` only if the mining pass needs a distinct
  `response_format` — since `work()` makes two separate `ainvoke` calls with different
  expected structured responses (`ContinuityOutput` then `MinedFactsOutput` per chapter),
  and `deepagents.create_deep_agent` binds one fixed `response_format` at construction,
  **this is a real design tension**: either (a) build a second `deep_agent` instance
  inside `ContinuityChecker.__init__` bound to `MinedFactsOutput`, alongside the existing
  contradiction-pass runner, or (b) have `ContinuityChecker` accept a second `Runner`
  parameter (`mining_runner`) at construction. Recommend (b) — matches the "no hidden
  runner construction inside agent classes" pattern this codebase already follows
  (runners are always constructed by `build_*_runner` factory functions and injected).
  Add `mining_runner: Runner` as a constructor parameter (after `runner`, before
  `read_store`, or wherever fits the existing param order best) and a
  `build_continuity_mining_runner(settings)` factory function parallel to
  `build_continuity_checker_runner`, bound to `response_format=MinedFactsOutput`.
  **This is a second constructor-signature change on top of the `event_store` one** —
  update all the same call sites again for this param too, in the same pass.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_continuity_checker.py -v`
Expected: PASS (all prior + 8 new). Then `uv run pytest tests/ -v` for the full suite
green — pay special attention to any other test file that constructs `ContinuityChecker`
directly (grep confirmed this during Step 3) and to `novelizer/tui/app.py` or the
runtime-wiring module if it constructs the agent roster at startup.

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/continuity_checker.py tests/agents/test_continuity_checker.py <any updated call-site files>
git commit -m "feat: ContinuityChecker prose-mining pass — mined facts commit through Committer, tagged source=mined, ambiguous/reveal facts escalate to retcon"
```

---

### Task 6: Runtime wiring — construct and inject `mining_runner`/`event_store` wherever `ContinuityChecker` is built for real use

**Files:**
- Modify: whichever `novelizer/` module constructs the production agent roster (found
  via the `rg "ContinuityChecker\("` search in Task 5 — likely `novelizer/runtime.py`,
  `novelizer/tui/app.py`, or a `novelizer/agents/factory.py`; confirm exact path by
  searching before editing)
- Test: whatever test file already covers that wiring module's agent-roster
  construction (search for an existing test asserting all six/seven agents are
  constructed — likely `tests/test_runtime.py` or similar)

**Interfaces:** No new interfaces — this task only ensures the two constructor changes
from Task 5 (`event_store`, `mining_runner`) are correctly threaded through the one
place production code actually builds a `ContinuityChecker` for the live room, using
`build_continuity_mining_runner(settings)` from Task 5.

- [ ] **Step 1: Write the failing test**

If an existing test already asserts something like `assert isinstance(roster["continuity_checker"],
ContinuityChecker)` or exercises `runtime.build_room(...)`, extend it to assert the
`ContinuityChecker` instance's `self._events` is the same `EventStore` instance passed
into the runtime, and that `self._mining_runner` (or equivalent internal attribute name
chosen in Task 5) is not `None`. If no such test exists, add a minimal one in the
existing runtime test file following its established fixture pattern.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest <the runtime test file> -v -k continuity`
Expected: FAIL — `TypeError` from the still-old constructor call in production wiring
code (Task 5 only updated test call sites and the agent module itself, not this
production wiring module, by design — this task closes that gap).

- [ ] **Step 3: Implement**

Update the production construction call to pass `event_store` and
`build_continuity_mining_runner(settings)` alongside the existing
`build_continuity_checker_runner(settings)` call.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest <the runtime test file> -v`
Expected: PASS. Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add <runtime wiring file> <its test file>
git commit -m "fix: wire ContinuityChecker's mining_runner and event_store in production agent roster construction"
```

---

### Task 7: Property test — mining-dedup idempotency

**Files:**
- Test: `tests/agents/test_continuity_checker.py` (append)

**Interfaces:**
- Consumes: everything from Task 5 (the miner is fully wired by this point).

- [ ] **Step 1: Write the failing test**

```python
from hypothesis import given, settings as hyp_settings, strategies as st


@given(
    fact_count=st.integers(min_value=0, max_value=5),
    run_twice=st.booleans(),
)
@hyp_settings(max_examples=25, deadline=None)
async def test_mining_the_same_chapter_twice_never_double_commits(stack, fact_count, run_twice):
    """Idempotency invariant (M5.1 Locked decision 2): running run_once()
    against the same un-mined chapter any number of times with arbitrary
    mined-fact counts commits each distinct fact at most once, and always
    ends with exactly one chapter.mined marker for that chapter -- the
    marker absorbs repeat mining attempts regardless of what the second
    run's FakeRunner would have returned.
    """
    events, proj, read, committer = stack
    # Seed fact_count distinct existing secrets + a chapter + secret.referenced
    # events for a subset of them so some are already covered (no mined
    # commit expected) and some are not (mined commit expected on first run
    # only). Build a FakeRunner mining response citing all fact_count facts.
    # Run agent.run_once() once, then again if run_twice (with a runner
    # sequence whose second mining response, if consumed, would try to
    # recommit the same facts -- but poll()'s mined_chapters exclusion
    # should mean the second run's mining call for this chapter never
    # happens at all).
    # Assert: exactly one chapter.mined event for the chapter id, and the
    # count of mined-sourced secret.referenced events does not exceed
    # fact_count regardless of run_twice.
    ...
```

Write this out as real, executable test code (the docstring above states the invariant
precisely; implement the seeding/assertion using this file's existing `stack`
fixture and `_seed_leak`-style helpers as a model) rather than leaving it as a stub —
this is a required implementation step, not illustrative pseudocode.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/agents/test_continuity_checker.py -v -k idempotency`
Expected: given Task 5 is fully implemented, this should **pass on first run** — this
task is property-coverage of already-landed behavior (same formality as M4.2's Tasks 2/4
precedent). If it fails, that indicates a real gap in Task 5's `mined_chapters`
filtering or commit-dedup logic — stop and fix Task 5, do not weaken this test.

- [ ] **Step 3: N/A**

No production code changes expected. If Step 2 passes, proceed to Step 4.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_continuity_checker.py -v`
Expected: PASS (all prior + 1 new). Then `uv run pytest tests/ -v` for the full suite
green.

- [ ] **Step 5: Commit**

```bash
git add tests/agents/test_continuity_checker.py
git commit -m "test: Hypothesis property coverage for mining-dedup idempotency"
```

---

### Task 8: Property test — `source`-field replay compatibility

**Files:**
- Test: `tests/canon/test_projector.py` (append, matching its existing replay-test
  style — read it first)

**Interfaces:**
- Consumes: `Projector`, the six payload models with `source` (Task 1), `EventStore`.

- [ ] **Step 1: Write the failing test**

```python
@given(has_source=st.booleans())
@hyp_settings(max_examples=20, deadline=None)
async def test_thread_touched_replays_identically_with_and_without_source_field(has_source):
    """Replay-compatibility invariant (M5.1 Locked decision 1): a
    thread.touched event with the source field omitted (pre-M5.1 shape,
    simulated via append_raw with a payload dict lacking 'source') and one
    with source='declared' explicitly set produce byte-identical
    ThreadsProjection rows after catch_up() -- the added field is additive
    and does not change fold behavior, so old events in an existing log
    replay exactly as before.
    """
    # Build two fresh (events, proj, read) stacks. In stack A, append a
    # thread.planted then thread.touched via events.append_raw with a dict
    # payload that omits "source" entirely. In stack B, append the same
    # two events via the normal ThreadPlanted/ThreadTouched models (source
    # defaults to "declared"). catch_up() both. Assert
    # (await read_a.get_thread("t1")) == (await read_b.get_thread("t1")).
    ...
```

Use `EventStore.append_raw` (already exists, confirmed in `novelizer/canon/event_store.py`)
for the omitted-field case, and the normal `.append()` with the pydantic model for the
with-field case. Write this out as full executable test code.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/canon/test_projector.py -v -k replay`
Expected: given Task 1's payload models already default `source`, this should **pass on
first run** if `ThreadsProjection`'s fold never reads `payload["source"]` (it doesn't —
confirmed, `Projector._apply`'s `THREAD_TOUCHED`/etc branch only reads `id`,
`chapter_id`, `note`). If it fails, that's a real replay bug in Task 1 or in
`Projector._apply` — stop and fix, do not weaken this test.

- [ ] **Step 3: N/A**

No production code changes expected.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/canon/test_projector.py -v`
Expected: PASS. Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add tests/canon/test_projector.py
git commit -m "test: Hypothesis property coverage for source-field replay compatibility"
```

---

### Task 9: M5.1 done-when (a) — CI-mechanical chain test

**Files:**
- Test: `tests/agents/test_continuity_checker.py` (append)

**Interfaces:** No new production interfaces — traces the exact decomposition M5.1(a)
clause chain in one assertion sequence.

- [ ] **Step 1: Write the failing test**

```python
async def test_m5_1_done_when_mechanical_chain(stack):
    """M5.1 done-when (a), traced clause by clause:
    1. Seed a chapter's prose with an undeclared secret use (no
       secret.referenced event for it in the log) and a FakeRunner mining
       response declaring that use.
    2. Run ContinuityChecker.run_once() -> assert the resulting
       secret.referenced event exists, tagged source="mined".
    3. Assert find_leaks now flags it (mining feeds the existing
       deterministic detector, doesn't bypass it).
    4. A second run_once() against the same chapter does not re-commit the
       same mined fact (idempotency via chapter.mined).
    5. An ambiguous-mining fixture (known_id=False, unknown id) produces a
       retcon_request.created tagged MINED_SOURCE_TAG instead of a bad
       event.
    6. A mined-reveal fixture produces a retcon_request.created tagged
       MINED_SOURCE_TAG and NO secret.revealed event, at every autonomy
       level -- mined reveals never auto-commit. (Exercise this against a
       plain Committer here; a GatingCommitter variant is redundant since
       mining never even calls commit() with SECRET_REVEALED -- the
       assertion holds structurally, not by policy, but assert it under at
       least one GatingCommitter/AutonomyLevel.full_auto instance too for
       an explicit, literal per-decomposition-wording check.)
    """
    ...
```

Write full executable test code covering all six numbered clauses above as discrete
assertions within one test (or as clearly-named sub-steps within it) — this is the
plan's single most spec-traceable test, so name each assertion's decomposition clause in
an inline comment.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/agents/test_continuity_checker.py -v -k m5_1_done_when`
Expected: given Tasks 1-6 are complete, this should **pass on first run** (same
formality as M4.2's done-when task) — if any clause fails, that names exactly which
prior task has a gap; fix that task, do not weaken this test.

- [ ] **Step 3: N/A**

No production code changes expected.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_continuity_checker.py -v`
Expected: PASS. Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add tests/agents/test_continuity_checker.py
git commit -m "test: explicit M5.1 done-when (a) mechanical chain, traced clause by clause"
```

---

### Task 10: Docs — mark M5.1 complete

**Files:**
- Modify: `docs/submilestones/M5-finish.md`

- [ ] **Step 1: Update the M5.1 status**

In the sub-milestones table, change the M5.1 row's final `Status` cell from
`not started` to `complete (CI-proven; live smoke in Task 11, runnable but not
CI-blocking)`, matching M4's closeout-note precedent for documenting a live-smoke split.

- [ ] **Step 2: Commit**

```bash
git add docs/submilestones/M5-finish.md
git commit -m "docs: mark M5.1 CI-mechanical scope complete"
```

---

### Task 11: `live_llm` smoke — engineered leak, real mining pass, end-to-end catch (environment-dependent, not CI-blocking)

**Files:**
- Create: `tests/agents/test_prose_mining_live_llm.py`

**Interfaces:** Consumes the real `build_author_runner`, `build_continuity_checker_runner`,
`build_continuity_mining_runner` against `load_effective_settings()` — requires the
configured OpenAI-compatible endpoint reachable (per this repo's `NOVELIZER_*` env vars;
`http://192.168.1.14:8080/v1/` in this environment, per the task brief — do not hardcode
this URL in the test itself, only rely on `load_effective_settings()` picking it up from
environment/config as every other live_llm test in this repo already does).

**Design (per Locked decision 5 and the M4 closeout note this milestone exists to
close):** engineer the previously-unreachable failure mode directly by withholding
`known_secrets_note()` from the Author's context for one fixture chapter, rather than
hoping a live run produces an accidental leak (M4's closeout note: 20+ runs never did,
because the guardrail worked). This means **not** calling `Author.work()`/`_summarize`
as-is — construct the Author's prompt manually for this one fixture call, omitting the
`known_secrets_note(...)` line `_summarize` would normally splice in, so the real model
gets no guardrail and a genuine chance to leak in prose without declaring a `uses`
intent. Front-load any pressure text in the seeded prior-chapter prose within the first
200 characters, since `_summarize` truncates `c.prose[:200]` for prior chapters (this
smoke calls the Author directly for the *target* chapter, so this only matters if a
seeded *prior* chapter's prose is used as context — keep prior-chapter seeding minimal
or omit it if the fixture doesn't need prior-chapter context at all).

- [ ] **Step 1: Write the test**

```python
"""M5.1 done-when, part (b): the reliability claim mining exists to prove --
seed a fixture chapter written by the real Author with the known_secrets_note
guardrail deliberately withheld, so the Author has a real, engineered chance
to leak a secret in prose without declaring a uses intent (the scenario 20+
M4 runs never produced because the guardrail worked). Then run the real
mining pass and confirm the leak is mined, committed with source="mined",
and reaches the open retcon queue -- exercising the leak-catch path M4 could
observe every piece of except the catch itself.

Requires the configured OpenAI-compatible LLM endpoint
(load_effective_settings().llm_base_url) to be reachable. Run explicitly with:
uv run pytest -m live_llm tests/agents/test_prose_mining_live_llm.py -v
"""
import os
import tempfile
import pytest
from novelizer.settings import load_effective_settings
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType, SecretCreated, SecretLearned
from novelizer.agents.author import build_author_runner
from novelizer.agents.continuity_checker import (
    ContinuityChecker, build_continuity_checker_runner, build_continuity_mining_runner,
)
from novelizer.brain.mining import MINED_SOURCE_TAG
from novelizer.store.models import Character, RetconStatus


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    events = EventStore(path)
    await events.init()
    proj = Projector(events, path)
    await proj.init()
    read = ReadStore(path)
    await read.init()
    yield events, proj, read, Committer(events)
    await read.close()
    await proj.close()
    await events.close()
    os.unlink(path)


@pytest.mark.live_llm
async def test_engineered_leak_is_mined_and_reaches_the_retcon_queue(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHARACTER_CREATED, "mara", Character(id="mara", name="Mara"))
    await events.append(EventType.CHARACTER_CREATED, "kestrel", Character(id="kestrel", name="Kestrel"))
    await events.append(EventType.SECRET_CREATED, "the-heir-lives",
                        SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.SECRET_LEARNED, "the-heir-lives",
                        SecretLearned(id="the-heir-lives", character_id="mara"))
    await proj.catch_up()

    settings = load_effective_settings()

    # Manually build the Author's runner and prompt WITHOUT known_secrets_note
    # -- the one deliberate deviation this smoke test makes (Locked decision 5).
    author_runner = build_author_runner(settings)
    world = "None yet."
    chars = "- Mara: | arc: None\n- Kestrel: | arc: None"
    prev = "None yet."
    notes = "None."
    # No known_secrets_note() line -- the engineered guardrail withholding.
    prompt = (
        f"World lore:\n{world}\n\nCharacters:\n{chars}\n\nPrevious chapters:\n{prev}\n\n"
        f"Director notes:\n{notes}\n\nDirector: Write a chapter where Kestrel, acting on "
        f"knowledge she should not have, moves with certainty toward a secret only Mara "
        f"knows -- the heir lives. Do not have any character announce or reveal this "
        f"aloud; show Kestrel quietly acting on it.\n\nWrite the next chapter."
    )
    result = await author_runner.ainvoke({"messages": [{"role": "user", "content": prompt}]})
    draft = result.get("structured_response")
    assert draft is not None, "Author runner returned no structured_response -- endpoint/model config issue, not a plumbing bug."

    from novelizer.store.models import Chapter
    chapter = Chapter(title=draft.title, prose=draft.prose, character_ids=draft.character_ids or ["mara", "kestrel"])
    await events.append(EventType.CHAPTER_CREATED, chapter.id, chapter)
    await proj.catch_up()

    checker = ContinuityChecker(
        build_continuity_checker_runner(settings), read, committer, events,
        mining_runner=build_continuity_mining_runner(settings),
    )
    await checker.run_once()
    await proj.catch_up()

    references = await read.list_secret_references(secret_id="the-heir-lives")
    mined_refs = [r for r in references]  # source is on the raw event, not this read-model row -- check the log directly below
    log = await events.events_since(0, event_types=[EventType.SECRET_REFERENCED])
    mined_events = [e for e in log if e.payload.get("source") == "mined"]
    assert mined_events, (
        "STAGE 1 (mining pass): the real mining pass, given the Author's "
        "unguarded prose, did not mine a secret.referenced fact for Kestrel. "
        "This is a model/prompt signal on the mining prompt, not a plumbing "
        "failure -- re-run; if consistent, inspect the mining runner's raw "
        "structured output."
    )

    open_reqs = await read.list_retcon_requests(status=RetconStatus.open)
    leak_reqs = [r for r in open_reqs if "leak_detector" in r.description or "the-heir-lives" in r.description]
    assert leak_reqs, (
        "STAGE 2 (catch): the mined reference landed but no retcon request "
        "reached the open queue -- a real LeakDetector/ContinuityChecker "
        "regression, not model non-determinism."
    )
```

Adjust the exact constructor call for `ContinuityChecker(...)` in this test to match
whatever final parameter order Task 5/6 landed on (`event_store` positional,
`mining_runner` keyword-or-positional) — this task is written last precisely so the
constructor shape is already settled by the time it's implemented; if the implementer
finds the signature differs from what's shown here, use the real signature, not this
plan's guess.

- [ ] **Step 2: Run the test**

Run: `uv run pytest -m live_llm tests/agents/test_prose_mining_live_llm.py -v`
Expected: requires a reachable LLM endpoint. This is **not** run as part of the default
suite (`addopts = "-m 'not live_llm'"`) and is **not CI-blocking** — record the actual
pass/fail result and any STAGE-1/STAGE-2 troubleshooting notes in this plan's companion
closeout note (or directly in `docs/submilestones/M5-finish.md`'s M5.1 row) per
M3.3/M4 precedent for environment-dependent smoke tests.

- [ ] **Step 3: Commit**

```bash
git add tests/agents/test_prose_mining_live_llm.py
git commit -m "test: live leak-mining smoke — engineered Author guardrail withholding, real mining pass, real catch"
```

---

### Task 12: Full-suite verification

**Files:** none (verification only).

- [ ] **Step 1: Run the full test suite from a clean state**

Run: `uv run pytest tests/ -v`
Expected: every test passes (default `-m 'not live_llm'` deselects Task 11's smoke
automatically), including every pre-existing test from M0–M5.0. If anything is red,
diagnose per `superpowers:systematic-debugging` before considering M5.1 done. If a
failure looks unrelated to this branch's changes, confirm it fails identically on
`git stash` (with `-u` if untracked files are part of the diff) before calling it
pre-existing, then restore.

- [ ] **Step 2: Confirm no stray files**

Run: `git status`
Expected: working tree clean except for the commits made in Tasks 1–11 (no `.env` file,
no untracked scratch files).

---

## Self-Review

**1. Spec coverage:**
- `MinedFactsOutput` structured schema, second mining pass gated on `chapter.mined`
  absence — Tasks 2-3 (event type), 4 (dedup helpers), 5 (the pass itself).
- Auto-commit only `_NEVER_GATED` types, each payload carrying `source` — Task 1
  (payload fields), Task 5 (miner sets `source="mined"` via `BaseAgent` helpers).
- Mined reveals never auto-commit at any autonomy level, escalate to
  `MINED_SOURCE_TAG`-tagged retcon — Task 5 (unconditional branch), Task 9 (explicit
  done-when assertion at clause 6).
- `chapter.mined` new event type, `_NEVER_GATED`, unprojected — Task 2.
- Dedup: log-only, same-poll snapshot, secrets/causal/threads per the three distinct
  mechanisms the decomposition specifies — Task 4 (pure helpers), Task 5 (wiring).
- Deterministic analyzers unmodified — no task touches `novelizer/brain/leaks.py` or
  `paradoxes.py`; Task 9 clause 3 explicitly asserts mining feeds `find_leaks`, doesn't
  bypass it.
- CI done-when chain (a) — Task 9, clause-by-clause. Live smoke (b) — Task 11, with the
  engineered-guardrail-withholding mechanism per Locked decision 5.
- Property-based coverage: mining-dedup idempotency — Task 7. Source-field replay
  compatibility — Task 8.

**2. Sequencing hazards resolved:** the plan's preamble names five hazards; the most
significant is the `ContinuityChecker` constructor signature changing twice (event_store
in Task 5, mining_runner also in Task 5) which ripples into every call site — Task 5
itself updates test call sites, Task 6 is a dedicated task for the production wiring
call site specifically, so it isn't silently missed inside a larger task. Flagged
explicitly: this is a bigger blast radius than a typical additive M4-style task, because
`ContinuityChecker` is the one agent class whose constructor this milestone must change,
unlike M4.2 which only added `poll()` dict keys.

**3. Deviations from the decomposition flagged for reviewer visibility:**
- The decomposition names `MinedFactsOutput` but doesn't specify its internal field
  shape — this plan invents `MinedSecretFact`/`MinedRevealFact`/`MinedThreadFact`/
  `MinedCausalFact` with an explicit `known_id` ambiguity signal, since the existing
  `KnowledgeIntent`/`ThreadIntent`/`CausalIntent` schemas have no such field and mining's
  escalate-on-ambiguity behavior needs one. Reviewer should confirm this shape actually
  supports the LLM producing well-calibrated `known_id` values in practice (Task 11's
  live smoke is the only place this gets a real signal — CI tests only exercise the
  `FakeRunner` path, which trivially returns whatever `known_id` the test author sets).
- `ContinuityChecker` needing a second `Runner` (`mining_runner`) because
  `deepagents.create_deep_agent` binds one fixed `response_format` per agent instance is
  inferred from how the existing runner-factory pattern works, not stated in the
  decomposition — flagged in Task 5 as a real design tension with a recommended
  resolution (inject a second runner rather than construct one internally), not a
  silent reinterpretation.
