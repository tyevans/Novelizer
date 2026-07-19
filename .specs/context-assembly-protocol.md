# Spec: Context-Assembly Protocol for Novelizer Agents

**Status:** DRAFT — ready for review
**Created:** 2026-07-19
**Author:** Claude (user-initiated, /spec workflow)

---

## Problem Statement

Novelizer agents currently truncate chapter prose with scattered, arbitrary per-agent character cutoffs (`prose[:200]` up to `prose[:6000]`), silently discarding text. Replace these with a shared context-assembly module where each agent declares its **fidelity requirement** (verbatim for extractor agents vs. summary-ok for advisory agents) and a **token budget**. The module supplies:

- Full text when it fits the budget.
- Overlapping windowed chunks when over budget (never silent truncation).
- Rolling, event-sourced chapter summaries for advisory agents.
- Per-agent high-water marks so extractors mine each chapter exactly once, in full.

Target models have 128k+ token contexts; the guiding invariant is **never silently truncate**.

This was motivated by a shipped bug: the Character Keeper showed the LLM only `prose[:300]` (later 6000) per chapter, so characters introduced past the cutoff could never be discovered. The cap fix (merge `f398dab`) treats the symptom; this protocol removes the class of bug.

---

## Context & Constraints

### Truncation-site inventory (chapter prose feeding LLM prompts)

| # | Site | Current | Fidelity need | Chapter scope |
|---|------|---------|---------------|---------------|
| 1 | `novelizer/agents/character_keeper.py:70` | `prose[:keeper_prose_chars]` (6000) | **VERBATIM** — mints characters; a late-chapter introduction is canonical | last 5, every cycle |
| 2 | `novelizer/agents/continuity_checker.py:151` (mining pass) | **already full prose** | **VERBATIM** — already watermarked via `chapter.mined` | unmined only |
| 3 | `novelizer/agents/editor.py:103` | **already full prose** | **VERBATIM** — single draft chapter; needs an oversize guard only | 1 |
| 4 | `novelizer/agents/continuity_checker.py:96` (retcon pass) | `prose[:300]` | SUMMARY_OK | last 10 |
| 5 | `novelizer/agents/structure_analyst.py:51` | `prose[:400]` | SUMMARY_OK (see Open Questions — pacing may need verbatim) | up to 5 unscored |
| 6 | `novelizer/agents/author.py:29` | `prose[:prior_chapter_summary_chars]` (200) | SUMMARY_OK | last 3 |
| 7 | `novelizer/chat/service.py:106` | `prose[:200]` | SUMMARY_OK | last 3 |

Two sites already send full prose; the "never silently truncate" invariant is violated by the five `prose[:N]` sites. The protocol brings all seven under one explicit contract. Sites 2 and 3 are the reference implementations, not regression targets.

Secondary truncations exist on non-prose bodies and lists (`world_entry.body[:100..200]`, character lists `[:8]`, entry lists `[:10..20]` in `author.py:27`, `continuity_checker.py:94`, `world_architect.py:40`, `chat/service.py:104`, plus `[-N:]` chapter-count caps in polls). These are out of scope here (see Non-Requirements) but the module is designed so they can adopt the same contract later.

### How prose flows storage → prompt

Event log (`event_store.py`) → `Projector` folds `chapter.*` events into the `chapters` read table → `ReadStore.list_chapters()` returns models with full `.prose` → each agent's `poll()` selects chapters into `ctx` → `work()` builds the prompt string, truncating inline at f-string time. Truncation is purely a prompt-assembly concern; storage and projections keep full text. The fix therefore lives entirely on the agents' read side.

### Architectural constraints (non-negotiable, per M1–M5 docs)

