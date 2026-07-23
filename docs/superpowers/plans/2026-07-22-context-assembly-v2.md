# Context-Assembly Protocol v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate all silent `prose[:N]` truncation: extractors get watermarked full-prose coverage (revision-aware, event-sourced), advisory contexts get rolling LLM chapter summaries, and pull-mode chapter maps gain per-chapter gists.

**Architecture:** Pure assembly/watermark math in `novelizer/brain/` (no I/O); two new bookkeeping events (`chapter.processed`, `chapter.summarized`); one projected table (`chapter_summaries`); one new untooled agent (Summarizer) registered via `AGENT_REGISTRY`; cutovers at the five existing truncation sites. Spec: `.specs/context-assembly-v2.md`.

**Tech Stack:** Python 3.12, pydantic v2, aiosqlite, deepagents/LangChain (`ProviderStrategy` structured output), pytest + Hypothesis, `uv` for everything.

## Global Constraints

- Run all commands from the worktree root: `/home/ty/workspace/novelizer/.claude/worktrees/context-assembly-v2`. NEVER run tests in `/home/ty/workspace/novelizer` (the main checkout — DB-lock incident).
- Test command shape: `uv run pytest <path> -q -W error` (warnings are errors repo-wide).
- Event sourcing: the log is sole truth; only the Projector writes projections; watermarks are derived by folding events, never mutable counters.
- Never silently truncate: any elision must carry an explicit marker.
- Style: `from __future__ import annotations` first line; module logger `logger = logging.getLogger(__name__)`; comments state constraints, not narration.
- Commit after every green task: conventional-commit subject + `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` trailer.
- Load-flake note: TUI pilot tests can go red under parallel pytest load — do not run parallel pytest invocations; compare identical scopes if a failure looks unrelated (docs/TESTING-TUI.md).

---

### Task 1: Pure assembly math — `brain/context_assembly.py`

**Files:**
- Create: `novelizer/brain/context_assembly.py`
- Test: `tests/brain/test_context_assembly.py`

**Interfaces:**
- Consumes: `novelizer.text_chunk.chunk_prose(text, chunk_chars, overlap) -> list[str]` (exists).
- Produces (later tasks import these exactly):
  - `class TokenEstimator(Protocol): def estimate(self, text: str) -> int`
  - `@dataclass(frozen=True) class CharHeuristicEstimator: chars_per_token: float = 4.0`
  - `@dataclass(frozen=True) class Window: text: str; index: int; total: int`
  - `assemble_verbatim(text: str, budget_tokens: int, estimator: TokenEstimator | None = None) -> list[Window]`
  - `@dataclass(frozen=True) class AdvisoryEntry: label: str; summary: str | None = None; verbatim: str | None = None`
  - `assemble_advisory(entries: list[AdvisoryEntry], budget_tokens: int, estimator: TokenEstimator | None = None) -> str`
  - Module constants: `ELISION_MARKER = "[…truncated — summary pending]"`, `OMITTED_HEADER_FMT = "[{n} earlier chapters omitted]"`

- [ ] **Step 1: Write the failing tests**

```python
# tests/brain/test_context_assembly.py
"""Property proof for the context-assembly invariants (spec .specs/context-assembly-v2.md):
window coverage/overlap/termination, and advisory never-silent."""
from __future__ import annotations
from hypothesis import given, settings, strategies as st
from novelizer.brain.context_assembly import (
    AdvisoryEntry, CharHeuristicEstimator, ELISION_MARKER, OMITTED_HEADER_FMT,
    Window, assemble_advisory, assemble_verbatim,
)


def test_estimator_ceil():
    est = CharHeuristicEstimator()
    assert est.estimate("") == 0
    assert est.estimate("abcd") == 1
    assert est.estimate("abcde") == 2


def test_fits_budget_single_window():
    ws = assemble_verbatim("hello world", budget_tokens=100)
    assert ws == [Window(text="hello world", index=0, total=1)]


def test_empty_prose_single_empty_window():
    assert assemble_verbatim("", budget_tokens=10) == [Window(text="", index=0, total=1)]


@given(st.text(min_size=0, max_size=4000), st.integers(min_value=1, max_value=200))
@settings(max_examples=100, deadline=None)
def test_windows_cover_input_in_order_no_gaps(text: str, budget: int):
    ws = assemble_verbatim(text, budget_tokens=budget)
    assert len(ws) >= 1
    assert [w.index for w in ws] == list(range(len(ws)))
    assert all(w.total == len(ws) for w in ws)
    # Each window is a substring of the input at a monotonically advancing
    # start; consecutive windows leave no gap; the last window reaches the end.
    # (find() with a moving lower bound is deliberately implementation-blind.)
    search_from = 0
    covered_to = 0
    for w in ws:
        start = text.find(w.text, search_from)
        assert start != -1, "window is not a substring at/after the previous start"
        assert start <= covered_to, "gap between consecutive windows"
        covered_to = max(covered_to, start + len(w.text))
        search_from = start + 1 if len(ws) > 1 else search_from
    assert covered_to == len(text)


def test_advisory_prefers_summary():
    out = assemble_advisory(
        [AdvisoryEntry(label="Ch One", summary="Ana finds the key.")], budget_tokens=100,
    )
    assert "Ana finds the key." in out and ELISION_MARKER not in out


def test_advisory_fallback_is_labeled():
    out = assemble_advisory(
        [AdvisoryEntry(label="Ch One", verbatim="x" * 4000)], budget_tokens=50,
    )
    assert ELISION_MARKER in out and "Ch One" in out
    assert len(out) < 4000


def test_advisory_omission_is_announced():
    entries = [AdvisoryEntry(label=f"Ch {i}", summary="s" * 200) for i in range(10)]
    out = assemble_advisory(entries, budget_tokens=60)
    kept = sum(1 for i in range(10) if f"Ch {i}:" in out)
    assert 0 < kept < 10
    assert OMITTED_HEADER_FMT.format(n=10 - kept) in out
    assert "Ch 9:" in out  # newest survives; oldest dropped first


@given(
    st.lists(
        st.tuples(st.booleans(), st.text(min_size=1, max_size=300)), min_size=1, max_size=12
    ),
    st.integers(min_value=10, max_value=500),
)
@settings(max_examples=100, deadline=None)
def test_advisory_never_silent(items, budget):
    entries = [
        AdvisoryEntry(label=f"Ch {i}", summary=(txt if has_summary else None),
                      verbatim=(None if has_summary else txt))
        for i, (has_summary, txt) in enumerate(items)
    ]
    out = assemble_advisory(entries, budget_tokens=budget)
    # Every entry is either present by label, or covered by the omitted header.
    present = sum(1 for i in range(len(entries)) if f"Ch {i}" in out)
    if present < len(entries):
        assert "omitted]" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/brain/test_context_assembly.py -q -W error`
Expected: FAIL — `ModuleNotFoundError: No module named 'novelizer.brain.context_assembly'`

- [ ] **Step 3: Write the implementation**

