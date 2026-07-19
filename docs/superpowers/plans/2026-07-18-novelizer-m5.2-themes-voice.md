# M5.2 · Theme & Motif Tracking + Voice Enforcement Maturity — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking.

> **NEVER create a `.env` file at any point in this plan.** A past subagent broke the
> suite doing this. Any scratch/temp file goes in the job's tmp dir, never the repo.
> Live-LLM settings load through `load_effective_settings()` only — never bare
> `EffectiveSettings()`. The live model in this environment is `qwen3.6-27b-mtp`
> (`NOVELIZER_LLM_MODEL`), endpoint `NOVELIZER_LLM_BASE_URL=http://192.168.1.14:8080/v1/`
> — never hardcode this URL in a test; only rely on `load_effective_settings()` picking it
> up from env.

**Branch:** `m5.2-themes-voice` (already checked out in this worktree, based on `master`
@ `daad41a`, which includes all of M5.1 — do not fight master's external work:
`llm_max_tokens` settings-layer plumbing and scheduler crash handling are already in.)

**Goal:** Two independent-but-parallel deliverables, both closing gaps the M5-finish spec
names explicitly:

1. **Theme tracking** — a new `theme.*` event domain (`introduced`, `developed`, no
   terminal state) following the exact M3.1 (threads) / M4.1 (secrets) identity pattern:
   mint an id at `introduced` via a new `novelizer/canon/themes.py` slug sibling,
   `developed` cites an existing id (unknown id dropped with a logged warning, same rule
   as threads/secrets), fed by a `ThemesProjection`/`ReadStore.list_themes()`/`get_theme()`
   pair, surfaced as a `themes` **section** in the Story Browser Tree
   (`browser_sections()`), **not** a tab and **not** a Brain view/analyzer.
2. **Voice drift maturity** — the Editor's structured output (`EditorVerdict`) gains a
   `voice_drift_flags` field; each flag commits a `retcon_request.created` event tagged
   `VOICE_SOURCE_TAG = "[source: voice_drift]"`, citing the specific voice-card trait
   violated — reusing the exact `retcon_request.created` seam M4.2 established for leaks/
   paradoxes and M5.1 for mined facts, not a parallel notification path.

**Architecture:**
- `novelizer/canon/themes.py` — `slugify_theme_name(name: str) -> str`, byte-identical
  logic to `slugify_thread_name`/`slugify_secret_name` (lowercase, collapse non-
  alphanumeric runs to `-`, strip; empty→`"theme"`).
- `novelizer/canon/events.py`: `EventType.THEME_INTRODUCED = "theme.introduced"`,
  `EventType.THEME_DEVELOPED = "theme.developed"`; `ThemeIntroduced(BaseModel)`
  (`id, title, chapter_id="", note="", source="declared"`), `ThemeDeveloped(BaseModel)`
  (`id, chapter_id="", note="", source="declared"`) — `source` field lands from day one
  (see Decision Note D1 below on why, given M5.1's precedent and M5.3's planned mining
  extension).
- `novelizer/canon/policy.py`: `THEME_INTRODUCED`, `THEME_DEVELOPED` added to
  `_NEVER_GATED` (bookkeeping class, same as `thread.*`).