- **Event sourcing:** the log is sole truth; only the Projector writes projections. A rolling summary must be an *event* folded by a *projection*, never a cached blob.
- **DDD boundaries:** canon / agents / brain communicate via events + read queries. The assembly module lives in `novelizer/brain/` (pure prompt-assembly helpers), consuming `ReadStore`/`EventStore` only.
- **SOLID / extension over modification:** reuse the Committer, `_project` dispatch, and settings-layering seams.
- **TDD with Hypothesis:** every invariant below gets a property test (window coverage, exactly-once, replay determinism).
- **Async/sync split:** store-touching APIs are `async`; window/token math is pure and sync (mirrors `brain/mining.py`).
- **Crash-safety:** watermarks are derived by replaying events (`events_since(0, [...])`), never a mutable counter; a crash before the marker simply re-runs the work, and commit paths are already idempotent.

### Model/token constraints

- No token counting exists anywhere in the repo; `tiktoken` is not a dependency. `llm_max_tokens=4096` is a *generation* cap, not a context budget — per-agent input budgets are new config.
- LLM endpoint is OpenAI-compatible local (llama.cpp-style) via LangChain; no stable tokenizer endpoint. Token estimation starts as a chars/4 heuristic behind a `TokenEstimator` protocol so a real tokenizer can be injected later.

---

## Prior Art

- **The high-water-mark extractor pattern already exists.** The Continuity Checker's mining pass reads `CHAPTER_MINED` events via `events_since(0, [CHAPTER_MINED])`, derives the done-set with the pure `brain/mining.py::already_mined_chapter_ids()`, mines only unmined chapters **with full prose**, and stamps `chapter.mined` last. `ChapterMined` is deliberately never projected (dedup is a pure log read — M5.1 locked decision). This protocol generalizes that pattern per-agent rather than inventing a new one.
- **Projection template:** `canon/projector.py` — new table in `_CREATE`, branch in `_project`, `catch_up()` fold; first-write-wins/upsert idioms to copy.
- **Write seam:** all new events flow through `Committer`/`GatingCommitter`; bookkeeping events (marks, summaries) belong in `AutonomyPolicy._NEVER_GATED` alongside `chapter.mined`.
- **Settings pattern:** `EffectiveSettings` frozen model + `STORY_OVERRIDABLE_KEYS` + `layers.py`/`loader.py` (env prefix `NOVELIZER_`), as used by `keeper_prose_chars` and `prior_chapter_summary_chars` — the fields this protocol supersedes.
- **Pure-helper precedent:** `brain/mining.py` (pure, DB-free, property-testable) is the shape for the new window/watermark math.
- **`tui/widgets/feed_model.py::clamp_text`** — the codebase's only "truncate and *signal* that you truncated" helper; its return-shape (content + explicit flag) is the naming precedent for labeled, never-silent elision.
- **Embeddings** (`store/embeddings.py`) already chunk/store full prose in Chroma with similarity search — available if windowing ever wants retrieval-ranked chunks (explicitly a non-requirement for v1).

---

## Proposed Approach

### New components

1. **`novelizer/brain/context_assembly.py`** (pure, sync, no I/O)
   - `Fidelity` enum: `VERBATIM` | `SUMMARY_OK`.
   - `TokenEstimator` protocol + `CharHeuristicEstimator` (chars/4) default.
   - `assemble_verbatim(text, budget) -> list[Window]`: one window containing the full text when it fits; otherwise ordered, overlapping windows covering the whole text with no gaps.
   - `assemble_advisory(summaries, recent_verbatim, budget) -> str`: "story so far" summaries plus a verbatim tail of the most recent chapter(s), packed within budget.

2. **`novelizer/brain/watermarks.py`** (pure)
   - `processed_chapter_ids(events, agent_name) -> set[str]`: per-agent generalization of `already_mined_chapter_ids`. Folds the log **in sequence order**: a `chapter.processed` event adds `(agent, chapter)` to the set; a later `chapter.revised` for that chapter removes it for *all* agents — so revised chapters are automatically re-mined and re-summarized without any mutable state.