```python
# novelizer/brain/context_assembly.py
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Protocol
from novelizer.text_chunk import chunk_prose

ELISION_MARKER = "[…truncated — summary pending]"
OMITTED_HEADER_FMT = "[{n} earlier chapters omitted]"


class TokenEstimator(Protocol):
    def estimate(self, text: str) -> int: ...


@dataclass(frozen=True)
class CharHeuristicEstimator:
    """chars/4 heuristic: no tokenizer exists in this repo (llm endpoint is a
    local OpenAI-compatible server with no stable tokenizer API); the protocol
    is the seam for injecting a real one later."""
    chars_per_token: float = 4.0

    def estimate(self, text: str) -> int:
        return math.ceil(len(text) / self.chars_per_token)


_DEFAULT_ESTIMATOR = CharHeuristicEstimator()


@dataclass(frozen=True)
class Window:
    text: str
    index: int
    total: int


def assemble_verbatim(
    text: str, budget_tokens: int, estimator: TokenEstimator | None = None
) -> list[Window]:
    """Full text as one window when it fits the budget; otherwise ordered
    overlapping windows covering the whole text — never a silent head-slice."""
    est = estimator or _DEFAULT_ESTIMATOR
    total = est.estimate(text)
    if not text or total <= budget_tokens:
        return [Window(text=text, index=0, total=1)]
    # Derive chars-per-token empirically so any estimator works here.
    ratio = len(text) / total
    window_chars = max(1, int(budget_tokens * ratio))
    overlap_chars = min(max(0, int((budget_tokens // 4) * ratio)), window_chars - 1)
    parts = chunk_prose(text, window_chars, overlap_chars)
    return [Window(text=p, index=i, total=len(parts)) for i, p in enumerate(parts)]


@dataclass(frozen=True)
class AdvisoryEntry:
    label: str
    summary: str | None = None
    verbatim: str | None = None


def assemble_advisory(
    entries: list[AdvisoryEntry], budget_tokens: int, estimator: TokenEstimator | None = None
) -> str:
    """Story-so-far block packed newest-first within budget. Prefers summaries;
    a chapter without one (Summarizer lag) falls back to a labeled verbatim
    head ending in ELISION_MARKER. Entries that don't fit are dropped oldest
    first and announced via OMITTED_HEADER_FMT — degraded is fine, silent is not."""
    est = estimator or _DEFAULT_ESTIMATOR
    kept: list[str] = []
    remaining = budget_tokens
    omitted = 0
    for entry in reversed(entries):  # newest first
        if entry.summary is not None:
            line = f"- {entry.label}: {entry.summary}"
        elif entry.verbatim is not None:
            head_chars = max(0, int(remaining * len(entry.verbatim) /
                                    max(1, est.estimate(entry.verbatim))))
            head = entry.verbatim[:head_chars]
            line = f"- {entry.label}: {head}"
            if head_chars < len(entry.verbatim):
                line += f" {ELISION_MARKER}"
        else:
            line = f"- {entry.label}: (no content)"
        cost = est.estimate(line)
        if kept and cost > remaining:
            omitted = len(entries) - len(kept)
            break
        kept.append(line)
        remaining -= cost
        if remaining <= 0 and len(kept) < len(entries):
            omitted = len(entries) - len(kept)
            break
    kept.reverse()  # chronological for the prompt
    if omitted:
        kept.insert(0, OMITTED_HEADER_FMT.format(n=omitted))
    return "\n".join(kept)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/brain/test_context_assembly.py -q -W error`
