# Context-Assembly Protocol for Novelizer Agents

Status: draft
Date: 2026-07-22
Author: Ty Evans (with Claude)

## Proposed Approach (context_assembly.py, watermarks.py, summarizer agent, agent cutover semantics, edge behavior)

The approach is verified against a working reference implementation on the (unmerged) branch
`worktree-context-assembly-v2` — this section describes that design, grounded in its actual code
and its spec at `.specs/context-assembly-v2.md`, rather than a fresh proposal from scratch.

### `novelizer/brain/context_assembly.py` — pure, sync, no I/O

Two entry points cover the two truncation shapes seen in the inventory: a single oversize chapter
(extractor path) and a growing list of past chapters competing for a fixed budget (advisory path).

- `TokenEstimator` protocol + a default `CharHeuristicEstimator(chars_per_token=4.0)`
  (`estimate(text) = ceil(len(text)/4)`). No tokenizer dependency exists anywhere in the repo today
  — the llm endpoint is a local OpenAI-compatible server with no stable tokenizer API — so the
  protocol exists purely as the seam for swapping in a real tokenizer later without touching
  callers.
- `Window(text, index, total)` — a frozen dataclass; `assemble_verbatim(text, budget_tokens,
  estimator=None) -> list[Window]`:
  - Text that fits the budget (or is empty) returns exactly one `Window`.
  - Oversize text is split via the existing `chunk_prose` helper (`novelizer/text_chunk.py`) into
    ordered, overlapping windows that cover the whole string with no gaps — overlap sized at
    `budget_tokens // 4` (converted to chars via the estimator's empirical ratio), so no estimator
    implementation can produce a zero-progress loop. This is the direct fix for the `prose[:N]`
    sites: instead of showing the model a head-slice and discarding the rest, every chapter is
    guaranteed to be shown to the model in full, across one or more calls.
- `AdvisoryEntry(label, summary=None, verbatim=None)` + `assemble_advisory(entries,
  budget_tokens, estimator=None) -> str`: packs chapters newest-first into a budget, preferring
  each chapter's `summary` (once the Summarizer has produced one) and falling back to a labeled
  verbatim head ending in a literal `ELISION_MARKER = "[…truncated — summary pending]"` when a
  summary doesn't exist yet. Chapters dropped for budget reasons are oldest-first and always
  announced via a `"[N earlier chapters omitted]"` header line inserted at the top of the packed
  block. This is the "never silently truncate" invariant made mechanical: every character the
  model doesn't see is disclosed as not-seen, in-band, rather than quietly absent.

### `novelizer/brain/watermarks.py` — pure, sync, revision-aware done-sets

A single function, `current_done_ids(done_events, revised_events) -> set[str]`, folds two lists of
`StoredEvent` in **global sequence order**: a "done" event (e.g. `chapter.mined`,
`chapter.processed`, `chapter.summarized`) adds its `chapter_id`; a later `chapter.revised` for the
same id removes it. The result is a plain derived set recomputed from the event log on every poll —
no mutable counters, no separate "high-water mark" integer to get out of sync, crash-safe and
replayable by construction. This generalizes the existing `brain/mining.already_mined_chapter_ids`
pattern (which is revision-blind today: `chapter.revised` exists and is projected, but nothing
currently un-marks a chapter as mined) into the one place every extractor-style agent's poll/commit
cycle needs it: the continuity miner, the Character Keeper, and the new Summarizer all become
`current_done_ids(events_since(0, [DONE_TYPE]), events_since(0, [CHAPTER_REVISED]))` callers, each
parameterized only by which "done" event type and (for the Keeper) which `agent` field they filter.

### Summarizer agent — `novelizer/agents/summarizer.py` (new, untooled)

A new agent, built the same way every other agent is: an `AgentSpec` registered in
`novelizer/agents/registry.py`, a runner constructed cold (temperature 0.2) with a
grammar-constrained `ProviderStrategy(SummarizerOutput)` — the same rationale already used by the
continuity miner (`continuity_checker.py:555-560`) for structured, low-variance extraction output.
It is untooled: it has no pull-mode role, it exists purely to produce the summaries other agents'
prompts consume.

- `poll()` computes `unsummarized = [c for c in chapters if c.id not in current_done_ids(
  CHAPTER_SUMMARIZED events, CHAPTER_REVISED events)]`; the fingerprint folds in
  `len(unsummarized)` so the agent wakes whenever new or revised chapters appear and goes idle
  (`note_pass()`) once the backlog is empty — the same idle/wake shape every other agent already
  uses.
- `work()` runs each unsummarized chapter's **full prose** through `assemble_verbatim(prose,
  extractor_token_budget)`. A chapter that fits in one window gets one call producing
  `SummarizerOutput(gist, summary)` directly. A chapter that doesn't fit gets one call per window
  followed by a single merge call over the per-window summaries — the same window→merge shape the
  Keeper cutover reuses (see below), so there is exactly one merging pattern in the codebase, not
  two independent ones.
- `commit()` emits one `CHAPTER_SUMMARIZED(chapter_id, gist, summary)` event per successfully
  summarized chapter, after the work is done — failures on a single chapter simply leave it
  unstamped so it's retried on the next poll, matching the miner's existing retry convention.
- Registry placement: after `structure_analyst`, before `triage` — it is an annotation-layer
  worker and must not preempt the planner/writer agents for turn order. It gets its own interval
  setting (`summarizer_interval`) wired into `runtime.py`'s `apply_settings` interval map, and
  (since it's untooled) it is not added to `_TOOLING_PINNED_NAMES`. A TUI `AgentIdentity` entry is
  added in `tui/identity.py` so it renders with a name instead of falling back to a generic label.

### Chapter-map enrichment (the pull-mode payoff)

`brain/context.py`'s `chapter_map_note(chapters, summaries=None)` gains an optional `summaries`
mapping (`chapter_id -> gist`); when present, each chapter's line in the index gains a second,
indented `    gist: {gist}` line. All seven existing call sites start passing
`{s.chapter_id: s.gist for s in read.list_chapter_summaries()}`. This is the payoff for building
the Summarizer at all under a pull-mode-default world: agents deciding *which* chapters to pull via
canon file tools now see a one-line description of what each chapter is about, not just its title
and status, before they decide to spend a tool call reading it.

### Agent cutover semantics — per-site treatment, not a single global switch

The five inventoried `prose[:N]` sites split into two treatments, both cutting over to the shared
helpers rather than each agent inventing its own windowing:

1. **Extractor site — Character Keeper (`agents/character_keeper.py:148`)**, the one with the
   shipped bug (characters introduced past the 6000-char cutoff were never discoverable). Its
   `poll()` gains `unmined = [c for c in chapters if c.id not in current_done_ids(
   CHAPTER_PROCESSED events filtered to agent=="character_keeper", CHAPTER_REVISED events)]`.
   `work()` selects unmined chapters oldest-first, greedily, until `extractor_token_budget` is
   spent (unselected chapters wait for the next run); a single chapter that alone exceeds the
   budget is windowed via `assemble_verbatim` and gets one labeled call per window (`'{title}'
   part i/n`), all within the same run so it can be stamped atomically. This replaces the
   `recent[-5:]` + `prose[:6000]` push-mode branch in **both** pull and push mode — deterministic
   character discovery cannot depend on what the LLM chooses to pull, so the full-prose sweep runs
   regardless of tooling mode; pull mode additionally keeps the `chapter_map_note` and live tools
   for cross-referencing. Multi-window outputs are merged by concatenating the
   `new_characters`/`updated_characters`/`flags`/`knowledge_intents`/`arc_intents` lists — safe
   because the existing commit-time guards (slug minted once via `seen_ids` re-read, flag dedup by
   description, learn-only knowledge intents) already tolerate exactly this shape of merge.
   `commit()` stamps `CHAPTER_PROCESSED` per fully-presented chapter, last, after the existing
   commit flow succeeds — crash before the stamp simply means a harmless re-run, the same
   convention the miner already relies on. **The first run under this design is expected to be a
   bootstrap sweep**: an empty processed set means the Keeper re-reads the entire backlog in full
   at `extractor_token_budget` per run, recovering exactly the characters the old fixed cutoff
   dropped, spread across as many runs as the backlog requires.

2. **Advisory sites — `author.py:183`, `continuity_checker.py:188`, `structure_analyst.py:128`,
   `chat/service.py:122`.** Each becomes `assemble_advisory(entries, advisory_token_budget)` where
   `entries` pairs each chapter's `summary` (from `list_chapter_summaries()`) with its raw prose
   for the labeled-fallback path. `author.py`'s immediately-prior chapter stays verbatim,
   unchanged — only the older "previous chapters" recap block is replaced.
   `structure_analyst.py`'s push-mode scoring block is the one site where v1 of this design left
   an open question about whether pacing analysis needs verbatim access; v2 resolves it by relying
   on pull mode (already the settings default) giving that agent full-chapter reads through canon
   file tools, leaving the advisory block as a push-mode-only fallback.

### Edge behavior

- **Empty prose**: `assemble_verbatim` returns a single empty `Window` rather than raising or
  producing zero windows — callers never need a special case for a chapter with no content yet.
- **Budget smaller than any single window's minimum content**: `assemble_verbatim` still
  guarantees at least one window and a strictly positive step size, so it cannot loop forever
  chasing an unreachable budget; `assemble_advisory` guarantees it always keeps at least the
  first (most recent) entry even if that entry alone exceeds the remaining budget, so the packed
  block is never truly empty when there is at least one chapter.
- **Revision during an in-flight sweep**: because done-sets are recomputed from the event log
  every poll (not cached), a `chapter.revised` landing between polls is picked up on the very next
  poll without any special-cased invalidation code — the chapter simply reappears in `unmined` /
  `unsummarized`.
- **Nothing left to do**: every consumer (`Summarizer`, Keeper's mining sweep, the continuity
  miner) folds the backlog size into its readiness fingerprint and calls the existing `note_pass()`
  idle convention when the backlog is empty, so this cutover introduces no new polling agent that
  spins when there's nothing to summarize or mine.
- **Degraded-but-visible, never silent**: both `ELISION_MARKER` and `OMITTED_HEADER_FMT` are
  literal, in-band strings inserted into the prompt text itself — the model (and anyone reading a
  transcript) can always tell when it is looking at a partial view versus the whole chapter,
  closing the failure mode that made the original bug hard to detect (the LLM had no signal that
  `prose[:300]` was a slice, not the whole chapter).

## API / Interface Contract

Grounded directly against the reference implementation on the (unmerged) branch
`worktree-context-assembly-v2` — signatures below are transcribed from that code, not invented.

### `novelizer/brain/context_assembly.py` — pure, synchronous, no I/O, no imports of canon/agents

```python
class TokenEstimator(Protocol):
    def estimate(self, text: str) -> int: ...