3. **`novelizer/agents/summarizer.py`** (new agent, `name="summarizer"`)
   - Standard `poll()/work()/commit()` triad. `poll()` finds chapters lacking a current `chapter.summarized` event (its own watermark, revision-aware as above); `work()` makes one LLM call per unsummarized chapter over its **full prose** (windowed if oversize, summaries-of-windows merged); `commit()` emits `ChapterSummarized`. Registered in `runtime.py`. There is no existing summarization to reuse — summaries must be a new LLM-produced, event-stored artifact; that is the only replayable option.

### Agent cutover semantics

- **VERBATIM extractors** (Keeper; Continuity miner; Editor oversize guard): `poll()` filters chapters through the per-agent watermark; `work()` iterates `assemble_verbatim` windows — one runner call per window — and merges structured outputs; `commit()` stamps `ChapterProcessed` last. Cross-window merge is already safe: the Keeper mints each slug exactly once at commit; the miner dedups against the log. In the common case (chapter ≤ budget) this is a single window — identical behavior to today, minus the data loss.
- **SUMMARY_OK advisory sites** (Author recap, Continuity retcon pass, Structure Analyst, Chat): replace `prose[:N]` with `assemble_advisory(...)` fed from `read_store.list_chapter_summaries()` plus the most recent chapter verbatim. Until a summary exists for a chapter (Summarizer lag), the assembler includes a *labeled* verbatim head with an explicit `[…]` elision marker — degraded but never silent.
- **Keeper watermark bootstrap:** starts **empty** — on first run under the new code the Keeper sweeps the entire backlog verbatim, recovering every character the old caps dropped. That sweep is the point of the protocol; the slug-collision guard makes it side-effect free.

### Edge behavior (all property-tested)

- Empty prose → single empty window.
- Budget smaller than one window → window clamped to budget, overlap to `budget//4`, step ≥ 1 estimated token: always ≥ 1 window, always full coverage, always terminates.
- Every fact spanning a window seam appears in both adjacent windows (overlap guarantee).

---

## API / Interface Contract

```python
# novelizer/brain/context_assembly.py
class Fidelity(StrEnum):
    VERBATIM = "verbatim"
    SUMMARY_OK = "summary_ok"

class TokenEstimator(Protocol):
    def estimate(self, text: str) -> int: ...

@dataclass(frozen=True)
class CharHeuristicEstimator:
    chars_per_token: float = 4.0
    def estimate(self, text: str) -> int: ...   # ceil(len(text) / chars_per_token)

@dataclass(frozen=True)
class Window:
    index: int
    total: int
    text: str
    @property
    def is_last(self) -> bool: ...

@dataclass(frozen=True)
class Budget:
    tokens: int
    overlap_tokens: int = 256
    estimator: TokenEstimator = CharHeuristicEstimator()

def assemble_verbatim(text: str, budget: Budget) -> list[Window]: ...
def assemble_advisory(summaries: list[ChapterSummary],
                      recent_verbatim: list[Chapter],
                      budget: Budget) -> str: ...

# novelizer/brain/watermarks.py
def processed_chapter_ids(events: list[StoredEvent], agent_name: str) -> set[str]: ...
```

**Guarantees of `assemble_verbatim`:** ≥ 1 window always; windows in reading order; consecutive windows overlap by `overlap_tokens`; concatenation minus declared overlaps reproduces the input byte-for-byte; every input character appears in at least one window.

**Events:** extractors emit `ChapterProcessed`; the Summarizer emits `ChapterSummarized`. Advisory assembly consumes summaries via `ReadStore.list_chapter_summaries()`; watermark reducers consume `chapter.processed` (and legacy `chapter.mined`) via `events_since`.

**Constructor changes:** `CharacterKeeper(..., budget: Budget)` replaces `prose_chars`; `runtime.py` builds per-fidelity `Budget`s from settings and registers `self.summarizer`.

---

