# M4.1 · Secret & Causal-Edge Ledgers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Branch:** `m4.1-secret-causal-ledgers` (create it before Task 1; this plan does not create it).

**Goal:** Two new canon event domains — `secret.*` (`created`, `learned`, `referenced`, `revealed`) and `causal_edge.*` (`declared`) — let Author/Editor (and, for `learned` only, CharacterKeeper) declare knowledge and causality bookkeeping in their existing structured output. Those intents flow through the existing `Committer`/`GatingCommitter` seam into the log; a `KnowledgeProjection` rebuilds a secret × character knowledge matrix (with a secret-level, set-once `revealed` flag) and a `CausalGraphProjection` rebuilds a causal adjacency list keyed by chapter id; `ReadStore` exposes both.

**Architecture:** Exactly the M3.1 precedent, reused rather than reinvented: additive canon through the existing `Committer`/`Projector`/`ReadStore` seams, no new seams. (1) **Secret identity is minted once, at `secret.created` time**, via `novelizer.canon.secrets.slugify_secret_name` — the same slug-from-freeform-name pattern as `slugify_thread_name`. `learn`/`reveal`/`uses` intents must cite an id drawn from the active-secret list already in the agent's context (`ReadStore.list_secrets()`, fetched fresh in each agent's `poll()`, exactly as M3.1 does for threads); an intent citing an unknown id is dropped with a logged warning and no event is committed. (2) **Causal edges have no minted identity and no lifecycle** — an edge is the triple `(cause_chapter_id, effect_chapter_id, note)` declared once; there is no touch/pay-off state machine to protect, so the `CausalGraphProjection` is a strict append (no dedup — see Task 11's rationale) validated only at commit time (both chapter ids must exist; self-edges are dropped). (3) **The knowledge matrix separates two concerns that the M3 thread ledger didn't need to**: a per-`(secret, character)` `learned` fact (a small `secret_knowledge` join table, idempotent insert) and a per-secret, set-once `revealed` flag stored on the secret's own record (never fanned out per character, so a character created after the reveal is still correctly `revealed` when the matrix accessor derives that character's cell — Locked decision #2). `secret.referenced` is durably recorded in its own `secret_references` table (never deduped, never inferred from transient `ChapterDraft` objects) precisely so M4.2's `LeakDetector` — out of scope here, but consuming this schema — can read committed history, not agent-turn-local state. (4) **Two sibling `BaseAgent` commit helpers**, `_commit_knowledge_intents` and `_commit_causal_intents`, mirror `_commit_thread_intents`'s shape (validate against an active-id/valid-id set, log-and-drop on failure, commit on success) without literal code reuse, since payload shapes differ. (5) **No cross-event transaction** — a single `run_once()` that creates a chapter and declares knowledge/causal intents performs N independent `Committer.commit()` appends, unchanged from M3.1's precedent.

**Tech Stack:** Python 3.13, `pydantic` v2, `aiosqlite`, `pytest`+`pytest-asyncio` (`asyncio_mode=auto`), `hypothesis>=6.156.6` (already used since M3.1's Task 5).

## Global Constraints

- Event sourcing: `secrets`, `secret_knowledge`, `secret_references`, `causal_edges` are Projector-owned projections, rebuildable from the log exactly like every other projection; only the Projector writes them.
- `secret.*` and `causal_edge.declared` are the only new event types in M4.1 (no `retcon_request.*` changes — that's M4.2).
- Autonomy (Locked decision #6): `secret.created`, `secret.learned`, `secret.referenced`, `causal_edge.declared` go into `AutonomyPolicy._NEVER_GATED`. `secret.revealed` is deliberately left **out** of `_NEVER_GATED` and instead added to `_CANON_EVENTS` (gated under `gated_canon`, per the module's existing `_GATED_SETS` wiring — no change to `is_gated`'s logic itself).
- Secret identity (Locked decision #1): an id is minted only once, at `secret.created`, via `slugify_secret_name(title)`; every other `secret.*` event type carries only an `id` reference, never a title. Any prose-producing agent (Author, Editor) may mint (`plant`); CharacterKeeper may declare `learn` only, never `plant`/`reveal`/`uses` — enforced by `_commit_knowledge_intents`'s `allowed_actions` parameter, not by CharacterKeeper's own ad hoc filtering.
- Knowledge matrix (Locked decision #2): three states per `(secret, character)` — `unknown` (default), `known` (a `secret.learned` row exists for that character), `revealed` (the secret's own record has `revealed=True`, which applies to *every* character, not written per cell). The fold is monotonic: a boolean can only flip `False → True`, never back — there is no `secret.unlearned` event in M4 (Locked decision #2's non-goal).
- Leak signal (Locked decision #3): a `secret.referenced` event is the durable `uses` record M4.2 reads; M4.1 only needs to commit and project it durably, not detect leaks.
- Causal edges (Locked decision #4): plain dicts/lists/SQL rows, no graph library; node identity is the chapter id; no dedup at commit or projection level (Task 11).
- No new dependencies (`networkx` explicitly rejected in the spec).
- DRY: knowledge-intent-to-event and causal-intent-to-event translation each live in exactly one place (`BaseAgent._commit_knowledge_intents`, `BaseAgent._commit_causal_intents`), called identically by every agent that uses them.
- TDD, black-box-first: every task starts with a failing test asserting on observable events/projections/output, not internals. Property tests (Tasks 7, 8) generalize the monotonic-fold and no-drop/no-duplicate invariants across any valid event sequence, per M3/M4's standing principle.
- Backward compatibility: `ChapterDraft.knowledge_intents`/`causal_intents`, `EditorVerdict.knowledge_intents`/`causal_intents`, and `KeeperOutput.knowledge_intents` all default to `[]`; when empty, the new commit-helper calls are no-ops — zero extra events, byte-identical behavior to pre-M4.1. The existing test suite stays green throughout every task.
- Do **not** create any `.env` file at any point in this plan. Every task ends by running the **full** suite (`uv run pytest`) and reporting real failures — never wave away a red test as "pre-existing" without first confirming it fails identically on `git stash` (i.e., before your change).

---

### Task 1: `secret.*`/`causal_edge.declared` event types, payload models, and id/matrix helpers

**Files:**
- Modify: `novelizer/canon/events.py`
- Create: `novelizer/canon/secrets.py`
- Test: `tests/canon/test_events.py`; new `tests/canon/test_secrets.py`

**Interfaces:**
- Produces: `EventType.SECRET_CREATED = "secret.created"`, `SECRET_LEARNED = "secret.learned"`, `SECRET_REFERENCED = "secret.referenced"`, `SECRET_REVEALED = "secret.revealed"`, `CAUSAL_EDGE_DECLARED = "causal_edge.declared"`; payload models `SecretCreated(id, title, chapter_id="", note="")`, `SecretLearned(id, character_id, chapter_id="", note="")`, `SecretReferenced(id, character_id, chapter_id="", note="")`, `SecretRevealed(id, chapter_id="", note="")`, `CausalEdgeDeclared(cause_chapter_id, effect_chapter_id, note="")` in `novelizer/canon/events.py`; `slugify_secret_name(title: str) -> str` and `knowledge_cell_state(matrix: dict[str, dict], secret_id: str, character_id: str) -> str` in `novelizer/canon/secrets.py`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/canon/test_events.py`:

```python
def test_secret_and_causal_edge_event_types_exist():
    from novelizer.canon.events import EventType
    assert EventType.SECRET_CREATED == "secret.created"
    assert EventType.SECRET_LEARNED == "secret.learned"
    assert EventType.SECRET_REFERENCED == "secret.referenced"
    assert EventType.SECRET_REVEALED == "secret.revealed"
    assert EventType.CAUSAL_EDGE_DECLARED == "causal_edge.declared"


def test_secret_payload_models_roundtrip():
    from novelizer.canon.events import (
        SecretCreated, SecretLearned, SecretReferenced, SecretRevealed,
    )
    created = SecretCreated(id="the-heir-lives", title="The Heir Lives", chapter_id="c1", note="planted")
    assert SecretCreated.model_validate_json(created.model_dump_json()) == created
    learned = SecretLearned(id="the-heir-lives", character_id="mara", chapter_id="c2", note="found the letter")
    assert SecretLearned.model_validate_json(learned.model_dump_json()) == learned
    referenced = SecretReferenced(id="the-heir-lives", character_id="mara", chapter_id="c3")
    assert SecretReferenced.model_validate_json(referenced.model_dump_json()) == referenced
    revealed = SecretRevealed(id="the-heir-lives", chapter_id="c4", note="public now")
    assert SecretRevealed.model_validate_json(revealed.model_dump_json()) == revealed


def test_causal_edge_declared_payload_roundtrips():
    from novelizer.canon.events import CausalEdgeDeclared
    edge = CausalEdgeDeclared(cause_chapter_id="c1", effect_chapter_id="c3", note="the fire forces the move")
    assert CausalEdgeDeclared.model_validate_json(edge.model_dump_json()) == edge
```

Create `tests/canon/test_secrets.py`:

```python
from novelizer.canon.secrets import slugify_secret_name, knowledge_cell_state


def test_slugify_lowercases_and_hyphenates():
    assert slugify_secret_name("The Heir Lives") == "the-heir-lives"


def test_slugify_strips_leading_trailing_punctuation():
    assert slugify_secret_name("  --Mara's Real Name!!--  ") == "mara-s-real-name"


def test_slugify_falls_back_when_title_has_no_alnum_chars():
    assert slugify_secret_name("###") == "secret"


def test_knowledge_cell_state_unknown_when_secret_missing():
    assert knowledge_cell_state({}, "the-heir-lives", "mara") == "unknown"


def test_knowledge_cell_state_unknown_when_not_learned():
    matrix = {"the-heir-lives": {"revealed": False, "known_by": set()}}
    assert knowledge_cell_state(matrix, "the-heir-lives", "mara") == "unknown"


def test_knowledge_cell_state_known_when_learned():
    matrix = {"the-heir-lives": {"revealed": False, "known_by": {"mara"}}}
    assert knowledge_cell_state(matrix, "the-heir-lives", "mara") == "known"


def test_knowledge_cell_state_revealed_applies_to_every_character():
    matrix = {"the-heir-lives": {"revealed": True, "known_by": set()}}
    assert knowledge_cell_state(matrix, "the-heir-lives", "a-character-created-after-the-reveal") == "revealed"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/canon/test_events.py tests/canon/test_secrets.py -v`
Expected: FAIL — `AttributeError: type object 'EventType' has no attribute 'SECRET_CREATED'` and `ModuleNotFoundError: No module named 'novelizer.canon.secrets'`.

- [ ] **Step 3: Implement**

In `novelizer/canon/events.py`, add the five event type constants to `EventType` (after `THREAD_ABANDONED`, before `ANNOTATION_STRUCTURE_SCORED`) and the five payload models (after `ThreadAbandoned`, before `AnnotationStructureScored`):

```python
class EventType:
    WORLD_ENTRY_CREATED = "world_entry.created"
    WORLD_ENTRY_SUPERSEDED = "world_entry.superseded"
    CHARACTER_CREATED = "character.created"
    CHARACTER_UPDATED = "character.updated"
    CHAPTER_CREATED = "chapter.created"
    CHAPTER_STATUS_CHANGED = "chapter.status_changed"
    DIRECTOR_SIGNAL_CREATED = "director_signal.created"
    DIRECTOR_SIGNAL_CONSUMED = "director_signal.consumed"
    RETCON_REQUEST_CREATED = "retcon_request.created"
    RETCON_REQUEST_RESOLVED = "retcon_request.resolved"
    RETCON_REQUEST_REJECTED = "retcon_request.rejected"
    PROPOSAL_CREATED = "proposal.created"
    PROPOSAL_APPROVED = "proposal.approved"
    PROPOSAL_REJECTED = "proposal.rejected"
    AUTONOMY_CHANGED = "autonomy.changed"
    AGENT_REMARKED = "agent.remarked"
    THREAD_PLANTED = "thread.planted"
    THREAD_TOUCHED = "thread.touched"
    THREAD_PAID_OFF = "thread.paid_off"
    THREAD_ABANDONED = "thread.abandoned"
    SECRET_CREATED = "secret.created"
    SECRET_LEARNED = "secret.learned"
    SECRET_REFERENCED = "secret.referenced"
    SECRET_REVEALED = "secret.revealed"
    CAUSAL_EDGE_DECLARED = "causal_edge.declared"
    ANNOTATION_STRUCTURE_SCORED = "annotation.structure_scored"
```

```python
class SecretCreated(BaseModel):
    """Payload for secret.created — mints a new secret's identity.

    `id` is the slug minted from `title` (see
    novelizer.canon.secrets.slugify_secret_name) at creation time; every
    later secret.* event for this secret must cite this id, never re-derive
    it (Locked decision #1). Any prose-producing agent (Author, Editor) may
    mint a secret; CharacterKeeper never does.
    """

    id: str
    title: str
    chapter_id: str = ""
    note: str = ""


class SecretLearned(BaseModel):
    """Payload for secret.learned — one character learns an existing secret,
    cited by id. Projected as a row in the secret_knowledge join table
    (idempotent: learning the same secret twice is a no-op, not a counter).
    """

    id: str
    character_id: str
    chapter_id: str = ""
    note: str = ""


class SecretReferenced(BaseModel):
    """Payload for secret.referenced — a character uses/references an
    existing secret in a chapter, cited by id. This is the durable,
    replayable 'uses' record M4.2's LeakDetector reads (Locked decision #3)
    — never deduped, every reference is its own fact.
    """

    id: str
    character_id: str
    chapter_id: str = ""
    note: str = ""


class SecretRevealed(BaseModel):
    """Payload for secret.revealed — an existing secret becomes public,
    cited by id. Secret-level, set-once: the KnowledgeProjection sets a
    `revealed` flag on the secret's own record once, never per character
    (Locked decision #2) — the matrix accessor derives `revealed` for every
    character, including ones created after this event.
    """

    id: str
    chapter_id: str = ""
    note: str = ""


class CausalEdgeDeclared(BaseModel):
    """Payload for causal_edge.declared — a claimed cause/effect relationship
    between two existing chapters. No minted identity and no lifecycle
    (Locked decision #4): every declaration is committed and projected as
    its own row, never deduped or superseded.
    """

    cause_chapter_id: str
    effect_chapter_id: str
    note: str = ""
```

Create `novelizer/canon/secrets.py`:

```python
from __future__ import annotations
import re

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify_secret_name(title: str) -> str:
    """Turn a freeform secret title into a stable, id-safe slug.

    Lowercases, collapses runs of non-alphanumeric characters into single
    hyphens, and strips leading/trailing hyphens. Called exactly once, at
    secret.created time, to mint a secret's aggregate_id — mirrors
    novelizer.canon.threads.slugify_thread_name exactly (see M4.1's Locked
    decision #1: same rule as threads, reused rather than reinvented).
    """
    slug = _SLUG_RE.sub("-", title.strip().lower()).strip("-")
    return slug or "secret"


def knowledge_cell_state(matrix: dict[str, dict], secret_id: str, character_id: str) -> str:
    """Derive one (secret, character) cell's state from the matrix returned
    by ReadStore.knowledge_matrix(): "unknown" | "known" | "revealed".

    `revealed` is secret-level state (Locked decision #2) — it is derived
    here for *any* character_id, including one created after the secret was
    revealed, rather than being looked up from a per-cell write. This keeps
    the matrix a simple two-flag structure (`revealed: bool`,
    `known_by: set[str]`) instead of fanning `revealed` out to every known
    character at event-application time, which would silently miss
    characters that didn't exist yet when the reveal happened.
    """
    entry = matrix.get(secret_id)
    if entry is None:
        return "unknown"
    if entry["revealed"]:
        return "revealed"
    return "known" if character_id in entry["known_by"] else "unknown"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/canon/test_events.py tests/canon/test_secrets.py -v`
Expected: PASS (all prior + 10 new). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/events.py novelizer/canon/secrets.py tests/canon/test_events.py tests/canon/test_secrets.py
git commit -m "feat: secret.*/causal_edge.declared event types, payloads, and id/matrix helpers"
```

---

### Task 2: Read-side models — `SecretRecord`, `CausalEdgeRecord`, `SecretReferenceRecord`

**Files:**
- Modify: `novelizer/store/models.py`
- Test: `tests/store/test_models.py`

**Interfaces:**
- Produces: `SecretRecord(BaseModel)` with fields `id: str`, `title: str`, `revealed: bool = False` — the row `KnowledgeProjection` (Task 4) stores in the `secrets` table and `ReadStore.list_secrets()`/`get_secret()` (Task 6) return. `CausalEdgeRecord(BaseModel)` with fields `cause_chapter_id: str`, `effect_chapter_id: str`, `note: str = ""` — returned by `ReadStore.list_causal_edges()` (Task 6). `SecretReferenceRecord(BaseModel)` with fields `secret_id: str`, `character_id: str`, `chapter_id: str = ""`, `note: str = ""` — returned by `ReadStore.list_secret_references()` (Task 6), the durable read path M4.2's `LeakDetector` will use.

- [ ] **Step 1: Write the failing test**

Append to `tests/store/test_models.py`:

```python
from novelizer.store.models import SecretRecord, CausalEdgeRecord, SecretReferenceRecord


def test_secret_record_defaults():
    s = SecretRecord(id="the-heir-lives", title="The Heir Lives")
    assert s.revealed is False


def test_secret_record_roundtrips_through_json():
    s = SecretRecord(id="the-heir-lives", title="The Heir Lives", revealed=True)
    again = SecretRecord.model_validate_json(s.model_dump_json())
    assert again == s


def test_causal_edge_record_defaults_and_roundtrips():
    e = CausalEdgeRecord(cause_chapter_id="c1", effect_chapter_id="c3")
    assert e.note == ""
    again = CausalEdgeRecord.model_validate_json(e.model_dump_json())
    assert again == e


def test_secret_reference_record_defaults_and_roundtrips():
    r = SecretReferenceRecord(secret_id="the-heir-lives", character_id="mara", chapter_id="c3")
    assert r.note == ""
    again = SecretReferenceRecord.model_validate_json(r.model_dump_json())
    assert again == r
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/store/test_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'SecretRecord' from 'novelizer.store.models'`.

- [ ] **Step 3: Implement**

In `novelizer/store/models.py`, add the three classes after `ThreadRecord` (before `StructureScore`):

```python
class SecretRecord(BaseModel):
    """Read-side row for a secret, built and rebuilt by the Projector from
    the secret.* event log (see novelizer/canon/projector.py). `revealed`
    is secret-level, set-once state (Locked decision #2 in M4's spec) — it
    is never written per character; ReadStore.knowledge_matrix() and
    novelizer.canon.secrets.knowledge_cell_state derive the per-character
    cell from this flag plus the secret_knowledge join table.
    """

    id: str
    title: str
    revealed: bool = False


class CausalEdgeRecord(BaseModel):
    """Read-side row for one declared causal edge, built by the Projector
    from causal_edge.declared events. No minted identity and no
    deduplication (Locked decision #4) — every declared edge, including an
    exact repeat, is its own row.
    """

    cause_chapter_id: str
    effect_chapter_id: str
    note: str = ""


class SecretReferenceRecord(BaseModel):
    """Read-side row for one secret.referenced event — the durable,
    replayable 'uses' record M4.2's LeakDetector reads (Locked decision #3).
    Never deduped: every reference is committed and projected as its own row.
    """

    secret_id: str
    character_id: str
    chapter_id: str = ""
    note: str = ""
```

(Only `SecretRecord`, `CausalEdgeRecord`, `SecretReferenceRecord` are new; every other class in the file is unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/store/test_models.py -v`
Expected: PASS. Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/store/models.py tests/store/test_models.py
git commit -m "feat: SecretRecord/CausalEdgeRecord/SecretReferenceRecord read-side models"
```

---

### Task 3: `secret.*`/`causal_edge.declared` autonomy gating

**Files:**
- Modify: `novelizer/canon/policy.py`
- Test: `tests/canon/test_policy.py`

**Interfaces:**
- Consumes: `EventType.SECRET_CREATED/SECRET_LEARNED/SECRET_REFERENCED/SECRET_REVEALED/CAUSAL_EDGE_DECLARED` (Task 1).
- Produces: no new public interface — `AutonomyPolicy._NEVER_GATED` gains `SECRET_CREATED`, `SECRET_LEARNED`, `SECRET_REFERENCED`, `CAUSAL_EDGE_DECLARED`; `AutonomyPolicy._CANON_EVENTS` gains `SECRET_REVEALED`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/canon/test_policy.py`:

```python
@pytest.mark.parametrize("level", list(AutonomyLevel))
@pytest.mark.parametrize("event_type", [
    EventType.SECRET_CREATED, EventType.SECRET_LEARNED,
    EventType.SECRET_REFERENCED, EventType.CAUSAL_EDGE_DECLARED,
])
async def test_knowledge_bookkeeping_events_are_never_gated(level, event_type):
    policy = AutonomyPolicy(FakeRead(AutonomyState(global_level=level)))
    assert await policy.is_gated("author", event_type) is False
    assert await policy.is_gated("editor", event_type) is False
    assert await policy.is_gated("character_keeper", event_type) is False


async def test_secret_revealed_is_gated_under_gated_canon_and_gated_all():
    policy = AutonomyPolicy(FakeRead(AutonomyState(global_level=AutonomyLevel.gated_canon)))
    assert await policy.is_gated("author", EventType.SECRET_REVEALED) is True
    policy_all = AutonomyPolicy(FakeRead(AutonomyState(global_level=AutonomyLevel.gated_all)))
    assert await policy_all.is_gated("author", EventType.SECRET_REVEALED) is True


async def test_secret_revealed_is_not_gated_under_full_auto_or_gated_retcons():
    policy_full = AutonomyPolicy(FakeRead(AutonomyState(global_level=AutonomyLevel.full_auto)))
    assert await policy_full.is_gated("author", EventType.SECRET_REVEALED) is False
    policy_retcons = AutonomyPolicy(FakeRead(AutonomyState(global_level=AutonomyLevel.gated_retcons)))
    assert await policy_retcons.is_gated("author", EventType.SECRET_REVEALED) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/canon/test_policy.py -v`
Expected: FAIL — `secret.created` etc. are currently gated under `gated_all` (not yet in `_NEVER_GATED`), and `secret.revealed` is currently never gated at all (not yet in `_CANON_EVENTS`), so `test_secret_revealed_is_gated_under_gated_canon_and_gated_all` fails.

- [ ] **Step 3: Implement**

In `novelizer/canon/policy.py`:

```python
from __future__ import annotations
from novelizer.canon.autonomy import AutonomyLevel
from novelizer.canon.events import EventType

_RETCON_EVENTS = {EventType.WORLD_ENTRY_SUPERSEDED, EventType.RETCON_REQUEST_RESOLVED}
_CANON_EVENTS = _RETCON_EVENTS | {
    EventType.WORLD_ENTRY_CREATED,
    EventType.CHARACTER_CREATED,
    EventType.CHARACTER_UPDATED,
    EventType.CHAPTER_CREATED,
    EventType.CHAPTER_STATUS_CHANGED,
    EventType.SECRET_REVEALED,
}
_NEVER_GATED = {
    EventType.DIRECTOR_SIGNAL_CREATED,
    EventType.DIRECTOR_SIGNAL_CONSUMED,
    EventType.AGENT_REMARKED,
    EventType.THREAD_PLANTED,
    EventType.THREAD_TOUCHED,
    EventType.THREAD_PAID_OFF,
    EventType.THREAD_ABANDONED,
    EventType.ANNOTATION_STRUCTURE_SCORED,
    EventType.SECRET_CREATED,
    EventType.SECRET_LEARNED,
    EventType.SECRET_REFERENCED,
    EventType.CAUSAL_EDGE_DECLARED,
}

_GATED_SETS: dict[AutonomyLevel, set[str]] = {
    AutonomyLevel.full_auto: set(),
    AutonomyLevel.gated_retcons: _RETCON_EVENTS,
    AutonomyLevel.gated_canon: _CANON_EVENTS,
    # gated_all is resolved dynamically in is_gated: everything not in _NEVER_GATED.
}


class AutonomyPolicy:
    """Reads the live AutonomyState from canon and decides what an agent may commit directly."""

    def __init__(self, read_store) -> None:
        self._read = read_store

    async def is_gated(self, agent_name: str, event_type: str) -> bool:
        if event_type in _NEVER_GATED:
            return False
        state = await self._read.get_autonomy_state()
        level = state.level_for(agent_name)
        if level == AutonomyLevel.gated_all:
            return True
        return event_type in _GATED_SETS.get(level, set())
```

(Only the four `SECRET_*`/`CAUSAL_EDGE_DECLARED` additions to `_NEVER_GATED` and the one `SECRET_REVEALED` addition to `_CANON_EVENTS` are new; `is_gated`'s logic is byte-identical.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/canon/test_policy.py -v`
Expected: PASS (all prior + new parametrized cases). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/policy.py tests/canon/test_policy.py
git commit -m "feat: secret.*/causal_edge.declared autonomy gating per M4.1 Locked decision #6"
```

---

### Task 4: `KnowledgeProjection` — `secrets`, `secret_knowledge`, `secret_references` tables

**Files:**
- Modify: `novelizer/canon/projector.py`
- Test: `tests/canon/test_projector.py`

**Interfaces:**
- Consumes: `EventType.SECRET_*` (Task 1); `SecretRecord` (Task 2).
- Produces: tables `secrets (id TEXT PRIMARY KEY, data TEXT NOT NULL)`, `secret_knowledge (secret_id TEXT NOT NULL, character_id TEXT NOT NULL, chapter_id TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', PRIMARY KEY (secret_id, character_id))`, `secret_references (secret_id TEXT NOT NULL, character_id TEXT NOT NULL, chapter_id TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '')`, maintained by `Projector._apply`; all three added to `Projector._reset_state`'s cleared-tables tuple.

- [ ] **Step 1: Write the failing tests**

Append to `tests/canon/test_projector.py`:

```python
async def _secret_rows(proj):
    cur = await proj._conn.execute("SELECT data FROM secrets ORDER BY rowid")
    return [json.loads(r[0]) for r in await cur.fetchall()]


async def test_secret_created_is_projected(wired):
    from novelizer.canon.events import SecretCreated
    events, proj, _ = wired
    await events.append(EventType.SECRET_CREATED, "the-heir-lives",
                        SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await proj.catch_up()
    rows = await _secret_rows(proj)
    assert len(rows) == 1
    assert rows[0]["id"] == "the-heir-lives" and rows[0]["revealed"] is False


async def test_secret_created_is_first_creation_wins(wired):
    from novelizer.canon.events import SecretCreated
    events, proj, _ = wired
    await events.append(EventType.SECRET_CREATED, "the-heir-lives",
                        SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.SECRET_CREATED, "the-heir-lives",
                        SecretCreated(id="the-heir-lives", title="A Different Title"))
    await proj.catch_up()
    rows = await _secret_rows(proj)
    assert len(rows) == 1
    assert rows[0]["title"] == "The Heir Lives"


async def test_secret_learned_inserts_knowledge_row(wired):
    from novelizer.canon.events import SecretCreated, SecretLearned
    events, proj, _ = wired
    await events.append(EventType.SECRET_CREATED, "the-heir-lives", SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.SECRET_LEARNED, "the-heir-lives",
                        SecretLearned(id="the-heir-lives", character_id="mara", chapter_id="c2"))
    await proj.catch_up()
    cur = await proj._conn.execute("SELECT character_id FROM secret_knowledge WHERE secret_id=?", ("the-heir-lives",))
    assert [r[0] for r in await cur.fetchall()] == ["mara"]


async def test_secret_learned_twice_by_same_character_is_idempotent(wired):
    from novelizer.canon.events import SecretCreated, SecretLearned
    events, proj, _ = wired
    await events.append(EventType.SECRET_CREATED, "the-heir-lives", SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.SECRET_LEARNED, "the-heir-lives", SecretLearned(id="the-heir-lives", character_id="mara"))
    await events.append(EventType.SECRET_LEARNED, "the-heir-lives", SecretLearned(id="the-heir-lives", character_id="mara"))
    await proj.catch_up()
    cur = await proj._conn.execute("SELECT character_id FROM secret_knowledge WHERE secret_id=?", ("the-heir-lives",))
    assert [r[0] for r in await cur.fetchall()] == ["mara"]


async def test_secret_referenced_is_never_deduped(wired):
    from novelizer.canon.events import SecretCreated, SecretReferenced
    events, proj, _ = wired
    await events.append(EventType.SECRET_CREATED, "the-heir-lives", SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.SECRET_REFERENCED, "the-heir-lives",
                        SecretReferenced(id="the-heir-lives", character_id="mara", chapter_id="c3"))
    await events.append(EventType.SECRET_REFERENCED, "the-heir-lives",
                        SecretReferenced(id="the-heir-lives", character_id="mara", chapter_id="c3"))
    await proj.catch_up()
    cur = await proj._conn.execute("SELECT COUNT(*) FROM secret_references WHERE secret_id=?", ("the-heir-lives",))
    assert (await cur.fetchone())[0] == 2


async def test_secret_revealed_sets_flag_once(wired):
    from novelizer.canon.events import SecretCreated, SecretRevealed
    events, proj, _ = wired
    await events.append(EventType.SECRET_CREATED, "the-heir-lives", SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.SECRET_REVEALED, "the-heir-lives", SecretRevealed(id="the-heir-lives", note="told the crowd"))
    await events.append(EventType.SECRET_REVEALED, "the-heir-lives", SecretRevealed(id="the-heir-lives", note="told again"))
    await proj.catch_up()
    rows = await _secret_rows(proj)
    assert rows[0]["revealed"] is True


async def test_reprojecting_secret_events_is_equivalent(wired):
    from novelizer.canon.events import SecretCreated, SecretLearned, SecretRevealed
    events, proj, path = wired
    await events.append(EventType.SECRET_CREATED, "the-heir-lives", SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.SECRET_LEARNED, "the-heir-lives", SecretLearned(id="the-heir-lives", character_id="mara"))
    await events.append(EventType.SECRET_REVEALED, "the-heir-lives", SecretRevealed(id="the-heir-lives"))
    await proj.catch_up()
    incremental = await _secret_rows(proj)
    proj2 = Projector(events, path)
    await proj2.init()
    await proj2._reset_state()
    await proj2.catch_up()
    from_scratch = await _secret_rows(proj2)
    await proj2.close()
    assert incremental == from_scratch


async def test_reset_state_clears_secret_tables(wired):
    from novelizer.canon.events import SecretCreated, SecretLearned, SecretReferenced
    events, proj, _ = wired
    await events.append(EventType.SECRET_CREATED, "the-heir-lives", SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.SECRET_LEARNED, "the-heir-lives", SecretLearned(id="the-heir-lives", character_id="mara"))
    await events.append(EventType.SECRET_REFERENCED, "the-heir-lives", SecretReferenced(id="the-heir-lives", character_id="mara"))
    await proj.catch_up()
    await proj._reset_state()
    for table in ("secrets", "secret_knowledge", "secret_references"):
        cur = await proj._conn.execute(f"SELECT COUNT(*) FROM {table}")
        assert (await cur.fetchone())[0] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/canon/test_projector.py -v`
Expected: FAIL — `sqlite3.OperationalError: no such table: secrets` (the tables don't exist yet).

- [ ] **Step 3: Implement**

In `novelizer/canon/projector.py`, add the import, three tables to `_CREATE`, the three table names to `_reset_state`, and three new `_apply` branches.

Add to the imports at the top of the file:

```python
from novelizer.store.models import ThreadRecord, ThreadState, SecretRecord
from novelizer.canon.threads import TERMINAL_STATES
```

Add to `_CREATE` (after the `threads` table, before `structure_scores`):

```sql
CREATE TABLE IF NOT EXISTS secrets (
    id TEXT PRIMARY KEY, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS secret_knowledge (
    secret_id TEXT NOT NULL, character_id TEXT NOT NULL, chapter_id TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '', PRIMARY KEY (secret_id, character_id)
);
CREATE TABLE IF NOT EXISTS secret_references (
    secret_id TEXT NOT NULL, character_id TEXT NOT NULL, chapter_id TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT ''
);
```

Update `_reset_state`:

```python
    async def _reset_state(self) -> None:
        """Testing/rebuild helper: forget position and clear projections."""
        for table in (
            "chapters", "world_entries", "characters", "director_signals",
            "retcon_requests", "proposals", "autonomy_state", "threads",
            "structure_scores", "secrets", "secret_knowledge", "secret_references",
        ):
            await self._conn.execute(f"DELETE FROM {table}")
        await self._set_last_sequence(0)
```

Add three new `elif` branches to `_apply`, immediately after the `THREAD_TOUCHED`/`THREAD_PAID_OFF`/`THREAD_ABANDONED` branch and before the `ANNOTATION_STRUCTURE_SCORED` branch:

```python
        elif t == EventType.SECRET_CREATED:
            cur = await self._conn.execute("SELECT id FROM secrets WHERE id=?", (p["id"],))
            existing = await cur.fetchone()
            if existing is None:
                record = SecretRecord(id=p["id"], title=p["title"], revealed=False)
                await self._conn.execute(
                    "INSERT OR REPLACE INTO secrets (id, data) VALUES (?,?)",
                    (record.id, record.model_dump_json()),
                )
            # else: a secret id is minted exactly once. A second secret.created
            # for an id that already has a row is a projection no-op — same
            # first-plant-wins rule as thread.planted.
        elif t == EventType.SECRET_LEARNED:
            await self._conn.execute(
                "INSERT OR IGNORE INTO secret_knowledge (secret_id, character_id, chapter_id, note) "
                "VALUES (?,?,?,?)",
                (p["id"], p["character_id"], p.get("chapter_id", ""), p.get("note", "")),
            )
        elif t == EventType.SECRET_REFERENCED:
            await self._conn.execute(
                "INSERT INTO secret_references (secret_id, character_id, chapter_id, note) VALUES (?,?,?,?)",
                (p["id"], p["character_id"], p.get("chapter_id", ""), p.get("note", "")),
            )
        elif t == EventType.SECRET_REVEALED:
            cur = await self._conn.execute("SELECT data FROM secrets WHERE id=?", (p["id"],))
            row = await cur.fetchone()
            if row is not None:
                record = SecretRecord.model_validate_json(row[0])
                if not record.revealed:
                    updated = record.model_copy(update={"revealed": True})
                    await self._conn.execute(
                        "INSERT OR REPLACE INTO secrets (id, data) VALUES (?,?)",
                        (updated.id, updated.model_dump_json()),
                    )
                # else: set-once — already revealed, event is a fact in the
                # log but the projection does not change (Locked decision #2).
            # else: no row for this id yet (shouldn't happen under correct
            # agent behavior) — nothing to project, no error raised.
```

(Only the import addition, the three new `_CREATE` tables, the three names in `_reset_state`, and the four new `elif` branches are new; every other branch of `_apply` is unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/canon/test_projector.py -v`
Expected: PASS (all prior + 8 new). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/projector.py tests/canon/test_projector.py
git commit -m "feat: KnowledgeProjection builds secrets/secret_knowledge/secret_references tables"
```

---

### Task 5: `CausalGraphProjection` — `causal_edges` table

**Files:**
- Modify: `novelizer/canon/projector.py`
- Test: `tests/canon/test_projector.py`

**Interfaces:**
- Consumes: `EventType.CAUSAL_EDGE_DECLARED` (Task 1).
- Produces: table `causal_edges (cause_chapter_id TEXT NOT NULL, effect_chapter_id TEXT NOT NULL, note TEXT NOT NULL DEFAULT '')`, maintained by `Projector._apply`; added to `Projector._reset_state`'s cleared-tables tuple.

- [ ] **Step 1: Write the failing tests**

Append to `tests/canon/test_projector.py`:

```python
async def _causal_edge_rows(proj):
    cur = await proj._conn.execute(
        "SELECT cause_chapter_id, effect_chapter_id, note FROM causal_edges ORDER BY rowid"
    )
    return await cur.fetchall()


async def test_causal_edge_declared_is_projected(wired):
    from novelizer.canon.events import CausalEdgeDeclared
    events, proj, _ = wired
    await events.append(EventType.CAUSAL_EDGE_DECLARED, "c3",
                        CausalEdgeDeclared(cause_chapter_id="c1", effect_chapter_id="c3", note="fire forces the move"))
    await proj.catch_up()
    rows = await _causal_edge_rows(proj)
    assert rows == [("c1", "c3", "fire forces the move")]


async def test_causal_edge_declared_is_never_deduped(wired):
    from novelizer.canon.events import CausalEdgeDeclared
    events, proj, _ = wired
    edge = CausalEdgeDeclared(cause_chapter_id="c1", effect_chapter_id="c3")
    await events.append(EventType.CAUSAL_EDGE_DECLARED, "c3", edge)
    await events.append(EventType.CAUSAL_EDGE_DECLARED, "c3", edge)
    await proj.catch_up()
    rows = await _causal_edge_rows(proj)
    assert len(rows) == 2


async def test_reprojecting_causal_edges_is_equivalent(wired):
    from novelizer.canon.events import CausalEdgeDeclared
    events, proj, path = wired
    await events.append(EventType.CAUSAL_EDGE_DECLARED, "c2", CausalEdgeDeclared(cause_chapter_id="c1", effect_chapter_id="c2"))
    await events.append(EventType.CAUSAL_EDGE_DECLARED, "c3", CausalEdgeDeclared(cause_chapter_id="c2", effect_chapter_id="c3"))
    await proj.catch_up()
    incremental = await _causal_edge_rows(proj)
    proj2 = Projector(events, path)
    await proj2.init()
    await proj2._reset_state()
    await proj2.catch_up()
    from_scratch = await _causal_edge_rows(proj2)
    await proj2.close()
    assert incremental == from_scratch


async def test_reset_state_clears_causal_edges(wired):
    from novelizer.canon.events import CausalEdgeDeclared
    events, proj, _ = wired
    await events.append(EventType.CAUSAL_EDGE_DECLARED, "c2", CausalEdgeDeclared(cause_chapter_id="c1", effect_chapter_id="c2"))
    await proj.catch_up()
    await proj._reset_state()
    cur = await proj._conn.execute("SELECT COUNT(*) FROM causal_edges")
    assert (await cur.fetchone())[0] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/canon/test_projector.py -v`
Expected: FAIL — `sqlite3.OperationalError: no such table: causal_edges`.

- [ ] **Step 3: Implement**

In `novelizer/canon/projector.py`, add the `causal_edges` table to `_CREATE` (after `secret_references`):

```sql
CREATE TABLE IF NOT EXISTS causal_edges (
    cause_chapter_id TEXT NOT NULL, effect_chapter_id TEXT NOT NULL, note TEXT NOT NULL DEFAULT ''
);
```

Add `"causal_edges"` to `_reset_state`'s tuple (after `"secret_references"`):

```python
    async def _reset_state(self) -> None:
        """Testing/rebuild helper: forget position and clear projections."""
        for table in (
            "chapters", "world_entries", "characters", "director_signals",
            "retcon_requests", "proposals", "autonomy_state", "threads",
            "structure_scores", "secrets", "secret_knowledge", "secret_references",
            "causal_edges",
        ):
            await self._conn.execute(f"DELETE FROM {table}")
        await self._set_last_sequence(0)
```

Add one new `elif` branch to `_apply`, after the `SECRET_REVEALED` branch and before `ANNOTATION_STRUCTURE_SCORED`:

```python
        elif t == EventType.CAUSAL_EDGE_DECLARED:
            await self._conn.execute(
                "INSERT INTO causal_edges (cause_chapter_id, effect_chapter_id, note) VALUES (?,?,?)",
                (p["cause_chapter_id"], p["effect_chapter_id"], p.get("note", "")),
            )
```

(Only the new table, the one new tuple entry, and the one new `elif` branch are new; every other line is unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/canon/test_projector.py -v`
Expected: PASS (all prior + 4 new). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/projector.py tests/canon/test_projector.py
git commit -m "feat: CausalGraphProjection builds the causal_edges table, no dedup"
```

---

### Task 6: `ReadStore` accessors — `list_secrets`, `get_secret`, `knowledge_matrix`, `list_causal_edges`, `list_secret_references`

**Files:**
- Modify: `novelizer/canon/read_store.py`
- Test: `tests/canon/test_read_store.py`

**Interfaces:**
- Consumes: `SecretRecord`, `CausalEdgeRecord`, `SecretReferenceRecord` (Task 2); the `secrets`/`secret_knowledge`/`secret_references`/`causal_edges` tables (Tasks 4, 5).
- Produces: `ReadStore.list_secrets() -> list[SecretRecord]`; `ReadStore.get_secret(secret_id: str) -> Optional[SecretRecord]`; `ReadStore.knowledge_matrix() -> dict[str, dict]` where each value is `{"revealed": bool, "known_by": set[str]}` (feed this into `novelizer.canon.secrets.knowledge_cell_state` for a single cell's state); `ReadStore.list_causal_edges() -> list[CausalEdgeRecord]`; `ReadStore.list_secret_references(secret_id: Optional[str] = None) -> list[SecretReferenceRecord]` (the durable read path M4.2's `LeakDetector` will use — included now, while this schema is defined, so M4.2 doesn't have to add a read path for data M4.1 already committed and projected).

- [ ] **Step 1: Write the failing test**

Append to `tests/canon/test_read_store.py`:

```python
async def test_list_and_get_secrets(stack):
    from novelizer.canon.events import SecretCreated
    events, proj, read = stack
    await events.append(EventType.SECRET_CREATED, "the-heir-lives", SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.SECRET_CREATED, "the-map-is-forged", SecretCreated(id="the-map-is-forged", title="The Map Is Forged"))
    await proj.catch_up()
    secrets = await read.list_secrets()
    assert {s.id for s in secrets} == {"the-heir-lives", "the-map-is-forged"}
    fetched = await read.get_secret("the-heir-lives")
    assert fetched is not None and fetched.title == "The Heir Lives"
    assert await read.get_secret("missing") is None


async def test_knowledge_matrix_reflects_learned_and_revealed(stack):
    from novelizer.canon.events import SecretCreated, SecretLearned, SecretRevealed
    events, proj, read = stack
    await events.append(EventType.SECRET_CREATED, "the-heir-lives", SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.SECRET_CREATED, "the-map-is-forged", SecretCreated(id="the-map-is-forged", title="The Map Is Forged"))
    await events.append(EventType.SECRET_LEARNED, "the-heir-lives", SecretLearned(id="the-heir-lives", character_id="mara"))
    await events.append(EventType.SECRET_REVEALED, "the-map-is-forged", SecretRevealed(id="the-map-is-forged"))
    await proj.catch_up()
    matrix = await read.knowledge_matrix()
    assert matrix["the-heir-lives"] == {"revealed": False, "known_by": {"mara"}}
    assert matrix["the-map-is-forged"] == {"revealed": True, "known_by": set()}


async def test_list_causal_edges(stack):
    from novelizer.canon.events import CausalEdgeDeclared
    events, proj, read = stack
    await events.append(EventType.CAUSAL_EDGE_DECLARED, "c2", CausalEdgeDeclared(cause_chapter_id="c1", effect_chapter_id="c2"))
    await events.append(EventType.CAUSAL_EDGE_DECLARED, "c3", CausalEdgeDeclared(cause_chapter_id="c2", effect_chapter_id="c3", note="the letter arrives"))
    await proj.catch_up()
    edges = await read.list_causal_edges()
    assert [(e.cause_chapter_id, e.effect_chapter_id, e.note) for e in edges] == [
        ("c1", "c2", ""), ("c2", "c3", "the letter arrives"),
    ]


async def test_list_secret_references_filters_by_secret_id(stack):
    from novelizer.canon.events import SecretCreated, SecretReferenced
    events, proj, read = stack
    await events.append(EventType.SECRET_CREATED, "the-heir-lives", SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.SECRET_REFERENCED, "the-heir-lives", SecretReferenced(id="the-heir-lives", character_id="mara", chapter_id="c3"))
    await events.append(EventType.SECRET_REFERENCED, "the-heir-lives", SecretReferenced(id="the-heir-lives", character_id="ren", chapter_id="c4"))
    await proj.catch_up()
    all_refs = await read.list_secret_references()
    assert len(all_refs) == 2
    filtered = await read.list_secret_references(secret_id="the-heir-lives")
    assert {r.character_id for r in filtered} == {"mara", "ren"}
    assert await read.list_secret_references(secret_id="missing") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/canon/test_read_store.py -v`
Expected: FAIL — `AttributeError: 'ReadStore' object has no attribute 'list_secrets'`.

- [ ] **Step 3: Implement**

In `novelizer/canon/read_store.py`, update the import and add five new methods after `get_structure_score`:

```python
from novelizer.store.models import (
    Chapter, WorldEntry, Character, DirectorSignal, RetconRequest, ThreadRecord, StructureScore,
    SecretRecord, CausalEdgeRecord, SecretReferenceRecord,
)
```

```python
    async def list_secrets(self) -> list[SecretRecord]:
        cur = await self._conn.execute("SELECT data FROM secrets ORDER BY rowid")
        return [SecretRecord.model_validate_json(r[0]) for r in await cur.fetchall()]

    async def get_secret(self, secret_id: str) -> Optional[SecretRecord]:
        cur = await self._conn.execute("SELECT data FROM secrets WHERE id=?", (secret_id,))
        row = await cur.fetchone()
        return SecretRecord.model_validate_json(row[0]) if row else None

    async def knowledge_matrix(self) -> dict[str, dict]:
        """Return {secret_id: {"revealed": bool, "known_by": set[character_id]}}
        for every secret. `revealed` is read directly off each secret's own
        record (Locked decision #2) -- callers derive a specific cell's
        state with novelizer.canon.secrets.knowledge_cell_state.
        """
        matrix: dict[str, dict] = {}
        for secret in await self.list_secrets():
            cur = await self._conn.execute(
                "SELECT character_id FROM secret_knowledge WHERE secret_id=?", (secret.id,)
            )
            known_by = {r[0] for r in await cur.fetchall()}
            matrix[secret.id] = {"revealed": secret.revealed, "known_by": known_by}
        return matrix

    async def list_causal_edges(self) -> list[CausalEdgeRecord]:
        cur = await self._conn.execute(
            "SELECT cause_chapter_id, effect_chapter_id, note FROM causal_edges ORDER BY rowid"
        )
        return [
            CausalEdgeRecord(cause_chapter_id=r[0], effect_chapter_id=r[1], note=r[2])
            for r in await cur.fetchall()
        ]

    async def list_secret_references(self, secret_id: Optional[str] = None) -> list[SecretReferenceRecord]:
        if secret_id is not None:
            cur = await self._conn.execute(
                "SELECT secret_id, character_id, chapter_id, note FROM secret_references "
                "WHERE secret_id=? ORDER BY rowid", (secret_id,),
            )
        else:
            cur = await self._conn.execute(
                "SELECT secret_id, character_id, chapter_id, note FROM secret_references ORDER BY rowid"
            )
        return [
            SecretReferenceRecord(secret_id=r[0], character_id=r[1], chapter_id=r[2], note=r[3])
            for r in await cur.fetchall()
        ]
```

(Only the import addition and these five methods are new; every other method is unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/canon/test_read_store.py -v`
Expected: PASS (all prior + 4 new). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/read_store.py tests/canon/test_read_store.py
git commit -m "feat: ReadStore exposes secrets/knowledge_matrix/causal_edges/secret_references"
```

---

### Task 7: Hypothesis property test — knowledge matrix is monotonic and `revealed` is set-once

**Files:**
- Create: `tests/canon/test_knowledge_projection_property.py`

**Interfaces:**
- Consumes: `Projector`, `EventStore`, `ReadStore` (existing); `EventType.SECRET_*`/payload models (Task 1); `knowledge_cell_state` (Task 1); `ReadStore.knowledge_matrix` (Task 6). No new production code — test-only, exercising Task 4's implementation.

- [ ] **Step 1: Write the property test**

Create `tests/canon/test_knowledge_projection_property.py`:

```python
import asyncio
import os
import tempfile
from hypothesis import given, settings, strategies as st
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.events import EventType, SecretCreated, SecretLearned, SecretRevealed
from novelizer.canon.secrets import knowledge_cell_state

SECRET_ID = "s1"
CHARACTER_ID = "c1"


def _expected_cell(actions: list[str]) -> str:
    """Pure oracle: known/revealed only ever flip False->True, so the final
    cell state is fully determined by whether 'learn'/'reveal' ever
    appeared, independent of order or repetition (Locked decision #2's
    monotonic-lattice contract)."""
    known = "learn" in actions
    revealed = "reveal" in actions
    if revealed:
        return "revealed"
    return "known" if known else "unknown"


async def _run_sequence(actions: list[str]) -> tuple[str, str]:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        events = EventStore(path)
        await events.init()
        proj = Projector(events, path)
        await proj.init()
        read = ReadStore(path)
        await read.init()

        await events.append(EventType.SECRET_CREATED, SECRET_ID, SecretCreated(id=SECRET_ID, title="A Secret"))
        for action in actions:
            if action == "learn":
                await events.append(EventType.SECRET_LEARNED, SECRET_ID, SecretLearned(id=SECRET_ID, character_id=CHARACTER_ID))
            else:
                await events.append(EventType.SECRET_REVEALED, SECRET_ID, SecretRevealed(id=SECRET_ID))
        await proj.catch_up()
        matrix = await read.knowledge_matrix()
        incremental_cell = knowledge_cell_state(matrix, SECRET_ID, CHARACTER_ID)

        # Rebuild equivalence: a fresh projector replaying from zero agrees.
        proj2 = Projector(events, path)
        await proj2.init()
        await proj2._reset_state()
        await proj2.catch_up()
        rebuilt_matrix = await read.knowledge_matrix()
        rebuilt_cell = knowledge_cell_state(rebuilt_matrix, SECRET_ID, CHARACTER_ID)
        await proj2.close()

        await read.close()
        await proj.close()
        await events.close()
        return incremental_cell, rebuilt_cell
    finally:
        os.unlink(path)


@given(st.lists(st.sampled_from(["learn", "reveal"]), max_size=8))
@settings(max_examples=50, deadline=None)
def test_knowledge_matrix_is_monotonic_for_any_event_sequence(actions):
    """For any interleaving/repetition of learn/reveal events following a
    secret.created, the projected (secret, character) cell state matches the
    monotonic-lattice oracle (unknown -> known -> revealed, never backwards,
    revealed is set-once), and a from-scratch rebuild agrees (replay
    idempotence)."""
    incremental_cell, rebuilt_cell = asyncio.run(_run_sequence(actions))
    expected = _expected_cell(actions)
    assert incremental_cell == expected
    assert rebuilt_cell == expected


@given(st.integers(min_value=0, max_value=5))
@settings(max_examples=20, deadline=None)
def test_revealed_flag_is_set_once_under_repeated_reveals(n_reveals):
    """Repeating secret.revealed any number of times never un-sets or
    re-sets the flag beyond True -- it is idempotent (Locked decision #2)."""
    actions = ["reveal"] * n_reveals
    incremental_cell, rebuilt_cell = asyncio.run(_run_sequence(actions))
    expected = "revealed" if n_reveals > 0 else "unknown"
    assert incremental_cell == expected
    assert rebuilt_cell == expected
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `uv run pytest tests/canon/test_knowledge_projection_property.py -v`
Expected: PASS (Hypothesis runs the generated sequences; Task 4's implementation is already correct, so this should pass immediately — its value is a regression guard, and per M4.1's done-when the property test is required to exist and hold). If it fails, the failure points at a specific counterexample sequence — fix `Projector._apply`'s `SECRET_LEARNED`/`SECRET_REVEALED` branches (Task 4) to match the oracle, not the other way around, since the oracle directly encodes Locked decision #2's stated monotonic lattice.

- [ ] **Step 3: Commit**

```bash
git add tests/canon/test_knowledge_projection_property.py
git commit -m "test: Hypothesis property test for knowledge-matrix monotonicity and set-once revealed"
```

---

### Task 8: Hypothesis property test — causal graph fold never drops or duplicates an edge

**Files:**
- Create: `tests/canon/test_causal_graph_projection_property.py`

**Interfaces:**
- Consumes: `Projector`, `EventStore`, `ReadStore` (existing); `EventType.CAUSAL_EDGE_DECLARED`/`CausalEdgeDeclared` (Task 1); `ReadStore.list_causal_edges` (Task 6). No new production code — test-only, exercising Task 5's implementation.

- [ ] **Step 1: Write the property test**

Create `tests/canon/test_causal_graph_projection_property.py`:

```python
import asyncio
import os
import tempfile
from hypothesis import given, settings, strategies as st
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.events import EventType, CausalEdgeDeclared

_CHAPTER_POOL = ["c1", "c2", "c3", "c4"]
_edge_strategy = st.tuples(
    st.sampled_from(_CHAPTER_POOL), st.sampled_from(_CHAPTER_POOL), st.text(max_size=5),
)


async def _run_edges(edges: list[tuple[str, str, str]]) -> tuple[list, list]:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        events = EventStore(path)
        await events.init()
        proj = Projector(events, path)
        await proj.init()
        read = ReadStore(path)
        await read.init()

        for cause, effect, note in edges:
            await events.append(
                EventType.CAUSAL_EDGE_DECLARED, effect,
                CausalEdgeDeclared(cause_chapter_id=cause, effect_chapter_id=effect, note=note),
            )
        await proj.catch_up()
        incremental = sorted(
            (e.cause_chapter_id, e.effect_chapter_id, e.note) for e in await read.list_causal_edges()
        )

        proj2 = Projector(events, path)
        await proj2.init()
        await proj2._reset_state()
        await proj2.catch_up()
        rebuilt = sorted(
            (e.cause_chapter_id, e.effect_chapter_id, e.note) for e in await read.list_causal_edges()
        )
        await proj2.close()

        await read.close()
        await proj.close()
        await events.close()
        return incremental, rebuilt
    finally:
        os.unlink(path)


@given(st.lists(_edge_strategy, max_size=10))
@settings(max_examples=50, deadline=None)
def test_causal_graph_fold_never_drops_or_duplicates_edges(edges):
    """For any sequence of declared edges (including exact repeats and
    self-edges -- the projection itself does no validation, that's the
    commit-time job of BaseAgent._commit_causal_intents in Task 11), the
    projected row multiset exactly matches the declared event multiset:
    no edge is dropped, none is duplicated beyond what was actually
    declared, and a from-scratch rebuild agrees (replay idempotence)."""
    incremental, rebuilt = asyncio.run(_run_edges(edges))
    expected = sorted(edges)
    assert incremental == expected
    assert rebuilt == expected
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `uv run pytest tests/canon/test_causal_graph_projection_property.py -v`
Expected: PASS (Task 5's implementation is a strict append with no dedup, so this should pass immediately — it's the regression guard the M4.1 done-when requires). If it fails, the failure points at a specific counterexample — fix `Projector._apply`'s `CAUSAL_EDGE_DECLARED` branch (Task 5), which must remain a strict `INSERT` with no `WHERE NOT EXISTS`/`OR IGNORE` uniqueness constraint.

- [ ] **Step 3: Commit**

```bash
git add tests/canon/test_causal_graph_projection_property.py
git commit -m "test: Hypothesis property test for causal-graph fold — no drops, no duplicates"
```

---

### Task 9: `KnowledgeIntent`/`CausalIntent` schemas; `ChapterDraft`/`EditorVerdict`/`KeeperOutput` gain the new fields

**Files:**
- Modify: `novelizer/agents/schemas.py`
- Modify: `novelizer/agents/base.py` (`ChapterDraft`)
- Test: `tests/agents/test_schemas.py`

**Interfaces:**
- Produces: `KnowledgeIntent(BaseModel)` in `novelizer/agents/schemas.py` with fields `action: Literal["plant", "learn", "reveal", "uses"]`, `title: str = ""` (used only for `plant`), `id: str = ""` (used for `learn`/`reveal`/`uses`), `character_id: str = ""` (used for `learn`/`uses`), `note: str = ""`. `CausalIntent(BaseModel)` with fields `cause_chapter_id: str`, `effect_chapter_id: str`, `note: str = ""`. `EditorVerdict.knowledge_intents: list[KnowledgeIntent] = Field(default_factory=list)`, `EditorVerdict.causal_intents: list[CausalIntent] = Field(default_factory=list)`. `KeeperOutput.knowledge_intents: list[KnowledgeIntent] = Field(default_factory=list)` (no `causal_intents` — CharacterKeeper never declares causal edges). `ChapterDraft.knowledge_intents: list[KnowledgeIntent] = Field(default_factory=list)`, `ChapterDraft.causal_intents: list[CausalIntent] = Field(default_factory=list)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/agents/test_schemas.py`:

```python
def test_knowledge_intent_plant_defaults():
    from novelizer.agents.schemas import KnowledgeIntent
    intent = KnowledgeIntent(action="plant", title="The Heir Lives")
    assert intent.id == "" and intent.character_id == "" and intent.note == ""


def test_knowledge_intent_learn_roundtrips():
    from novelizer.agents.schemas import KnowledgeIntent
    intent = KnowledgeIntent(action="learn", id="the-heir-lives", character_id="mara", note="found the letter")
    again = KnowledgeIntent.model_validate_json(intent.model_dump_json())
    assert again == intent


def test_causal_intent_roundtrips():
    from novelizer.agents.schemas import CausalIntent
    intent = CausalIntent(cause_chapter_id="c1", effect_chapter_id="c3", note="the fire forces the move")
    again = CausalIntent.model_validate_json(intent.model_dump_json())
    assert again == intent


def test_editor_verdict_default_knowledge_and_causal_intents_empty():
    assert EditorVerdict().knowledge_intents == []
    assert EditorVerdict().causal_intents == []


def test_editor_verdict_carries_knowledge_and_causal_intents():
    from novelizer.agents.schemas import KnowledgeIntent, CausalIntent
    v = EditorVerdict(
        verdict="approve",
        knowledge_intents=[KnowledgeIntent(action="learn", id="the-heir-lives", character_id="mara")],
        causal_intents=[CausalIntent(cause_chapter_id="c1", effect_chapter_id="c2")],
    )
    assert v.knowledge_intents[0].character_id == "mara"
    assert v.causal_intents[0].effect_chapter_id == "c2"


def test_keeper_output_default_knowledge_intents_empty():
    from novelizer.agents.schemas import KeeperOutput
    assert KeeperOutput().knowledge_intents == []


def test_keeper_output_carries_knowledge_intents():
    from novelizer.agents.schemas import KeeperOutput, KnowledgeIntent
    out = KeeperOutput(knowledge_intents=[KnowledgeIntent(action="learn", id="the-heir-lives", character_id="mara")])
    assert out.knowledge_intents[0].id == "the-heir-lives"


def test_chapter_draft_default_knowledge_and_causal_intents_empty():
    from novelizer.agents.base import ChapterDraft
    d = ChapterDraft(title="T", prose="P")
    assert d.knowledge_intents == [] and d.causal_intents == []


def test_chapter_draft_carries_knowledge_and_causal_intents():
    from novelizer.agents.base import ChapterDraft
    from novelizer.agents.schemas import KnowledgeIntent, CausalIntent
    d = ChapterDraft(
        title="T", prose="P",
        knowledge_intents=[KnowledgeIntent(action="plant", title="The Heir Lives")],
        causal_intents=[CausalIntent(cause_chapter_id="c1", effect_chapter_id="c2")],
    )
    assert d.knowledge_intents[0].title == "The Heir Lives"
    assert d.causal_intents[0].cause_chapter_id == "c1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_schemas.py -v`
Expected: FAIL — `ImportError: cannot import name 'KnowledgeIntent' from 'novelizer.agents.schemas'`.

- [ ] **Step 3: Implement**

In `novelizer/agents/schemas.py`, add `KnowledgeIntent`/`CausalIntent` after `ThreadIntent` and update `KeeperOutput`/`EditorVerdict`:

```python
class KnowledgeIntent(BaseModel):
    """One agent-declared secret-knowledge action from structured output.

    `plant` mints a new secret from a freeform `title` (the system slugs it
    into an id -- see novelizer.canon.secrets.slugify_secret_name); `learn`,
    `reveal`, and `uses` must cite an existing secret's `id` rather than
    inventing one. `learn`/`uses` additionally require `character_id` (the
    character who learns/uses the secret); `reveal` and `plant` leave it
    blank. `BaseAgent._commit_knowledge_intents` turns validated intents
    into secret.* commits (see novelizer/agents/base.py). CharacterKeeper is
    restricted to `learn` only (Locked decision #1) -- minting/revealing a
    secret is a narrative-authoring act reserved for Author/Editor.
    """

    action: Literal["plant", "learn", "reveal", "uses"]
    title: str = ""
    id: str = ""
    character_id: str = ""
    note: str = ""


class CausalIntent(BaseModel):
    """One agent-declared causal-edge claim from structured output.

    An edge has no minted identity and no lifecycle (Locked decision #4):
    `cause_chapter_id`/`effect_chapter_id` must each cite an existing
    chapter id. `BaseAgent._commit_causal_intents` drops self-edges
    (cause == effect) and edges citing an unknown chapter id, with a logged
    warning; every other declared edge is committed as its own fact, with
    no deduplication (see novelizer/agents/base.py).
    """

    cause_chapter_id: str
    effect_chapter_id: str
    note: str = ""


class RetconDraft(BaseModel):
    description: str
    conflicting_entry_ids: list[str] = Field(default_factory=list)
    proposed_resolution: str = ""


class KeeperOutput(BaseModel):
    updated_characters: list[CharacterUpdate] = Field(default_factory=list)
    retcon_requests: list[RetconDraft] = Field(default_factory=list)
    knowledge_intents: list[KnowledgeIntent] = Field(default_factory=list)
    feed_note: str = ""


class EditorVerdict(BaseModel):
    verdict: Literal["approve", "revise"] = "approve"
    notes: str = ""
    feed_note: str = ""
    thread_intents: list[ThreadIntent] = Field(default_factory=list)
    knowledge_intents: list[KnowledgeIntent] = Field(default_factory=list)
    causal_intents: list[CausalIntent] = Field(default_factory=list)
```

(`KnowledgeIntent`/`CausalIntent` are new classes; `KeeperOutput` gains one field, `EditorVerdict` gains two fields, both defaulting to `[]`; every other class in `schemas.py` — `WorldEntryDraft`, `WorldEntriesDraft`, `CharacterUpdate`, `ThreadIntent`, `RetconDraft`, `ContinuityOutput`, `RetconAmendments`, `ChapterScore`, `StructureAnalystOutput` — is unchanged.)

In `novelizer/agents/base.py`, update the import and `ChapterDraft`:

```python
from novelizer.agents.schemas import ThreadIntent, KnowledgeIntent, CausalIntent


class ChapterDraft(BaseModel):
    title: str
    prose: str
    character_ids: list[str] = Field(default_factory=list)
    feed_note: str = ""
    thread_intents: list[ThreadIntent] = Field(default_factory=list)
    knowledge_intents: list[KnowledgeIntent] = Field(default_factory=list)
    causal_intents: list[CausalIntent] = Field(default_factory=list)
```

(Only the import addition and the two new `ChapterDraft` fields are new; every other line in `base.py` — `BaseAgent` and all its methods — is unchanged in this task; Task 10/11 add the new commit-helper methods.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_schemas.py -v`
Expected: PASS (all prior + 9 new). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/schemas.py novelizer/agents/base.py tests/agents/test_schemas.py
git commit -m "feat: KnowledgeIntent/CausalIntent schemas; ChapterDraft/EditorVerdict/KeeperOutput gain them"
```

---

### Task 10: `BaseAgent._commit_knowledge_intents` — the shared knowledge-intent-to-event translator

**Files:**
- Modify: `novelizer/agents/base.py`
- Test: `tests/agents/test_base.py`

**Interfaces:**
- Consumes: `KnowledgeIntent` (Task 9); `EventType.SECRET_*`/payload models (Task 1); `slugify_secret_name` (Task 1).
- Produces: `BaseAgent._commit_knowledge_intents(self, intents: list[KnowledgeIntent], active_secret_ids: set[str], chapter_id: str = "", allowed_actions: frozenset[str] = frozenset({"plant", "learn", "reveal", "uses"})) -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/agents/test_base.py`:

```python
from novelizer.agents.schemas import KnowledgeIntent
from novelizer.canon.events import SecretCreated


async def test_commit_knowledge_intents_plant_mints_slugged_id(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._commit_knowledge_intents(
        [KnowledgeIntent(action="plant", title="The Heir Lives")], active_secret_ids=set(),
    )
    await proj.catch_up()
    log = await events.events_since(0)
    assert len(log) == 1
    assert log[0].event_type == EventType.SECRET_CREATED
    assert log[0].payload["id"] == "the-heir-lives"
    assert log[0].payload["title"] == "The Heir Lives"


async def test_commit_knowledge_intents_plant_dropped_when_title_blank(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._commit_knowledge_intents([KnowledgeIntent(action="plant", title="   ")], active_secret_ids=set())
    assert await events.events_since(0) == []


async def test_commit_knowledge_intents_plant_dropped_on_id_collision(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._commit_knowledge_intents(
        [KnowledgeIntent(action="plant", title="The Heir Lives")],
        active_secret_ids={"the-heir-lives"},
    )
    assert await events.events_since(0) == []


async def test_commit_knowledge_intents_learn_commits_when_id_known(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="character_keeper")
    await agent._commit_knowledge_intents(
        [KnowledgeIntent(action="learn", id="the-heir-lives", character_id="mara", note="found the letter")],
        active_secret_ids={"the-heir-lives"}, chapter_id="c2",
    )
    log = await events.events_since(0)
    assert len(log) == 1
    assert log[0].event_type == EventType.SECRET_LEARNED
    assert log[0].payload == {"id": "the-heir-lives", "character_id": "mara", "chapter_id": "c2", "note": "found the letter"}


async def test_commit_knowledge_intents_learn_dropped_when_character_id_blank(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._commit_knowledge_intents(
        [KnowledgeIntent(action="learn", id="the-heir-lives")], active_secret_ids={"the-heir-lives"},
    )
    assert await events.events_since(0) == []


async def test_commit_knowledge_intents_drops_unknown_id_with_no_event(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._commit_knowledge_intents(
        [KnowledgeIntent(action="reveal", id="not-a-real-secret")], active_secret_ids={"the-heir-lives"},
    )
    assert await events.events_since(0) == []


async def test_commit_knowledge_intents_reveal_commits_without_character_id(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="editor")
    await agent._commit_knowledge_intents(
        [KnowledgeIntent(action="reveal", id="the-heir-lives", note="told the crowd")],
        active_secret_ids={"the-heir-lives"}, chapter_id="c5",
    )
    log = await events.events_since(0)
    assert len(log) == 1
    assert log[0].event_type == EventType.SECRET_REVEALED
    assert log[0].payload == {"id": "the-heir-lives", "chapter_id": "c5", "note": "told the crowd"}


async def test_commit_knowledge_intents_uses_commits_secret_referenced(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._commit_knowledge_intents(
        [KnowledgeIntent(action="uses", id="the-heir-lives", character_id="mara")],
        active_secret_ids={"the-heir-lives"}, chapter_id="c6",
    )
    log = await events.events_since(0)
    assert log[0].event_type == EventType.SECRET_REFERENCED
    assert log[0].payload["character_id"] == "mara"


async def test_commit_knowledge_intents_respects_allowed_actions(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="character_keeper")
    await agent._commit_knowledge_intents(
        [KnowledgeIntent(action="plant", title="Should Not Commit")],
        active_secret_ids=set(), allowed_actions=frozenset({"learn"}),
    )
    assert await events.events_since(0) == []


async def test_commit_knowledge_intents_noop_on_empty_list(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._commit_knowledge_intents([], active_secret_ids=set())
    assert await events.events_since(0) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_base.py -v`
Expected: FAIL — `AttributeError: 'BaseAgent' object has no attribute '_commit_knowledge_intents'`.

- [ ] **Step 3: Implement**

In `novelizer/agents/base.py`, update the imports and add the new method after `_commit_thread_intents`:

```python
from novelizer.canon.events import (
    EventType, AgentRemark, ThreadPlanted, ThreadTouched, ThreadPaidOff, ThreadAbandoned,
    SecretCreated, SecretLearned, SecretReferenced, SecretRevealed, CausalEdgeDeclared,
)
from novelizer.canon.threads import slugify_thread_name
from novelizer.canon.secrets import slugify_secret_name
from novelizer.agents.schemas import ThreadIntent, KnowledgeIntent, CausalIntent
```

```python
_KNOWLEDGE_EVENT_BY_ACTION = {
    "learn": (EventType.SECRET_LEARNED, SecretLearned),
    "reveal": (EventType.SECRET_REVEALED, SecretRevealed),
    "uses": (EventType.SECRET_REFERENCED, SecretReferenced),
}
```

```python
    async def _commit_knowledge_intents(
        self,
        intents: list[KnowledgeIntent],
        active_secret_ids: set[str],
        chapter_id: str = "",
        allowed_actions: frozenset[str] = frozenset({"plant", "learn", "reveal", "uses"}),
    ) -> None:
        """Turn agent-declared KnowledgeIntent entries into secret.* commits.

        `plant` mints a new id via slugify_secret_name(intent.title) and is
        dropped only if the title is blank; a plant colliding with an
        already-known active id is dropped with a warning -- secrets have no
        touch-analog the way threads do (M3.1's plant-collision downgrades
        to thread.touched, which needs only id+note; a colliding secret
        plant can't be safely reinterpreted as learn/reveal/uses without a
        character_id or reveal semantics the plant intent doesn't carry, so
        the safe choice is to drop it, matching how an unknown-id
        learn/reveal/uses intent is dropped below). `learn`/`reveal`/`uses`
        must cite an id present in `active_secret_ids`; `learn`/`uses`
        additionally require a non-blank `character_id`. `allowed_actions`
        restricts which actions this caller may commit -- CharacterKeeper
        passes frozenset({"learn"}) since minting/revealing a secret is a
        narrative-authoring act reserved for Author/Editor (Locked decision
        #1). Any intent whose action is not in `allowed_actions`, or that
        fails validation, is dropped with a logged warning and no event is
        committed. No-op on an empty list.
        """
        for intent in intents:
            if intent.action not in allowed_actions:
                logger.warning(
                    "%s: dropped knowledge intent action %r not permitted for this agent",
                    self.name, intent.action,
                )
                continue
            if intent.action == "plant":
                if not intent.title.strip():
                    logger.warning("%s: dropped secret plant intent with empty title", self.name)
                    continue
                secret_id = slugify_secret_name(intent.title)
                if secret_id in active_secret_ids:
                    logger.warning(
                        "%s: plant %r collides with existing secret id %r, dropping",
                        self.name, intent.title, secret_id,
                    )
                    continue
                await self._committer.commit(
                    self.name, EventType.SECRET_CREATED, secret_id,
                    SecretCreated(id=secret_id, title=intent.title, chapter_id=chapter_id, note=intent.note),
                )
                continue
            if intent.id not in active_secret_ids:
                logger.warning(
                    "%s: dropped knowledge %s intent for unknown secret id %r", self.name, intent.action, intent.id
                )
                continue
            if intent.action in ("learn", "uses") and not intent.character_id.strip():
                logger.warning(
                    "%s: dropped knowledge %s intent with empty character_id", self.name, intent.action
                )
                continue
            event_type, payload_cls = _KNOWLEDGE_EVENT_BY_ACTION[intent.action]
            if intent.action == "reveal":
                payload = payload_cls(id=intent.id, chapter_id=chapter_id, note=intent.note)
            else:
                payload = payload_cls(
                    id=intent.id, character_id=intent.character_id, chapter_id=chapter_id, note=intent.note
                )
            await self._committer.commit(self.name, event_type, intent.id, payload)
```

(Only the import additions, the `_KNOWLEDGE_EVENT_BY_ACTION` module-level dict, and the new `_commit_knowledge_intents` method are new; `ChapterDraft`, `Runner`, `BaseAgent.__init__` and every other existing method, including `_commit_thread_intents`, are unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_base.py -v`
Expected: PASS (all prior + 10 new). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/base.py tests/agents/test_base.py
git commit -m "feat: BaseAgent._commit_knowledge_intents translates agent secret declarations into commits"
```

---

### Task 11: `BaseAgent._commit_causal_intents` — the shared causal-intent-to-event translator

**Files:**
- Modify: `novelizer/agents/base.py`
- Test: `tests/agents/test_base.py`

**Interfaces:**
- Consumes: `CausalIntent` (Task 9); `EventType.CAUSAL_EDGE_DECLARED`/`CausalEdgeDeclared` (Task 1).
- Produces: `BaseAgent._commit_causal_intents(self, intents: list[CausalIntent], valid_chapter_ids: set[str]) -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/agents/test_base.py`:

```python
from novelizer.agents.schemas import CausalIntent


async def test_commit_causal_intents_commits_when_both_chapters_valid(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._commit_causal_intents(
        [CausalIntent(cause_chapter_id="c1", effect_chapter_id="c3", note="fire forces the move")],
        valid_chapter_ids={"c1", "c3"},
    )
    log = await events.events_since(0)
    assert len(log) == 1
    assert log[0].event_type == EventType.CAUSAL_EDGE_DECLARED
    assert log[0].payload == {"cause_chapter_id": "c1", "effect_chapter_id": "c3", "note": "fire forces the move"}


async def test_commit_causal_intents_drops_self_edge(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._commit_causal_intents(
        [CausalIntent(cause_chapter_id="c1", effect_chapter_id="c1")], valid_chapter_ids={"c1"},
    )
    assert await events.events_since(0) == []


async def test_commit_causal_intents_drops_unknown_chapter_id(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="editor")
    await agent._commit_causal_intents(
        [CausalIntent(cause_chapter_id="c1", effect_chapter_id="ghost")], valid_chapter_ids={"c1"},
    )
    assert await events.events_since(0) == []


async def test_commit_causal_intents_does_not_dedup_repeated_identical_edges(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    intent = CausalIntent(cause_chapter_id="c1", effect_chapter_id="c2")
    await agent._commit_causal_intents([intent, intent], valid_chapter_ids={"c1", "c2"})
    log = await events.events_since(0)
    assert len(log) == 2


async def test_commit_causal_intents_noop_on_empty_list(stack):
    events, proj, read, committer = stack
    agent = BaseAgent(None, read, committer, interval=60, name="author")
    await agent._commit_causal_intents([], valid_chapter_ids=set())
    assert await events.events_since(0) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_base.py -v`
Expected: FAIL — `AttributeError: 'BaseAgent' object has no attribute '_commit_causal_intents'`.

- [ ] **Step 3: Implement**

In `novelizer/agents/base.py`, add the new method after `_commit_knowledge_intents`:

```python
    async def _commit_causal_intents(self, intents: list[CausalIntent], valid_chapter_ids: set[str]) -> None:
        """Turn agent-declared CausalIntent entries into
        causal_edge.declared commits.

        Both `cause_chapter_id` and `effect_chapter_id` must be present in
        `valid_chapter_ids`, or the intent is dropped with a logged warning;
        a self-edge (cause == effect) is dropped with a logged warning
        first, since Locked decision #4's ordering-violation check (M4.2)
        needs two distinct chapters to be meaningful. No deduplication: an
        edge has no minted identity and no lifecycle (Locked decision #4),
        so every valid declared edge is committed as its own fact even if
        identical to a prior commit -- the property test in this plan
        (Task 8) asserts replay never drops OR duplicates a declared edge,
        which requires a strict 1:1 event-to-row mapping, not a deduped one.
        No-op on an empty list.
        """
        for intent in intents:
            if intent.cause_chapter_id == intent.effect_chapter_id:
                logger.warning(
                    "%s: dropped self-edge causal intent for chapter %r", self.name, intent.cause_chapter_id
                )
                continue
            if intent.cause_chapter_id not in valid_chapter_ids or intent.effect_chapter_id not in valid_chapter_ids:
                logger.warning(
                    "%s: dropped causal intent citing unknown chapter id(s) %r -> %r",
                    self.name, intent.cause_chapter_id, intent.effect_chapter_id,
                )
                continue
            await self._committer.commit(
                self.name, EventType.CAUSAL_EDGE_DECLARED, intent.effect_chapter_id,
                CausalEdgeDeclared(
                    cause_chapter_id=intent.cause_chapter_id,
                    effect_chapter_id=intent.effect_chapter_id,
                    note=intent.note,
                ),
            )
```

(Only this new method is added; `_commit_knowledge_intents` and every earlier method are unchanged. `aggregate_id` is set to `intent.effect_chapter_id` since edges have no minted identity of their own — arbitrary but stable, matching how `EventStore.append` requires *some* aggregate id for every event.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_base.py -v`
Expected: PASS (all prior + 5 new). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/base.py tests/agents/test_base.py
git commit -m "feat: BaseAgent._commit_causal_intents translates agent causal declarations into commits, no dedup"
```

---

### Task 12: Author wires `knowledge_intents`/`causal_intents` into `poll()`/`commit()`

**Files:**
- Modify: `novelizer/agents/author.py`
- Test: `tests/agents/test_author.py`

**Interfaces:**
- Consumes: `ReadStore.list_secrets()` (Task 6); `BaseAgent._commit_knowledge_intents`/`_commit_causal_intents` (Tasks 10, 11); `ChapterDraft.knowledge_intents`/`causal_intents` (Task 9).
- Produces: no new public interface — `Author.poll()`'s returned dict gains a `"secrets"` key (it already has `"chapters"`, added in M3.3, which Task 11's `_commit_causal_intents` needs for `valid_chapter_ids`); `Author.commit()` calls `self._commit_knowledge_intents(...)` and `self._commit_causal_intents(...)` after its existing `_commit_thread_intents(...)` call.

- [ ] **Step 1: Write the failing tests**

Append to `tests/agents/test_author.py`:

```python
from novelizer.agents.schemas import KnowledgeIntent, CausalIntent
from novelizer.canon.events import SecretCreated, Chapter as ChapterEvent  # noqa: F401 (SecretCreated used below)
from novelizer.store.models import Chapter


async def test_author_commit_plants_a_secret_from_structured_output(stack):
    events, proj, read, committer = stack
    draft = ChapterDraft(
        title="T", prose="P",
        knowledge_intents=[KnowledgeIntent(action="plant", title="The Heir Lives")],
    )
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    secret = await read.get_secret("the-heir-lives")
    assert secret is not None and secret.title == "The Heir Lives"


async def test_author_commit_uses_a_known_active_secret(stack):
    events, proj, read, committer = stack
    await events.append(EventType.SECRET_CREATED, "the-heir-lives", SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await proj.catch_up()
    draft = ChapterDraft(
        title="T", prose="P",
        knowledge_intents=[KnowledgeIntent(action="uses", id="the-heir-lives", character_id="mara")],
    )
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    refs = await read.list_secret_references(secret_id="the-heir-lives")
    assert len(refs) == 1 and refs[0].character_id == "mara"


async def test_author_commit_drops_causal_edge_citing_unknown_chapter(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    draft = ChapterDraft(
        title="Two", prose="P",
        causal_intents=[CausalIntent(cause_chapter_id="c1", effect_chapter_id="ghost")],
    )
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    assert await read.list_causal_edges() == []


async def test_author_commit_declares_a_valid_causal_edge_between_prior_chapters(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(EventType.CHAPTER_CREATED, "c2", Chapter(id="c2", title="Two", prose="p"))
    await proj.catch_up()
    draft = ChapterDraft(
        title="Three", prose="P",
        causal_intents=[CausalIntent(cause_chapter_id="c1", effect_chapter_id="c2", note="sets it up")],
    )
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    edges = await read.list_causal_edges()
    assert len(edges) == 1
    assert edges[0].cause_chapter_id == "c1" and edges[0].effect_chapter_id == "c2"


async def test_author_commit_with_no_knowledge_or_causal_intents_emits_no_new_event_types(stack):
    events, proj, read, committer = stack
    draft = ChapterDraft(title="T", prose="P")
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    assert [e.event_type for e in log if e.event_type.startswith(("secret.", "causal_edge."))] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_author.py -v`
Expected: FAIL — `test_author_commit_plants_a_secret_from_structured_output` fails with `assert secret is None`, since `Author.commit()` doesn't yet call `_commit_knowledge_intents`.

- [ ] **Step 3: Implement**

In `novelizer/agents/author.py`, add the `"secrets"` key to `poll()` and the two new commit calls in `commit()`:

```python
    async def poll(self) -> dict:
        chapters = await self._read.list_chapters()
        return {
            "world": await self._read.list_world_entries(),
            "characters": await self._read.list_characters(),
            "previous": chapters[-3:],
            "chapters": chapters,
            "signals": await self._read.list_unconsumed_signals(target_agent=self.name),
            "threads": await self._read.list_threads(),
            "secrets": await self._read.list_secrets(),
        }
```

```python
    async def commit(self, draft: ChapterDraft | None, ctx: dict) -> None:
        if draft is None:
            return
        chapter = Chapter(title=draft.title, prose=draft.prose, character_ids=draft.character_ids)
        await self._committer.commit(self.name, EventType.CHAPTER_CREATED, chapter.id, chapter)
        active_thread_ids = {
            t.id for t in ctx["threads"] if t.state.value not in TERMINAL_STATES
        }
        await self._commit_thread_intents(draft.thread_intents, active_thread_ids, chapter_id=chapter.id)
        active_secret_ids = {s.id for s in ctx["secrets"]}
        await self._commit_knowledge_intents(draft.knowledge_intents, active_secret_ids, chapter_id=chapter.id)
        valid_chapter_ids = {c.id for c in ctx["chapters"]} | {chapter.id}
        await self._commit_causal_intents(draft.causal_intents, valid_chapter_ids)
        await self._remark(draft.feed_note)
        await self._consume_signals(ctx["signals"])
```

(Only the `"secrets"` key in `poll()` and the three new lines — `active_secret_ids`, `_commit_knowledge_intents`, `valid_chapter_ids`/`_commit_causal_intents` — in `commit()` are new; `AUTHOR_SYSTEM_PROMPT`, `_summarize`, `Author.__init__`, `readiness`, `work`, `run_once`, `build_author_runner` are unchanged. `valid_chapter_ids` includes the just-minted `chapter.id` so an Author can declare a causal edge involving the chapter it is authoring in this same turn, not only prior chapters.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_author.py -v`
Expected: PASS (all prior + 4 new). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/author.py tests/agents/test_author.py
git commit -m "feat: Author turns declared knowledge/causal intents into secret.*/causal_edge.declared commits"
```

---

### Task 13: Editor wires `knowledge_intents`/`causal_intents` into `poll()`/`commit()`

**Files:**
- Modify: `novelizer/agents/editor.py`
- Test: `tests/agents/test_editor.py`

**Interfaces:**
- Consumes: `ReadStore.list_secrets()`/`list_chapters()` (Task 6, existing); `BaseAgent._commit_knowledge_intents`/`_commit_causal_intents` (Tasks 10, 11); `EditorVerdict.knowledge_intents`/`causal_intents` (Task 9).
- Produces: no new public interface — `Editor.poll()`'s returned dict gains `"secrets"` and `"chapters"` keys; `Editor.commit()` calls `self._commit_knowledge_intents(...)` and `self._commit_causal_intents(...)` after its existing `_commit_thread_intents(...)` call.

- [ ] **Step 1: Write the failing tests**

Append to `tests/agents/test_editor.py`:

```python
from novelizer.agents.schemas import KnowledgeIntent, CausalIntent
from novelizer.canon.events import SecretCreated


async def test_editor_commit_uses_a_known_active_secret(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(EventType.SECRET_CREATED, "the-heir-lives", SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await proj.catch_up()
    verdict = EditorVerdict(
        verdict="approve", notes="clean",
        knowledge_intents=[KnowledgeIntent(action="uses", id="the-heir-lives", character_id="mara")],
    )
    agent = Editor(FakeRunner(verdict), read, committer)
    await agent.run_once()
    await proj.catch_up()
    refs = await read.list_secret_references(secret_id="the-heir-lives")
    assert len(refs) == 1 and refs[0].chapter_id == "c1"


async def test_editor_commit_declares_a_valid_causal_edge(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c0", Chapter(id="c0", title="Zero", prose="p"))
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    verdict = EditorVerdict(
        verdict="approve", notes="clean",
        causal_intents=[CausalIntent(cause_chapter_id="c0", effect_chapter_id="c1", note="sets up the reveal")],
    )
    agent = Editor(FakeRunner(verdict), read, committer)
    await agent.run_once()
    await proj.catch_up()
    edges = await read.list_causal_edges()
    assert len(edges) == 1 and edges[0].cause_chapter_id == "c0" and edges[0].effect_chapter_id == "c1"


async def test_editor_commit_drops_unknown_secret_id(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    verdict = EditorVerdict(
        verdict="approve", notes="clean",
        knowledge_intents=[KnowledgeIntent(action="reveal", id="ghost-secret")],
    )
    agent = Editor(FakeRunner(verdict), read, committer)
    await agent.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    assert [e.event_type for e in log if e.event_type.startswith("secret.")] == []


async def test_editor_commit_with_no_knowledge_or_causal_intents_emits_no_new_event_types(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    verdict = EditorVerdict(verdict="approve", notes="clean")
    agent = Editor(FakeRunner(verdict), read, committer)
    await agent.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    assert [e.event_type for e in log if e.event_type.startswith(("secret.", "causal_edge."))] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_editor.py -v`
Expected: FAIL — `test_editor_commit_uses_a_known_active_secret` fails with `assert len(refs) == 0`, since `Editor.commit()` doesn't yet call `_commit_knowledge_intents`.

- [ ] **Step 3: Implement**

In `novelizer/agents/editor.py`, update `poll()`/`commit()`:

```python
    async def poll(self) -> dict:
        drafts = await self._read.list_chapters(status=EditorialStatus.draft)
        return {
            "target": drafts[0] if drafts else None,
            "threads": await self._read.list_threads(),
            "scores": await self._read.list_structure_scores(),
            "secrets": await self._read.list_secrets(),
            "chapters": await self._read.list_chapters(),
        }
```

```python
    async def commit(self, verdict: EditorVerdict | None, ctx: dict) -> None:
        ch = ctx["target"]
        if ch is None or verdict is None:
            return
        if verdict.verdict == "approve":
            updated = ch.model_copy(update={"editorial_status": EditorialStatus.reviewed, "editor_notes": verdict.notes})
            await self._committer.commit(self.name, EventType.CHAPTER_STATUS_CHANGED, updated.id, updated)
        else:
            sig = DirectorSignal(kind=SignalKind.note, body=f"[Editor on '{ch.title}'] {verdict.notes}", target_agent="author")
            await self._committer.commit(self.name, EventType.DIRECTOR_SIGNAL_CREATED, sig.id, sig)
        active_thread_ids = {
            t.id for t in ctx["threads"] if t.state.value not in TERMINAL_STATES
        }
        await self._commit_thread_intents(verdict.thread_intents, active_thread_ids, chapter_id=ch.id)
        active_secret_ids = {s.id for s in ctx["secrets"]}
        await self._commit_knowledge_intents(verdict.knowledge_intents, active_secret_ids, chapter_id=ch.id)
        valid_chapter_ids = {c.id for c in ctx["chapters"]}
        await self._commit_causal_intents(verdict.causal_intents, valid_chapter_ids)
        await self._remark(verdict.feed_note)
```

(Only the `"secrets"`/`"chapters"` keys in `poll()` and the three new lines in `commit()` are new; `SYSTEM_PROMPT`, `Editor.__init__`, `readiness`, `_character_voices_block`, `work`, `run_once`, `build_editor_runner` are unchanged. Editor never authors a new chapter, so unlike Author's `valid_chapter_ids`, no `| {new_id}` union is needed here — `ctx["chapters"]` already includes the chapter under review.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_editor.py -v`
Expected: PASS (all prior + 4 new). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/editor.py tests/agents/test_editor.py
git commit -m "feat: Editor turns declared knowledge/causal intents into secret.*/causal_edge.declared commits"
```

---

### Task 14: CharacterKeeper wires `knowledge_intents` into `poll()`/`commit()` (`learn` only)

**Files:**
- Modify: `novelizer/agents/character_keeper.py`
- Test: `tests/agents/test_character_keeper.py`

**Interfaces:**
- Consumes: `ReadStore.list_secrets()` (Task 6); `BaseAgent._commit_knowledge_intents` (Task 10); `KeeperOutput.knowledge_intents` (Task 9).
- Produces: no new public interface — `CharacterKeeper.poll()`'s returned dict gains a `"secrets"` key; `CharacterKeeper.commit()` calls `self._commit_knowledge_intents(out.knowledge_intents, active_secret_ids, allowed_actions=frozenset({"learn"}))` after its existing retcon-request loop.

- [ ] **Step 1: Write the failing tests**

Append to `tests/agents/test_character_keeper.py`:

```python
from novelizer.agents.schemas import KnowledgeIntent
from novelizer.canon.events import SecretCreated


async def test_character_keeper_commit_learn_commits_secret_learned(stack):
    events, proj, read, committer = stack
    await events.append(EventType.SECRET_CREATED, "the-heir-lives", SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await proj.catch_up()
    out = KeeperOutput(
        knowledge_intents=[KnowledgeIntent(action="learn", id="the-heir-lives", character_id="mara", note="pieced it together")],
    )
    keeper = CharacterKeeper(FakeRunner(out), read, committer)
    await keeper.run_once()
    await proj.catch_up()
    matrix = await read.knowledge_matrix()
    assert "mara" in matrix["the-heir-lives"]["known_by"]


async def test_character_keeper_commit_drops_non_learn_actions(stack):
    events, proj, read, committer = stack
    out = KeeperOutput(knowledge_intents=[KnowledgeIntent(action="plant", title="Should Not Commit")])
    keeper = CharacterKeeper(FakeRunner(out), read, committer)
    await keeper.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    assert [e.event_type for e in log if e.event_type.startswith("secret.")] == []


async def test_character_keeper_commit_with_no_knowledge_intents_emits_no_secret_events(stack):
    events, proj, read, committer = stack
    out = KeeperOutput()
    keeper = CharacterKeeper(FakeRunner(out), read, committer)
    await keeper.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    assert [e.event_type for e in log if e.event_type.startswith("secret.")] == []
```

Check the existing fixtures at the top of `tests/agents/test_character_keeper.py` first (`FakeRunner`, `stack`, `EventType`, `KeeperOutput` imports) — reuse them exactly as the file's existing tests do; do not redefine.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_character_keeper.py -v`
Expected: FAIL — `test_character_keeper_commit_learn_commits_secret_learned` fails with `assert "mara" not in set()`, since `CharacterKeeper.commit()` doesn't yet call `_commit_knowledge_intents`.

- [ ] **Step 3: Implement**

In `novelizer/agents/character_keeper.py`, update `poll()`/`commit()`:

```python
    async def poll(self) -> dict:
        chapters = await self._read.list_chapters()
        return {
            "characters": await self._read.list_characters(),
            "recent": chapters[-5:],
            "secrets": await self._read.list_secrets(),
        }
```

```python
    async def commit(self, out: KeeperOutput | None, ctx: dict) -> None:
        if out is None:
            return
        for upd in out.updated_characters:
            current = await self._read.get_character(upd.id)
            if current is None:
                continue
            fields = {}
            for f in ("arc_status", "traits", "motivations", "backstory", "voice"):
                v = getattr(upd, f)
                if v is not None:
                    fields[f] = v
            updated = current.model_copy(update=fields)
            await self._committer.commit(self.name, EventType.CHARACTER_UPDATED, updated.id, updated)
        for r in out.retcon_requests:
            req = RetconRequest(description=r.description, conflicting_entry_ids=r.conflicting_entry_ids,
                                proposed_resolution=r.proposed_resolution)
            await self._committer.commit(self.name, EventType.RETCON_REQUEST_CREATED, req.id, req)
        active_secret_ids = {s.id for s in ctx["secrets"]}
        await self._commit_knowledge_intents(
            out.knowledge_intents, active_secret_ids, allowed_actions=frozenset({"learn"})
        )
        await self._remark(out.feed_note)
```

(Only the `"secrets"` key in `poll()` and the two new lines — `active_secret_ids`, `_commit_knowledge_intents(..., allowed_actions=frozenset({"learn"}))` — in `commit()` are new; `SYSTEM_PROMPT`, `CharacterKeeper.__init__`, `readiness`, `work`, `run_once`, `build_character_keeper_runner` are unchanged. `chapter_id` is left at its default `""` since CharacterKeeper works across a rolling window of recent chapters, not one target chapter — there is no single chapter id to attribute a `learn` to, matching how `ThreadIntent`'s `chapter_id` already defaults to `""` for contexts without one target chapter.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_character_keeper.py -v`
Expected: PASS (all prior + 3 new). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/character_keeper.py tests/agents/test_character_keeper.py
git commit -m "feat: CharacterKeeper declares learn-only knowledge intents into secret.learned commits"
```

---

### Task 15: Docs — mark M4.1 complete, document the knowledge/causal ledgers

**Files:**
- Modify: `docs/submilestones/M4-knowledge-and-cause.md`
- Modify: `README.md`

**Interfaces:** none (documentation only).

- [ ] **Step 1: Update the sub-milestone table**

In `docs/submilestones/M4-knowledge-and-cause.md`, change the M4.1 row's `Status` cell from `not started` to `complete`.

- [ ] **Step 2: Add a README section**

In `README.md`, add a new subsection near the existing "thread ledger" material (after the "The thread ledger (Story Brain, Phase 1)" subsection added in M3.1, before any following top-level heading):

```markdown
### Secret & causal-edge ledgers (Story Brain, Phase 2)

The Author and Editor can declare secret bookkeeping alongside their normal
output: `plant` a new secret from a freeform title (slugged into a stable
id, same rule as threads), `learn` an existing secret for a character,
`reveal` a secret publicly, or record a character `uses` an existing secret
in a chapter. CharacterKeeper may only declare `learn` — minting or
revealing a secret is a narrative-authoring act reserved for Author/Editor.
Author and Editor can also declare `causal_intents`: a claimed
`(cause_chapter_id, effect_chapter_id, note)` relationship between two
existing chapters.

```bash
novelizer proposals   # secret.created/learned/referenced and
                       # causal_edge.declared never appear here, at any
                       # autonomy level; secret.revealed can, under
                       # gated_canon or gated_all
```

The knowledge matrix (`ReadStore.knowledge_matrix()`) tracks, per secret,
which characters have `learned` it and whether it has been `revealed`
(revealed is secret-level, set-once state that applies to every character
— including ones created after the reveal — never written per character).
`secret.referenced` events are the durable record of a character using a
secret in a chapter; the causal-edge ledger (`ReadStore.list_causal_edges()`)
is a strict, never-deduped append of every declared edge. Leak/paradox
detection over these ledgers, and their TUI views, are M4.2/M4.3.
```

- [ ] **Step 3: Commit**

```bash
git add docs/submilestones/M4-knowledge-and-cause.md README.md
git commit -m "docs: mark M4.1 complete; document the secret and causal-edge ledgers"
```

---

### Task 16: Full-suite verification

**Files:** none (verification only).

**Interfaces:** none.

- [ ] **Step 1: Run the complete test suite**

Run: `uv run pytest tests/ -v`
Expected: PASS, zero failures, zero errors. Confirm the new modules are exercised: `tests/canon/test_secrets.py`, `tests/canon/test_knowledge_projection_property.py`, `tests/canon/test_causal_graph_projection_property.py` all appear in the output with passing status, alongside every pre-existing test file.

- [ ] **Step 2: Run the suite a second time to catch nondeterminism**

Run: `uv run pytest tests/ -v`
Expected: identical PASS result — no flaky Hypothesis-generated failures (the two property tests use `deadline=None` and fixed `max_examples`, so they should be deterministic modulo Hypothesis's own seed, which reproduces failures with a printed `@reproduce_failure` decorator if one ever occurs; if either property test fails intermittently, do not suppress it — it means Task 4/5's `_apply` branches have a real ordering bug, and Tasks 7/8's oracle-vs-implementation mismatch must be root-caused before continuing).

- [ ] **Step 3: Confirm no `.env` file was created**

Run: `git status --porcelain | grep -i '\.env$'`
Expected: no output. If any `.env` file appears, it must not be committed — remove it before proceeding (this plan's Global Constraints explicitly forbid creating one).

- [ ] **Step 4: Report**

State plainly: full suite green (N passed, 0 failed), M4.1 done-when satisfied — an Author `learn` knowledge intent produces a `secret.learned` event and an updated `knowledge_matrix()` cell after `catch_up()` (Task 12's `test_author_commit_uses_a_known_active_secret`-style coverage plus Task 7's property test), and the causal-graph property test (Task 8) passes. No further commit needed for this task — it is verification-only.

---

## Self-Review

**Spec coverage against the M4.1 row and Locked decisions in `docs/submilestones/M4-knowledge-and-cause.md`:**
- "New `secret.*` event domain (`created`, `learned`, `referenced`, `revealed`) and `causal_edge.*` event domain (`declared`)" — Task 1.
- "Author/Editor/CharacterKeeper structured output gains an optional `knowledge_intents` field... Author/Editor gain an optional `causal_intents` field" — Task 9 (`KnowledgeIntent`/`CausalIntent`, `ChapterDraft`/`EditorVerdict`/`KeeperOutput` fields — `KeeperOutput` deliberately has no `causal_intents`, matching the spec's exact wording).
- "`work()` turns these into `committer.commit(...)` calls through the existing `Committer`/`GatingCommitter` seam" — Tasks 10/11 (shared translators) + Tasks 12/13/14 (wiring into `commit()`, called after existing commits, N+1-separate-appends precedent).
- "Secret identity: minted only at `secret.created` time from a freeform title, slugged the same way as threads... `learned`/`revealed` intents must cite an id drawn from the active-secret list... an intent naming an unknown id is dropped with a logged warning" — Task 1 (`slugify_secret_name`) + Task 10 (`_commit_knowledge_intents`'s validation/logging/no-op-on-unknown-id).
- "Any agent producing prose in a chapter (Author, Editor) may mint a secret at plant time; CharacterKeeper may also declare `learned` intents" — Task 1's docstring + Task 10's `allowed_actions` parameter + Task 14 (`allowed_actions=frozenset({"learn"})`).
- "A `uses` intent commits a `secret.referenced` event carrying `(secret_id, character_id, chapter_id)` — the durable, replayable record M4.2's `LeakDetector` reads" — Task 4 (`secret_references` table, never deduped) + Task 6 (`ReadStore.list_secret_references`, added now so the schema and read path are defined together, ahead of M4.2 needing it).
- "Causal edge identity: `(cause_chapter_id, effect_chapter_id, note)` — no separate minted id... no touch/pay-off lifecycle" — Task 1 (`CausalEdgeDeclared`) + Task 5 (append-only projection, no PK/uniqueness constraint) + Task 11 (`_commit_causal_intents`, no dedup).
- "`KnowledgeProjection`... per-character `learned` cells plus a secret-level `revealed` flag... the matrix accessor derives `revealed` for every character, including characters created after the reveal, rather than fanning out per-cell writes" — Task 4 (`secrets`/`secret_knowledge` tables, `revealed` stored once on the secret record) + Task 6 (`knowledge_matrix()` derives per-cell state at read time) + Task 1 (`knowledge_cell_state` helper, tested against a character id that was never written to any row).
- "`CausalGraphProjection` folding `causal_edge.declared` events into an adjacency list keyed by chapter id" — Task 5 (append-only `causal_edges` table; Task 6's `list_causal_edges()` is the adjacency data M4.2/M4.3 will fold into a dict-of-lists — M4.1 itself only needs to project and expose it, not build the DFS/cycle-detection logic, which is M4.2's `ParadoxDetector`).
- "`ReadStore.list_secrets()`/`get_secret()`/`knowledge_matrix()` and `ReadStore.list_causal_edges()`" — Task 6, verbatim.
- "Autonomy: `secret.created`/`secret.learned`/`secret.referenced` and `causal_edge.declared` added to `_NEVER_GATED`" — Task 3.
- Done-when: "Author declaring a `learned` knowledge intent results in a `secret.learned` event in the log and an updated cell in the knowledge-matrix read table after `catch_up()`" — Task 14's `test_character_keeper_commit_learn_commits_secret_learned` and Task 10's direct `_commit_knowledge_intents` learn tests exercise this exact chain (the spec's example agent is unspecified beyond "Author declaring" as an illustrative example; the plan additionally covers Author/Editor declaring `uses`/`reveal`/`plant` and CharacterKeeper declaring `learn`, since all four agents' wiring is in scope per the row's own text). "Hypothesis property test asserts the knowledge-matrix fold is monotonic... and secret-level `revealed` flag is set-once" — Task 7. "second property test asserts the causal-graph fold never drops or duplicates a declared edge across replay order" — Task 8.

**Decisions the M4.1 dispatch left open, resolved here:**
1. **Plant-collision downgrade analog (secrets).** M3.1's `_commit_thread_intents` downgrades a colliding `plant` to `thread.touched` because `touch` needs only `id`+`note`, which a plant intent already carries. Secrets have no such minimal-payload sibling event: `learn`/`uses` require a `character_id` the plant intent doesn't supply, and `reveal` is a much stronger claim ("this secret is now public") than "I tried to introduce a secret that already exists." Reinterpreting a collision as any of those would fabricate agent intent that was never declared. So Task 10 drops a colliding `plant` with a logged warning — the same fallback M3.1 already uses for *every other* unknown/invalid intent shape (unknown id on `touch`/`pay_off`/`abandon`), just applied one case earlier for secrets. This is documented inline in `_commit_knowledge_intents`'s docstring.
2. **Causal-edge dedup: none, by design, at both commit and projection level.** The M4-knowledge-and-cause.md decomposition explicitly asks for a decision "with justification." Two facts drove it: (a) Locked decision #4 states edges have "no separate minted id" and "no touch/pay-off lifecycle to protect," meaning there is no natural key to dedup *on* — `(cause, effect)` isn't unique by design (two different Editor passes might independently notice and declare the same causal link with different notes, and both are legitimate facts); (b) the M4.1 done-when's own wording — "never drops or duplicates a declared edge across replay order" — reads as a strict 1:1 event-to-row replay guarantee, which a dedup step would violate (a dedup that collapses two identical declarations would, from the property test's point of view, be "dropping" one of them). Task 8's property test pins this literally: `sorted(edges) == incremental == rebuilt`, with no set-based collapsing. Self-edges are still rejected — at commit time, not projection time — because they're not "the same edge declared twice," they're a malformed edge that would break M4.2's ordering-violation check (`cause == effect` isn't a meaningful cause/effect claim).
3. **`ReadStore.list_secret_references()` added in Task 6, one accessor beyond the M4.1 row's explicit four-accessor list.** The row's Done-when text itself calls `secret.referenced` "the durable, replayable record M4.2's `LeakDetector` reads," which requires *some* read path — without one, Task 4/5's projected data would be committed and stored but structurally unreachable until M4.2 adds both the schema knowledge *and* the accessor in the same session. Adding the accessor now, while `secret_references`' shape is already fully specified and tested (Task 4), is lower-risk than deferring it: it costs one small task addition and removes a full reverse-engineering step from M4.2's planner. Flagged explicitly here per the dispatch's instruction to surface such calls.
4. **Editor's `valid_chapter_ids` vs. Author's.** Author includes its own newly-minted `chapter.id` in the set passed to `_commit_causal_intents` (Task 12) since it can declare an edge involving the chapter it's about to create; Editor never authors chapters, so its `ctx["chapters"]` already covers every chapter it could plausibly cite (Task 13). This asymmetry is called out explicitly in both tasks' Step 3 commentary so a reviewer doesn't mistake it for an oversight.
5. **`aggregate_id` for `causal_edge.declared` commits is `effect_chapter_id`.** `EventStore.append` requires an aggregate id and edges have no minted identity of their own (Locked decision #4); `effect_chapter_id` was chosen over `cause_chapter_id` arbitrarily but consistently — it means all edges landing on the same chapter share an aggregate id, which is a harmless, unused grouping today (the projection never queries by aggregate id) but is a defensible default a later milestone could build on (e.g., "show all causal edges into chapter X" as an aggregate-id-keyed query) without a payload migration.

**Placeholder scan:** every task's Step 3 shows complete code — full new files (`secrets.py`), full new classes/methods, or exact before/after snippets anchored to the current file contents read during planning (`events.py`, `store/models.py`, `policy.py`, `projector.py`, `read_store.py`, `schemas.py`, `base.py`, `author.py`, `editor.py`, `character_keeper.py` were all read in full before this plan was written, including M3.1's already-implemented `_commit_thread_intents` plant-collision-downgrade code in the current `base.py`, which this plan explicitly diverges from with stated rationale rather than copying blindly). No "similar to Task N," no `...` elisions, no TODOs.

**Type consistency:** `SecretRecord.revealed: bool` (Task 2) matches `Projector._apply`'s `SECRET_REVEALED` branch's `record.revealed`/`not record.revealed` checks (Task 4) and `ReadStore.knowledge_matrix()`'s `secret.revealed` read (Task 6). `KnowledgeIntent.action: Literal["plant", "learn", "reveal", "uses"]` (Task 9) matches the four branches in `_commit_knowledge_intents` (Task 10) and `_KNOWLEDGE_EVENT_BY_ACTION`'s three non-`plant` keys exactly. `CausalIntent.cause_chapter_id`/`effect_chapter_id` (Task 9) match `CausalEdgeDeclared`'s field names (Task 1) and `CausalEdgeRecord`'s field names (Task 2) verbatim — no drift across the write-model/read-model boundary, unlike `ThreadIntent`'s deliberate `pay_off`(intent)/`paid_off`(state) distinction, which has no analog here since causal edges have no state machine.

**DDD/SOLID:**
- Single Responsibility: `slugify_secret_name`/`knowledge_cell_state` only slug/derive; `Projector._apply`'s `SECRET_*`/`CAUSAL_EDGE_DECLARED` branches are the only place that knows each event's projection rule; `BaseAgent._commit_knowledge_intents`/`_commit_causal_intents` are the only places that turn an intent into a commit; `ReadStore`'s five new methods are the only read path.
- Open/Closed: `ChapterDraft`/`EditorVerdict`/`KeeperOutput` each gain one or two new defaulted fields; `Author`/`Editor`/`CharacterKeeper`'s `poll()`/`commit()` each gain new dict keys and new trailing lines, following the exact M3.1 `active_thread_ids`/`_commit_thread_intents` precedent — no existing logic branch is modified.
- Dependency Inversion / bounded context: Story Brain's write path (`secret.*`/`causal_edge.*` events, `KnowledgeProjection`, `CausalGraphProjection`) depends only on the existing `Committer`/`Projector`/`EventStore` seam; agents depend only on `ReadStore` accessors and the shared `BaseAgent` helpers, never on Projector internals.
- Event sourcing: `secrets`/`secret_knowledge`/`secret_references`/`causal_edges` are disposable, rebuildable projections (Tasks 4/5's rebuild-equivalence tests + Tasks 7/8's property tests all assert this); no persistence path bypasses the event log.

**Backward-compatibility check:** `ChapterDraft.knowledge_intents`/`causal_intents`, `EditorVerdict.knowledge_intents`/`causal_intents`, `KeeperOutput.knowledge_intents` all default to `[]`; `_commit_knowledge_intents([], ...)`/`_commit_causal_intents([], ...)` iterate zero times and commit nothing (Tasks 10/11's `noop_on_empty_list` tests, plus Tasks 12/13/14's "with no ... intents emits no new event types" tests pin this directly against the live agent classes). Every existing `ChapterDraft(...)`/`EditorVerdict(...)`/`KeeperOutput(...)` construction across the pre-M4.1 suite omits these fields, so none of them are affected. `Author.poll()`/`Editor.poll()`/`CharacterKeeper.poll()` each gain new dict keys that no pre-existing test reads, so no existing assertion on `ctx` contents breaks — mirroring M3.1's Task 10 note about `Editor.poll()`'s prompt-construction code (`work()`) never touching the new keys, so byte-identical-prompt tests are unaffected.