@dataclass(frozen=True)
class CharHeuristicEstimator:
    chars_per_token: float = 4.0
    def estimate(self, text: str) -> int: ...   # ceil(len(text) / chars_per_token)

@dataclass(frozen=True)
class Window:
    text: str
    index: int
    total: int

def assemble_verbatim(
    text: str, budget_tokens: int, estimator: TokenEstimator | None = None
) -> list[Window]: ...

@dataclass(frozen=True)
class AdvisoryEntry:
    label: str
    summary: str | None = None
    verbatim: str | None = None

def assemble_advisory(
    entries: list[AdvisoryEntry], budget_tokens: int, estimator: TokenEstimator | None = None
) -> str: ...

ELISION_MARKER = "[…truncated — summary pending]"
OMITTED_HEADER_FMT = "[{n} earlier chapters omitted]"
```

Contract notes:

- Both functions are pure and synchronous — no `ReadStore`, `EventStore`, or LLM call inside this
  module. Every caller does its own I/O (`await read.list_chapters()`, etc.) and passes plain data
  in; this is what makes the module unit-testable without an event store or DB fixture, matching
  the existing `clamp_text`/`text_chunk.chunk_prose` precedent.
- `assemble_verbatim` always returns a non-empty list. `total` on every `Window` equals
  `len(result)`, so a caller can always render `"part {index+1}/{total}"` without a separate
  length check.
- `estimator` is optional everywhere and defaults to `CharHeuristicEstimator()`; callers never
  construct an estimator unless they want to override the heuristic (e.g. in a test asserting an
  exact window boundary).
- `assemble_advisory` returns a single joined string (not a list) — it is a drop-in replacement for
  the string interpolation each of the four advisory call sites already builds by hand.
- Neither function raises on empty input, a budget of 0, or an entry with neither `summary` nor
  `verbatim` set (renders `"(no content)"`) — see Edge behavior in Proposed Approach for the
  guarantees this buys callers.

### `novelizer/brain/watermarks.py` — pure, synchronous, one function

```python
def current_done_ids(
    done_events: list[StoredEvent], revised_events: list[StoredEvent]
) -> set[str]: ...
```

- Both arguments are lists of `StoredEvent` (the existing type returned by
  `EventStore.events_since`), read from `payload["chapter_id"]` — callers are responsible for
  fetching the right event-type-filtered slices before calling in; the function does not touch
  `EventStore` itself.
- Ordering is by `StoredEvent.sequence` (global, monotonic), not by list position, so callers may
  pass the two lists in any order or interleaving.
- Return value is a plain `set[str]` of chapter ids currently considered "done" for whatever
  `done_events` represents (e.g. all `chapter.processed` events already filtered to one agent, or
  all `chapter.summarized` events) — the caller intersects/subtracts this against
  `list_chapters()` to get its backlog.

### `Summarizer` agent — `novelizer/agents/summarizer.py`

Follows the existing `BaseAgent` contract exactly (same shape as `ContinuityChecker`,
`CharacterKeeper`): `readiness() -> float`, `poll() -> dict`, `work(ctx) -> dict[str,
SummarizerOutput]`, `commit(results, ctx) -> None`, driven by the shared `_run()` idle/wake loop.

```python
class Summarizer(BaseAgent):
    def __init__(
        self, runner: Runner, read_store: ReadStore, committer: Committer,
        event_store: EventStore, interval: int = 300, personality: str = "",
        extractor_token_budget: int = 24000,
    ) -> None: ...

    async def readiness(self) -> float: ...      # 0.0 if no backlog, else _gate_on_watermark(0.6)
    async def poll(self) -> dict: ...             # {"pending": list[Chapter]}
    async def work(self, ctx: dict) -> dict[str, SummarizerOutput]: ...  # keyed by chapter_id
    async def commit(self, results: dict[str, SummarizerOutput], ctx: dict) -> None: ...