## Data Model Changes

**New event types** (`canon/events.py`):

```python
CHAPTER_SUMMARIZED = "chapter.summarized"
CHAPTER_PROCESSED  = "chapter.processed"
```

```python
class ChapterSummarized(BaseModel):      # PROJECTED
    chapter_id: str
    summary: str
    prose_len: int          # revision detection: mismatch vs current prose ⇒ stale

class ChapterProcessed(BaseModel):       # NOT projected (pure log read, like ChapterMined)
    agent_name: str
    chapter_id: str
```

**Watermark namespacing (migration-critical):** legacy `chapter.mined` events carry no agent field and remain the Continuity miner's exclusive mark. The Keeper and all future extractors use `chapter.processed` with `agent_name`. The Keeper must **never** read `chapter.mined` as its own — doing so would skip the backlog and silently reintroduce the discovery bug.

**New projection table** (`projector._CREATE`, `_reset_state` list, `_project` upsert branch mirroring `ANNOTATION_STRUCTURE_SCORED`):

```sql
CREATE TABLE IF NOT EXISTS chapter_summaries (
    chapter_id TEXT PRIMARY KEY,
    summary    TEXT NOT NULL,
    prose_len  INTEGER NOT NULL DEFAULT 0
);
```

Plus `ReadStore.list_chapter_summaries()` and a `ChapterSummary` record in `store/models.py`.

**New settings** (`EffectiveSettings` + `STORY_OVERRIDABLE_KEYS` + layers/loader threading):

```python
context_chars_per_token: float = 4.0
extractor_token_budget: int = 24000     # VERBATIM sites (Keeper, miner, Editor)
advisory_token_budget: int = 6000       # SUMMARY_OK sites
context_window_overlap_tokens: int = 256
summarizer_interval: int = 150
```

`keeper_prose_chars` and `prior_chapter_summary_chars` are superseded: accepted-but-ignored for one release (parse tests updated in lockstep), then removed.

**Autonomy:** `chapter.processed` and `chapter.summarized` join `AutonomyPolicy._NEVER_GATED` (bookkeeping, like `chapter.mined`).

---

## Migration / Rollout Plan

**No event-log migration and no forced projection rebuild.** Verified: `EventStore` deserializes generically (no per-type registry to reject unknown types), and `Projector._project` is an if/elif chain with no catch-all — unknown event types are silent no-ops. Old DBs load unchanged; `chapter_summaries` is a replay-built projection that simply starts empty.

**Ordering of changes (each step red/green before the next):**
1. `brain/context_assembly.py` + `brain/watermarks.py` with unit + Hypothesis property tests (pure, no DB).
2. Events, payloads, projection table, `ReadStore.list_chapter_summaries()`, settings keys.
3. Summarizer agent + runtime registration.
4. Agent-by-agent cutover: **Keeper first** (the motivating bug), then Editor oversize guard and Continuity miner (cosmetic — wrap already-full prose in `assemble_verbatim`), then advisory sites (Author, Continuity retcon, Structure Analyst, Chat).

**Keeper backlog bootstrap: Option A — start with an empty watermark and re-mine every existing chapter once in full.** This recovers characters the old caps dropped (the point of the branch). Duplicate risk is neutralized by the existing slug-collision guard. Rejected: seeding the mark to "all done" (permanently forfeits the dropped characters). Optional optimization: restrict the initial sweep to chapters with `len(prose) > 6000` — only those could have lost content — at the cost of hard-coding the retired cap; not the default.

**No feature flag:** single-user local tool; "deployment" is the next CLI run against an existing `stories/*.db`.

---

## Non-Requirements