Expected: PASS. If the coverage property fails on `chunk_prose` edge behavior, fix window math (never weaken the test's coverage reconstruction).

- [ ] **Step 5: Commit**

```bash
git add novelizer/brain/context_assembly.py tests/brain/test_context_assembly.py
git commit -m "feat(brain): context_assembly — verbatim windows + advisory packing (v2 D1)"
```

---

### Task 2: Revision-aware done-sets — `brain/watermarks.py`

**Files:**
- Create: `novelizer/brain/watermarks.py`
- Test: `tests/brain/test_watermarks.py`

**Interfaces:**
- Consumes: `novelizer.canon.events.StoredEvent` (has `.sequence: int`, `.payload: dict`).
- Produces: `current_done_ids(done_events: list[StoredEvent], revised_events: list[StoredEvent]) -> set[str]` — both lists carry payloads with a `"chapter_id"` key.

- [ ] **Step 1: Write the failing tests**

```python
# tests/brain/test_watermarks.py
"""Property proof for the revision-aware done-set (spec invariant 2): a chapter
is done iff its latest done-event outranks its latest revised-event."""
from __future__ import annotations
from hypothesis import given, settings, strategies as st
from novelizer.brain.watermarks import current_done_ids
from novelizer.canon.events import StoredEvent


def _ev(seq: int, event_type: str, chapter_id: str) -> StoredEvent:
    return StoredEvent(
        sequence=seq, id=f"e{seq}", event_type=event_type, aggregate_id=chapter_id,
        agent_name="t", payload={"chapter_id": chapter_id}, created_at="2026-07-22T00:00:00",
    )


def test_done_then_revised_is_not_done():
    done = [_ev(1, "chapter.processed", "c1")]
    revised = [_ev(2, "chapter.revised", "c1")]
    assert current_done_ids(done, revised) == set()


def test_revised_then_done_is_done():
    done = [_ev(3, "chapter.processed", "c1")]
    revised = [_ev(2, "chapter.revised", "c1")]
    assert current_done_ids(done, revised) == {"c1"}


def test_empty():
    assert current_done_ids([], []) == set()


@given(st.lists(st.tuples(st.sampled_from(["done", "revised"]),
                          st.sampled_from(["a", "b", "c"])), max_size=30))
@settings(max_examples=200, deadline=None)
def test_matches_naive_fold(script):
    done, revised = [], []
    for seq, (kind, cid) in enumerate(script, start=1):
        (done if kind == "done" else revised).append(
            _ev(seq, "chapter.processed" if kind == "done" else "chapter.revised", cid)
        )
    expected: set[str] = set()
    for kind, cid in script:
        expected.add(cid) if kind == "done" else expected.discard(cid)
    assert current_done_ids(done, revised) == expected
```

NOTE: check `StoredEvent`'s actual required fields in `novelizer/canon/events.py` (`sequence`, `id`, `event_type`, then whatever else is required — e.g. `aggregate_id`, `agent_name`, `payload`, `created_at`) and adjust `_ev` so it validates. Do not change `StoredEvent`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/brain/test_watermarks.py -q -W error`
Expected: FAIL — `ModuleNotFoundError: No module named 'novelizer.brain.watermarks'`

- [ ] **Step 3: Write the implementation**

```python
# novelizer/brain/watermarks.py
from __future__ import annotations
from novelizer.canon.events import StoredEvent


def current_done_ids(
    done_events: list[StoredEvent], revised_events: list[StoredEvent]
) -> set[str]:
    """Fold done/revised markers in global sequence order: a done-event adds
    its chapter, a later chapter.revised removes it — so revised chapters are
    automatically re-processed with no mutable state (generalizes
    brain/mining.already_mined_chapter_ids, which is revision-blind).
    Pure function: callers fetch the two lists via EventStore.events_since."""
    timeline = [(e.sequence, True, e.payload["chapter_id"]) for e in done_events]
    timeline += [(e.sequence, False, e.payload["chapter_id"]) for e in revised_events]
    done: set[str] = set()
    for _, is_done, chapter_id in sorted(timeline):
        if is_done:
            done.add(chapter_id)
        else:
            done.discard(chapter_id)
    return done
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/brain/test_watermarks.py -q -W error`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/brain/watermarks.py tests/brain/test_watermarks.py
git commit -m "feat(brain): revision-aware done-set fold for per-agent watermarks (v2 D2)"
```

---

### Task 3: Events + gating — `chapter.processed`, `chapter.summarized`

**Files:**
- Modify: `novelizer/canon/events.py` (EventType constants near `CHAPTER_MINED`; payload classes near `ChapterMined`)
- Modify: `novelizer/canon/policy.py` (`_NEVER_GATED` set)
- Test: `tests/canon/test_policy.py` (extend existing), `tests/canon/test_events.py` (extend existing)

**Interfaces:**
- Produces: `EventType.CHAPTER_PROCESSED = "chapter.processed"`, `EventType.CHAPTER_SUMMARIZED = "chapter.summarized"`, `class ChapterProcessed(BaseModel): agent: str; chapter_id: str`, `class ChapterSummarized(BaseModel): chapter_id: str; gist: str; summary: str`.

- [ ] **Step 1: Write the failing tests** — append to the existing test modules (read them first; follow their local style):

```python
# append to tests/canon/test_policy.py
import pytest
from novelizer.canon.events import EventType
from novelizer.canon.policy import _NEVER_GATED, _FICTION_REGISTRY, FICTION_TIER_ORDER
from substrate import is_gated


@pytest.mark.parametrize("et", [EventType.CHAPTER_PROCESSED, EventType.CHAPTER_SUMMARIZED])
def test_assembly_bookkeeping_events_never_gated(et):
    assert et in _NEVER_GATED
    # Never gated even at the most restrictive tier (gated_all == index 3).
    assert is_gated(et, _FICTION_REGISTRY, FICTION_TIER_ORDER, 3) is False
```

```python
# append to tests/canon/test_events.py
from novelizer.canon.events import ChapterProcessed, ChapterSummarized, EventType


def test_chapter_processed_payload_roundtrip():
    p = ChapterProcessed(agent="character_keeper", chapter_id="c1")
    assert ChapterProcessed.model_validate_json(p.model_dump_json()) == p
    assert EventType.CHAPTER_PROCESSED == "chapter.processed"


def test_chapter_summarized_payload_roundtrip():
    p = ChapterSummarized(chapter_id="c1", gist="Ana finds the key.", summary="Longer para.")
    assert ChapterSummarized.model_validate_json(p.model_dump_json()) == p
    assert EventType.CHAPTER_SUMMARIZED == "chapter.summarized"
```

If `test_policy.py` imports differ (e.g. no `is_gated` import exists), match how that file already exercises gating; the assertion that matters is: both events in `_NEVER_GATED` and not gated at the `gated_all` tier.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/canon/test_policy.py tests/canon/test_events.py -q -W error`
Expected: FAIL — `AttributeError: ... CHAPTER_PROCESSED`

- [ ] **Step 3: Implement**

In `novelizer/canon/events.py`, after `CHAPTER_MINED = "chapter.mined"` add:

```python
    CHAPTER_PROCESSED = "chapter.processed"
    CHAPTER_SUMMARIZED = "chapter.summarized"
```

After the `ChapterMined` payload class add:

```python
class ChapterProcessed(BaseModel):
    """Payload for chapter.processed -- per-agent bookkeeping marker that
    `agent` has seen this chapter's full prose. Never projected; done-sets are
    a pure log fold (brain/watermarks.current_done_ids), and a later
    chapter.revised clears the marker so revised chapters re-process."""

    agent: str
    chapter_id: str


class ChapterSummarized(BaseModel):
    """Payload for chapter.summarized -- the Summarizer's rolling summary of
    one chapter revision. Projected into chapter_summaries (upsert by
    chapter_id: the latest summary wins on replay). gist is one line for the
    pull-mode chapter map; summary is one paragraph for advisory contexts."""

    chapter_id: str
    gist: str
    summary: str
```

In `novelizer/canon/policy.py`, inside `_NEVER_GATED` after `EventType.CHAPTER_MINED,` add:

```python
    EventType.CHAPTER_PROCESSED,
    EventType.CHAPTER_SUMMARIZED,
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/canon/test_policy.py tests/canon/test_events.py -q -W error`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/events.py novelizer/canon/policy.py tests/canon/test_policy.py tests/canon/test_events.py
git commit -m "feat(canon): chapter.processed + chapter.summarized bookkeeping events, never gated (v2 D3)"
```

---

### Task 4: `chapter_summaries` projection + reads

**Files:**
- Modify: `novelizer/store/models.py` (new `ChapterSummary` model, near `StructureScore` ~line 281)
- Modify: `novelizer/canon/projector.py` (`_CREATE` table, `_reset_state_locked` tuple, `_project` branch — copy the `ANNOTATION_STRUCTURE_SCORED` upsert at ~503)
- Modify: `novelizer/canon/read_store.py` (queries, near `list_structure_scores` ~184)
- Test: `tests/canon/test_chapter_summaries_projection_property.py` (new)

**Interfaces:**
- Consumes: Task 3's `EventType.CHAPTER_SUMMARIZED`, `ChapterSummarized`.
- Produces: `class ChapterSummary(BaseModel): chapter_id: str; gist: str = ""; summary: str = ""`; `ReadStore.list_chapter_summaries() -> list[ChapterSummary]`; `ReadStore.get_chapter_summary(chapter_id) -> ChapterSummary | None`.

- [ ] **Step 1: Write the failing test** (model on `tests/canon/test_*_projection_property.py` — real stores on a temp sqlite file, replay-from-0 equality):

```python
# tests/canon/test_chapter_summaries_projection_property.py
"""Property proof: chapter_summaries is an upsert-by-chapter_id projection of
chapter.summarized, and a from-zero projector rebuild reproduces it exactly."""
from __future__ import annotations
import asyncio
import os
import tempfile
from hypothesis import given, settings, strategies as st
from novelizer.canon.event_store import EventStore
from novelizer.canon.events import ChapterSummarized, EventType
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore


async def _run(script: list[tuple[str, str]]) -> None:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        events = EventStore(path)
        await events.init()
        proj = Projector(events, path)
        await proj.init()
        read = ReadStore(path)
        await read.init()
        for chapter_id, gist in script:
            await events.append(
                EventType.CHAPTER_SUMMARIZED, chapter_id,
                ChapterSummarized(chapter_id=chapter_id, gist=gist, summary=f"para: {gist}"),
            )
        await proj.catch_up()
        expected = {}
        for chapter_id, gist in script:
            expected[chapter_id] = gist  # last event per chapter wins
        rows = await read.list_chapter_summaries()
        assert {r.chapter_id: r.gist for r in rows} == expected
        if script:
            last_id = script[-1][0]
            got = await read.get_chapter_summary(last_id)
            assert got is not None and got.chapter_id == last_id
        assert await read.get_chapter_summary("missing") is None

        proj2 = Projector(events, path)
        await proj2.init()
        await proj2._reset_state()
        await proj2.catch_up()
        rebuilt = await read.list_chapter_summaries()
        assert {r.chapter_id: r.gist for r in rebuilt} == expected
        await proj2.close()
        await read.close()
        await proj.close()
        await events.close()
    finally:
        os.unlink(path)


@given(st.lists(st.tuples(st.sampled_from(["c1", "c2", "c3"]),
                          st.text(min_size=1, max_size=20)), max_size=12))
@settings(max_examples=25, deadline=None)
def test_upsert_and_replay_stable(script):
    asyncio.run(_run(script))
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/canon/test_chapter_summaries_projection_property.py -q -W error`
Expected: FAIL — `ImportError` (no `ChapterSummary`) then, after model exists, missing table/branch/method failures.

- [ ] **Step 3: Implement**

`novelizer/store/models.py` — after `StructureScore`:

```python
class ChapterSummary(BaseModel):
    """Read-side row for one chapter's rolling summary, built by the Projector
    from chapter.summarized events. gist feeds the pull-mode chapter map;
    summary feeds advisory (push-mode) contexts. Upsert by chapter_id: a
    re-summarize after chapter.revised replaces the row."""

    chapter_id: str
    gist: str = ""
    summary: str = ""
```

`novelizer/canon/projector.py`:
- `_CREATE`: add

```sql
CREATE TABLE IF NOT EXISTS chapter_summaries (
    id TEXT PRIMARY KEY, data TEXT NOT NULL
);
```

- `_reset_state_locked`: add `"chapter_summaries",` to the table tuple.
- `_project` (next to the `ANNOTATION_STRUCTURE_SCORED` branch):

```python
        elif t == EventType.CHAPTER_SUMMARIZED:
            await self._conn.execute(
                "INSERT OR REPLACE INTO chapter_summaries (id, data) VALUES (?,?)",
                (p["chapter_id"], data),
            )
```

(Match the surrounding branch style exactly — `p` is the payload dict and `data` the serialized payload; confirm variable names in the actual `_apply`/`_project` body.)

`novelizer/canon/read_store.py` — import `ChapterSummary` in the existing `store.models` import list, then next to `list_structure_scores`:

```python
    async def list_chapter_summaries(self) -> list[ChapterSummary]:
        cur = await self._conn.execute("SELECT data FROM chapter_summaries ORDER BY rowid")
        return [ChapterSummary.model_validate_json(r[0]) for r in await cur.fetchall()]

    async def get_chapter_summary(self, chapter_id: str) -> Optional[ChapterSummary]:
        cur = await self._conn.execute("SELECT data FROM chapter_summaries WHERE id=?", (chapter_id,))
        row = await cur.fetchone()
        return ChapterSummary.model_validate_json(row[0]) if row else None
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/canon/test_chapter_summaries_projection_property.py tests/canon -q -W error`
Expected: PASS (whole canon suite stays green).

- [ ] **Step 5: Commit**

```bash
git add novelizer/store/models.py novelizer/canon/projector.py novelizer/canon/read_store.py tests/canon/test_chapter_summaries_projection_property.py
git commit -m "feat(canon): chapter_summaries projection + read queries (v2 D4)"
```

---

### Task 5: Settings — budgets + summarizer interval

**Files:**
- Modify: `novelizer/settings/models.py`, `novelizer/settings/layers.py`, `novelizer/settings/loader.py`
- Test: `tests/settings/test_models.py` (extend)

**Interfaces:**
- Produces on `EffectiveSettings`: `extractor_token_budget: int = 24000`, `advisory_token_budget: int = 2000`, `summarizer_interval: int = 300`; all three in `STORY_OVERRIDABLE_KEYS`.

- [ ] **Step 1: Failing test** (append; mirror the file's existing assertions style):

```python
# append to tests/settings/test_models.py
from novelizer.settings.models import EffectiveSettings, STORY_OVERRIDABLE_KEYS


def test_context_assembly_settings_defaults():
    s = EffectiveSettings()
    assert s.extractor_token_budget == 24000
    assert s.advisory_token_budget == 2000
    assert s.summarizer_interval == 300


def test_context_assembly_settings_story_overridable():
    assert {"extractor_token_budget", "advisory_token_budget",
            "summarizer_interval"} <= STORY_OVERRIDABLE_KEYS
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/settings/test_models.py -q -W error`
Expected: FAIL — `AttributeError: extractor_token_budget`

- [ ] **Step 3: Implement**

`models.py` — in `EffectiveSettings`, after `keeper_prose_chars` block:

```python
    # Context-assembly protocol v2 (.specs/context-assembly-v2.md).
    # Per-run verbatim budget for extractor sweeps (Keeper mining, Summarizer
    # input): ~96k chars at the chars/4 heuristic — fits 128k-context local
    # models with headroom for instructions and output.
    extractor_token_budget: int = 24000
    # Packed story-so-far budget for push-mode advisory blocks.
    advisory_token_budget: int = 2000
```

Mark the two superseded fields (comment only — fields stay so existing story.toml/global.toml layers keep loading):

```python
    # DEPRECATED (context-assembly v2): no code path reads this any more.
    prior_chapter_summary_chars: int = 200
    # DEPRECATED (context-assembly v2): no code path reads this any more.
    keeper_prose_chars: int = 6000
```

In the cadence block: `summarizer_interval: int = 300`.
`STORY_OVERRIDABLE_KEYS`: add `"extractor_token_budget", "advisory_token_budget", "summarizer_interval",`.
`layers.py` (`GlobalConfig` AND `StoryConfig`) and `loader.py` (`EnvOverrides`): add the three fields as `int | None = None` next to their existing siblings.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/settings -q -W error`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/settings/models.py novelizer/settings/layers.py novelizer/settings/loader.py tests/settings/test_models.py
git commit -m "feat(settings): extractor/advisory token budgets + summarizer_interval (v2 D9)"
```

---

### Task 6: Summarizer agent + registration

**Files:**
- Modify: `novelizer/agents/schemas.py` (add `SummarizerOutput`)
- Create: `novelizer/agents/summarizer.py`
- Modify: `novelizer/agents/registry.py` (import + registry slot after `structure_analyst.SPEC`, before `triage.SPEC`)
- Modify: `novelizer/runtime.py` (named attr + `interval_map` entry `"summarizer_interval": [self.summarizer]`)
- Modify: `novelizer/tui/identity.py` (add identity)
- Test: `tests/agents/test_summarizer.py` (new)

**Interfaces:**
- Consumes: Task 1 `assemble_verbatim`/`Window`; Task 2 `current_done_ids`; Task 3 events; Task 4 reads; Task 5 settings.
- Produces: `class SummarizerOutput(BaseModel): gist: str = ""; summary: str = ""; feed_note: str = ""`; `class Summarizer(BaseAgent)` with `name="summarizer"`, constructor `(runner, read_store, committer, event_store, interval=300, personality="", extractor_token_budget=24000)`; `build_summarizer_runner(settings, callbacks=None)`; `SPEC`.

- [ ] **Step 1: Failing tests**

```python
# tests/agents/test_summarizer.py
"""Summarizer: event-sourced rolling chapter summaries — exactly-once per
revision, projection upsert, revision re-summarize."""
from __future__ import annotations
import os
import tempfile
import pytest
from novelizer.agents.schemas import SummarizerOutput
from novelizer.agents.summarizer import Summarizer
from novelizer.canon.committer import Committer
from novelizer.canon.event_store import EventStore
from novelizer.canon.events import ChapterRevised, EventType
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.store.models import Chapter


class CountingRunner:
    def __init__(self):
        self.calls = 0

    async def ainvoke(self, inputs):
        self.calls += 1
        return {"structured_response": SummarizerOutput(gist=f"gist {self.calls}",
                                                        summary=f"summary {self.calls}")}


async def _stores(path):
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    return events, proj, read


@pytest.mark.asyncio
async def test_summarizes_each_chapter_once_then_idles():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        events, proj, read = await _stores(path)
        for i in (1, 2):
            await events.append(EventType.CHAPTER_CREATED, f"c{i}",
                                Chapter(id=f"c{i}", title=f"Ch {i}", prose="word " * 50))
        await proj.catch_up()
        runner = CountingRunner()
        agent = Summarizer(runner, read, Committer(events), events)
        await agent.run_once()
        await proj.catch_up()
        rows = await read.list_chapter_summaries()
        assert {r.chapter_id for r in rows} == {"c1", "c2"}
        calls_after_first = runner.calls
        await agent.run_once()  # nothing new: no further LLM calls, no new events
        assert runner.calls == calls_after_first
        log = await events.events_since(0, event_types=[EventType.CHAPTER_SUMMARIZED])
        assert len(log) == 2
        await read.close(); await proj.close(); await events.close()
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_revision_triggers_resummarize():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        events, proj, read = await _stores(path)
        await events.append(EventType.CHAPTER_CREATED, "c1",
                            Chapter(id="c1", title="Ch 1", prose="old prose"))
        await proj.catch_up()
        agent = Summarizer(CountingRunner(), read, Committer(events), events)
        await agent.run_once()
        await events.append(EventType.CHAPTER_REVISED, "c1",
                            ChapterRevised(chapter_id="c1", prose="new prose"))
        await proj.catch_up()
        await agent.run_once()
        log = await events.events_since(0, event_types=[EventType.CHAPTER_SUMMARIZED])
        assert len(log) == 2  # summarized once per revision
        await proj.catch_up()
        row = await read.get_chapter_summary("c1")
        assert row.gist == "gist 2"
        await read.close(); await proj.close(); await events.close()
    finally:
        os.unlink(path)
```

Check how existing async agent tests declare async (`pytest.mark.asyncio` vs `asyncio.run` wrappers — `tests/agents/test_character_keeper_property.py` uses `asyncio.run`); match the prevailing convention if `pytest-asyncio` isn't configured.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agents/test_summarizer.py -q -W error`
Expected: FAIL — no `SummarizerOutput` / no module `summarizer`.

- [ ] **Step 3: Implement**

`schemas.py` (near `KeeperOutput`):

```python
class SummarizerOutput(BaseModel):
    """One chapter's rolling summary. gist: a single line (<=140 chars) for
    the chapter map; summary: one paragraph for advisory contexts."""

    gist: str = ""
    summary: str = ""
    feed_note: str = ""
```

`novelizer/agents/summarizer.py`:

```python
from __future__ import annotations
import logging
from novelizer.agents.base import BaseAgent, Runner
from novelizer.agents.schemas import SummarizerOutput
from novelizer.brain.context_assembly import assemble_verbatim
from novelizer.brain.watermarks import current_done_ids
from novelizer.canon.committer import Committer
from novelizer.canon.event_store import EventStore
from novelizer.canon.events import ChapterSummarized, EventType
from novelizer.canon.read_store import ReadStore

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Summarizer for a living fictional world. You produce the rolling
story-so-far record other agents rely on. For the chapter you are shown, return:
- gist: ONE line (at most ~140 characters) naming what happens — the events, not the vibes.
- summary: one paragraph covering the chapter's events, character developments, reveals and
  new locations, in order, so an agent who never reads the prose still knows what is canon.
Work strictly from the prose shown. Never invent, never editorialize, never omit a reveal."""

MERGE_PROMPT = (
    "The chapter was too long for one pass; below are summaries of its consecutive,\n"
    "overlapping parts, in order. Merge them into ONE gist and ONE paragraph summary for\n"
    "the whole chapter, deduplicating the overlap."
)


class Summarizer(BaseAgent):
    def __init__(
        self,
        runner: Runner,
        read_store: ReadStore,
        committer: Committer,
        event_store: EventStore,
        interval: int = 300,
        personality: str = "",
        extractor_token_budget: int = 24000,
    ) -> None:
        super().__init__(runner, read_store, committer, interval, name="summarizer", personality=personality)
        self._events = event_store
        self._budget = extractor_token_budget

    async def _unsummarized(self) -> list:
        chapters = await self._read.list_chapters()
        done = current_done_ids(
            await self._events.events_since(0, event_types=[EventType.CHAPTER_SUMMARIZED]),
            await self._events.events_since(0, event_types=[EventType.CHAPTER_REVISED]),
        )
        return [c for c in chapters if c.id not in done]

    async def readiness(self) -> float:
        pending = await self._unsummarized()
        if not pending:
            return 0.0
        return await self._gate_on_watermark(0.6)

    async def _fingerprint(self) -> tuple:
        chapters = await self._read.list_chapters()
        pending = await self._unsummarized()
        return (len(chapters), chapters[-1].id if chapters else "", len(pending))

    async def poll(self) -> dict:
        return {"pending": await self._unsummarized()}

    async def work(self, ctx: dict) -> dict[str, SummarizerOutput]:
        results: dict[str, SummarizerOutput] = {}
        for chapter in ctx["pending"]:
            out = await self._summarize_chapter(chapter)
            if out is not None:
                results[chapter.id] = out
        return results

    async def _summarize_chapter(self, chapter) -> SummarizerOutput | None:
        windows = assemble_verbatim(chapter.prose, self._budget)
        parts: list[str] = []
        for w in windows:
            label = f"Chapter '{chapter.title}'" + (
                f" (part {w.index + 1}/{w.total})" if w.total > 1 else ""
            )
            out = await self._call(f"{label}:\n{w.text}")
            if out is None:
                # miner convention: unstamped, retried next poll
                return None
            if w.total == 1:
                return out
            parts.append(out.summary)
        merged = await self._call(MERGE_PROMPT + "\n\n" + "\n\n".join(parts))
        return merged

    async def _call(self, msg: str) -> SummarizerOutput | None:
        try:
            result = await self._runner.ainvoke({"messages": [{"role": "user", "content": msg}]})
        except Exception:
            logger.warning("%s: summarize call raised; will retry next poll", self.name, exc_info=True)
            return None
        out = result.get("structured_response")
        if not isinstance(out, SummarizerOutput):
            logger.warning("%s: no usable structured response (%r); will retry next poll",
                           self.name, type(out).__name__)
            return None
        return out

    async def commit(self, results: dict[str, SummarizerOutput], ctx: dict) -> None:
        for chapter_id, out in results.items():
            await self._committer.commit(
                self.name, EventType.CHAPTER_SUMMARIZED, chapter_id,
                ChapterSummarized(chapter_id=chapter_id, gist=out.gist, summary=out.summary),
            )

    async def _run(self) -> None:
        ctx = await self.poll()
        if not ctx["pending"]:
            self.note_pass()
            return
        results = await self.work(ctx)
        await self.commit(results, ctx)
        await self._record_watermark()


def build_summarizer_runner(settings, callbacks=None):
    from deepagents import create_deep_agent
    from langchain.agents.structured_output import ProviderStrategy
    from novelizer.agents.llm import build_chat_model
    # Summarization is extraction, not composition: run cold, grammar-constrained
    # (same rationale as the continuity mining runner).
    model = build_chat_model(
        settings.agent_model, settings.llm_base_url, settings.llm_api_key,
        temperature=0.2, max_tokens=settings.llm_max_tokens, callbacks=callbacks,
    )
    return create_deep_agent(model=model, system_prompt=SYSTEM_PROMPT,
                             response_format=ProviderStrategy(SummarizerOutput))


from novelizer.agents.registry_types import AgentContext, AgentSpec


def _construct(ctx: AgentContext) -> Summarizer:
    runner = ctx.runner_for("summarizer", build_summarizer_runner)
    return Summarizer(
        runner, ctx.read, ctx.committer, ctx.events,
        interval=ctx.settings.summarizer_interval,
        personality=ctx.personalities.get("summarizer", ""),
        extractor_token_budget=ctx.settings.extractor_token_budget,
    )


SPEC = AgentSpec(name="summarizer", tool_grant=None, construct=_construct)
```

`registry.py`: add `summarizer` to the `novelizer.agents` import list and insert `summarizer.SPEC` after `structure_analyst.SPEC`, before `triage.SPEC`.
`runtime.py`: after `self.structure_analyst = ...` add `self.summarizer = self.agents_by_name["summarizer"]`; in `interval_map` add `"summarizer_interval": [self.summarizer],`.
`tui/identity.py`: add `"summarizer": AgentIdentity("summarizer", "Summary", "≡", "Z", "sky_blue3"),`.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/agents/test_summarizer.py tests/agents -q -W error && uv run pytest tests/tui -q -W error`
Expected: PASS (roster/TUI tests must absorb the new agent; if a TUI test asserts a fixed agent roster, update that fixture to include `summarizer`).

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/schemas.py novelizer/agents/summarizer.py novelizer/agents/registry.py novelizer/runtime.py novelizer/tui/identity.py tests/agents/test_summarizer.py
git commit -m "feat(agents): Summarizer — event-sourced rolling chapter summaries (v2 D5)"
```

---

### Task 7: Continuity miner becomes revision-aware

**Files:**
- Modify: `novelizer/agents/continuity_checker.py` (`_fingerprint` ~142-149, `poll` ~151-178)
- Test: `tests/agents/test_continuity_checker.py` (extend)

**Interfaces:**
- Consumes: Task 2 `current_done_ids`.
- Behavior change: a chapter with `chapter.revised` after its `chapter.mined` reappears in `ctx["mined_chapters"]` (the to-mine list) and in the unmined fingerprint count.

- [ ] **Step 1: Failing test** — read the existing mining test in `tests/agents/test_continuity_checker.py` first and extend with its fixtures/conventions:

```python
# append to tests/agents/test_continuity_checker.py (adapt store/runner setup
# to this file's existing helpers — the assertion is what matters)
async def _revision_reopens_mining(make_checker, events, proj) -> None:
    # given a chapter already mined (chapter.mined stamped)...
    # when chapter.revised lands for it and the checker polls again...
    # then it is back in ctx["mined_chapters"].
    ...


def test_revised_chapter_is_remined():
    """chapter.revised after chapter.mined puts the chapter back in the
    to-mine list (v1 gap: revised chapters were never re-mined)."""
```

Concretely: append `Chapter` + `ChapterMined` events, `proj.catch_up()`, build the checker exactly as neighboring tests do, assert `chapter_id not in [c.id for c in (await checker.poll())["mined_chapters"]]`; then append `ChapterRevised(chapter_id=..., prose="new")`, `proj.catch_up()`, assert it IS in `mined_chapters`.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agents/test_continuity_checker.py -q -W error -k remined`
Expected: FAIL — revised chapter still counted as mined.

- [ ] **Step 3: Implement**

In `continuity_checker.py`, replace the two `already_mined = already_mined_chapter_ids(mined_events)` computations (in `_fingerprint` and `poll`) with:

```python
        mined_events = await self._events.events_since(0, event_types=[EventType.CHAPTER_MINED])
        revised_events = await self._events.events_since(0, event_types=[EventType.CHAPTER_REVISED])
        already_mined = current_done_ids(mined_events, revised_events)
```

Import: `from novelizer.brain.watermarks import current_done_ids`. Remove the now-unused `already_mined_chapter_ids` import (leave `brain/mining.py` itself untouched — `thread_touch_log` is still used; delete `already_mined_chapter_ids` only if nothing else imports it, checked via `grep -rn already_mined_chapter_ids novelizer tests`; if only its own unit test remains, migrate that test to cover `current_done_ids` semantics and drop the helper).

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/agents/test_continuity_checker.py tests/brain -q -W error`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/continuity_checker.py tests/agents/test_continuity_checker.py novelizer/brain/mining.py tests/brain
git commit -m "fix(checker): revised chapters re-mine — revision-aware mined set (v2 D2)"
```

---

### Task 8: Keeper watermarked full-prose mining sweep

**Files:**
- Modify: `novelizer/agents/character_keeper.py` (constructor, `poll`, `work`, `commit`, `_fingerprint`, `_construct`)
- Test: `tests/agents/test_character_keeper.py`, `tests/agents/test_character_keeper_property.py` (extend)

**Interfaces:**
- Consumes: Tasks 1-3, 5. New constructor signature (replaces `prose_chars`):
  `CharacterKeeper(runner, read_store, committer, event_store, interval=120, personality="", pull_mode=False, extractor_token_budget=24000)` — **`event_store` is new and required**; `prose_chars` is gone.
- Produces: after a successful run, one `chapter.processed` event per fully-presented chapter with payload `ChapterProcessed(agent="character_keeper", chapter_id=...)`.

- [ ] **Step 1: Failing tests**

```python
# append to tests/agents/test_character_keeper.py (adapt to the file's store
# helpers; CountingRunner mirrors tests/agents/test_summarizer.py)
async def test_sweep_stamps_processed_and_drains_backlog():
    # 3 chapters, small extractor_token_budget so only ~2 fit per run.
    # run 1: chapter.processed stamped for the selected chapters, oldest first.
    # run 2: remaining chapter stamped. run 3: zero LLM calls, zero new events.
    ...

async def test_revised_chapter_is_reswept():
    # after chapter.revised for a stamped chapter, it is back in ctx["unmined"].
    ...

async def test_oversize_chapter_windows_merge():
    # one chapter with prose >> budget: FakeRunner returns a different
    # NewCharacter per call; both characters are minted; chapter stamped once.
    ...

async def test_prompt_contains_full_prose_not_slice():
    # capture the runner's inbound message; assert the full prose of an
    # unmined chapter is present verbatim (a sentinel placed at the END of the
    # prose must appear in the prompt) in BOTH pull and push modes.
    ...
```

Write all four as real tests (the existing file shows the store scaffold; `FakeRunner` capturing `inputs["messages"][0]["content"]` covers the prompt assertions). Sentinel pattern: `prose = "filler " * 2000 + "LATE_ARRIVAL_SENTINEL"`.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agents/test_character_keeper.py -q -W error`
Expected: FAIL — constructor has no `event_store`, no stamps.

- [ ] **Step 3: Implement**

Constructor: drop `prose_chars`, add `event_store: EventStore` (positional after `committer`, matching ContinuityChecker) and `extractor_token_budget: int = 24000`; store as `self._events`, `self._budget`. Keep the class comment about discovery needing whole chapters.

`poll()` additions:

```python
        processed = await self._events.events_since(0, event_types=[EventType.CHAPTER_PROCESSED])
        own = [e for e in processed if e.payload.get("agent") == self.name]
        revised = await self._events.events_since(0, event_types=[EventType.CHAPTER_REVISED])
        done = current_done_ids(own, revised)
        unmined = [c for c in chapters if c.id not in done]
```

Greedy oldest-first budget selection (pure, in `work()` or a small helper):

```python
        est = CharHeuristicEstimator()
        selected, spent = [], 0
        for c in ctx["unmined"]:
            cost = est.estimate(c.prose)
            if selected and spent + cost > self._budget:
                break
            selected.append(c)
            spent += cost
```

`work()` prose section — both modes get the same full-prose block for `selected`; pull mode keeps the map:

```python
        blocks = []
        for c in selected:
            for w in assemble_verbatim(c.prose, self._budget):
                part = f" (part {w.index + 1}/{w.total})" if w.total > 1 else ""
                blocks.append((f"Chapter '{c.title}'{part}", w.text))
```

Single selected set that fits (the common case): ONE runner call whose chapter section is `"\n\n".join(f"{label}:\n{text}" for ...)` (this replaces the old `prose[:6000]` join; in pull mode it is appended after the `chapter_map_note` index under a heading `Unread chapters (full prose — mine these now):`). A single oversize chapter (`w.total > 1`): one runner call per window (same surrounding context blocks), outputs merged:

```python
def _merge_outputs(outs: list[KeeperOutput]) -> KeeperOutput:
    merged = KeeperOutput()
    for o in outs:
        merged.new_characters.extend(o.new_characters)
        merged.updated_characters.extend(o.updated_characters)
        merged.flags.extend(o.flags)
        merged.knowledge_intents.extend(o.knowledge_intents)
        merged.arc_intents.extend(o.arc_intents)
        merged.feed_note = o.feed_note or merged.feed_note
    merged.no_action = all(o.no_action for o in outs) if outs else False
    return merged
```

(Cross-window duplicates are safe: slugs mint exactly once via the commit-time `seen_ids` re-read; flags dedup by description.) `work()` returns `(out, [c.id for c in selected])` or stashes selected ids in `ctx["processed_now"]` — pick the ctx approach to keep `work`'s return type.

`commit()` — after the existing commit flow (including the `no_action` early path — presented-in-full-and-judged still counts as processed), stamp last:

```python
        for chapter_id in ctx.get("processed_now", []):
            await self._committer.commit(
                self.name, EventType.CHAPTER_PROCESSED, chapter_id,
                ChapterProcessed(agent=self.name, chapter_id=chapter_id),
            )
```

Move the `no_action` early-`return` so stamping still happens on a pass (remark + `note_pass()` + stamp). When `out is None` (LLM failure): no stamp, retry next poll.

`_fingerprint()` gains the unmined count (4th component; `_run`'s `fp_now[:2] == fp_seen[:2]` external-comparison stays valid):

```python
        return (len(chapters), chapters[-1].id if chapters else "", len(open_retcons), len(unmined))
```

`_construct`: pass `ctx.events`, `extractor_token_budget=ctx.settings.extractor_token_budget`; delete the `prose_chars=ctx.settings.keeper_prose_chars` line. Fix every other `CharacterKeeper(...)` construction site: `grep -rn "CharacterKeeper(" novelizer tests` and thread a real/`EventStore` through each (tests already build one).

Imports: `EventStore`, `ChapterProcessed`, `current_done_ids`, `assemble_verbatim`, `CharHeuristicEstimator`.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/agents -q -W error`
Expected: PASS (including the pre-existing keeper property test, now constructing with an EventStore).

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/character_keeper.py tests/agents/test_character_keeper.py tests/agents/test_character_keeper_property.py
git commit -m "feat(keeper): watermarked full-prose mining sweep, both modes (v2 D6)"
```

---

### Task 9: Chapter-map gist enrichment

**Files:**
- Modify: `novelizer/brain/context.py:102-117` (`chapter_map_note`)
- Modify (call sites pass gists): `novelizer/agents/character_keeper.py`, `novelizer/agents/continuity_checker.py`, `novelizer/agents/author.py`, `novelizer/agents/plotter.py`, `novelizer/agents/world_architect.py`, `novelizer/chat/service.py`
- Test: `tests/brain/test_context.py` (extend — find the existing `chapter_map_note` tests)

**Interfaces:**
- Produces: `chapter_map_note(chapters: list[Chapter], gists: Mapping[str, str] | None = None) -> str`. Backward compatible: omitted/`None` gists → byte-identical output to today.
- Call-site recipe (identical everywhere): `poll()` adds `"summaries": await self._read.list_chapter_summaries()`; the `chapter_map_note(...)` call becomes `chapter_map_note(chapters, gists={s.chapter_id: s.gist for s in ctx["summaries"] if s.gist})`.

- [ ] **Step 1: Failing test**

```python
# append to tests/brain/test_context.py
from novelizer.brain.context import chapter_map_note


def test_chapter_map_note_gists_annotate_lines():
    chapters = _make_chapters(2)  # use/extend this file's existing chapter fixture
    out = chapter_map_note(chapters, gists={chapters[0].id: "Ana finds the key."})
    lines = out.splitlines()
    idx = next(i for i, l in enumerate(lines) if chapters[0].id in l)
    assert lines[idx + 1].strip() == "gist: Ana finds the key."
    assert "gist:" not in "\n".join(l for l in lines if chapters[1].id in l)


def test_chapter_map_note_without_gists_unchanged():
    chapters = _make_chapters(2)
    assert chapter_map_note(chapters) == chapter_map_note(chapters, gists=None)
    assert "gist:" not in chapter_map_note(chapters, gists={})
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/brain/test_context.py -q -W error`
Expected: FAIL — unexpected keyword `gists`.

- [ ] **Step 3: Implement**

```python
def chapter_map_note(chapters: list[Chapter], gists: Mapping[str, str] | None = None) -> str:
    """Pull-mode chapter index: one line per chapter, never prose. A chapter
    with a Summarizer gist gains an indented gist line — what the chapter IS
    ABOUT — so tooled agents choose what to pull from more than a title.
    (docstring's existing chNNN rationale stays)"""
    if not chapters:
        return "None yet."
    ordinal = chapter_ordinals([c.id for c in chapters])
    lines = []
    for c in chapters:
        lines.append(
            f"- {ordinal[c.id]} '{c.title}' ({c.editorial_status.value}) "
            f"cast: {', '.join(c.character_ids) if c.character_ids else 'none'} [id:{c.id}]"
        )
        gist = (gists or {}).get(c.id)
        if gist:
            lines.append(f"    gist: {gist}")
    return "\n".join(lines)
```

(`from collections.abc import Mapping` at top.) Then apply the call-site recipe from Interfaces to all six modules (chat/service fetches summaries inside `_story_context`). Byte-identical-when-quiet is the repo's prompt rule — the no-gists path must not change output.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/brain tests/agents tests/chat -q -W error`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/brain/context.py novelizer/agents/character_keeper.py novelizer/agents/continuity_checker.py novelizer/agents/author.py novelizer/agents/plotter.py novelizer/agents/world_architect.py novelizer/chat/service.py tests/brain/test_context.py
git commit -m "feat(brain): chapter map gains Summarizer gists at every pull-mode call site (v2 D7)"
```

---

### Task 10: Advisory cutover — the four push-mode `prose[:N]` sites

**Files:**
- Modify: `novelizer/agents/author.py` (~155-190: `prior_chapter_chars` param → advisory), `novelizer/agents/continuity_checker.py` (~188), `novelizer/agents/structure_analyst.py` (~128), `novelizer/chat/service.py` (~122)
- Test: extend `tests/agents/test_author.py`, `tests/agents/test_continuity_checker.py`, `tests/agents/test_structure_analyst.py`, `tests/chat/test_service.py`

**Interfaces:**
- Consumes: Task 1 `AdvisoryEntry`/`assemble_advisory`/`ELISION_MARKER`; Task 4 `list_chapter_summaries`; Task 5 `advisory_token_budget`.
- Site recipe (push-mode branch only; pull branches were handled in Task 9):

```python
        summaries = {s.chapter_id: s.summary for s in ctx["summaries"]}
        entries = [
            AdvisoryEntry(label=f"'{c.title}'", summary=summaries.get(c.id), verbatim=c.prose)
            for c in <site's chapter window>
        ]
        block = assemble_advisory(entries, self._advisory_budget)
```

- Author specifics: the window is `previous[:-1]`; the latest chapter stays appended verbatim-in-full exactly as today. `build_author_prompt`'s `prior_chapter_chars: int = 200` parameter is REPLACED by `advisory_budget: int = 2000` and `summaries: dict[str, str] | None = None`; `Author.__init__`'s `prior_chapter_summary_chars` plumbing is replaced by `advisory_token_budget` from settings; `_construct` updated.
- Checker specifics: window is `ctx["chapters"]` (last 10) in the retcon-pass block; keep the `[{c.id[:8]}]` id prefix inside the label.
- Structure analyst: window is `ctx["unscored"]`; keep `Chapter id:{c.id}` in the label (commit validates ids against it).
- Chat: window is `chapters[-3:]` inside `_story_context`; ChatService gains `advisory_token_budget` (thread from `Runtime` construction: `s.advisory_token_budget`).

- [ ] **Step 1: Failing tests** — one per site, same shape (write all four; capture the prompt through the site's existing fake runner):

```python
def test_push_mode_recap_uses_summary_when_available():
    # seed a ChapterSummarized event for c1, push mode agent, capture prompt:
    # assert the summary text is in the prompt and c1.prose[:N] head-slice is not.

def test_push_mode_recap_labels_missing_summary():
    # no summaries seeded: assert ELISION_MARKER appears (long prose) —
    # degraded, never silent.
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agents/test_author.py tests/agents/test_structure_analyst.py tests/chat -q -W error -k "summary or elision or advisory"`
Expected: FAIL

- [ ] **Step 3: Implement** per the site recipe above. Delete each `prose[:N]` slice. Every touched `poll()` already fetches `summaries` if Task 9 reached it; otherwise add the fetch. Grep afterwards:

Run: `grep -rn "prose\[:" novelizer/` → only hits allowed: none. (`context_assembly.py` slices `verbatim[:head_chars]` — a different name, by design.)

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/agents tests/chat -q -W error`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/author.py novelizer/agents/continuity_checker.py novelizer/agents/structure_analyst.py novelizer/chat/service.py novelizer/runtime.py tests/agents tests/chat
git commit -m "feat(agents): advisory context assembly replaces all prose[:N] fallbacks (v2 D8)"
```

---

### Task 11: Acceptance sweep + spec status

**Files:**
- Modify: `.specs/context-assembly-v2.md` (status → IMPLEMENTED + deviations), `.specs/context-assembly-protocol.md` (status line → `SUPERSEDED by context-assembly-v2.md`)
- No new tests — this task runs the gates.

- [ ] **Step 1: Grep gate**

Run: `grep -rn "prose\[:" novelizer/`
Expected: no output.

- [ ] **Step 2: Deprecated-settings gate**

Run: `grep -rn "keeper_prose_chars\|prior_chapter_summary_chars" novelizer/ | grep -v "settings/"`
Expected: no output (fields exist only in the settings layer, marked deprecated).

- [ ] **Step 3: Full suite (wedge recipe — docs/TESTING-TUI.md)**

Run: `timeout -s KILL 1500 uv run pytest -W error -o faulthandler_timeout=120 -q > /home/ty/.claude/jobs/ba47ff69/tmp/fullsuite.log 2>&1; tail -5 /home/ty/.claude/jobs/ba47ff69/tmp/fullsuite.log`
Expected: all pass (known load-flaky TUI pilots: re-run the failing scope solo before treating as real).

- [ ] **Step 4: Import-linter gate**

Run: `uv run lint-imports`
Expected: clean (brain imports canon events only; no `substrate.*` submodule imports were added).

- [ ] **Step 5: Update spec statuses + commit**

`.specs/context-assembly-v2.md`: `**Status:** IMPLEMENTED — 2026-07-22` plus a `## Deviations` section listing anything that diverged during implementation (or "None").
`.specs/context-assembly-protocol.md`: change the Status line to `**Status:** SUPERSEDED — see context-assembly-v2.md`.

```bash
git add .specs/context-assembly-v2.md .specs/context-assembly-protocol.md
git commit -m "docs(spec): mark context-assembly v2 implemented; supersede v1"
```