SPEC = AgentSpec(name="summarizer", tool_grant=None, construct=_construct)
```

- `tool_grant=None` is load-bearing: the Summarizer is registered the same way every other agent
  is (through `AgentSpec` + `registry.py`), but it is explicitly untooled — it never gets a
  pull-mode canon file tool grant, unlike the miner/Keeper/Author/etc.
- `_construct(ctx: AgentContext)` reads `ctx.settings.summarizer_interval` and
  `ctx.settings.extractor_token_budget` and `ctx.personalities.get("summarizer", "")` —
  the same three-source wiring (`settings`, `personalities`, `events`/`read`/`committer` off
  `ctx`) every other agent's `_construct` uses.
- `SummarizerOutput` (in `novelizer/agents/schemas.py`, extending the existing structured-output
  family) is `{gist: str = "", summary: str = "", feed_note: str = ""}` — `feed_note` follows the
  existing convention (shared by other structured outputs) for an agent's optional live-feed line;
  it is not required for the summarization contract itself.

### Existing call-site signature changes (no new public functions, existing ones gain an optional param)

```python
# novelizer/brain/context.py
def chapter_map_note(
    chapters: list[Chapter], summaries: dict[str, str] | None = None
) -> str: ...
```

- `summaries` is `chapter_id -> gist`, optional and defaulting to `None` (renders exactly as
  before when omitted) — this is an additive, backward-compatible signature change, not a new
  function; all seven existing call sites are updated to pass
  `{s.chapter_id: s.gist for s in await read.list_chapter_summaries()}` rather than getting a new
  parameter forced on them silently.

### Read-side additions (already present in `ReadStore`, reused as-is — no new methods needed)

```python
async def list_chapter_summaries(self) -> list[ChapterSummary]: ...
async def get_chapter_summary(self, chapter_id: str) -> Optional[ChapterSummary]: ...
```

These already exist against the `chapter_summaries` projection table (see Data Model Changes) and
require no interface change — the protocol's only job here is to specify that every advisory call
site and `chapter_map_note` caller goes through `list_chapter_summaries()`, not a bespoke query.

### Settings surface consumed by this contract

`ctx.settings.extractor_token_budget: int` (default `24000`), `ctx.settings.advisory_token_budget:
int` (default `2000`), `ctx.settings.summarizer_interval: int` (default `300`) — plumbed through
`AppSettings`/`SettingsLayer`/`SettingsLoader` the same three-file way every other per-agent
setting is (see Data Model Changes for the full layering).

### Non-contract (explicitly out of scope for this section)

No new HTTP/RPC surface, no new TUI command, and no change to `BaseAgent`'s public method
signatures beyond what `Summarizer` already implements — this is a library-level contract
(pure helper module + one new agent class + one widened existing function), not a service API.

## Migration / Rollout Plan

No data migration, no backfill script, and no schema change to existing tables — every piece of
this design is additive (new event types, one new projection table, new optional function
parameters, new settings with defaults). Existing `chapter.mined` events, the `chapters` table, and
every current agent's committed history stay valid and untouched; nothing here rewrites the event
log.

### Sequencing (each step independently mergeable and shippable)

1. **Land the pure helpers first, with no caller.** `novelizer/brain/context_assembly.py` and
   `novelizer/brain/watermarks.py` ship with unit tests only (property-based per the repo's
   red/green + property-based TDD standard — no event store, no DB, no LLM fixture needed, since
   both modules are pure and synchronous). This step is zero-risk: nothing imports either module
   yet, so it cannot regress any running agent.
2. **Add `ChapterProcessed`/`ChapterSummarized` event types, the `chapter_summaries` projection
   table, and the `ReadStore.list_chapter_summaries`/`get_chapter_summary` methods.** Both event
   types are added to the gated set in `canon/policy.py` alongside the existing gate list. The
   `chapter_summaries` table is created via the same `CREATE TABLE IF NOT EXISTS` pattern already
   used for every other projection table in `projector.py`, so existing databases pick it up on
   next open with no explicit migration step. Replaying the full event log from empty on a fresh
   DB is unaffected — `ChapterProcessed` is never projected (matches `ChapterMined`'s
   never-projected precedent), `ChapterSummarized` upserts by `chapter_id`.
3. **Add the three settings (`extractor_token_budget=24000`, `advisory_token_budget=2000`,
   `summarizer_interval=300`) to `settings/models.py`, `layers.py`, and `loader.py`**, following
   the existing three-layer plumbing exactly (defaults in the model, optional override in the
   layer and loader). Because every field is `int | None = None` at the layer/loader level with a
   concrete default only in the model, an existing `settings.toml`/env with no opinion on these
   keys behaves identically to today — this step ships with no behavior change for any current
   user.
4. **Land the Summarizer agent, registered but initially idle.** `Summarizer` is registered in
   `agents/registry.py` (after `structure_analyst`, before `triage`, per Proposed Approach), wired
   into `runtime.py`'s interval map, and given a `tui/identity.py` entry. On a fresh backlog run
   it does a bootstrap sweep of every existing chapter (empty processed set), same as the Keeper's
   bootstrap sweep in the next step — this is expected, not a bug, and is called out explicitly so
   it isn't mistaken for a runaway agent on first boot after upgrade.
5. **Cut over the one extractor site (Character Keeper) and the four advisory sites one at a
   time, each its own commit.** Each site swaps its existing ad hoc slicing
   (`prose[:6000]`/`recent[-5:]`) for the shared helper; each is independently revertable (a git
   revert of one site's commit does not touch the others, since none of the sites share code
   beyond the two pure helpers already merged in step 1). The Keeper cutover in particular
   triggers its own bootstrap sweep of the full chapter backlog on first run post-merge — expected
   for the reasons in Proposed Approach's Edge behavior — and should be watched once in a real run
   before considering the cutover done, per this repo's standing rule to run acceptance passes
   live rather than only in test.
6. **Wire `chapter_map_note`'s new `summaries` parameter at all seven call sites**, each passing
   `{s.chapter_id: s.gist for s in await read.list_chapter_summaries()}`. Until the Summarizer has
   produced a gist for a given chapter, `list_chapter_summaries()` simply omits it, so every call
   site degrades gracefully to today's no-gist rendering for any chapter not yet summarized — no
   site needs a conditional to handle a partially-populated summary set.

### Ordering constraints

- Steps 2–3 (events/table/settings) must land before step 4 (Summarizer) and step 5 (advisory
  cutovers using `summary` in `AdvisoryEntry`), since both consume `ChapterSummarized` and the new
  settings.
- Step 1 (pure helpers) must land before steps 4 and 5, obviously, but has no other dependency and
  can be developed and merged independently of everything else.
- The Keeper's extractor cutover (step 5, Keeper half) does not depend on the Summarizer being
  live — `assemble_verbatim` needs no summaries — so it can land before or after step 4 with no
  interaction.
- The advisory cutovers (step 5, the four `assemble_advisory` sites) work correctly with zero
  summaries present (falling back to the labeled-verbatim-plus-`ELISION_MARKER` path), so they do
  not have to wait for the Summarizer to finish its bootstrap sweep before being merged — quality
  of the advisory block simply improves incrementally as summaries accumulate.

### Rollback

Every step above is a small, independently revertable commit per the existing repo convention
(steps 2–3 add columns/tables/settings that are additive and inert if unused; steps 4–5 each touch
one call site or one new agent). Reverting any single cutover commit (step 5) restores that site's
previous ad hoc slicing without affecting the shared helpers or any other cutover site. Reverting
the Summarizer registration (step 4) simply stops new `ChapterSummarized` events from being
produced; already-recorded summaries remain valid and harmless in the projection, and every
advisory site already tolerates an empty or partial summary set by design, so there is no unwind
step required for the data itself — only for the code producing it.

### What this rollout explicitly does not do

No feature flag gates this work: because every cutover site independently degrades to
today's behavior in the absence of summaries (advisory sites) or is strictly a bug fix with no
alternate mode to preserve (the Keeper's fixed-cutoff truncation was never a deliberate design
users could opt back into), there is no dual-path/flag-toggle complexity to build or later remove.
There is no backfill job to summarize the existing backlog in one shot — the Summarizer's own
bootstrap sweep (step 4) is the backfill, running at `summarizer_interval` cadence like any other
agent poll rather than as a special one-off script.

## Non-Requirements

This protocol is deliberately scoped to fixing the truncation-site inventory and giving pull-mode
agents a gist-level index. The following are explicitly out of scope for this design, listed with
the reason each is excluded rather than merely deferred:

- **A real tokenizer.** `TokenEstimator` is a protocol precisely so `CharHeuristicEstimator` can be
  swapped later; this design does not integrate `tiktoken` or any model-specific tokenizer. The
  llm endpoint is a local OpenAI-compatible server with no stable tokenizer API to target, so
  building against one now would be speculative. The char-heuristic estimator is accepted as
  approximate by design.
- **A generalized RAG/embeddings/vector-search layer.** Embeddings appear only in Prior Art as
  a design point considered and rejected for this iteration — semantic retrieval over chapter
  history is a different, larger problem (indexing, similarity search infra, staleness on
  revision) than the budget-packing and disclosure problem this protocol solves. Nothing here
  precludes adding it later; nothing here builds toward it either.
- **Backfilling or rewriting the event log.** As stated in Migration/Rollout, this is purely
  additive. No existing `chapter.mined`/`chapter.processed` event is reinterpreted, replayed with
  new semantics, or migrated. The Summarizer's bootstrap sweep is the only "catch-up" mechanism,
  and it runs as an ordinary poll/work/commit cycle, not a one-off migration script.
- **A summarization quality bar or eval harness.** This design specifies the mechanical contract
  (one `CHAPTER_SUMMARIZED` event per chapter, `gist` + `summary` fields, retry-on-failure) but
  does not define prompt-quality acceptance criteria, a golden-set eval, or a quality regression
  test for the Summarizer's output. That is a prompt-engineering concern for whoever implements
  `novelizer/agents/summarizer.py`'s prompt, not a data-flow concern this protocol governs.
- **Changing `BaseAgent`'s public contract.** As stated in API/Interface Contract, `Summarizer`
  implements the existing `readiness/poll/work/commit` shape as-is. No new agent lifecycle hook,
  no new base-class method, no change to `_run()`'s idle/wake loop is introduced.

## Acceptance Criteria

Grounded in the reference implementation's own acceptance criteria at
`.claude/worktrees/context-assembly-v2/.specs/context-assembly-v2.md`, expanded to name every
site and observable inventoried elsewhere in this doc so a reviewer can check each item against a
concrete file/line rather than a vague claim.

### Truncation sites eliminated

- `grep -rn 'prose\[:' novelizer/` returns no matches outside the labeled-fallback construction
  inside `context_assembly.py` itself (the one place a bounded slice is intentional and disclosed
  via `ELISION_MARKER`). All five inventoried sites — `character_keeper.py:148`, `author.py:183`,
  `continuity_checker.py:188`, `structure_analyst.py:128`, `chat/service.py:122` — are confirmed
  individually cut over, not just absent from the grep by coincidence of a rename.
- No agent-facing prompt ever shows a silently truncated chapter: any packed advisory block
  that omits content includes `OMITTED_HEADER_FMT`/`ELISION_MARKER` text, verified by a test that
  asserts the marker string appears in `assemble_advisory`'s output whenever an entry lacks a
  summary and exceeds budget.

### Character discovery bug (the shipped regression this protocol exists to fix)

- A property/integration test reproduces the original failure mode — a character introduced past
  the old 6000-char cutoff in a long chapter — and demonstrates the Character Keeper now discovers
  them, via `assemble_verbatim` windowing plus the multi-window merge in `work()`.
- Keeper's first run over a pre-existing backlog (empty `chapter.processed` done-set) stamps
  `ChapterProcessed` for every chapter within `extractor_token_budget`, and resumes correctly
  across subsequent runs until the backlog is fully drained — verified in a real run, not only in
  test, per this repo's standing rule (see `docs/TESTING-TUI.md`) to watch acceptance passes live.
- A `chapter.revised` event for an already-processed chapter makes that chapter reappear in
  `current_done_ids`'s complement on the very next poll, with no cache-invalidation code required —
  verified by a unit test on `watermarks.current_done_ids` and an integration test that revises a
  chapter and confirms the Keeper re-mines it.

### Summarizer agent

- On a fresh story, the Summarizer produces one `ChapterSummarized(gist, summary)` per chapter;
  `chapter_map_note`'s rendering shows a `gist:` line for every chapter that has been summarized
  and degrades to today's no-gist rendering for any chapter not yet summarized — verified at all
  seven `chapter_map_note` call sites.
- The Summarizer goes idle (`note_pass()`) once its backlog is empty and wakes again the instant a
  new or revised chapter appears, matching every other agent's idle/wake convention — verified by
  inspecting `readiness()`/fingerprint behavior in test, not just code reading.
- The Summarizer is registered with `tool_grant=None` and does not appear in
  `_TOOLING_PINNED_NAMES`; it is positioned after `structure_analyst` and before `triage` in
  `agents/registry.py`'s turn order, confirmed by reading the registry list, not just spec intent.

### Settings and rollout

- `extractor_token_budget` (default 24000), `advisory_token_budget` (default 2000), and
  `summarizer_interval` (default 300) are present in `settings/models.py`, `layers.py`, and
  `loader.py` with the existing three-layer override precedence; an existing `settings.toml` with
  no opinion on these keys behaves identically to today (no forced behavior change on upgrade) —
  verified by a settings-layering test asserting the documented defaults apply when unset.
- Replaying the full event log from empty against a fresh DB succeeds with `ChapterProcessed`
  never projected and `ChapterSummarized` upserting `chapter_summaries` by `chapter_id`, mirroring
  `ChapterMined`'s existing never-projected precedent — verified by a projector replay test.
- Every step in Migration/Rollout is independently revertable: reverting any one cutover-site
  commit restores that site's prior behavior without touching the shared helpers or any other
  site — verified by confirming (not merely asserting) that each cutover commit touches exactly
  one call site plus its test.

### Testing and quality bar

- Both `context_assembly.py` and `watermarks.py` carry property-based tests (per this repo's
  non-negotiable red/green + property-based TDD standard) that need no event store, DB, or LLM
  fixture — e.g. "for all texts and budgets, `assemble_verbatim` returns a non-empty list whose
  concatenation (accounting for overlap) covers the input" and "for all interleavings of done/
  revised events, `current_done_ids` matches a naive replay."
- Full test suite is green (`uv run pytest -W error`, TUI pilot tests via the pytest wedge recipe
  in `docs/TESTING-TUI.md`) and import-linter is clean — run in an isolated worktree, never the
  main checkout, per the standing DB-lock incident note in project memory.
- No new flake is introduced in the TUI pilot suite under load — compared against base parity per
  the existing testing-load-flakes convention, not assumed clean from a single quiet run.

### Explicitly not required for acceptance

Per Non-Requirements: no tokenizer integration, no embeddings/RAG layer, no event-log backfill or
rewrite, and no summarization-quality eval harness are required to accept this work — their
absence is not a blocking finding against this design.

## Open Questions

- **Is `CharHeuristicEstimator`'s fixed 4.0 chars/token ratio safe for this project's prose?**
  The ratio is a generic English-prose approximation baked into the reference implementation, not
  measured against this project's own chapter text or the specific local model serving requests.
  If actual token density differs meaningfully (dialogue-heavy prose, unusual punctuation density,
  non-English content), `extractor_token_budget`/`advisory_token_budget` could over- or
  under-pack windows relative to the model's real context limit. Worth a quick empirical check
  (sample a few chapters through the actual endpoint's completions usage stats, if exposed) before
  or shortly after rollout, rather than trusting the constant unexamined.

- **Does the local OpenAI-compatible endpoint expose real token counts anywhere Claude/agents
  could read them post-hoc?** If a response's `usage` field is available, `Summarizer` or the
  Keeper could log estimated-vs-actual token counts for a cheap, ongoing calibration signal
  without building a full tokenizer integration (explicitly out of scope per Non-Requirements).
  Unresolved because it depends on the specific endpoint's API surface, not inspected as part of
  this doc.

- **What is `structure_analyst.py`'s actual behavior in the (default) pull-mode-off case?**
  Proposed Approach resolves the pacing-analysis question by relying on pull mode being the
  settings default, but does not verify whether pull mode is unconditionally on for this agent, a
  per-story setting, or user-toggleable per session. If a user runs push-mode-only for any reason,
  the advisory-block fallback becomes the *only* view the analyst gets — worth confirming that
  fallback quality (summary + labeled elision) is acceptable for pacing work specifically, not just
  for narrative recap (author.py) or continuity (continuity_checker.py), before accepting this as
  resolved rather than deferred.

- **Should `chapter_map_note`'s new gist line count against any of the three token budgets?**
  The chapter-map index itself is a prompt component read by multiple agents; enriching every
  chapter's line with a `gist:` line grows that index linearly with story length. Neither this doc
  nor the reference implementation specifies a budget or truncation rule for the index itself —
  only for prose/advisory content. For a very long story (hundreds of chapters), the enriched index
  could itself become large enough to need the same disclosed-truncation treatment this protocol
  gives everything else. Not addressed here; likely fine at current expected story lengths but
  worth flagging as a latent gap rather than silently assuming it never matters.

- **Who writes the Summarizer's prompt, and against what quality bar?** Non-Requirements
  explicitly excludes a summarization-quality eval harness from this protocol's acceptance
  criteria, which is correct scoping — but it leaves open who owns verifying that `gist`/`summary`
  output is actually useful for the agents consuming it (pull-mode chapter selection, advisory
  fallback readability) versus merely present. Likely a follow-up task for whoever implements
  `novelizer/agents/summarizer.py`'s prompt, not something this design doc should resolve, but
  worth naming explicitly so it isn't lost between "spec accepted" and "prompt written."

- **Does merging multi-window Keeper output risk duplicate or conflicting `updated_characters`
  entries across windows in a way the existing commit-time dedup guards don't fully cover?**
  Proposed Approach asserts the existing slug-mint/flag-dedup/learn-only guards "already tolerate
  exactly this shape of merge," reasoning from what those guards do for the current single-call
  path — this has not been verified against an actual multi-window run producing genuinely
  conflicting updates to the same character from two windows of the same chapter (as opposed to
  merely additive lists). Worth a targeted test case (a synthetic chapter that triggers two windows
  and edits the same character's traits differently) before trusting the assertion in Acceptance
  Criteria.

- **What happens to in-flight advisory prompts if `list_chapter_summaries()` is slow or the
  projection table is large?** The design assumes this read is cheap enough to call on every
  advisory-site invocation (four sites, each per-turn), consistent with how other projection reads
  are used elsewhere in the codebase, but no explicit performance budget or caching strategy is
  stated for `chapter_summaries` specifically as story length grows. Likely fine given the existing
  precedent, but not measured here.

## Next Step (promote to docs/superpowers/plans/ once accepted, pair with milestone doc)

This doc is a design spec, not yet a plan. Before it is promoted to
`docs/superpowers/plans/`, it needs an explicit acceptance pass by Ty against every section above —
in particular the Open Questions, none of which are blocking by construction but several of which
(the multi-window Keeper merge risk, the `structure_analyst` pull-mode-off fallback quality) are
worth a deliberate "accepted as-is" or "needs a follow-up spike" decision rather than silent
carry-forward.

### What "promote" means concretely

1. **Resolve or explicitly defer each Open Question.** A question doesn't have to be answered to
   promote the doc — but each one needs a one-line disposition (answered here / deferred to
   implementation / deferred to a follow-up spec) recorded before this becomes a plan, so the plan
   doesn't inherit silent unknowns.
2. **Reconcile against the existing reference implementation rather than re-deriving it.** Nearly
   every section of this design (Proposed Approach through Acceptance Criteria) was written by
   reading the actual code on the unmerged branch `worktree-context-assembly-v2` —
   `novelizer/brain/context_assembly.py`, `novelizer/brain/watermarks.py`,
   `novelizer/agents/summarizer.py`, and `.specs/context-assembly-v2.md` in that worktree. The
   promotion step should confirm that worktree still exists and still matches this doc (a `git
   diff` against it, not a re-read from scratch) before treating it as the implementation
   starting point — the worktree may have drifted since this doc was written.
3. **Write the paired milestone doc.** Per this repo's convention (see
   `docs/MILESTONES.md`... note: currently deleted per git status at conversation start — confirm
   whether milestone tracking has moved elsewhere, e.g. `docs/superpowers/plans/`, before assuming
   the old path), a milestone doc should enumerate the six-step sequencing from Migration/Rollout
   Plan as concrete, checkable milestones (M1: pure helpers merged + property tests green; M2:
   events/table/settings landed; M3: Summarizer registered and idle-verified; M4: Keeper cutover
   + live-run character-discovery verification; M5: four advisory-site cutovers; M6: all seven
   `chapter_map_note` call sites enriched) — mirroring the granularity of past milestone docs
   referenced in project memory (e.g. `milestone-execution-state.md`'s M0→M5 structure).
4. **Convert this doc's structure into a plan via `superpowers:writing-plans`**, using the
   Migration/Rollout Plan's six steps as the plan's task breakdown and the Acceptance Criteria
   section as each task's Definition of Done — the two sections were written to already be in that
   shape (independently mergeable, independently revertable, each with its own verification
   bullets) specifically so this conversion is mechanical rather than a rewrite.
5. **File the plan for execution the way every other milestone in this repo has been**: isolated
   worktree per the standing DB-lock-incident rule in project memory (never the main checkout for
   test runs), red/green + property-based TDD per `engineering-principles.md`, and a live
   acceptance run (not just a green test suite) for the Keeper cutover specifically, since that is
   the one step whose entire justification is a previously-shipped, previously-invisible bug.

### Who should do this next

Given this doc was produced by reading a working, unmerged reference implementation rather than
designing from a blank page, the fastest accepted-to-implemented path is likely: Ty reviews and
dispositions the Open Questions in one pass, then implementation proceeds directly from the
`worktree-context-assembly-v2` branch (rebased/reconciled against current `main`, since that
worktree predates this doc and main has moved — e.g. the tui_kit migration merged since) rather
than a fresh implementation from this spec alone. The spec's job at that point is to be the
acceptance-criteria and rollout reference the implementer checks work against, not a from-scratch
blueprint.

### Cross-links

None yet — this doc does not currently link to `docs/superpowers/plans/` (nothing has been
promoted there for this work) or to a milestone doc (none exists for this work; `docs/MILESTONES.md`
itself shows as deleted in the working tree as of this writing, per git status). Both links should
be added here, and back-links added on the promoted side, once promotion happens — do not treat
their absence as an oversight to fix now; it is the expected state of an unpromoted spec.