- **Non-prose truncations** — world-entry bodies (`body[:100..200]`), character/entry list caps (`[:8]`, `[:20]`), retcon-queue caps — are out of scope; the module is shaped so they can adopt the contract later.
- **Real tokenizer integration** — chars/4 heuristic only; `TokenEstimator` protocol is the extension point.
- **Retrieval-ranked windowing** — no Chroma/similarity-based chunk selection; windows are positional.
- **Hierarchical/recursive summarization** (summary-of-summaries) — flat per-chapter summaries only; `assemble_advisory` packs greedily within budget.
- **Cross-provider token accounting** — no per-model context-window registry; budgets are plain settings.

---

## Acceptance Criteria

- **No prose char-slices remain in prompt assembly:** `grep -rnE 'prose\[:' novelizer/agents novelizer/chat` returns nothing. Specifically gone: `character_keeper.py:70`, `continuity_checker.py:96`, `structure_analyst.py:51`, `author.py:29`, `chat/service.py:106`.
- **Hypothesis property tests** on `assemble_verbatim` over generated `(text, budget, overlap)`:
  - *Fits:* `len ≤ budget` ⇒ exactly one window equal to the full text.
  - *Coverage:* every input character index appears in ≥ 1 window.
  - *Reconstruction:* windows minus declared overlaps concatenate to the input byte-for-byte.
  - *Overlap bound:* adjacent windows share exactly the configured overlap; every window respects the budget.
- **Keeper sees whole chapters:** a test asserts the assembled prompt contains the *entire* prose of a > 6000-char chapter (inverts today's `test_work_prompt_caps_prose_at_configured_prose_chars`, which asserts the bug).
- **Exactly-once across restart:** mine chapters, rebuild agent + read store against the same event store, assert marked chapters are skipped and a new chapter is processed. Assert Keeper marks are disjoint from Continuity's `chapter.mined` namespace.
- **Revision staleness:** a `chapter.revised` after `chapter.processed`/`chapter.summarized` makes that chapter re-mined and re-summarized.
- **Summaries replayable:** `ChapterSummarized` round-trips the `EventStore`, and a `Projector` replay-from-0 rebuild reproduces identical `chapter_summaries` state (per existing projection-property test pattern).
- **Backward compat:** an event stream with only pre-existing event types runs `catch_up` without error and with unchanged projections.
- **Idempotent re-mine:** re-processing a seen chapter mints no duplicate characters (slug-collision guard exercised).
- **Full suite green in a worktree** (never the main checkout): `timeout -s KILL 1500 uv run pytest -v -W error -o faulthandler_timeout=120 > "$CLAUDE_JOB_DIR/tmp/fullsuite.log" 2>&1`, plus TUI gate `uv run pytest tests/tui -q -W error`; `live_llm` stays deselected.

---

## Open Questions

1. **Structure Analyst fidelity.** Classified SUMMARY_OK, but tension/pacing is a textural property that summaries flatten. Options: keep SUMMARY_OK (cheap), upgrade to VERBATIM with its own watermark (scores each chapter once anyway — it already tracks unscored chapters), or a hybrid (verbatim for the chapter being scored, summaries for surrounding context). Leaning: VERBATIM for the scored chapter — it is closer to an extractor than an advisor.
2. **Summary shape.** Single free-text paragraph vs. lightly structured (events / character appearances / threads touched)? Structured summaries would let advisory agents *and* future extractors reuse them, but couple the Summarizer's output schema to consumers. Leaning: free text ≤ ~150 words for v1; structure later if a consumer demands it.
3. **Summarizer model/temperature.** Reuse `agent_model`/`agent_temperature` or dedicated settings? Leaning: reuse; add settings only when someone actually wants a different model.
4. **Window-merge prompt framing.** When a chapter spans multiple windows, should the Keeper's per-window prompt say "this is part i of n; you may see repeated overlap text"? Leaning: yes — cheap and reduces duplicate/contradictory outputs at seams.
5. **Advisory tail size.** How many recent chapters appear verbatim in `assemble_advisory` before summaries take over — fixed count (1) or budget-greedy from newest backward? Leaning: budget-greedy, newest first.