- `novelizer/store/models.py`: `ThemeRecord(BaseModel)` — `id, title, touch_count=0,
  last_note="", last_chapter_id=""` (no `state` field — themes have no lifecycle states
  to track, unlike `ThreadRecord.state`; this is the one structural difference from the
  thread precedent, per Locked decision 6's "no terminal state").
- `novelizer/canon/projector.py`: new `themes` table (`id TEXT PRIMARY KEY, data TEXT NOT
  NULL`, no `state` column — nothing to index on), `_apply` branches for
  `THEME_INTRODUCED` (first-mint-wins, mirrors `THREAD_PLANTED`'s existing-row check) and
  `THEME_DEVELOPED` (bump `touch_count`, update `last_note`/`last_chapter_id` — mirrors
  `THREAD_TOUCHED` minus the terminal-state guard, since themes have none).
- `novelizer/canon/read_store.py`: `list_themes() -> list[ThemeRecord]`,
  `get_theme(theme_id) -> Optional[ThemeRecord]` — byte-identical shape to
  `list_threads`/`get_thread`.
- `novelizer/agents/schemas.py`: `ThemeIntent(BaseModel)` — `action: Literal["introduce",
  "develop"], title: str = "", id: str = "", note: str = ""` (mirrors `ThreadIntent`
  exactly, `"introduce"` instead of `"plant"`, no `"pay_off"`/`"abandon"`). Added as
  `theme_intents: list[ThemeIntent] = Field(default_factory=list)` to `EditorVerdict` and
  to `AuthorOutput`-equivalent (confirm exact Author output schema name during Task 1 —
  see Decision Note D2).
- `novelizer/agents/base.py`: `BaseAgent._commit_theme_intents(intents, active_theme_ids,
  chapter_id="", source="declared")` — third sibling of `_commit_thread_intents`/
  `_commit_knowledge_intents`, same collision/dedup/drop-unknown-with-warning rules,
  `"introduce"` mints via `slugify_theme_name`, `"develop"` cites an existing id.
- `novelizer/agents/schemas.py`: `EditorVerdict.voice_drift_flags: list[VoiceDriftFlag] =
  Field(default_factory=list)`; `VoiceDriftFlag(BaseModel)` — `character_id: str, line:
  str, trait_violated: str, note: str = ""`.
- `novelizer/agents/editor.py`: `VOICE_SOURCE_TAG = "[source: voice_drift]"` module
  constant (this module owns voice enforcement, parallel to `leaks.py` owning
  `LEAK_SOURCE_TAG`); `commit()` gains a branch that, for each `voice_drift_flags` entry,
  commits a `RetconRequest(description=f"{VOICE_SOURCE_TAG} {trait_violated}: {note}
  (character {character_id}, line: \"{line}\")", conflicting_entry_ids=[character_id],
  proposed_resolution="")` via `EventType.RETCON_REQUEST_CREATED` — same call shape
  `continuity_checker.py` already uses (see Task 5).
- `novelizer/tui/widgets/browser_model.py`: `browser_sections()` gains a fourth section,
  `{"key": "themes", "label": f"Themes ({len(themes)})", "items": [{"id": t.id, "label":
  f"{t.title} (touched {t.touch_count}x)"} for t in themes]}`; `detail_text()` gains a
  `"themes"` branch.

**Tech Stack:** Python 3.13, `pydantic` v2, `aiosqlite`, `pytest`+`pytest-asyncio`
(`asyncio_mode=auto`), `hypothesis>=6.156.6`.

## Global Constraints

- Event sourcing: theme identity is minted exactly once, at `theme.introduced` — no other
  `theme.*` event mints or re-derives an id (same rule as `thread.planted`/
  `secret.created`). `theme.developed` citing an unknown id is dropped with a logged
  warning, never silently invented (same rule M3.1/M4.1 established, **not** M5.1's
  mining-escalate rule — themes are self-declared by Author/Editor, not mined, so the
  drop-and-warn rule applies here, not the escalate-to-retcon rule).
- No new autonomy-policy gating class: `theme.*` joins `_NEVER_GATED` exactly like
  `thread.*`. Voice-drift retcons need **no new policy entry** — `retcon_request.created`
  is already ungated below `gated_all` (M4 Locked decision #5, reconfirmed by this
  milestone's spec).
- `_commit_theme_intents` is a **separate method** from `_commit_thread_intents`, not a
  generic reuse — same rationale M4.1 gave for not literally sharing thread's method with
  secrets: different payload models, different event types, different collision
  semantics (no terminal-state guard for themes).
- TDD, black-box-first: every task starts with a failing test on `ReadStore`/`Committer`-
  visible output, not internals. Hypothesis property tests generalize the
  introduced→developed* monotonicity invariant and rebuild equivalence.
- Every new runner builder passes `max_tokens=settings.llm_max_tokens` (M5.1 live lesson
  — this domain adds no new runner builder, since theme intents ride the existing Author/
  Editor runners; the only runner-builder-adjacent action this plan takes is Task 8's fix
  to `test_runner_builders_pass_llm_max_tokens`, which is a **pre-existing gap** the M5.1
  branch left behind — the mining builder, `build_continuity_mining_runner`, is present in
  `novelizer/agents/continuity_checker.py` but missing from
  `tests/agents/test_llm.py`'s parametrize list. This plan fixes that gap since it's
  cheap, adjacent, and flagged explicitly in the decomposition brief).
- Do **not** create any `.env` file. Every task ends by running the **full** suite
  (`uv run pytest`) and reporting real failures.

## Decision notes (flagging where the decomposition needs a call this plan makes)

**D1 — `source` field on theme payloads from day one.** The decomposition doesn't say
whether `ThemeIntroduced`/`ThemeDeveloped` should carry `source: str = "declared"` the way
M5.1 retrofitted it onto six existing payload models. This plan adds it **from day one**
(Task 2) rather than retrofitting later, because: (a) M5.3's plan (per the spec's Locked
decision 8/M5.3 row) already commits to extending `EmbeddingStore` to themes, and a
themes-mining extension is explicitly named in M5-finish.md's non-goals list as "a
natural follow-on once M5.1's pattern is proven, not a day-one requirement" — meaning it
is anticipated, just not required now; (b) adding the field now costs nothing (every
payload model in this codebase defaults it and no model sets `extra="forbid"`, so this is
free/replay-compatible by the same argument M5.1's Locked decision #1 already verified);
(c) retrofitting it later would require a second replay-compatibility property test pass
exactly like M5.1 Task 8, which is strictly more work than doing it now. **Recommendation
adopted**: `source` ships on both theme payload models from Task 2, defaulted
`"declared"`, unused by any commit path in this plan (theme intents are always
self-declared by Author/Editor in M5.2 — nothing in this plan ever passes
`source="mined"`), documented as forward-compatible plumbing for a hypothetical future
mining extension, not a M5.2 behavioral claim.

**D2 — Which agent(s) get `theme_intents`.** The decomposition says "Author/Editor gain
an optional `theme_intents` field." `EditorVerdict` is confirmed (`novelizer/agents/
schemas.py:94`). The Author's structured-output schema needs to be located and confirmed
by name in Task 1 before any schema edit — **do this via `grep -n "class.*Output"
novelizer/agents/schemas.py` and cross-reference `novelizer/agents/author.py`'s
`response_format=` argument to `build_author_runner`** (this plan does not assume the
exact class name sight-unseen; Task 1's first step is this confirmation, and if the
Author's schema turns out to already be `EditorVerdict`-shaped or named something this
plan didn't anticipate, adjust the field-addition target accordingly and note the
deviation in the task's commit message).

**D3 — ProviderStrategy for the Editor's structured output.** The decomposition brief
asks for a recommendation, not a silent switch, because the Editor participates in the
agent loop with tools (`create_deep_agent(..., response_format=EditorVerdict)` with no
`ProviderStrategy` wrapper today — confirmed at `novelizer/agents/editor.py`'s
`build_editor_runner`), unlike the mining pass (`continuity_checker.py`'s
`build_continuity_mining_runner`, single-shot no-tools, **already** wrapped in
`ProviderStrategy(MinedFactsOutput)`, confirmed at `novelizer/agents/
continuity_checker.py:295`). **Recommendation: do not switch the Editor's strategy in this
plan.** Rationale: `ProviderStrategy` forces grammar-constrained `json_schema` output,
which is well-suited to a single-shot no-tool call (mining) but is a bigger behavioral
change for an agent mid-agent-loop with tool calls available — it could constrain or
interact with tool-call formatting in ways this plan has not scoped or tested, and the
decomposition brief explicitly frames this as "think about it and make a recommendation
rather than silently switching," not a request to switch. This plan adds
`voice_drift_flags` to the existing unconstrained `EditorVerdict` schema and defers any
`ProviderStrategy` change to a follow-up **only if** Task 9's live smoke (Task 9 below)
is observed to flake with `structured_response=None` — Task 9's steps include an explicit
checkpoint for this contingency (see Task 9's Step 3 note) rather than pre-emptively
applying the fix M5.1 needed for a structurally different call shape.

## Sequencing hazards flagged up front

1. **`slugify_theme_name` (Task 1) must land before any payload/schema/agent task
   references it** — same ordering as M5.1's Task 4-before-Task 5 precedent. Task 1 is
   pure, no DB, independently testable in isolation.
2. **Payload models + `EventType` + `_NEVER_GATED` registration (Task 2) must land before
   `ThemesProjection`'s `_apply` branches (Task 3) reference `EventType.THEME_INTRODUCED`/
   `THEME_DEVELOPED`**, and before `ThemeRecord`/`ReadStore` accessors (also Task 3) can be
   exercised end-to-end. This mirrors M5.1's Task 2-before-Task 5 pattern.
3. **`ThemeIntent` schema (Task 4) must land before `_commit_theme_intents` (Task 5)
   references it**, and `_commit_theme_intents` must land before Author/Editor wiring
   (Task 6) calls it — same plumbing-before-feature ordering as every prior sub-milestone
   in this codebase.
4. **Voice drift (Tasks 7) is fully independent of theme tracking (Tasks 1-6)** — no
   shared files, no shared event types. These two halves of M5.2 could be parallelized
   across two branches in principle, but this plan sequences them serially (themes first)
   because the browser-section task (Task 6.5, folded into Task 6) benefits from
   `list_themes()` already existing, and because a single linear task list is easier to
   execute with `subagent-driven-development`'s fresh-subagent-per-task model than
   coordinating two interleaved branches. If time pressure demands parallelization, Tasks
   7-8 (voice drift) have zero file overlap with Tasks 1-6 (themes) and can run as a
   second parallel track.
5. **D2's Author-schema confirmation (Task 1) gates Task 6** — if the Author's structured
   output schema name or shape differs from what this plan assumes, Task 6 must adapt; do
   not skip the confirmation step to save time.
6. **Task 9 (voice-drift live smoke) depends on Task 7 (schema) and Task 8 (Editor
   commit wiring) both being green**, and is the only task in this plan requiring the live
   endpoint — sequence it last among the voice-drift tasks, matching M5.1 Task 11's
   position as the final environment-dependent task.

---

### Task 1: `slugify_theme_name` + confirm Author's structured-output schema name (D2)

**Files:**
- Create: `novelizer/canon/themes.py`
- Test: `tests/canon/test_themes.py`

**Interfaces:**
- `slugify_theme_name(name: str) -> str` — byte-identical algorithm to
  `slugify_thread_name`/`slugify_secret_name` (lowercase, `_SLUG_RE = re.compile(r"[^a-z0-9]+")`
  substitution to `-`, strip leading/trailing `-`; empty input → `"theme"`).

- [ ] **Step 0: Confirm the Author's structured-output schema (D2)**

Run:
```bash
grep -n "response_format=" novelizer/agents/author.py
grep -n "^class.*Output\|^class.*Draft\|^class.*Verdict" novelizer/agents/schemas.py
```
Record the exact class name and its current field list in this task's commit message
(e.g. "confirmed: Author uses `AuthorOutput` with fields X, Y, Z") — this unblocks Task 6
without another lookup.

- [ ] **Step 1: Write the failing tests**

Create `tests/canon/test_themes.py`, mirroring `tests/canon/test_threads.py` (read that
file first for exact style):

```python
from novelizer.canon.themes import slugify_theme_name


def test_slugify_theme_name_lowercases_and_hyphenates():
    assert slugify_theme_name("The Cost of Ambition") == "the-cost-of-ambition"


def test_slugify_theme_name_collapses_punctuation():
    assert slugify_theme_name("Loyalty & Betrayal!!") == "loyalty-betrayal"


def test_slugify_theme_name_empty_falls_back():
    assert slugify_theme_name("   ") == "theme"


def test_slugify_theme_name_strips_leading_trailing_hyphens():
    assert slugify_theme_name("-- redemption --") == "redemption"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/canon/test_themes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'novelizer.canon.themes'`.

- [ ] **Step 3: Implement**

Create `novelizer/canon/themes.py`, copying `novelizer/canon/threads.py`'s
`slugify_thread_name` verbatim except for the function name, docstring (cite
`theme.introduced` and this plan/Locked decision 6 instead of `thread.planted`), and the
`"thread"` → `"theme"` fallback string.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/canon/test_themes.py -v`
Expected: PASS. Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/themes.py tests/canon/test_themes.py
git commit -m "feat: slugify_theme_name — third sibling of thread/secret slug helpers, plus Author schema confirmation for M5.2 Task 6"
```

---

### Task 2: `theme.introduced`/`theme.developed` event types, payload models, `_NEVER_GATED` registration

**Files:**
- Modify: `novelizer/canon/events.py`, `novelizer/canon/policy.py`
- Test: `tests/canon/test_policy.py` (or wherever the existing `chapter.mined`
  never-gated test lives — check first with `grep -rn "is_gated.*CHAPTER_MINED" tests/`)

**Interfaces:**
- `EventType.THEME_INTRODUCED = "theme.introduced"`, `EventType.THEME_DEVELOPED =
  "theme.developed"`.
- `ThemeIntroduced(BaseModel)`: `id: str, title: str, chapter_id: str = "", note: str =
  "", source: str = "declared"` (see Decision Note D1).
- `ThemeDeveloped(BaseModel)`: `id: str, chapter_id: str = "", note: str = "", source: str
  = "declared"`.
- Both added to `AutonomyPolicy._NEVER_GATED` in `policy.py`.

- [ ] **Step 1: Write the failing tests**

```bash
grep -rn "is_gated.*CHAPTER_MINED\|is_gated.*THREAD_PLANTED" tests/canon/
```
Append to whichever file that search finds:

```python
async def test_theme_introduced_is_never_gated(stack):
    from novelizer.canon.events import EventType
    from novelizer.canon.policy import AutonomyPolicy
    events, proj, read, committer = stack
    policy = AutonomyPolicy(read)
    assert await policy.is_gated("author", EventType.THEME_INTRODUCED) is False


async def test_theme_developed_is_never_gated(stack):
    from novelizer.canon.events import EventType
    from novelizer.canon.policy import AutonomyPolicy
    events, proj, read, committer = stack
    policy = AutonomyPolicy(read)
    assert await policy.is_gated("editor", EventType.THEME_DEVELOPED) is False
```

Match the exact fixture name (`stack` or whatever the file already uses) — read the file
in full before appending.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/canon/ -v -k theme`
Expected: FAIL — `AttributeError: type object 'EventType' has no attribute 'THEME_INTRODUCED'`.

- [ ] **Step 3: Implement**

In `novelizer/canon/events.py`, add the two `EventType` constants (alongside
`CHAPTER_MINED`, same section) and the two payload models (alongside `ThreadPlanted`/
`ThreadTouched`, matching their docstring style and citing this plan + Locked decision 6
for the no-terminal-state design).

In `novelizer/canon/policy.py`, add both to `_NEVER_GATED`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/canon/ -v`
Expected: PASS. Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/events.py novelizer/canon/policy.py tests/canon/
git commit -m "feat: register theme.introduced/theme.developed as never-gated event types"
```

---

### Task 3: `ThemeRecord`, `ThemesProjection` fold, `ReadStore.list_themes`/`get_theme`

**Files:**
- Modify: `novelizer/store/models.py`, `novelizer/canon/projector.py`,
  `novelizer/canon/read_store.py`
- Test: `tests/canon/test_projector.py` (append, matching existing `THREAD_PLANTED`/
  `THREAD_TOUCHED` fold-test style — read that section first)

**Interfaces:**
- `ThemeRecord(BaseModel)` in `novelizer/store/models.py`: `id: str, title: str,
  touch_count: int = 0, last_note: str = "", last_chapter_id: str = ""` — no `state`
  field (Locked decision 6: no terminal state to track).
- New `themes` table in `Projector._CREATE`: `CREATE TABLE IF NOT EXISTS themes (id TEXT
  PRIMARY KEY, data TEXT NOT NULL);` (no `state` column — nothing to index on, unlike
  `threads`).
- `Projector._apply` gains a `THEME_INTRODUCED` branch (first-mint-wins, mirrors
  `THREAD_PLANTED`'s existing-row check, no `state` field to set) and a `THEME_DEVELOPED`
  branch (bump `touch_count` by 1, update `last_note`/`last_chapter_id` — no terminal-
  state guard needed, since themes have none; if no row exists yet for the cited id, no-op
  — same "shouldn't happen under correct agent behavior" comment style as
  `THREAD_TOUCHED`'s else-branch).
- `Projector._reset_state` gains `"themes"` in its table-clear list.
- `ReadStore.list_themes() -> list[ThemeRecord]`, `ReadStore.get_theme(theme_id: str) ->
  Optional[ThemeRecord]` — byte-identical shape to `list_threads`/`get_thread`.

- [ ] **Step 1: Write the failing tests**

Read `tests/canon/test_projector.py` in full first for its fixture/seeding style around
`THREAD_PLANTED`/`THREAD_TOUCHED`. Append:

```python
async def test_theme_introduced_creates_a_theme_record(stack):
    events, proj, read, committer = stack
    from novelizer.canon.events import EventType, ThemeIntroduced
    await committer.commit("author", EventType.THEME_INTRODUCED, "t1",
                            ThemeIntroduced(id="t1", title="Loss of Innocence"))
    await proj.catch_up()
    theme = await read.get_theme("t1")
    assert theme is not None and theme.title == "Loss of Innocence" and theme.touch_count == 0


async def test_theme_developed_increments_touch_count(stack):
    events, proj, read, committer = stack
    from novelizer.canon.events import EventType, ThemeIntroduced, ThemeDeveloped
    await committer.commit("author", EventType.THEME_INTRODUCED, "t1", ThemeIntroduced(id="t1", title="Loss"))
    await committer.commit("editor", EventType.THEME_DEVELOPED, "t1", ThemeDeveloped(id="t1", chapter_id="c1", note="revisited"))
    await proj.catch_up()
    theme = await read.get_theme("t1")
    assert theme.touch_count == 1 and theme.last_chapter_id == "c1" and theme.last_note == "revisited"


async def test_theme_introduced_id_minted_exactly_once(stack):
    events, proj, read, committer = stack
    from novelizer.canon.events import EventType, ThemeIntroduced
    await committer.commit("author", EventType.THEME_INTRODUCED, "t1", ThemeIntroduced(id="t1", title="First"))
    await committer.commit("author", EventType.THEME_INTRODUCED, "t1", ThemeIntroduced(id="t1", title="Second"))
    await proj.catch_up()
    theme = await read.get_theme("t1")
    assert theme.title == "First"  # first-mint-wins, same rule as thread.planted


async def test_theme_developed_on_unknown_id_is_a_projection_noop(stack):
    events, proj, read, committer = stack
    from novelizer.canon.events import EventType, ThemeDeveloped
    await committer.commit("editor", EventType.THEME_DEVELOPED, "ghost", ThemeDeveloped(id="ghost"))
    await proj.catch_up()
    assert await read.get_theme("ghost") is None


async def test_list_themes_returns_all_themes(stack):
    events, proj, read, committer = stack
    from novelizer.canon.events import EventType, ThemeIntroduced
    await committer.commit("author", EventType.THEME_INTRODUCED, "t1", ThemeIntroduced(id="t1", title="A"))
    await committer.commit("author", EventType.THEME_INTRODUCED, "t2", ThemeIntroduced(id="t2", title="B"))
    await proj.catch_up()
    themes = await read.list_themes()
    assert {t.id for t in themes} == {"t1", "t2"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/canon/test_projector.py -v -k theme`
Expected: FAIL — `AttributeError: 'ReadStore' object has no attribute 'get_theme'` (or
similar, once `ThemeIntroduced`/`ThemeDeveloped` import succeeds from Task 2).

- [ ] **Step 3: Implement**

In `novelizer/store/models.py`, add `ThemeRecord` near `ThreadRecord`, with a docstring
explicitly noting the absence of a `state` field versus `ThreadRecord` and citing Locked
decision 6.

In `novelizer/canon/projector.py`:
- Add `CREATE TABLE IF NOT EXISTS themes (id TEXT PRIMARY KEY, data TEXT NOT NULL);` to
  `_CREATE`.
- Add `"themes"` to `_reset_state`'s table list.
- Add the two `_apply` branches (`THEME_INTRODUCED`, `THEME_DEVELOPED`) immediately after
  the existing thread branches, following their exact control-flow shape (existing-row
  check via `SELECT id/data FROM themes WHERE id=?`, `INSERT OR REPLACE` on write).

In `novelizer/canon/read_store.py`, add `list_themes`/`get_theme` immediately after
`list_threads`/`get_thread`, importing `ThemeRecord` from `novelizer.store.models`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/canon/test_projector.py -v`
Expected: PASS. Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/store/models.py novelizer/canon/projector.py novelizer/canon/read_store.py tests/canon/test_projector.py
git commit -m "feat: ThemesProjection fold + ReadStore.list_themes/get_theme — introduced/developed, no terminal state"
```

---

### Task 4: `ThemeIntent` schema

**Files:**
- Modify: `novelizer/agents/schemas.py`
- Test: `tests/agents/test_schemas.py` (or wherever `ThreadIntent`'s tests live — check
  first)

**Interfaces:**
- `ThemeIntent(BaseModel)`: `action: Literal["introduce", "develop"], title: str = "", id:
  str = "", note: str = ""` — mirrors `ThreadIntent` exactly except the action vocabulary
  (`"introduce"`/`"develop"` instead of `"plant"/"touch"/"pay_off"/"abandon"` — no
  terminal actions).

- [ ] **Step 1: Write the failing tests**

```bash
grep -rln "ThreadIntent" tests/agents/
```
Append to whichever file that finds (or create `tests/agents/test_theme_schemas.py` if
`ThreadIntent` has no dedicated schema test file — in that case `ThreadIntent` is likely
only exercised indirectly via `test_base.py`/`test_editor.py`; match whichever pattern
exists):

```python
def test_theme_intent_introduce_action():
    from novelizer.agents.schemas import ThemeIntent
    intent = ThemeIntent(action="introduce", title="The Weight of Secrets")
    assert intent.action == "introduce" and intent.id == ""


def test_theme_intent_develop_action_cites_id():
    from novelizer.agents.schemas import ThemeIntent
    intent = ThemeIntent(action="develop", id="the-weight-of-secrets", note="revisited in ch3")
    assert intent.action == "develop" and intent.id == "the-weight-of-secrets"


def test_theme_intent_rejects_terminal_actions():
    import pytest, pydantic
    from novelizer.agents.schemas import ThemeIntent
    with pytest.raises(pydantic.ValidationError):
        ThemeIntent(action="pay_off", id="t1")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/ -v -k theme_intent`
Expected: FAIL — `ImportError: cannot import name 'ThemeIntent'`.

- [ ] **Step 3: Implement**

Add `ThemeIntent` to `novelizer/agents/schemas.py` immediately after `ThreadIntent`,
docstring citing this plan and Locked decision 6's action vocabulary.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/ -v -k theme_intent`
Expected: PASS. Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/schemas.py tests/agents/
git commit -m "feat: ThemeIntent schema — introduce/develop action vocabulary, no terminal actions"
```

---

### Task 5: `BaseAgent._commit_theme_intents`

**Files:**
- Modify: `novelizer/agents/base.py`
- Test: `tests/agents/test_base.py`

**Interfaces:**
- `BaseAgent._commit_theme_intents(intents: list[ThemeIntent], active_theme_ids: set[str],
  chapter_id: str = "", source: str = "declared") -> None` — third sibling of
  `_commit_thread_intents`/`_commit_knowledge_intents`. `"introduce"` mints via
  `slugify_theme_name(intent.title)`, dropped only if title is blank; a mint colliding
  with an already-active id downgrades to a `develop` (same reasoning M3.1 gave for
  thread-plant-collision — "the agent clearly means this theme is live," see
  `_commit_thread_intents`'s existing collision-downgrade comment for the exact
  precedent to mirror). `"develop"` must cite an id present in `active_theme_ids`; an
  intent citing an unknown id is dropped with a logged warning (drop-and-warn, **not**
  escalate — themes are self-declared, this is the M3.1/M4.1 rule, not M5.1's mining
  rule). No-op on an empty list.

- [ ] **Step 1: Write the failing tests**

Read `tests/agents/test_base.py` in full first (already read for Task 1's `_commit_thread_intents`
tests if this plan's executor is following file order — re-read regardless, since exact
fixture/test-double style must match). Append:

```python
async def test_commit_theme_intents_introduce_mints_id(stack_or_fixture):
    # ThemeIntent(action="introduce", title="Loss of Innocence"), call
    # agent._commit_theme_intents([intent], set()), catch up, assert
    # read.get_theme("loss-of-innocence") is not None.
    ...


async def test_commit_theme_intents_develop_cites_existing_id(stack_or_fixture):
    # Seed a theme.introduced for "t1", call _commit_theme_intents with
    # ThemeIntent(action="develop", id="t1"), assert touch_count == 1.
    ...


async def test_commit_theme_intents_develop_unknown_id_dropped_with_warning(stack_or_fixture, caplog):
    # ThemeIntent(action="develop", id="ghost"), active_theme_ids=set();
    # assert no theme.developed event committed and a warning is logged.
    ...


async def test_commit_theme_intents_introduce_collision_downgrades_to_develop(stack_or_fixture):
    # Seed theme.introduced for "t1" (title "Loss"), then call
    # _commit_theme_intents([ThemeIntent(action="introduce", title="Loss")],
    # active_theme_ids={"t1"}); assert a theme.developed (not a second
    # theme.introduced) event lands for "t1" -- mirrors
    # test_commit_thread_intents' plant-collision-downgrade test, find and
    # match that test's exact assertion style.
    ...


async def test_commit_theme_intents_accepts_explicit_source(stack_or_fixture):
    # ThemeIntent(action="introduce", title="X"), source="mined" (forward-
    # compat check only -- M5.2 never actually calls this with source=
    # "mined", see Decision Note D1); assert payload["source"] == "mined".
    ...
```

Use the exact fixture/stack pattern already present in the file.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_base.py -v -k theme`
Expected: FAIL — `AttributeError: 'Author' object has no attribute '_commit_theme_intents'`
(or whichever bare-agent test double the file uses).

- [ ] **Step 3: Implement**

Add `_commit_theme_intents` to `novelizer/agents/base.py` immediately after
`_commit_thread_intents`, mirroring its control flow (collision-downgrade branch,
unknown-id drop-and-warn branch) but referencing `ThemeIntroduced`/`ThemeDeveloped`/
`slugify_theme_name` instead of the thread equivalents, and with no terminal-state
check anywhere in the method (themes have none).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_base.py -v`
Expected: PASS. Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/base.py tests/agents/test_base.py
git commit -m "feat: BaseAgent._commit_theme_intents — third sibling of thread/knowledge intent commit helpers, no terminal state"
```

---

### Task 6: Wire `theme_intents` into Author and Editor + Story Browser `themes` section

**Files:**
- Modify: `novelizer/agents/schemas.py` (Author's output schema — exact name confirmed
  in Task 1 Step 0; `EditorVerdict` in the same file), `novelizer/agents/author.py`,
  `novelizer/agents/editor.py`, `novelizer/tui/widgets/browser_model.py`
- Test: `tests/agents/test_author.py`, `tests/agents/test_editor.py`,
  `tests/tui/test_browser_model.py`

**Interfaces:**
- Author's output schema and `EditorVerdict` both gain `theme_intents: list[ThemeIntent]
  = Field(default_factory=list)`.
- `Author.commit()`/`Editor.commit()` gain a `_commit_theme_intents` call, mirroring the
  existing `_commit_thread_intents` call site exactly: `active_theme_ids = {t.id for t in
  ctx["themes"]}` (both agents' `poll()` need a new `"themes": await
  self._read.list_themes()` key — add it next to the existing `"threads"` key in each
  agent's `poll()` dict), then `await self._commit_theme_intents(verdict_or_output.theme_intents,
  active_theme_ids, chapter_id=ch.id)`.
- `browser_sections()` gains a `themes` section (fourth sibling, after `retcons` per the
  spec's "fourth-sibling section" wording — or wherever reads naturally alongside
  chapters/characters/world; match the existing section-ordering convention, which
  appears to be creation-chronology-adjacent — place `themes` last since it's the newest
  domain, consistent with how `retcons` was likely appended when M4 landed it, confirm by
  checking `git log -p` on this function if ordering rationale is unclear).
- `detail_text()` gains a `"themes"` branch: `f"{theme.title}\n\nTouched {touch_count}x.
  Last note: {last_note}"` or similar — match the existing branches' terseness.

- [ ] **Step 1: Write the failing tests**

For `tests/agents/test_author.py` and `tests/agents/test_editor.py`: read both files in
full first for their `FakeRunner`/fixture conventions (already read during this plan's
own research — re-read to confirm current state, since master may have drifted). Append
one test per agent:

```python
async def test_author_commits_theme_introduce_intent(stack):  # match fixture name
    # FakeRunner returns AuthorOutput(..., theme_intents=[ThemeIntent(action="introduce", title="Loss")])
    # run_once(); assert read.get_theme("loss") is not None.
    ...


async def test_editor_commits_theme_develop_intent(stack):
    # Seed a theme.introduced for "loss". FakeRunner returns
    # EditorVerdict(verdict="approve", theme_intents=[ThemeIntent(action="develop", id="loss")]).
    # run_once(); assert theme's touch_count == 1.
    ...
```

For `tests/tui/test_browser_model.py`: read it in full first (already read during
research). Append:

```python
async def test_browser_sections_includes_themes(stack):  # match fixture pattern
    # Seed a theme.introduced event, catch up, call browser_sections(read).
    # Assert a section with key "themes" exists with the right label/items shape.
    ...


async def test_detail_text_renders_theme():
    # Seed a theme, call detail_text(read, "themes", theme_id).
    # Assert the title appears in the returned string.
    ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/agents/test_author.py tests/agents/test_editor.py tests/tui/test_browser_model.py -v -k theme
```
Expected: FAIL — `TypeError: unexpected keyword argument 'theme_intents'` (schema field
missing) or `KeyError: 'themes'` (poll/browser_sections missing the key).

- [ ] **Step 3: Implement**

Add `theme_intents` field to both schemas. In `author.py` and `editor.py`, add
`"themes": await self._read.list_themes()` to `poll()`, and the `_commit_theme_intents`
call in `commit()` immediately after the existing `_commit_thread_intents` call, using
the same `chapter_id=ch.id` argument the neighboring thread call already passes.

In `browser_model.py`, add the `themes` section fetch (`themes = await
read.list_themes()`) and its entry in the returned list, plus the `detail_text()` branch.

- [ ] **Step 4: Run tests to verify they pass**

Run the same targeted command from Step 2, then `uv run pytest tests/ -v` for the full
suite green — pay attention to any existing browser/roster test that asserts an exact
section count or list shape (`test_browser_widget.py`, `test_app_layout.py`) and update
it for the new fourth section if it breaks on a hardcoded count.

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/schemas.py novelizer/agents/author.py novelizer/agents/editor.py novelizer/tui/widgets/browser_model.py tests/agents/test_author.py tests/agents/test_editor.py tests/tui/test_browser_model.py
git commit -m "feat: Author/Editor theme_intents wiring + themes section in Story Browser tree"
```

---

### Task 7: Property tests — theme monotonicity + rebuild equivalence

**Files:**
- Test: `tests/canon/test_themes_projection_property.py` (new file, mirroring
  `tests/canon/test_threads_projection_property.py` — read that file in full first for
  its exact Hypothesis strategy/falsification-check convention)

**Interfaces:**
- Consumes: everything from Tasks 1-3 (ThemesProjection fully wired by this point).

- [ ] **Step 1: Write the failing tests**

Read `tests/canon/test_threads_projection_property.py` in full. Write the theme
equivalent with two properties:

```python
from hypothesis import given, settings as hyp_settings, strategies as st


@given(
    action_sequence=st.lists(
        st.sampled_from(["introduce", "develop", "develop_unknown"]), min_size=1, max_size=15
    )
)
@hyp_settings(max_examples=30, deadline=None)
async def test_theme_state_is_monotonic_appending(stack, action_sequence):
    """Falsification check: a theme's touch_count only ever increases (or
    stays flat on a dropped/unknown-id develop) across any sequence of
    introduce/develop actions, and the record, once introduced, is never
    deleted or reset -- no terminal state exists to protect (Locked
    decision 6), so this property is simpler than ThreadsProjection's
    lattice: there is no 'absorbing state' branch to falsify against,
    only 'touch_count never decreases and the row never disappears.'
    If this test ever fails by finding touch_count decreasing or the row
    vanishing, that is a real projection bug -- do not weaken the
    assertion to make it pass.
    """
    events, proj, read, committer = stack
    from novelizer.canon.events import EventType, ThemeIntroduced, ThemeDeveloped
    theme_id = "t1"
    introduced = False
    last_count = 0
    for action in action_sequence:
        if action == "introduce" and not introduced:
            await committer.commit("author", EventType.THEME_INTRODUCED, theme_id,
                                    ThemeIntroduced(id=theme_id, title="Test Theme"))
            introduced = True
        elif action == "develop" and introduced:
            await committer.commit("editor", EventType.THEME_DEVELOPED, theme_id,
                                    ThemeDeveloped(id=theme_id))
        elif action == "develop_unknown":
            await committer.commit("editor", EventType.THEME_DEVELOPED, "nonexistent-id",
                                    ThemeDeveloped(id="nonexistent-id"))
        await proj.catch_up()
        if introduced:
            theme = await read.get_theme(theme_id)
            assert theme is not None
            assert theme.touch_count >= last_count
            last_count = theme.touch_count


@given(action_count=st.integers(min_value=0, max_value=10))
@hyp_settings(max_examples=20, deadline=None)
async def test_theme_projection_rebuild_equivalence(stack, action_count):
    """Falsification check: replaying the full theme.* event log from
    scratch (Projector._reset_state then catch_up) produces byte-identical
    ThemeRecord rows to the live-folded state -- the read model is a pure
    function of the log, same invariant M3.1 established for threads.
    """
    events, proj, read, committer = stack
    from novelizer.canon.events import EventType, ThemeIntroduced, ThemeDeveloped
    await committer.commit("author", EventType.THEME_INTRODUCED, "t1", ThemeIntroduced(id="t1", title="Rebuild Test"))
    for _ in range(action_count):
        await committer.commit("editor", EventType.THEME_DEVELOPED, "t1", ThemeDeveloped(id="t1"))
    await proj.catch_up()
    before = await read.get_theme("t1")
    await proj._reset_state()
    await proj.catch_up()
    after = await read.get_theme("t1")
    assert before == after
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/canon/test_themes_projection_property.py -v`
Expected: given Tasks 1-3 are complete, this should **pass on first run** (same formality
as M5.1's Tasks 7/8 precedent — this is property-coverage of already-landed behavior). If
it fails, that names a real gap in Task 3's fold logic — stop and fix Task 3, do not
weaken this test.

- [ ] **Step 3: N/A**

No production code changes expected.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/canon/test_themes_projection_property.py -v`
Expected: PASS. Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add tests/canon/test_themes_projection_property.py
git commit -m "test: Hypothesis property coverage — theme monotonic-appending and rebuild equivalence"
```

---

### Task 8: `VoiceDriftFlag` schema, `Editor.commit()` voice-drift → tagged retcon, `test_llm.py` mining-builder gap fix

**Files:**
- Modify: `novelizer/agents/schemas.py`, `novelizer/agents/editor.py`,
  `tests/agents/test_llm.py`
- Test: `tests/agents/test_editor.py`, `tests/agents/test_llm.py`

**Interfaces:**
- `VoiceDriftFlag(BaseModel)`: `character_id: str, line: str, trait_violated: str, note:
  str = ""`.
- `EditorVerdict.voice_drift_flags: list[VoiceDriftFlag] = Field(default_factory=list)`.
- `VOICE_SOURCE_TAG = "[source: voice_drift]"` module constant in `novelizer/agents/editor.py`.
- `Editor.commit()` gains, after the existing thread/knowledge/causal intent commits: for
  each flag in `verdict.voice_drift_flags`, build `description = f"{VOICE_SOURCE_TAG}
  {flag.trait_violated} violated by {flag.character_id}: \"{flag.line}\"" + (f" —
  {flag.note}" if flag.note else "")`, then `req = RetconRequest(description=description,
  conflicting_entry_ids=[flag.character_id], proposed_resolution=""); await
  self._committer.commit(self.name, EventType.RETCON_REQUEST_CREATED, req.id, req)` — same
  call shape `continuity_checker.py` already uses for `MINED_SOURCE_TAG` retcons
  (confirmed at `novelizer/agents/continuity_checker.py:217-222`).
- `tests/agents/test_llm.py`'s `test_runner_builders_pass_llm_max_tokens` parametrize list
  gains `("continuity_checker", "build_continuity_mining_runner")` — closing the gap
  flagged in the decomposition brief (the mining builder exists in
  `novelizer/agents/continuity_checker.py` but was never added to this test when M5.1
  landed it).

- [ ] **Step 1: Write the failing tests**

For `tests/agents/test_editor.py` (read in full first):

```python
async def test_editor_voice_drift_flag_commits_tagged_retcon(stack):  # match fixture
    # FakeRunner returns EditorVerdict(verdict="approve",
    # voice_drift_flags=[VoiceDriftFlag(character_id="mara", line="I dunno, whatever.",
    # trait_violated="formal, clipped diction", note="drops into casual slang")]).
    # run_once(); assert a retcon_request.created event exists whose
    # description starts with VOICE_SOURCE_TAG and mentions "formal, clipped diction".
    ...


async def test_editor_voice_drift_flag_cites_character_in_conflicting_entry_ids(stack):
    # Same setup; assert the committed RetconRequest's conflicting_entry_ids == ["mara"].
    ...


async def test_editor_no_voice_drift_flags_commits_no_extra_retcon(stack):
    # EditorVerdict with voice_drift_flags=[] (default); assert no
    # VOICE_SOURCE_TAG-tagged retcon_request.created event exists.
    ...
```

For `tests/agents/test_llm.py`, add the missing tuple to the existing parametrize list
(one-line change, no new test function).

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/agents/test_editor.py -v -k voice_drift
uv run pytest tests/agents/test_llm.py -v -k continuity_mining
```
Expected: first FAILs with `TypeError: unexpected keyword argument 'voice_drift_flags'`;
second FAILs because the new parametrize case isn't collected as a distinct test yet
before Step 3 (verify by running the full parametrized test and confirming the new case
appears) — actually the parametrize-list edit itself IS step 3 for that half; run
`uv run pytest tests/agents/test_llm.py -v` before editing to confirm current pass count,
then after editing confirm one more case appears and passes.

- [ ] **Step 3: Implement**

Add `VoiceDriftFlag` to `schemas.py` next to `EditorVerdict`, add the field to
`EditorVerdict`. Add `VOICE_SOURCE_TAG` and the commit branch to `editor.py`'s `commit()`
method (import `RetconRequest` from `novelizer.store.models` — already imported by
`continuity_checker.py`, add the import to `editor.py` if not already present). Add the
missing parametrize tuple to `test_llm.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
uv run pytest tests/agents/test_editor.py tests/agents/test_llm.py -v
```
Expected: PASS. Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/schemas.py novelizer/agents/editor.py tests/agents/test_editor.py tests/agents/test_llm.py
git commit -m "feat: Editor voice_drift_flags -> tagged retcon_request.created; fix mining-builder gap in max_tokens test coverage"
```

---

### Task 9: M5.2 done-when (a) — CI-mechanical chain test

**Files:**
- Test: `tests/agents/test_editor.py` and/or `tests/canon/test_themes_projection_property.py`
  (pick whichever hosts the theme half most naturally; the voice-drift half belongs in
  `test_editor.py` regardless)

**Interfaces:** No new production interfaces — traces the exact M5.2(a) decomposition
clause chain in explicit assertions, same formality as M5.1's Task 9 precedent.

- [ ] **Step 1: Write the failing test(s)**

```python
async def test_m5_2_done_when_mechanical_chain_themes(stack):
    """M5.2 done-when (a), theme half, traced clause by clause:
    1. Declaring a theme_intents entry (action='introduce') via Author or
       Editor commits a theme.introduced event.
    2. list_themes() after catch_up() reflects it.
    3. A subsequent theme_intents entry (action='develop') citing that id
       commits theme.developed and increments touch_count.
    4. State is monotonic-appending: no event type resets touch_count or
       removes the record (covered structurally by Task 7's property test;
       assert it holds for this specific fixture's sequence too, as an
       explicit literal check per the decomposition wording).
    """
    ...


async def test_m5_2_done_when_mechanical_chain_voice_drift(stack):
    """M5.2 done-when (a), voice-drift half, traced clause by clause:
    1. A FakeRunner EditorVerdict carrying a voice_drift_flags entry
       (character_id, line, trait_violated, note) is returned from
       Editor.work().
    2. Editor.commit() produces a retcon_request.created event.
    3. Its description is tagged with VOICE_SOURCE_TAG.
    4. It lands in the open retcon queue (read.list_retcon_requests(status='open')).
    """
    ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_editor.py -v -k m5_2_done_when`
Expected: given Tasks 1-8 are complete, this should **pass on first run** — if any clause
fails, that names exactly which prior task has a gap; fix that task, do not weaken this
test.

- [ ] **Step 3: N/A**

No production code changes expected.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_editor.py -v`
Expected: PASS. Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add tests/agents/test_editor.py
git commit -m "test: explicit M5.2 done-when (a) mechanical chain — themes and voice drift, traced clause by clause"
```

---

### Task 10: Docs — mark M5.2 CI-mechanical scope complete

**Files:**
- Modify: `docs/submilestones/M5-finish.md`

- [ ] **Step 1: Update the M5.2 status**

Change the M5.2 row's `Status` cell from `not started` to `complete (CI-proven; live
smoke in Task 11, runnable but not CI-blocking)`, matching M5.1's Task 10 precedent.

- [ ] **Step 2: Commit**

```bash
git add docs/submilestones/M5-finish.md
git commit -m "docs: mark M5.2 CI-mechanical scope complete"
```

---

### Task 11: `live_llm` smoke — voice-drift catch, real Editor, `VOICE_SOURCE_TAG` retcon citing the violated trait (environment-dependent, not CI-blocking)

**Files:**
- Create: `tests/agents/test_voice_drift_live_llm.py`

**Interfaces:** Consumes the real `build_editor_runner` against `load_effective_settings()`
— requires the configured OpenAI-compatible endpoint reachable
(`NOVELIZER_LLM_BASE_URL=http://192.168.1.14:8080/v1/`, `NOVELIZER_LLM_MODEL=qwen3.6-27b-mtp`
in this environment — **do not hardcode this URL in the test**, only rely on
`load_effective_settings()` picking it up from env, per this repo's `live_llm` marker
convention: `@pytest.mark.live_llm`, deselected by default via `addopts = "-m 'not
live_llm'"` in `pyproject.toml`).

- [ ] **Step 1: Design the fixture (write it as a plan comment first, then code)**

Seed:
- A character with an established `voice` field on their `Character` record — something
  concrete and checkable, e.g. `voice="Speaks in short, formal, clipped sentences; never
  uses contractions or slang; addresses others by full title."`
- A chapter whose prose gives that character dialogue that clearly violates the stated
  trait — e.g. casual contractions and slang ("Nah, I dunno, whatever works I guess") —
  drafted in `draft` editorial status so `Editor.poll()` picks it up as `target`.

Run the real `Editor` (`build_editor_runner(load_effective_settings())`) via
`run_once()` against this seeded state (no `FakeRunner` — this is the live smoke's whole
point, mirroring M5.1 Task 11's "engineer the fixture, use the real model" pattern).

- [ ] **Step 2: Write the test**

```python
@pytest.mark.live_llm
async def test_voice_drift_live_catch(stack):  # match this repo's live-smoke fixture convention (see tests/agents/test_leak_live_llm.py, test_prose_mining_live_llm.py)
    """Seeds a character with an established voice card and a chapter whose
    dialogue clearly violates a specific stated trait. Runs the real Editor.
    Asserts a retcon_request.created event lands, tagged VOICE_SOURCE_TAG,
    whose description references the violated trait -- proving
    citation-grounded enforcement (M5.2 done-when (b)), not generic "this
    feels off" prose. Casing-tolerant assertion on the trait text, per
    M5.1's live-smoke lesson that live model output isn't guaranteed to
    echo the exact casing of the source string.
    """
    from novelizer.settings.loader import load_effective_settings
    from novelizer.agents.editor import build_editor_runner, Editor, VOICE_SOURCE_TAG
    settings = load_effective_settings()
    events, proj, read, committer = stack
    # ... seed character with voice card, seed draft chapter with violating dialogue ...
    runner = build_editor_runner(settings)
    editor = Editor(runner, read, committer)
    await editor.run_once()
    await proj.catch_up()
    open_retcons = await read.list_retcon_requests(status="open")
    voice_retcons = [r for r in open_retcons if r.description.startswith(VOICE_SOURCE_TAG)]
    assert voice_retcons, f"expected a voice-drift retcon; got: {[r.description for r in open_retcons]}"
    assert "formal" in voice_retcons[0].description.lower() or "clipped" in voice_retcons[0].description.lower()
```

- [ ] **Step 3: Run the live smoke**

Run: `uv run pytest tests/agents/test_voice_drift_live_llm.py -v -m live_llm`

**Contingency checkpoint (per Decision Note D3):** if `structured_response` comes back
`None` or the test flakes on missing `voice_drift_flags` across a few runs, this is the
signal D3 named as the trigger for reconsidering `ProviderStrategy(EditorVerdict)` on the
Editor's runner. Do not apply that change silently — if it's needed, land it as an
explicit follow-up task with its own red/green cycle (write a CI-mechanical test first
proving the Editor still handles its existing thread/knowledge/causal-intent + tool-call
behavior correctly under `ProviderStrategy` before trusting it in the live smoke), and
record the observed flake rate in this task's commit message either way — passing
cleanly on first try is also a valid, reportable outcome.

Record the actual verdict (pass/flake/fail and why) — this is a documented manual run per
M5.1 Task 11 / M1-M4 precedent for claims no CI oracle can verify.

- [ ] **Step 4: Commit**

```bash
git add tests/agents/test_voice_drift_live_llm.py
git commit -m "test: live voice-drift smoke — established voice card violated, real Editor, VOICE_SOURCE_TAG retcon citing the trait"
```

---

### Task 12: Full-suite verification + M5.2 done-when (a) chain traced clause by clause (final gate)

**Files:** None modified — verification only.

- [ ] **Step 1: Run the full suite**

```bash
uv run pytest tests/ -v
```
Confirm zero failures, zero errors, and that `live_llm`-marked tests are deselected by
default (confirm the collected/deselected counts in the summary line match expectations).

- [ ] **Step 2: Trace the done-when (a) clause chain literally**

Re-read the M5.2 row's done-when (a) sentence in `docs/submilestones/M5-finish.md`
verbatim and confirm, clause by clause, which task and test in this plan proves each:
- "declaring a `theme_intents` entry commits a `theme.introduced`/`theme.developed`
  event" → Task 6 (wiring) + Task 9's theme chain test.
- "updates `list_themes()` after `catch_up()`" → Task 3 + Task 9.
- "a property test asserts theme state is monotonic-appending (introduced → developed*,
  no terminal state to protect...)" → Task 7.
- "a fixture where the Editor's `FakeRunner` returns a `voice_drift_flags` entry produces
  a `retcon_request.created` tagged `VOICE_SOURCE_TAG` in the open queue" → Task 8 + Task
  9's voice-drift chain test.

If any clause has no corresponding test, stop and add it before declaring the plan
complete — do not close this task on an incomplete trace.

- [ ] **Step 3: Update `docs/submilestones/M5-finish.md`'s M5.2 closeout note**

Following M4/M5.1's closeout-note precedent, add a short note under the M5.2 row (or in a
dedicated closeout section if this doc has one by now) recording: the CI-mechanical status
(complete), the live smoke's actual run result from Task 11 (including the D3 contingency
outcome — did `ProviderStrategy` end up needed or not), and any deviation this plan's
executor made from the plan as written (per D2's Author-schema-name confirmation, per any
task that had to adapt to master drift).

- [ ] **Step 4: Commit**

```bash
git add docs/submilestones/M5-finish.md
git commit -m "docs: mark M5.2 complete — done-when (a) traced clause by clause, live smoke recorded"
```
