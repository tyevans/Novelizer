# M4.2 · Leak & Paradox Analyzers, Continuity Checker Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Branch:** `m4.2-leak-paradox-analyzers` (create it before Task 1; this plan does not create it).

**Goal:** Two deterministic, pure analyzers — `LeakDetector` (a committed `secret.referenced` event whose character hasn't `learned`/isn't covered by `revealed`) and `ParadoxDetector` (a causal edge whose effect chapter is at-or-before its cause chapter, or that closes a cycle) — run every `ContinuityChecker` cycle alongside the existing LLM contradiction pass, and file their hits as `retcon_request.created` events tagged with pinned source constants, landing in the same open-retcon queue the LLM path already uses.

**Architecture:** Two new `novelizer/brain/` modules (`leaks.py`, `paradoxes.py`) as pure-function siblings to M3.2's `staleness.py`/`sag_spike.py`: plain inputs (lists/dicts shaped exactly like `ReadStore` accessors already return), no DB access, no persistence, never memoized — computed fresh every call so the Who-Knows-What/Causeway views (M4.3) and the Continuity Checker can never disagree. `ContinuityChecker.poll()` gains four more `ReadStore` reads (`list_secret_references()`, `knowledge_matrix()`, `list_causal_edges()`, the full `list_chapters()` id order); `work()` is unchanged (still exactly one LLM call for the free-text contradiction pass); `commit()` gains two more loops that turn detector hits into `retcon_request.created` events through the *same* `RetconRequest`/`Committer` call the LLM path already uses, each tagged by prefixing `description` with `LEAK_SOURCE_TAG`/`PARADOX_SOURCE_TAG` — no new event type, no policy change (Locked decision #5: `retcon_request.created` is already ungated below `gated_all`). Re-filing is prevented by a plain string-membership check against currently-open retcon descriptions (see Task 5's dedup rationale) — no new persisted state, no new event type, matching the "route through the existing path unchanged" constraint.

**Tech Stack:** Python 3.13, `pydantic` v2, `aiosqlite`, `pytest`+`pytest-asyncio` (`asyncio_mode=auto`), `hypothesis>=6.156.6`.

## Global Constraints

