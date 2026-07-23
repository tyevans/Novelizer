# Spec: Context-Assembly Protocol v2 (pull-mode-first)

**Status:** IMPLEMENTED — 2026-07-22 (scope confirmed by user: "both halves, rethought for pull mode")
**Created:** 2026-07-22
**Supersedes:** `.specs/context-assembly-protocol.md` (v1, DRAFT). v1 was implemented on branch
`worktree-fix-keeper-prose-truncation` but never merged; main has since diverged (Flag API replaced
retcons, agent registry, pull-mode default). This v2 redesigns from current main rather than porting.

---

## Problem statement (what still holds from v1)

Agents truncate chapter prose with arbitrary per-agent char cutoffs, silently discarding text.
Guiding invariant: **never silently truncate**. The shipped-bug class: the Character Keeper showed
the LLM only a prose head per chapter, so characters introduced past the cutoff could never be
discovered.

## What changed since v1 (why this is a redesign, not a port)

1. **Pull mode is now the default for every agent** (`*_tools_enabled = True`,
   `settings/models.py:91-101`). Agents receive a `chapter_map_note` (index: title/status/cast, no
   prose — `brain/context.py:102-117`) and read full chapters via canon file tools
   (`read_file` returns full prose, `canon_fs/render.py:12-19`). The five `prose[:N]` slices are
   now **push-mode fallback branches**:
   - `agents/character_keeper.py:148` — `prose[:keeper_prose_chars]` (6000)
   - `agents/author.py:183` — `prose[:prior_chapter_summary_chars]` (200)
   - `agents/continuity_checker.py:188` — `prose[:300]` (retcon pass)
   - `agents/structure_analyst.py:128` — `prose[:400]`
   - `chat/service.py:122` — `prose[:200]`