- Event sourcing: neither detector persists anything; both are pure functions over data `ReadStore` already exposes (all landed in M4.1: `list_secret_references()`, `knowledge_matrix()`, `list_causal_edges()`, `list_chapters()`).
- No new event types and no autonomy-policy change (Locked decision #5): leak/paradox hits become `retcon_request.created` events via the existing `RetconRequest` model and `Committer.commit()` call, identical in shape to what the LLM contradiction pass already produces.
- Pinned constants (exact strings, fixed in the M4 spec so M4.3's done-when assertion cannot drift): `LEAK_SOURCE_TAG = "[source: leak_detector]"` in `novelizer/brain/leaks.py`; `PARADOX_SOURCE_TAG = "[source: paradox_detector]"` in `novelizer/brain/paradoxes.py`. Both are description-string **prefixes** — `retcon_request.description.startswith(TAG)` must hold.
- Leak rule (Locked decision #3): a `secret.referenced` event naming character C using secret S in chapter H is a leak iff `novelizer.canon.secrets.knowledge_cell_state(matrix, S, C) == "unknown"` — i.e. C has not `learned` S and S is not `revealed`, per the *current* knowledge matrix (see Task 1's Decision Note for why the aggregate current-matrix, not a chapter-H-bounded historical snapshot, is the correct and sufficient input shape).
- Paradox rule (Locked decision #4): a causal edge is a paradox candidate if its effect chapter's index in `ReadStore.list_chapters()` order is at-or-before its cause chapter's index, **or** if the edge participates in a cycle in the adjacency list. Every edge on a cycle is its own candidate (a 2-cycle yields two candidates, one per edge). Plain dicts/lists, DFS over a dict-of-lists (~20 lines) — no `networkx`.
- No new dependencies.
- DRY: leak/paradox → retcon-description formatting each lives in exactly one function (`leak_description`, `paradox_description`), called identically everywhere a description string is needed (commit path and the dedup check use the same function, so they can never drift into two different formats for the same fact).
- TDD, black-box-first: every task starts with a failing test asserting on observable output (detector return values, or committed events / `list_retcon_requests` rows), not internals. Hypothesis property tests generalize the "no false positives" and "no false negatives on real cycles" invariants (Tasks 2 and 4), mirroring M3.2's `test_staleness.py`/`test_sag_spike.py` style exactly.
- Do **not** create any `.env` file at any point in this plan. Every task ends by running the **full** suite (`uv run pytest`) and reporting real failures — never wave away a red test as "pre-existing" without first confirming it fails identically on `git stash` (i.e., before your change).

---

### Task 1: `LeakDetector` — `novelizer/brain/leaks.py`

**Files:**
- Create: `novelizer/brain/leaks.py`
- Test: `tests/brain/test_leaks.py`

**Interfaces:**
- Consumes: `novelizer.canon.secrets.knowledge_cell_state(matrix, secret_id, character_id) -> str` (landed in M4.1, `novelizer/canon/secrets.py`); `novelizer.store.models.SecretReferenceRecord(secret_id, character_id, chapter_id, note)` (landed in M4.1); the `dict[str, dict]` shape `ReadStore.knowledge_matrix()` returns: `{secret_id: {"revealed": bool, "known_by": set[str]}}`.
- Produces: `LEAK_SOURCE_TAG = "[source: leak_detector]"` (module constant); `Leak(BaseModel)` with fields `secret_id: str`, `character_id: str`, `chapter_id: str`, `note: str = ""`; `find_leaks(references: list[SecretReferenceRecord], matrix: dict[str, dict]) -> list[Leak]`; `leak_description(leak: Leak) -> str` (used by Task 5's Continuity Checker commit path and its dedup check — the single place a leak's retcon description is formatted).

**Decision Note (for the implementer, not a step to skip):** The M4 spec's Locked decision #3 says a leak is evaluated "over the full log through and including chapter H's own commits." `find_leaks` satisfies this by checking against the *current* `knowledge_matrix()` — which, by construction, always includes every commit up to and including chapter H's, since the log only grows forward and the Continuity Checker always polls fresh state. This correctly implements the spec's explicit test case (self-consistent learn-then-use in the same chapter is not a leak, because the matrix already reflects same-cycle learns) and the CI-provable done-when (a reference with no learn/reveal anywhere in the log is a leak). It does **not** implement a stricter reading where a learn in a chapter *strictly after* H would still leave H's reference flagged as a leak (an as-of-chapter-H historical snapshot) — that would require extending `SecretRecord`/`secret_knowledge` with per-event chapter-ordering data not currently exposed by `ReadStore`, a scope increase the spec's own done-when does not exercise and the non-goals list doesn't call for. This is documented here as an explicit, intentional simplification (YAGNI), not an oversight — flag it in the M4.2 PR description for reviewer visibility.

- [ ] **Step 1: Write the failing tests**

Create `tests/brain/test_leaks.py`:

```python
from novelizer.brain.leaks import LEAK_SOURCE_TAG, Leak, find_leaks, leak_description
from novelizer.store.models import SecretReferenceRecord


def test_reference_with_no_learn_or_reveal_is_a_leak():
    refs = [SecretReferenceRecord(secret_id="the-heir-lives", character_id="mara", chapter_id="c3")]
    matrix = {"the-heir-lives": {"revealed": False, "known_by": set()}}
    leaks = find_leaks(refs, matrix)
    assert leaks == [Leak(secret_id="the-heir-lives", character_id="mara", chapter_id="c3")]


def test_reference_by_a_character_who_learned_is_not_a_leak():
    refs = [SecretReferenceRecord(secret_id="the-heir-lives", character_id="mara", chapter_id="c3")]
    matrix = {"the-heir-lives": {"revealed": False, "known_by": {"mara"}}}
    assert find_leaks(refs, matrix) == []


def test_reference_to_a_revealed_secret_is_not_a_leak_even_if_unlearned():
    refs = [SecretReferenceRecord(secret_id="the-heir-lives", character_id="ren", chapter_id="c5")]
    matrix = {"the-heir-lives": {"revealed": True, "known_by": set()}}
    assert find_leaks(refs, matrix) == []


def test_reference_to_a_secret_missing_from_the_matrix_is_a_leak():
    refs = [SecretReferenceRecord(secret_id="unminted", character_id="mara", chapter_id="c1")]
    assert find_leaks(refs, {}) == [Leak(secret_id="unminted", character_id="mara", chapter_id="c1")]


def test_multiple_references_flag_only_the_unknown_ones():
    refs = [
        SecretReferenceRecord(secret_id="s1", character_id="mara", chapter_id="c1"),
        SecretReferenceRecord(secret_id="s1", character_id="ren", chapter_id="c2"),
    ]
    matrix = {"s1": {"revealed": False, "known_by": {"mara"}}}
    leaks = find_leaks(refs, matrix)
    assert leaks == [Leak(secret_id="s1", character_id="ren", chapter_id="c2")]


def test_leak_description_starts_with_the_pinned_tag_and_names_the_fact():
    leak = Leak(secret_id="the-heir-lives", character_id="mara", chapter_id="c3")
    desc = leak_description(leak)
    assert desc.startswith(LEAK_SOURCE_TAG)
    assert "the-heir-lives" in desc and "mara" in desc and "c3" in desc


def test_leak_description_is_deterministic_for_the_same_leak():
    leak = Leak(secret_id="s1", character_id="mara", chapter_id="c1")
    assert leak_description(leak) == leak_description(Leak(secret_id="s1", character_id="mara", chapter_id="c1"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/brain/test_leaks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'novelizer.brain.leaks'`.

- [ ] **Step 3: Implement**

Create `novelizer/brain/leaks.py`:

```python
from __future__ import annotations
from pydantic import BaseModel
from novelizer.canon.secrets import knowledge_cell_state
from novelizer.store.models import SecretReferenceRecord

LEAK_SOURCE_TAG = "[source: leak_detector]"


class Leak(BaseModel):
    """A committed secret.referenced event with no covering learn/reveal.

    Never persisted -- computed fresh from ReadStore data every time
    find_leaks runs (novelizer/agents/continuity_checker.py, and later
    M4.3's Who-Knows-What render helper), same precedent as
    novelizer/brain/staleness.py's is_thread_stale.
    """

    secret_id: str
    character_id: str
    chapter_id: str
    note: str = ""


def find_leaks(references: list[SecretReferenceRecord], matrix: dict[str, dict]) -> list[Leak]:
    """A reference is a leak iff knowledge_cell_state(matrix, secret_id,
    character_id) == "unknown" -- the character has neither learned the
    secret nor is it revealed, per the current knowledge matrix (see this
    module's Decision Note in the M4.2 plan for why the current aggregate
    matrix, not a chapter-H-bounded historical snapshot, is the input
    shape). References are never deduped (M4.1 Locked decision #3) --
    every leaking reference is reported, preserving input order.
    """
    return [
        Leak(secret_id=ref.secret_id, character_id=ref.character_id, chapter_id=ref.chapter_id, note=ref.note)
        for ref in references
        if knowledge_cell_state(matrix, ref.secret_id, ref.character_id) == "unknown"
    ]


def leak_description(leak: Leak) -> str:
    """The single place a leak's retcon-request description is formatted.
    Deterministic given the same (secret_id, character_id, chapter_id) --
    novelizer/agents/continuity_checker.py's dedup check relies on this to
    recognize "the same leak" across polling cycles without any new
    persisted state.
    """
    return (
        f"{LEAK_SOURCE_TAG} secret '{leak.secret_id}' is referenced by character "
        f"'{leak.character_id}' in chapter '{leak.chapter_id}' with no prior learn or reveal."
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/brain/test_leaks.py -v`
Expected: PASS (7 new). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/brain/leaks.py tests/brain/test_leaks.py
git commit -m "feat: pure LeakDetector over the knowledge matrix and secret references"
```

---

### Task 2: `LeakDetector` — Hypothesis property test

**Files:**
- Test: `tests/brain/test_leaks.py` (append)

**Interfaces:**
- Consumes: `Leak`, `find_leaks` (Task 1).

- [ ] **Step 1: Write the failing test**

Append to `tests/brain/test_leaks.py`:

```python
from hypothesis import given, settings, strategies as st

_ids = st.text(alphabet="abcdefghij", min_size=1, max_size=6)


@given(
    secret_id=_ids, character_id=_ids, chapter_id=_ids,
    revealed=st.booleans(), other_known=st.sets(_ids, max_size=5),
)
@settings(max_examples=100)
def test_a_learned_or_revealed_reference_is_never_a_leak(secret_id, character_id, chapter_id, revealed, other_known):
    """No false positives: for any matrix state where the referencing
    character has learned the secret (or the secret is revealed), find_leaks
    never flags that reference -- regardless of who else does or doesn't
    know it."""
    known_by = other_known | {character_id}
    matrix = {secret_id: {"revealed": revealed, "known_by": known_by}}
    refs = [SecretReferenceRecord(secret_id=secret_id, character_id=character_id, chapter_id=chapter_id)]
    assert find_leaks(refs, matrix) == []


@given(secret_id=_ids, character_id=_ids, chapter_id=_ids, other_known=st.sets(_ids, max_size=5))
@settings(max_examples=100)
def test_an_unlearned_unrevealed_reference_is_always_a_leak(secret_id, character_id, chapter_id, other_known):
    """No false negatives: for any matrix state where the referencing
    character is absent from known_by and the secret isn't revealed,
    find_leaks always flags that reference."""
    known_by = other_known - {character_id}
    matrix = {secret_id: {"revealed": False, "known_by": known_by}}
    refs = [SecretReferenceRecord(secret_id=secret_id, character_id=character_id, chapter_id=chapter_id)]
    leaks = find_leaks(refs, matrix)
    assert len(leaks) == 1
    assert leaks[0].secret_id == secret_id and leaks[0].character_id == character_id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/brain/test_leaks.py -v -k hypothesis or property or learned_or_revealed or always_a_leak`
Expected: These two new tests currently pass trivially once Task 1 lands (they exercise Task 1's implementation, not new production code) — this task is a red/green formality only in the sense that the file doesn't exist before Task 1. Since Task 1 is already merged when this task starts, run the tests directly and confirm they **pass** on the first try (Task 1's implementation already satisfies both properties by construction). If either fails, that means Task 1's `find_leaks` has a bug — stop and fix Task 1's implementation, do not weaken this test.

- [ ] **Step 3: N/A**

No production code changes in this task — it adds property-based coverage of Task 1's `find_leaks`. If Step 2 passed, skip to Step 4.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/brain/test_leaks.py -v`
Expected: PASS (9 total). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add tests/brain/test_leaks.py
git commit -m "test: Hypothesis property coverage for LeakDetector no-false-positive/negative invariants"
```

---

### Task 3: `ParadoxDetector` — `novelizer/brain/paradoxes.py`

**Files:**
- Create: `novelizer/brain/paradoxes.py`
- Test: `tests/brain/test_paradoxes.py`

**Interfaces:**
- Consumes: `novelizer.store.models.CausalEdgeRecord(cause_chapter_id, effect_chapter_id, note)` (landed in M4.1).
- Produces: `PARADOX_SOURCE_TAG = "[source: paradox_detector]"` (module constant); `ParadoxCandidate(BaseModel)` with fields `cause_chapter_id: str`, `effect_chapter_id: str`, `note: str = ""`, `reason: str` (one of `"ordering"` or `"cycle"`); `find_paradoxes(edges: list[CausalEdgeRecord], chapter_order: list[str]) -> list[ParadoxCandidate]`; `paradox_description(p: ParadoxCandidate) -> str` (the single place a paradox's retcon description is formatted — used by Task 5's commit path and its dedup check, same role as `leak_description`).

- [ ] **Step 1: Write the failing tests**

Create `tests/brain/test_paradoxes.py`:

```python
from novelizer.brain.paradoxes import PARADOX_SOURCE_TAG, ParadoxCandidate, find_paradoxes, paradox_description
from novelizer.store.models import CausalEdgeRecord


def test_forward_edge_is_not_a_paradox():
    edges = [CausalEdgeRecord(cause_chapter_id="c1", effect_chapter_id="c3")]
    assert find_paradoxes(edges, ["c1", "c2", "c3"]) == []


def test_effect_at_or_before_cause_is_an_ordering_paradox():
    edges = [CausalEdgeRecord(cause_chapter_id="c3", effect_chapter_id="c1")]
    result = find_paradoxes(edges, ["c1", "c2", "c3"])
    assert result == [ParadoxCandidate(cause_chapter_id="c3", effect_chapter_id="c1", reason="ordering")]


def test_effect_equal_to_cause_is_an_ordering_paradox():
    edges = [CausalEdgeRecord(cause_chapter_id="c2", effect_chapter_id="c2")]
    result = find_paradoxes(edges, ["c1", "c2", "c3"])
    assert result == [ParadoxCandidate(cause_chapter_id="c2", effect_chapter_id="c2", reason="ordering")]


def test_two_cycle_reports_both_edges():
    edges = [
        CausalEdgeRecord(cause_chapter_id="c1", effect_chapter_id="c2", note="fire"),
        CausalEdgeRecord(cause_chapter_id="c2", effect_chapter_id="c1", note="revenge"),
    ]
    result = find_paradoxes(edges, ["c1", "c2"])
    reasons = {(p.cause_chapter_id, p.effect_chapter_id, p.reason) for p in result}
    assert ("c1", "c2", "ordering") in reasons or ("c1", "c2", "cycle") in reasons
    assert ("c2", "c1", "ordering") in reasons
    assert len(result) == 2


def test_three_cycle_reports_all_three_edges_as_cycle_candidates():
    edges = [
        CausalEdgeRecord(cause_chapter_id="c1", effect_chapter_id="c2"),
        CausalEdgeRecord(cause_chapter_id="c2", effect_chapter_id="c3"),
        CausalEdgeRecord(cause_chapter_id="c3", effect_chapter_id="c1"),
    ]
    result = find_paradoxes(edges, ["c1", "c2", "c3"])
    pairs = {(p.cause_chapter_id, p.effect_chapter_id) for p in result}
    assert pairs == {("c1", "c2"), ("c2", "c3"), ("c3", "c1")}
    for p in result:
        assert p.reason in ("ordering", "cycle")


def test_acyclic_forward_graph_has_no_candidates():
    edges = [
        CausalEdgeRecord(cause_chapter_id="c1", effect_chapter_id="c2"),
        CausalEdgeRecord(cause_chapter_id="c2", effect_chapter_id="c3"),
        CausalEdgeRecord(cause_chapter_id="c1", effect_chapter_id="c3"),
    ]
    assert find_paradoxes(edges, ["c1", "c2", "c3"]) == []


def test_paradox_description_starts_with_the_pinned_tag():
    p = ParadoxCandidate(cause_chapter_id="c3", effect_chapter_id="c1", reason="ordering")
    desc = paradox_description(p)
    assert desc.startswith(PARADOX_SOURCE_TAG)
    assert "c3" in desc and "c1" in desc
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/brain/test_paradoxes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'novelizer.brain.paradoxes'`.

- [ ] **Step 3: Implement**

Create `novelizer/brain/paradoxes.py`:

```python
from __future__ import annotations
from pydantic import BaseModel
from novelizer.store.models import CausalEdgeRecord

PARADOX_SOURCE_TAG = "[source: paradox_detector]"


class ParadoxCandidate(BaseModel):
    """A causal edge flagged as internally inconsistent. Never persisted --
    computed fresh from ReadStore data every time find_paradoxes runs
    (novelizer/agents/continuity_checker.py, and later M4.3's Causeway
    render helper), same precedent as novelizer/brain/sag_spike.py.
    `reason` is "ordering" (effect chapter is at-or-before cause chapter)
    or "cycle" (this edge closes a cycle in the adjacency list).
    """

    cause_chapter_id: str
    effect_chapter_id: str
    note: str = ""
    reason: str


def _cycle_edges(edges: list[CausalEdgeRecord]) -> set[tuple[str, str]]:
    """Every (cause, effect) pair that lies on some cycle in the adjacency
    list built from `edges`. Plain dict-of-lists DFS, no graph library
    (M4 Locked decision #4). Duplicate declared edges (no dedup at the
    projection level) are naturally handled: the adjacency list carries
    them as repeated entries, and each occurrence is checked independently.
    """
    adjacency: dict[str, list[str]] = {}
    for e in edges:
        adjacency.setdefault(e.cause_chapter_id, []).append(e.effect_chapter_id)

    cyclic: set[tuple[str, str]] = set()
    visiting: set[str] = set()
    visited: set[str] = set()
    path: list[str] = []

    def dfs(node: str) -> None:
        visiting.add(node)
        path.append(node)
        for neighbor in adjacency.get(node, []):
            if neighbor in visiting:
                idx = path.index(neighbor)
                cycle_nodes = path[idx:] + [neighbor]
                for a, b in zip(cycle_nodes, cycle_nodes[1:]):
                    cyclic.add((a, b))
            elif neighbor not in visited:
                dfs(neighbor)
        path.pop()
        visiting.discard(node)
        visited.add(node)

    for node in list(adjacency):
        if node not in visited:
            dfs(node)
    return cyclic


def find_paradoxes(edges: list[CausalEdgeRecord], chapter_order: list[str]) -> list[ParadoxCandidate]:
    """An edge is a paradox candidate if its effect chapter's index in
    `chapter_order` is at or before its cause chapter's index ("ordering"),
    or if it participates in a cycle in the declared-edge adjacency list
    ("cycle") -- every edge on a cycle is its own candidate, so a 2-cycle
    yields two candidates (M4 spec's done-when). An edge citing a chapter
    id absent from `chapter_order` cannot be ordering-checked and is
    skipped for that check (chapter ids are validated to exist at commit
    time by BaseAgent._commit_causal_intents, so this should not occur in
    practice) but is still eligible for the cycle check.
    """
    index = {cid: i for i, cid in enumerate(chapter_order)}
    cyclic_pairs = _cycle_edges(edges)
    candidates: list[ParadoxCandidate] = []
    for e in edges:
        cause_idx = index.get(e.cause_chapter_id)
        effect_idx = index.get(e.effect_chapter_id)
        if cause_idx is not None and effect_idx is not None and effect_idx <= cause_idx:
            candidates.append(ParadoxCandidate(
                cause_chapter_id=e.cause_chapter_id, effect_chapter_id=e.effect_chapter_id,
                note=e.note, reason="ordering",
            ))
        elif (e.cause_chapter_id, e.effect_chapter_id) in cyclic_pairs:
            candidates.append(ParadoxCandidate(
                cause_chapter_id=e.cause_chapter_id, effect_chapter_id=e.effect_chapter_id,
                note=e.note, reason="cycle",
            ))
    return candidates


def paradox_description(p: ParadoxCandidate) -> str:
    """The single place a paradox's retcon-request description is
    formatted. Deterministic given the same edge -- the Continuity
    Checker's dedup check (novelizer/agents/continuity_checker.py) relies
    on this to recognize "the same paradox" across polling cycles.
    """
    return (
        f"{PARADOX_SOURCE_TAG} causal edge {p.cause_chapter_id} -> {p.effect_chapter_id} "
        f"is a paradox candidate ({p.reason})."
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/brain/test_paradoxes.py -v`
Expected: PASS (7 new). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/brain/paradoxes.py tests/brain/test_paradoxes.py
git commit -m "feat: pure ParadoxDetector — ordering violations and cycle-closing edges"
```

---

### Task 4: `ParadoxDetector` — Hypothesis property test

**Files:**
- Test: `tests/brain/test_paradoxes.py` (append)

**Interfaces:**
- Consumes: `ParadoxCandidate`, `find_paradoxes` (Task 3).

- [ ] **Step 1: Write the failing test**

Append to `tests/brain/test_paradoxes.py`:

```python
from hypothesis import given, settings, strategies as st


@given(n=st.integers(min_value=1, max_value=8), data=st.data())
@settings(max_examples=100)
def test_any_forward_only_dag_has_no_candidates(n, data):
    """No false positives: for chapter_order of length n, any set of edges
    that all point strictly forward (effect index > cause index) never
    produces a paradox candidate -- neither an ordering violation (by
    construction) nor a cycle (a forward-only graph over a total order
    cannot contain a cycle)."""
    chapter_order = [f"c{i}" for i in range(n)]
    edge_pairs = data.draw(st.lists(
        st.tuples(st.integers(0, n - 1), st.integers(0, n - 1)).filter(lambda t: t[1] > t[0]),
        max_size=10,
    ))
    edges = [CausalEdgeRecord(cause_chapter_id=chapter_order[a], effect_chapter_id=chapter_order[b])
             for a, b in edge_pairs]
    assert find_paradoxes(edges, chapter_order) == []


def test_falsification_self_loop_is_an_ordering_paradox():
    """A self-edge (cause == effect) is always effect-at-or-before-cause,
    the degenerate case of the ordering rule."""
    edges = [CausalEdgeRecord(cause_chapter_id="c1", effect_chapter_id="c1")]
    result = find_paradoxes(edges, ["c1", "c2"])
    assert len(result) == 1 and result[0].reason == "ordering"


def test_falsification_a_backward_edge_among_forward_edges_is_isolated():
    """Mixing one backward edge into an otherwise-forward graph flags only
    the backward edge, not its forward neighbors."""
    edges = [
        CausalEdgeRecord(cause_chapter_id="c1", effect_chapter_id="c2"),
        CausalEdgeRecord(cause_chapter_id="c3", effect_chapter_id="c1"),
    ]
    result = find_paradoxes(edges, ["c1", "c2", "c3"])
    assert len(result) == 1
    assert result[0].cause_chapter_id == "c3" and result[0].effect_chapter_id == "c1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/brain/test_paradoxes.py -v`
Expected: since Task 3's implementation already satisfies these properties by construction, run directly and confirm PASS on first try (same formality as Task 2 — this task adds property/falsification coverage of already-landed production code, not new production code). If any assertion fails, that indicates a bug in Task 3's `find_paradoxes`/`_cycle_edges` — stop and fix Task 3, do not weaken this test.

- [ ] **Step 3: N/A**

No production code changes in this task.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/brain/test_paradoxes.py -v`
Expected: PASS (10 total). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add tests/brain/test_paradoxes.py
git commit -m "test: Hypothesis property + falsification coverage for ParadoxDetector"
```

---

### Task 5: `ContinuityChecker` upgrade — run both detectors every cycle, file tagged retcon requests, dedup

**Files:**
- Modify: `novelizer/agents/continuity_checker.py`
- Test: `tests/agents/test_continuity_checker.py`

**Interfaces:**
- Consumes: `find_leaks`, `Leak`, `leak_description`, `LEAK_SOURCE_TAG` (Task 1); `find_paradoxes`, `ParadoxCandidate`, `paradox_description`, `PARADOX_SOURCE_TAG` (Task 3); `ReadStore.list_secret_references()`, `ReadStore.knowledge_matrix()`, `ReadStore.list_causal_edges()`, `ReadStore.list_chapters()` (all landed in M4.1); `ReadStore.list_retcon_requests(status=...)` (pre-existing).
- Produces: `ContinuityChecker.poll()` now returns a dict with four additional keys — `"chapter_order": list[str]`, `"secret_references": list[SecretReferenceRecord]`, `"knowledge_matrix": dict[str, dict]`, `"causal_edges": list[CausalEdgeRecord]` — alongside the pre-existing `"world"`, `"characters"`, `"chapters"` keys. `commit()`'s signature and the LLM-sourced commit behavior are unchanged; it additionally files one `retcon_request.created` per leak/paradox not already open.

**Dedup Decision (for the implementer):** The LLM contradiction path has no dedup today (confirmed: `commit()` files every `RetconDraft` the LLM returns, every cycle, with no guard) — there is no existing pattern to mirror. For the two new deterministic paths, dedup is done by exact-string membership: before filing anything, fetch all currently-open retcon requests (`list_retcon_requests(status=RetconStatus.open)`) and build a set of their `description` strings. Both `leak_description()` and `paradox_description()` are pure and deterministic for the same fact (same secret/character/chapter, or same edge+reason), so re-running the detector against an unchanged log produces byte-identical description strings — skip filing whenever the formatted description is already in the open set, and add each newly-filed description to that set immediately (so a single cycle's own duplicates, e.g. from a duplicated `secret.referenced` event, are also caught). This adds no new event type, no new persisted state, and does not touch the LLM path — satisfying Locked decision #5's "route through the existing path unchanged."

- [ ] **Step 1: Write the failing tests**

Append to `tests/agents/test_continuity_checker.py`:

```python
from novelizer.brain.leaks import LEAK_SOURCE_TAG
from novelizer.brain.paradoxes import PARADOX_SOURCE_TAG
from novelizer.canon.events import SecretCreated, SecretReferenced, CausalEdgeDeclared
from novelizer.store.models import Chapter


async def _seed_leak(events, proj):
    await events.append(EventType.SECRET_CREATED, "the-heir-lives", SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(EventType.SECRET_REFERENCED, "the-heir-lives",
                        SecretReferenced(id="the-heir-lives", character_id="mara", chapter_id="c1"))
    await proj.catch_up()


async def test_leak_is_filed_as_a_tagged_retcon_request(stack):
    events, proj, read, committer = stack
    await _seed_leak(events, proj)
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), read, committer)
    await agent.run_once()
    await proj.catch_up()
    open_reqs = await read.list_retcon_requests(status=RetconStatus.open)
    leak_reqs = [r for r in open_reqs if r.description.startswith(LEAK_SOURCE_TAG)]
    assert len(leak_reqs) == 1
    assert "the-heir-lives" in leak_reqs[0].description and "mara" in leak_reqs[0].description


async def test_leak_is_not_refiled_on_a_second_cycle(stack):
    events, proj, read, committer = stack
    await _seed_leak(events, proj)
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), read, committer)
    await agent.run_once()
    await proj.catch_up()
    await agent.run_once()
    await proj.catch_up()
    open_reqs = await read.list_retcon_requests(status=RetconStatus.open)
    leak_reqs = [r for r in open_reqs if r.description.startswith(LEAK_SOURCE_TAG)]
    assert len(leak_reqs) == 1


async def test_learned_reference_does_not_get_flagged(stack):
    events, proj, read, committer = stack
    from novelizer.canon.events import SecretLearned
    await events.append(EventType.SECRET_CREATED, "the-heir-lives", SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(EventType.SECRET_LEARNED, "the-heir-lives", SecretLearned(id="the-heir-lives", character_id="mara", chapter_id="c1"))
    await events.append(EventType.SECRET_REFERENCED, "the-heir-lives", SecretReferenced(id="the-heir-lives", character_id="mara", chapter_id="c1"))
    await proj.catch_up()
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), read, committer)
    await agent.run_once()
    await proj.catch_up()
    open_reqs = await read.list_retcon_requests(status=RetconStatus.open)
    assert [r for r in open_reqs if r.description.startswith(LEAK_SOURCE_TAG)] == []


async def test_paradox_is_filed_as_a_tagged_retcon_request(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(EventType.CHAPTER_CREATED, "c2", Chapter(id="c2", title="Two", prose="p"))
    await events.append(EventType.CAUSAL_EDGE_DECLARED, "c1",
                        CausalEdgeDeclared(cause_chapter_id="c2", effect_chapter_id="c1"))
    await proj.catch_up()
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), read, committer)
    await agent.run_once()
    await proj.catch_up()
    open_reqs = await read.list_retcon_requests(status=RetconStatus.open)
    paradox_reqs = [r for r in open_reqs if r.description.startswith(PARADOX_SOURCE_TAG)]
    assert len(paradox_reqs) == 1


async def test_llm_and_deterministic_findings_coexist_in_one_cycle(stack):
    events, proj, read, committer = stack
    await _seed_leak(events, proj)
    llm_out = ContinuityOutput(retcon_requests=[RetconDraft(description="two suns vs one", conflicting_entry_ids=["w1"], proposed_resolution="pick one")])
    agent = ContinuityChecker(FakeRunner(llm_out), read, committer)
    await agent.run_once()
    await proj.catch_up()
    open_reqs = await read.list_retcon_requests(status=RetconStatus.open)
    assert len(open_reqs) == 2
    assert any(r.description == "two suns vs one" for r in open_reqs)
    assert any(r.description.startswith(LEAK_SOURCE_TAG) for r in open_reqs)


async def test_poll_includes_knowledge_and_causal_data(stack):
    events, proj, read, committer = stack
    await _seed_leak(events, proj)
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), read, committer)
    ctx = await agent.poll()
    assert "the-heir-lives" in ctx["knowledge_matrix"]
    assert ctx["secret_references"][0].character_id == "mara"
    assert ctx["chapter_order"] == ["c1"]
    assert ctx["causal_edges"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_continuity_checker.py -v`
Expected: FAIL — `KeyError: 'knowledge_matrix'` (poll doesn't return the new keys yet) and no `LEAK_SOURCE_TAG`/`PARADOX_SOURCE_TAG`-prefixed requests are ever filed.

- [ ] **Step 3: Implement**

Replace `novelizer/agents/continuity_checker.py` in full:

```python
from __future__ import annotations
from novelizer.agents.base import BaseAgent, Runner
from novelizer.agents.schemas import ContinuityOutput
from novelizer.brain.leaks import find_leaks, leak_description
from novelizer.brain.paradoxes import find_paradoxes, paradox_description
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.store.models import RetconRequest, RetconStatus

SYSTEM_PROMPT = """You are the Continuity Checker for a living fictional world. Review the given world
entries, characters, and chapter excerpts for contradictions, anachronisms, or logical inconsistencies.
Return retcon_requests, each with a description (what contradicts what), conflicting_entry_ids (the ids
of the conflicting records), and a proposed_resolution. Return an empty list if you find nothing."""


class ContinuityChecker(BaseAgent):
    def __init__(
        self,
        runner: Runner,
        read_store: ReadStore,
        committer: Committer,
        interval: int = 900,
        personality: str = "",
    ) -> None:
        super().__init__(runner, read_store, committer, interval, name="continuity_checker", personality=personality)

    async def readiness(self) -> float:
        open_retcons = len(await self._read.list_retcon_requests(status=RetconStatus.open))
        return max(0.1, 1.0 - open_retcons / 5)

    async def poll(self) -> dict:
        chapters = await self._read.list_chapters()
        return {
            "world": await self._read.list_world_entries(),
            "characters": await self._read.list_characters(),
            "chapters": chapters[-10:],
            "chapter_order": [c.id for c in chapters],
            "secret_references": await self._read.list_secret_references(),
            "knowledge_matrix": await self._read.knowledge_matrix(),
            "causal_edges": await self._read.list_causal_edges(),
        }

    async def work(self, ctx: dict) -> ContinuityOutput | None:
        world = "\n".join(f"[{e.id[:8]}] {e.title}: {e.body[:200]}" for e in ctx["world"][:20]) or "None."
        chars = "\n".join(f"[{c.id[:8]}] {c.name}: {c.traits}" for c in ctx["characters"][:10]) or "None."
        chapters = "\n".join(f"[{c.id[:8]}] {c.title}: {c.prose[:300]}" for c in ctx["chapters"]) or "None."
        cast = f"\n\nIn character: {self.personality}" if self.personality else ""
        msg = f"World entries:\n{world}\n\nCharacters:\n{chars}\n\nRecent chapters:\n{chapters}{cast}"
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": msg}]})
        return result.get("structured_response")

    async def commit(self, out: ContinuityOutput | None, ctx: dict) -> None:
        open_reqs = await self._read.list_retcon_requests(status=RetconStatus.open)
        seen_descriptions = {r.description for r in open_reqs}

        if out is not None:
            for r in out.retcon_requests:
                req = RetconRequest(description=r.description, conflicting_entry_ids=r.conflicting_entry_ids,
                                    proposed_resolution=r.proposed_resolution)
                await self._committer.commit(self.name, EventType.RETCON_REQUEST_CREATED, req.id, req)
            await self._remark(out.feed_note)

        for leak in find_leaks(ctx["secret_references"], ctx["knowledge_matrix"]):
            description = leak_description(leak)
            if description in seen_descriptions:
                continue
            seen_descriptions.add(description)
            req = RetconRequest(
                description=description,
                conflicting_entry_ids=[leak.secret_id, leak.character_id, leak.chapter_id],
                proposed_resolution="Review whether the reference should be removed or a learn/reveal event added.",
            )
            await self._committer.commit(self.name, EventType.RETCON_REQUEST_CREATED, req.id, req)

        for paradox in find_paradoxes(ctx["causal_edges"], ctx["chapter_order"]):
            description = paradox_description(paradox)
            if description in seen_descriptions:
                continue
            seen_descriptions.add(description)
            req = RetconRequest(
                description=description,
                conflicting_entry_ids=[paradox.cause_chapter_id, paradox.effect_chapter_id],
                proposed_resolution="Review the causal edge for an ordering or cycle correction.",
            )
            await self._committer.commit(self.name, EventType.RETCON_REQUEST_CREATED, req.id, req)

    async def run_once(self) -> None:
        ctx = await self.poll()
        out = await self.work(ctx)
        await self.commit(out, ctx)


def build_continuity_checker_runner(settings):
    from deepagents import create_deep_agent
    from novelizer.agents.llm import build_chat_model
    model = build_chat_model(settings.agent_model, settings.llm_base_url, settings.llm_api_key, settings.agent_temperature)
    return create_deep_agent(model=model, system_prompt=SYSTEM_PROMPT, response_format=ContinuityOutput)
```

(`readiness`, `work`, `run_once`, `build_continuity_checker_runner`, and the LLM-sourced half of `commit` are byte-identical to before; `poll` gains four dict keys and `commit` gains the two dedup-guarded detector loops.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_continuity_checker.py -v`
Expected: PASS (all prior + 6 new). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/continuity_checker.py tests/agents/test_continuity_checker.py
git commit -m "feat: ContinuityChecker runs LeakDetector/ParadoxDetector every cycle, dedups by description"
```

---

### Task 6: M4.2 done-when — mechanical chain test

**Files:**
- Test: `tests/agents/test_continuity_checker.py` (append)

**Interfaces:**
- Consumes: everything from Task 5 — this task adds no new production interfaces, only the explicit done-when assertion chain from the M4 spec's M4.2 row.

- [ ] **Step 1: Write the failing test**

Append to `tests/agents/test_continuity_checker.py`:

```python
async def test_m4_2_done_when_leak_fixture_reaches_the_open_retcon_queue(stack):
    """M4.2 done-when (mechanical half): seed a secret.referenced event with
    no covering learn/reveal, run ContinuityChecker.run_once() with a
    FakeRunner that finds nothing on its own, and confirm a
    retcon_request.created event lands via the Committer with a
    LEAK_SOURCE_TAG-prefixed description, visible in
    list_retcon_requests(status=open)."""
    events, proj, read, committer = stack
    await _seed_leak(events, proj)
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), read, committer)

    await agent.run_once()
    await proj.catch_up()

    open_reqs = await read.list_retcon_requests(status=RetconStatus.open)
    leak_reqs = [r for r in open_reqs if r.description.startswith(LEAK_SOURCE_TAG)]
    assert len(leak_reqs) == 1
    assert leak_reqs[0].status == RetconStatus.open

    log = await events.events_since(0)
    created = [e for e in log if e.event_type == EventType.RETCON_REQUEST_CREATED
               and e.payload["description"].startswith(LEAK_SOURCE_TAG)]
    assert len(created) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/agents/test_continuity_checker.py -k m4_2_done_when -v`
Expected: this should already **pass** given Task 5's implementation — Task 5's `test_leak_is_filed_as_a_tagged_retcon_request` exercises the same path. Run it and confirm PASS; this test exists as the explicit, spec-traceable done-when assertion (naming both the queue-visibility check and the raw event-log check in one place) rather than as new red/green production work. If it fails, that means Task 5's implementation has a gap — stop and fix Task 5.

- [ ] **Step 3: N/A**

No production code changes.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_continuity_checker.py -v`
Expected: PASS (all prior + 1 new). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add tests/agents/test_continuity_checker.py
git commit -m "test: explicit M4.2 done-when mechanical chain assertion"
```

---

### Task 7: Docs — mark M4.2 complete

**Files:**
- Modify: `docs/submilestones/M4-knowledge-and-cause.md`

- [ ] **Step 1: Update the M4.2 status**

In the sub-milestones table (around line 23), change the M4.2 row's final `Status` cell from `not started` to `complete`.

- [ ] **Step 2: Commit**

```bash
git add docs/submilestones/M4-knowledge-and-cause.md
git commit -m "docs: mark M4.2 complete"
```

---

### Task 8: Full-suite verification

**Files:** none (verification only).

- [ ] **Step 1: Run the full test suite from a clean state**

Run: `uv run pytest tests/ -v`
Expected: every test passes, including all tests from Tasks 1–7 and every pre-existing test (M0–M4.1). If anything is red, do not proceed — diagnose per `superpowers:systematic-debugging` and fix before considering M4.2 done. If a failure looks unrelated to this branch's changes, confirm it fails identically on `git stash` before calling it pre-existing; then `git stash pop` and continue.

- [ ] **Step 2: Confirm no stray files**

Run: `git status`
Expected: working tree clean except for the commits made in Tasks 1–7 (no `.env` file, no untracked scratch files).

---

## Self-Review

**1. Spec coverage:**
- `LeakDetector` (pure function over `ReadStore` knowledge-matrix + `secret.referenced` rows) — Task 1, property-tested in Task 2.
- `ParadoxDetector` (pure function over the causal-graph adjacency list, ordering + cycle) — Task 3, property-tested in Task 4. Plain dicts/lists, DFS, no `networkx` — confirmed in Task 3's `_cycle_edges`.
- `ContinuityChecker` upgrade: `poll()`/`work()` run both detectors every cycle deterministically; `commit()` routes hits through the existing `RetconRequest`/`Committer` path, tagged with the pinned constants — Task 5.
- Pinned constants `LEAK_SOURCE_TAG`/`PARADOX_SOURCE_TAG`, exact strings — defined verbatim in Tasks 1 and 3.
- No autonomy-policy change — confirmed: no task modifies `novelizer/canon/policy.py`.
- Dedup so the same leak/paradox isn't re-filed every 900s cycle — Task 5, with the decision documented inline (string-membership on formatted, deterministic descriptions).
- M4.2 done-when mechanical chain (seed leak → detector flags → `run_once()` with `FakeRunner` → `retcon_request.created` with `LEAK_SOURCE_TAG` → visible in `list_retcon_requests(status=open)`) — Task 6, explicit.
- Docs completion — Task 7. Full-suite verification — Task 8.

**2. Placeholder scan:** No "TBD"/"handle edge cases"/elided code — every step shows complete file contents or exact appended code. Tasks 2, 4, and 6 are explicitly marked as formality red/green steps (their "new code" is test-only, exercising already-landed Task 1/3/5 production code) rather than glossing over what "Step 3: Implement" means when there's no new production code — each says so explicitly and explains why, rather than silently omitting the step.

**3. Type consistency:** `Leak`/`ParadoxCandidate` field names match between Tasks 1/3's definitions and Task 5's `continuity_checker.py` usage (`leak.secret_id`/`leak.character_id`/`leak.chapter_id`; `paradox.cause_chapter_id`/`paradox.effect_chapter_id`). `leak_description`/`paradox_description` signatures match their single call sites in Task 5. `find_leaks(references, matrix)` and `find_paradoxes(edges, chapter_order)` parameter order matches every call site across Tasks 1–2 (tests) and Task 5 (production).