2. **Coverage is not guaranteed in pull mode either.** The Keeper's pull note says "read every
   chapter new since your last pass IN FULL" but nothing enforces or records it; the tooled Keeper
   re-reads (or doesn't) on every fingerprint delta. Guaranteed discovery needs a deterministic
   push of full prose plus a per-agent processed ledger — independent of mode.
3. **Revision invalidation does not exist.** `chapter.revised` exists and is projected
   (`projector.py:203-228`, `revision_count`), but nothing removes `chapter.mined` markers — a
   revised chapter is **never re-mined** today. v2 makes done-sets revision-aware and fixes this
   for the continuity miner too.
4. **Keeper output API changed**: `KeeperOutput` now has `flags: list[FlagDraft]` +
   `arc_intents`; the retcon API is gone. Slug/alias dedup and commit-time re-reads
   (`character_keeper.py:181-260`) are the idempotency guards the mining sweep relies on.
5. **Agents are built from `AGENT_REGISTRY`** (`novelizer/agents/registry.py`) via per-module
   `SPEC: AgentSpec` and `construct(ctx)`; `substrate/agent_registry.py` defines the types.

## Scope

**Half A — guaranteed-coverage verbatim mining** for extractors (Keeper first; miner gains
revision-awareness). **Half B — event-sourced rolling chapter summaries** (new Summarizer agent)
feeding (i) the pull-mode `chapter_map_note` every tooled agent already sees, and (ii) the four
push-mode advisory sites. Both designed pull-mode-first.

---

## Design

### D1. Pure helpers — `novelizer/brain/context_assembly.py` (new, pure, sync, no I/O)

- `TokenEstimator` protocol + `CharHeuristicEstimator(chars_per_token=4.0)` default
  (`estimate(text) = ceil(len(text)/chars_per_token)`). No tokenizer dependency exists in the
  repo; the protocol is the seam for injecting a real one later.
- `@dataclass(frozen=True) Window(text: str, index: int, total: int)`.
- `assemble_verbatim(text, budget_tokens, estimator) -> list[Window]`
  - Full text fits → exactly one window.
  - Oversize → ordered overlapping windows covering the whole text, no gaps; overlap
    `budget//4` estimated tokens; step ≥ 1 token; always ≥ 1 window; always terminates.
  - Empty prose → single empty window.
- `assemble_advisory(entries, budget_tokens, estimator) -> str`
  - `entries: list[AdvisoryEntry]` where `AdvisoryEntry(label, summary | None, verbatim | None)`.
  - Packs newest-first within budget: prefer `summary`; when a chapter has no summary yet
    (Summarizer lag), fall back to a **labeled** verbatim head ending with an explicit
    `[…truncated — summary pending]` marker. Degraded but never silent. Drops oldest entries
    first when over budget, and says so (`[N earlier chapters omitted]` header line).

### D2. Revision-aware done-sets — `novelizer/brain/watermarks.py` (new, pure)

- `current_done_ids(done_events, revised_events, chapter_id_of=...) -> set[str]`
  - Folds both lists **in global sequence order**: a done-event adds its chapter id; a later
    `chapter.revised` for that chapter removes it. Crash-safe and replayable by construction —
    derived from the log every poll, never a mutable counter.
- Consumers:
  - **Continuity miner** replaces `already_mined_chapter_ids` usage with
    `current_done_ids(events_since(0,[CHAPTER_MINED]), events_since(0,[CHAPTER_REVISED]))` —
    revised chapters re-mine. (`brain/mining.py` helper stays for back-compat of its tests or is
    reimplemented atop the new fold; existing `chapter.mined` events remain valid — no migration.)
  - **Keeper** uses the new `CHAPTER_PROCESSED` event filtered to `agent == "character_keeper"`.
  - **Summarizer** uses `CHAPTER_SUMMARIZED` events (payload carries chapter_id).
  - Intent-commit dedup already re-reads state at commit; a re-mined chapter is side-effect-safe.

### D3. Events + gating — `canon/events.py`, `canon/policy.py`

- `EventType.CHAPTER_PROCESSED = "chapter.processed"`, payload
  `ChapterProcessed(agent: str, chapter_id: str)`. Not projected (dedup is a pure log read —
  same locked decision as `chapter.mined`).
- `EventType.CHAPTER_SUMMARIZED = "chapter.summarized"`, payload
  `ChapterSummarized(chapter_id: str, gist: str, summary: str)`.
  - `gist`: one line (≤ ~140 chars target, prompt-enforced) for the chapter map.
  - `summary`: one paragraph for advisory contexts.
  - Projected (D4). Re-summarizing a revised chapter emits a new event; projection upserts.
- Both events join `_NEVER_GATED` (`canon/policy.py:17-60`) and the `never` tier in
  `_build_fiction_registry` — they are bookkeeping, like `chapter.mined`.

### D4. Projection + reads — `canon/projector.py`, `canon/read_store.py`

- New table `chapter_summaries (id TEXT PRIMARY KEY, data TEXT)` in `_CREATE`; add to
  `_reset_state_locked` delete tuple; `_project` branch on `CHAPTER_SUMMARIZED` does
  `INSERT OR REPLACE` keyed by `chapter_id` (pattern: `structure_scores`, projector.py:503-507).
- `store/models.py`: `ChapterSummary(chapter_id, gist, summary)`.
- `ReadStore.list_chapter_summaries() -> list[ChapterSummary]`,
  `get_chapter_summary(chapter_id) -> ChapterSummary | None` (uniform
  `SELECT data … model_validate_json` pattern).

### D5. Summarizer agent — `novelizer/agents/summarizer.py` (new, untooled)

- Standard shape: class overriding `_run()` (poll → work → commit), `build_summarizer_runner`
  (cold temp 0.2, `ProviderStrategy(SummarizerOutput)` grammar-constrained JSON — same rationale
  as the miner, continuity_checker.py:555-560), `_construct(ctx)`, `SPEC = AgentSpec(name=
  "summarizer", tool_grant=None, construct=_construct)`.
- `poll()`: `unsummarized = [c for c in chapters if c.id not in current_done_ids(
  CHAPTER_SUMMARIZED events, CHAPTER_REVISED events)]`.
- `_fingerprint()`: `(len(chapters), latest_chapter_id, len(unsummarized))` — idle when nothing
  new; `note_pass()` on empty backlog.
- `work()`: per unsummarized chapter, **full prose** through `assemble_verbatim(prose,
  extractor_token_budget)`; single window → one call producing `SummarizerOutput(gist, summary)`;
  multi-window → summarize each window, then one merge call over the window summaries. Failure on
  a chapter → skip stamping it, retry next poll (miner convention).
- `commit()`: emit `CHAPTER_SUMMARIZED` per successful chapter, last. Exactly-once per
  (chapter, revision) by construction of D2.
- Registry order: after `structure_analyst`, before `triage` (annotation-layer worker; must not
  preempt the planner/writer). `runtime.py` `apply_settings` interval-map gains
  `"summarizer": settings.summarizer_interval`. Untooled → not in `_TOOLING_PINNED_NAMES`.
- TUI: add an `AgentIdentity` for `summarizer` in `tui/identity.py:26` (falls back gracefully
  meanwhile).

### D6. Keeper mining sweep — `agents/character_keeper.py`

Replaces the `recent[-5:]` + `prose[:6000]` push branch with watermarked full-prose mining,
**in both modes** (deterministic coverage cannot depend on what the LLM chooses to pull):

- `poll()` additionally computes `unmined = [c for c in chapters if c.id not in
  current_done_ids(CHAPTER_PROCESSED where agent=="character_keeper", CHAPTER_REVISED)]`.
- `work()`: prompt keeps the existing cast/secrets/arcs/flags blocks. The prose section becomes:
  - Pull mode: `chapter_map_note` (unchanged) **plus** full prose of unmined chapters. Tools
    stay available for cross-referencing.
  - Batching semantics (both modes): select unmined chapters oldest-first, greedily, until
    `extractor_token_budget` is spent; unselected chapters wait for the next run. Selected
    chapters ≤ budget share **one** runner call (today's shape). A single chapter larger than
    the whole budget is windowed via `assemble_verbatim` and gets one call per window, labeled
    `('{title}' part i/n)`, all within the same run so it can be stamped atomically.
  - Push mode: same unmined full-prose blocks (replacing the `prose[:6000]` slice) plus the
    existing summaries of older context via D8 where the prompt used recent chapters.
  - Multi-window outputs are merged by concatenating `new_characters`/`updated_characters`/
    `flags`/`knowledge_intents`/`arc_intents` lists; the existing commit-time guards make the
    merge safe (slug minted exactly once via `seen_ids` re-read, flag dedup by description,
    learn-only knowledge intents, arc intents keyed to `seen_ids`).
- `commit()`: after the existing commit flow succeeds, stamp `CHAPTER_PROCESSED(agent=
  "character_keeper", chapter_id)` for each chapter that was fully presented this run — stamps
  **last** (miner convention: crash before stamp → harmless re-run).
- **Bootstrap sweep is intended behavior**: first run under v2 has an empty processed set, so the
  Keeper re-reads the whole backlog in full and recovers characters the old caps dropped. Cap the
  per-run batch at `extractor_token_budget`; remaining chapters continue next run (the readiness
  fingerprint folds the unmined count, so it keeps waking until drained — same as the miner).
- `keeper_prose_chars` becomes unused → mark deprecated (kept in settings for layer-file
  compatibility; see D9).

### D7. Chapter-map enrichment (pull-mode-first payoff) — `brain/context.py`

- `chapter_map_note(chapters, summaries: Mapping[str, str] | None = None)`: when a gist exists
  for a chapter, its line gains a second indented line `    gist: {gist}`. All seven call sites
  pass `{s.chapter_id: s.gist for s in read.list_chapter_summaries()}` from their `poll()`s.
  Agents deciding what to pull now see what each chapter *is about*, not just its title.

### D8. Advisory cutover (push-mode fallbacks) — four sites

Replace the `prose[:N]` slices with `assemble_advisory(entries, advisory_token_budget)` where
`entries` pair each chapter's summary (from `list_chapter_summaries`) with its prose for the
labeled-fallback path:

- `author.py:183` — "previous chapters" recap (latest chapter stays verbatim, unchanged).
  `prior_chapter_summary_chars` deprecated.
- `continuity_checker.py:188` — retcon-pass recent-chapters block.
- `structure_analyst.py:128` — push-mode scoring block. (Pull mode already reads full chapters;
  v1's open question — pacing may need verbatim — is resolved by pull mode being the default.)
- `chat/service.py:122` — Director chat story context.

### D9. Settings — `settings/models.py`, `layers.py`, `loader.py`

New (all story-overridable, auto-surfaced in the settings TUI):
- `extractor_token_budget: int = 24000` — per-run verbatim budget (≈96k chars at chars/4;
  fits 128k-context local models with headroom for instructions + output).
- `advisory_token_budget: int = 2000` — packed story-so-far budget for D8 sites.
- `summarizer_interval: int = 300`.

Deprecated but retained (so existing story-layer files keep loading): `keeper_prose_chars`,
`prior_chapter_summary_chars` — no longer read by any code path; comment marks them deprecated.

### D10. Out of scope (unchanged from v1 non-requirements)

Editor oversize handling (single draft chapter, already full prose — windowed *editing* is a
different problem); non-prose truncations (`world_entry.body[:200]`, list caps); retrieval-ranked
windowing via embeddings; real tokenizer integration (protocol seam exists).

---

## Deviations (recorded at implementation, 2026-07-22)

- **Keeper batching**: selected unmined chapters that fit the budget share ONE runner call (today's
  prompt shape preserved); per-window calls happen only for a single chapter larger than the whole
  budget — as clarified in D6's batching semantics. `chapter.processed` is also stamped on a
  `no_action` verdict (presented-in-full-and-judged counts as processed).
- **Keeper constructor**: `event_store` is positional after `committer` (ContinuityChecker
  convention); `prose_chars` removed outright rather than deprecated in the agent.
- **Gist threading**: Author and Plotter pass gists to their module-level prompt builders via the
  existing `ctx` dict rather than new function parameters; `build_author_prompt` gained
  `advisory_budget` + `summaries` parameters replacing `prior_chapter_chars` (D8).
- **`brain/mining.already_mined_chapter_ids`** was removed with its callers migrated to
  `current_done_ids` (its unit test migrated too); `thread_touch_log` and `MINED_SOURCE_TAG` remain.
- **structure_analyst** got its `summaries` poll fetch in the D8 cutover rather than D7 (it never
  called `chapter_map_note`).
- **Known environmental issue, not a deviation**: `tests/chat/test_service.py` fails under
  `-W error` on the BASELINE too (chromadb DeprecationWarning + aiosqlite ResourceWarning); it was
  run without `-W error` during per-task verification.

## Invariants (each gets a property test)

1. **Coverage**: `assemble_verbatim` windows concatenate-cover the input — every character of the
   input appears in ≥ 1 window; consecutive windows overlap; terminates for any budget ≥ 1.
2. **Exactly-once per revision**: for any interleaving of done/revised events, a chapter is in
   the done-set iff its latest done-event is later than its latest revised-event.
3. **Replay determinism**: rebuilding the projector from sequence 0 reproduces the identical
   `chapter_summaries` table; `current_done_ids` over a replayed log equals the original.
4. **Idempotent sweep**: running the Keeper mining sweep twice over the same log mints no
   duplicate character slugs and no duplicate flags (extends the existing Hypothesis slug test).
5. **Never silent**: `assemble_advisory` output either contains a chapter's summary, or a
   fallback block containing the explicit elision marker, or an explicit omitted-count header —
   for every chapter given to it.

## Acceptance criteria

- All five `prose[:N]` sites are gone (grep-clean for `prose[:` under `novelizer/` except
  labeled-fallback construction inside `context_assembly.py`).
- A revised chapter is re-mined by the continuity miner and re-summarized by the Summarizer.
- Fresh story: Summarizer produces gist+summary per chapter; `chapter_map_note` lines show gists.
- Keeper first run over a pre-existing backlog stamps `chapter.processed` for every chapter
  within budget and continues across runs until drained.
- Full suite green in the worktree (`uv run pytest -W error`, TUI wedge recipe per
  docs/TESTING-TUI.md); import-linter clean.
