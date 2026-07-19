# M5.3 · UX Polish, Performance, Deferred-Backlog Cleanup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking.

> **NEVER create a `.env` file at any point in this plan.** A past subagent broke the suite
> doing this. Any scratch/temp file goes in the job's tmp dir, never the repo. Live-LLM
> settings load through `load_effective_settings()` only — never bare `EffectiveSettings()`.
> The live model in this environment is `qwen3.6-27b-mtp` (`NOVELIZER_AUTHOR_MODEL` /
> `NOVELIZER_AGENT_MODEL`), endpoint via `NOVELIZER_LLM_BASE_URL` — never hardcode the URL
> in a test; rely on `load_effective_settings()` picking it up from env.

**Branch:** `m5.3-polish-perf` (already checked out in this worktree, based on `master` @
`e7a25a6` = M5.1 + M5.2 merged and closed out, plus M4.3 closeout).

**Goal:** Sweep the mechanical M4 deferred-backlog items so master carries no silent debt
into M5.4's acceptance pass, land the two M4 re-evaluation items with an explicit verdict
(causal-graph-in-prompt, staleness/sag-spike thresholds), fix the Editor "revise" bug via a
tightly-scoped `chapter.revised` event, wire `EmbeddingStore` for one real use
(theme-similarity suggestion), and — the centerpiece, user-directed 2026-07-18 — replace
the Scheduler's strictly-serial `tick()` with a bounded concurrent-dispatch pool so the
inference endpoint stops sitting idle between agent calls. **M5.3's done-when has no
live_llm half** per the decomposition (entirely mechanical/UX/perf, no new LLM-judgment
surface) — the one exception is an **optional, non-blocking** live observation note on the
concurrency work, because the user explicitly cares about saturation and a CI clock-mocked
test alone doesn't prove the real endpoint overlaps two calls.

**Tech Stack:** Python 3.13, `pydantic` v2, `aiosqlite`, `pytest`+`pytest-asyncio`
(`asyncio_mode=auto`), `hypothesis>=6.156.6`, `chromadb`.

## Global constraints

- Event sourcing / DDD / SOLID / red-green TDD with property tests where invariants
  generalize — same standing principles as M5.1/M5.2. `chapter.revised` and the
  concurrency pool are the two places this plan adds real new surface; everything else is
  cleanup of existing surface, so those two tasks get the most rigor.
- `ctx.get(...)` vs `ctx[...]` (Locked decision 9) is applied **only in files this plan
  touches for other reasons** — not a standalone repo-wide sweep. Each task below that
  edits `poll()`/`work()`/`commit()` in an agent file re-audits that file's `ctx` access
  against the rule (`ctx[...]` for keys every path populates, `ctx.get(...)` only for
  genuinely conditional keys) while it's open.
- Every task ends with the **full suite green** (`uv run pytest`). Do not create `.env`.
- Live tests are `@pytest.mark.live_llm`, deselected by default
  (`addopts = "-m 'not live_llm'"`), and must call `load_effective_settings()`.

## Scope-risk flag (read before starting)

Locked decision 10 says explicitly: *"If M5.3's plan review finds this crowding out the
sweep, [`chapter.revised`] gets promoted to its own branch rather than trimmed."* This plan
sequences `chapter.revised` (Task 10) and the concurrency pool (Tasks 11–14) **after** all
the smaller sweep items, specifically so that if either one blows its budget mid-execution,
the sweep items are already merged/committed on this branch and only the oversized piece
needs splitting off — not the other way around. If Task 10 or Task 11 discovers the
Scheduler/Author/Editor coupling is messier than this plan assumes (see hazards below),
stop, commit what's landed, and escalate a branch-split recommendation rather than trimming
scope silently.

## Sequencing hazards flagged up front

1. **Casing normalization (Task 1) touches `_commit_knowledge_intents` and its sibling
   `_commit_thread_intents`/`_commit_theme_intents`/`_commit_causal_intents` in
   `novelizer/agents/base.py`** — the same file Task 2 (`_guarded_line`) does NOT touch
   (that's agent-specific prompt-building code, not `base.py`'s commit helpers, except the
   helper itself lives in `base.py` too). Sequence Task 1 before Task 2 only because Task 1
   is smaller and de-risks first; they do not actually conflict on the same lines.
2. **`_guarded_line` (Task 2) touches seven agent files** (`character_keeper.py`,
   `author.py`, `structure_analyst.py`, `editor.py`, `continuity_checker.py`,
   `retconner.py`, `world_architect.py`) — do this task in one pass with byte-identical
   output pinned by a test for every call site, not agent-by-agent, so there's one commit
   to bisect if a prompt regresses.
3. **`prior_chapter_summary_chars` (Task 3) and staleness/sag-spike settings (Task 8) both
   add fields to `EffectiveSettings`/`GlobalConfig`/`StoryConfig`/`EnvOverrides`** — do
   Task 3 first (it's simpler, one setting) to prove the settings-layer pattern still holds
   post-M5.2, then Task 8 follows the same four-file pattern for two settings at once.
4. **The CLI `ProposalService` de-dup fix (Task 6) changes `commands.approve`/
   `commands.reject`'s signature** — Task 5 (CliRunner tests) is written and run *against
   the signature as it exists today* first (red/green on the CLI surface, not the internal
   plumbing), then Task 6 changes the plumbing under those same passing tests, which must
   stay green. Sequence Task 5 before Task 6 so Task 6 has characterization tests to guard
   against a behavior change, not just an implementation change.
5. **`chapter.revised` (Task 10) depends on nothing else in this plan** but is the largest
   single item after concurrency — see the scope-risk flag above.
6. **The concurrency pool (Tasks 11–14) depends on nothing else in this plan** and is fully
   independent of `chapter.revised` (different files: `scheduler.py` vs.
   `events.py`/`projector.py`/`author.py`/`editor.py`). These two could run as parallel
   tracks if time pressure demands it; this plan sequences them serially (revise first,
   concurrency last) because concurrency is the higher-risk, most-scrutinized item and
   benefits from being executed once the rest of the branch is stable and green.
7. **EmbeddingStore theme-similarity (Task 9) depends on M5.2's `theme.introduced` event
   existing** (already merged) but is otherwise independent of every other task in this
   plan — sequenced mid-plan for no reason other than list order; safe to reorder.

---

### Task 1: Character-id casing normalization at the commit-helper boundary

**Files:**
- Modify: `novelizer/agents/base.py`
- Test: `tests/agents/test_base.py`

**Problem:** M5.1's live run observed the mining pass emitting `character_id="Kestrel"`
for the canonical id `kestrel` (minted lowercase by `slugify_*_name`). Every commit helper
that receives an agent- or LLM-supplied id string (`character_id` on knowledge intents;
`id` citations on thread/theme `touch`/`develop`/`learn`/`reveal`/`uses`/`pay_off`/
`abandon` intents) currently does an exact-string membership check against
`active_*_ids`/known character ids, so a casing mismatch silently drops the fact with a
warning instead of committing it — a correctness bug hiding behind the drop-and-warn
UX that's supposed to catch *unknown* ids, not *misspelled-case* ones.

**Interfaces:**
- Add a module-level `_normalize_id(raw: str) -> str` helper in `base.py`:
  `raw.strip().lower()`. Used at the point of comparison/lookup, never mutating what gets
  stored as `character_id`/`id` in the payload (store the normalized form — canon ids are
  canonically lowercase everywhere else in this codebase, e.g. every `slugify_*_name`
  output, so storing the raw casing would be the actual inconsistency).
- `_commit_knowledge_intents`: normalize `intent.id` before the `active_secret_ids`
  membership check, and normalize `intent.character_id` before the empty-check and before
  it's written into the payload.
- `_commit_thread_intents`/`_commit_theme_intents`: normalize `intent.id` before the
  `active_*_ids` membership check for `touch`/`pay_off`/`abandon`/`develop` (plant/introduce
  already mint lowercase via slug, unaffected).
- `_commit_causal_intents`: normalize `cause_chapter_id`/`effect_chapter_id` before the
  `valid_chapter_ids` membership check — chapter ids are UUIDs from `Chapter.id`'s default
  factory (already lowercase hex), so this is defense-in-depth, not a live-observed gap;
  include it for consistency since the helper family should have one uniform rule.

- [ ] **Step 1: Write the failing tests**

Read `tests/agents/test_base.py` in full first for its exact fixture/stack pattern (already
established by M5.1/M5.2's `_commit_theme_intents` tests). Append:

```python
async def test_commit_knowledge_intents_normalizes_character_id_casing(stack):
    # Seed a secret "s1". Call _commit_knowledge_intents with
    # KnowledgeIntent(action="learn", id="s1", character_id="Kestrel"),
    # active_secret_ids={"s1"}. Assert the committed SecretLearned payload's
    # character_id == "kestrel" (lowercase), not "Kestrel".
    ...


async def test_commit_knowledge_intents_normalizes_id_casing_for_membership_check(stack):
    # active_secret_ids={"s1"} (lowercase, as minted). Call with
    # KnowledgeIntent(action="uses", id="S1", character_id="kestrel").
    # Assert the intent is NOT dropped -- a secret.uses event commits.
    ...


async def test_commit_thread_intents_normalizes_touch_id_casing(stack):
    # active_thread_ids={"t1"}. ThreadIntent(action="touch", id="T1").
    # Assert a thread.touched event commits for "t1", not dropped.
    ...


async def test_commit_theme_intents_normalizes_develop_id_casing(stack):
    # active_theme_ids={"loss"}. ThemeIntent(action="develop", id="Loss").
    # Assert a theme.developed event commits, not dropped with a warning.
    ...


async def test_commit_causal_intents_normalizes_chapter_id_casing(stack, caplog):
    # valid_chapter_ids={"abc123"}. CausalIntent(cause_chapter_id="ABC123",
    # effect_chapter_id="abc123", ...) -- wait, cause==effect after
    # normalization would hit the self-edge drop; use two distinct valid
    # ids and mixed casing on one of them to prove the membership check
    # normalizes without breaking the self-edge check.
    ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_base.py -v -k normalize`
Expected: FAIL — assertions fail because ids are currently dropped (warning logged, no
event committed) or the payload retains the original casing.

- [ ] **Step 3: Implement**

Add `_normalize_id` near the top of `base.py`. Apply it at each membership-check and
payload-construction site named above. Do not change `slugify_*_name` (already lowercase)
or any code path that mints a new id — normalization only applies to *citing* an existing
id or a character id, never to minting.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_base.py -v`, then `uv run pytest tests/ -v` for the
full suite green — pay attention to any existing test that asserted a specific-case
`character_id` round-trips unchanged; those tests should already use lowercase ids (this
codebase's convention) and stay green, but verify.

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/base.py tests/agents/test_base.py
git commit -m "fix: normalize character/thread/theme/chapter id casing at the commit-helper boundary (live-observed 'Kestrel' vs 'kestrel' gap from M5.1)"
```

---

### Task 2: `_guarded_line(label, value)` DRY helper — adopted by all seven duplicating agents

**Files:**
- Modify: `novelizer/agents/base.py`, `novelizer/agents/character_keeper.py`,
  `novelizer/agents/author.py`, `novelizer/agents/structure_analyst.py`,
  `novelizer/agents/editor.py`, `novelizer/agents/continuity_checker.py`,
  `novelizer/agents/retconner.py`, `novelizer/agents/world_architect.py`
- Test: `tests/agents/test_base.py`, plus one byte-identical-output assertion per adopting
  agent in that agent's own test file (or a single consolidated
  `tests/agents/test_guarded_line_adoption.py` — pick whichever keeps per-agent test files
  from growing unrelated helper-only tests; recommend the consolidated file since the
  helper itself is `BaseAgent`-level, not agent-specific).

**Problem:** `f"\n\nIn character: {self.personality}" if self.personality else ""` (or the
`author.py` prose-voice variant) is duplicated verbatim across seven agent files. This is
pure DRY cleanup — the *output* must not change.

**Interfaces:**
- `BaseAgent._guarded_line(label: str, value: str) -> str`: returns
  `f"\n\n{label}: {value}"` if `value` (after implicit truthiness — empty string is falsy)
  else `""`. This exactly reproduces `f"\n\nIn character: {self.personality}" if
  self.personality else ""` when called as `self._guarded_line("In character",
  self.personality)`.
- `author.py`'s two call sites (`voice`, `cast` in `_summarize`) become
  `_guarded_line("Write in this prose voice", casting_note)` and
  `_guarded_line("In character", personality)` — note `_summarize` is a module function,
  not a `BaseAgent` method, so it needs `_guarded_line` importable as a free function too;
  make `BaseAgent._guarded_line` a `@staticmethod` (it uses no `self` state beyond the
  arguments) so both the six agent-method call sites and `author.py`'s free-function call
  site can use `BaseAgent._guarded_line(...)` uniformly.
- `editor.py`'s voice line is NOT byte-identical to the other six (`"Enforce this prose
  voice: {...}; note any drift in your feedback."` — different wording, not just a label
  swap) — do **not** force it into `_guarded_line`'s exact template; either leave it
  untouched (recommended — it's not actually the duplicated pattern the decomposition
  named) or, if adopted, extend `_guarded_line` with an optional `suffix` parameter and
  verify the byte-identical test still holds for editor.py's exact current string. Recommend
  leaving `editor.py`'s voice line untouched and only converting its `cast` line (the
  actual `"In character: ..."` duplicate) — the decomposition brief's grep target is the
  duplicated string, and editor's voice line isn't that string.

- [ ] **Step 1: Write the failing tests**

```python
def test_guarded_line_returns_labeled_value_when_present():
    from novelizer.agents.base import BaseAgent
    assert BaseAgent._guarded_line("In character", "gruff and terse") == "\n\nIn character: gruff and terse"


def test_guarded_line_returns_empty_when_value_falsy():
    from novelizer.agents.base import BaseAgent
    assert BaseAgent._guarded_line("In character", "") == ""
```

Then, for each of the seven files, a byte-identical pin test comparing the *old* inline
f-string result against the *new* `_guarded_line` call result for both a populated and an
empty value — write these as a table-driven test in
`tests/agents/test_guarded_line_adoption.py`:

```python
import pytest
from novelizer.agents.base import BaseAgent

CASES = [
    ("character_keeper", "In character"),
    ("structure_analyst", "In character"),
    ("editor", "In character"),          # cast line only, not the voice line
    ("continuity_checker", "In character"),
    ("retconner", "In character"),
    ("world_architect", "In character"),
    ("author_cast", "In character"),
    ("author_voice", "Write in this prose voice"),
]

@pytest.mark.parametrize("name,label", CASES)
def test_guarded_line_byte_identical_to_prior_inline_pattern(name, label):
    value = "some casting note"
    old = f"\n\n{label}: {value}" if value else ""
    new = BaseAgent._guarded_line(label, value)
    assert new == old
    old_empty = f"\n\n{label}: {''}" if "" else ""
    new_empty = BaseAgent._guarded_line(label, "")
    assert new_empty == old_empty == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_guarded_line_adoption.py -v`
Expected: FAIL — `AttributeError: type object 'BaseAgent' has no attribute '_guarded_line'`.

- [ ] **Step 3: Implement**

Add `_guarded_line` as a `@staticmethod` on `BaseAgent`. Then, one file at a time, replace
each duplicated inline f-string with the `_guarded_line` call (`Agent._guarded_line(...)`
or `self._guarded_line(...)` for instance-method call sites, `BaseAgent._guarded_line(...)`
for `author.py`'s module-level `_summarize` function). Re-audit each touched file's other
`ctx[...]`/`ctx.get(...)` call sites against Locked decision 9 while the file is open (per
this plan's Global Constraints) — do not do a separate pass.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/ -v`, then `uv run pytest tests/ -v` for the full suite
green. Specifically re-run any existing prompt-content assertion test for each of the seven
agents (grep `tests/agents/test_*.py` for assertions on `"In character:"` substrings) to
confirm prompt text is unchanged.

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/base.py novelizer/agents/character_keeper.py novelizer/agents/author.py novelizer/agents/structure_analyst.py novelizer/agents/editor.py novelizer/agents/continuity_checker.py novelizer/agents/retconner.py novelizer/agents/world_architect.py tests/agents/test_base.py tests/agents/test_guarded_line_adoption.py
git commit -m "refactor: BaseAgent._guarded_line DRY helper, adopted by all agents duplicating the In-character/prose-voice pattern; byte-identical output pinned"
```

---

### Task 3: `prior_chapter_summary_chars` setting (default 200)

**Files:**
- Modify: `novelizer/agents/author.py`, `novelizer/settings/models.py`,
  `novelizer/settings/layers.py`, `novelizer/settings/loader.py`
- Test: `tests/agents/test_author.py`, `tests/settings/test_*.py` (grep for
  `llm_max_tokens`'s existing settings-layer test and mirror its exact shape)

**Interfaces:**
- `EffectiveSettings.prior_chapter_summary_chars: int = 200` in `models.py`, alongside
  `llm_max_tokens` (same "per-request generation tuning" comment block, or its own
  one-line comment: `# Chars of prior-chapter prose shown to the Author as context`).
- `GlobalConfig`/`StoryConfig` (`layers.py`) and `EnvOverrides` (`loader.py`) each gain
  `prior_chapter_summary_chars: int | None = None`, following `llm_max_tokens`'s exact
  four-file pattern (confirm the env var name convention — grep
  `NOVELIZER_LLM_MAX_TOKENS` handling in `loader.py` to find where env var names are
  derived from field names, likely `NOVELIZER_<UPPER_SNAKE_FIELD_NAME>`, giving
  `NOVELIZER_PRIOR_CHAPTER_SUMMARY_CHARS`).
- Add `"prior_chapter_summary_chars"` to `STORY_OVERRIDABLE_KEYS` in `models.py` (a prose
  window like this is a per-story authorial choice, same category as `author_temperature`,
  not a connection-level setting like `llm_base_url`) — **not** `RESTART_REQUIRED_KEYS`
  (it's read fresh on every `Author.poll()`/`_summarize()` call, no cached client to
  rebuild, unlike `llm_max_tokens` which bakes into a constructed chat model).
- `author.py`'s `_summarize(ctx, casting_note="", personality="")` gains a
  `prior_chapter_chars: int = 200` parameter, used as `c.prose[:prior_chapter_chars]`
  instead of the hardcoded `c.prose[:200]`. `Author.__init__` gains a
  `prior_chapter_summary_chars: int = 200` constructor parameter (mirroring how
  `casting_note`/`personality` are already threaded from `settings` through `runtime.py`'s
  `Author(...)` construction), passed through to `_summarize` inside `work()`.
- `runtime.py`'s `Author(...)` construction gains
  `prior_chapter_summary_chars=s.prior_chapter_summary_chars`.

- [ ] **Step 1: Write the failing tests**

```python
def test_summarize_uses_configured_prior_chapter_chars():
    from novelizer.agents.author import _summarize
    ctx = {"world": [], "characters": [], "previous": [make_chapter(prose="x" * 500)],
           "chapters": [], "signals": [], "threads": [], "secrets": [],
           "knowledge_matrix": {}, "themes": []}
    out = _summarize(ctx, prior_chapter_chars=50)
    # The previous-chapter line should contain exactly 50 chars of prose, not 200.
    assert "x" * 51 not in out
    assert "x" * 50 in out


def test_summarize_default_prior_chapter_chars_is_200():
    from novelizer.agents.author import _summarize
    ctx = {...}  # same shape, prose="x" * 500
    out = _summarize(ctx)
    assert "x" * 200 in out and "x" * 201 not in out
```

Read `tests/agents/test_author.py`'s existing `_summarize`/`make_chapter` fixtures first
and match the exact helper names in use. For the settings-layer test, grep
`tests/settings/` for `llm_max_tokens`'s existing override test (global/story/env
precedence) and write the mirror for `prior_chapter_summary_chars`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_author.py -v -k prior_chapter`
Expected: FAIL — `TypeError: _summarize() got an unexpected keyword argument
'prior_chapter_chars'`.

- [ ] **Step 3: Implement**

Thread the setting through as described above. Update `runtime.py`'s `Author(...)`
construction. Re-audit `author.py`'s `ctx[...]` access against Locked decision 9 while the
file is open.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_author.py tests/settings/ -v`, then
`uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/author.py novelizer/settings/models.py novelizer/settings/layers.py novelizer/settings/loader.py novelizer/runtime.py tests/agents/test_author.py tests/settings/
git commit -m "feat: prior_chapter_summary_chars setting (default 200) replaces hardcoded prose[:200] window in Author"
```

---

### Task 4: `ctx.get(...)` vs `ctx[...]` audit (files already touched by Tasks 1–3)

**Files:** Whichever of `novelizer/agents/{base,author,character_keeper,editor,
continuity_checker,retconner,structure_analyst,world_architect}.py` were touched by Tasks
1–3 and were **not** already re-audited inline during those tasks' Step 3 (this task exists
as an explicit checkpoint in case the "audit while the file is open" instruction was
missed for any file — read each touched file's `poll()` method and confirm every `ctx[...]`
access is a key that method's own return dict always populates, and every `ctx.get(...)`
access is a key that's genuinely conditional).

**Interfaces:** None new — this is a read-and-fix-if-needed task, not a new feature.

- [ ] **Step 1: Audit**

For each file touched in Tasks 1–3, grep `ctx\.get(\|ctx\[` within that file and cross-
reference against its own `poll()` return dict. Flag any `ctx.get("key")` where `"key"` is
unconditionally set in `poll()` (should be `ctx["key"]`), and any `ctx["key"]` where
`"key"` is conditionally absent (should be `ctx.get("key", default)`).

- [ ] **Step 2: Fix any violations found, run the affected test file(s), then the full
  suite**

If no violations are found (likely, since Tasks 1–3 already re-audit inline per the Global
Constraints), this task is a no-op confirmation — record "no violations found, Tasks 1–3's
inline audits were sufficient" rather than fabricating a change.

- [ ] **Step 3: Commit (only if a fix was made; otherwise fold this confirmation into
  Task 14's final commit message)**

```bash
git add <changed files>
git commit -m "refactor: ctx.get/ctx[] consistency fixes found during Locked decision 9 audit"
```

---

### Task 5: `CliRunner` tests for `autonomy`/`proposals`/`approve`/`reject` + Literal-rejection + caplog

**Files:**
- Test: `tests/director/test_cli.py`

**Problem:** These four M1.3 commands (`novelizer/director/cli.py`) have no `CliRunner`
coverage today — `tests/director/test_cli.py` exists (covers config-error/seed/wizard-gate
paths) but not these four. The M4 backlog also flagged a missing "Literal-rejection" test
(an invalid `autonomy` level string should be rejected with a friendly message, not a
traceback — `AutonomyLevel(level)` already raises `ValueError` and is caught, per
`cli.py`'s existing `except ValueError` branch) and missing caplog assertions.

**Interfaces:** No production interfaces — pure test coverage against the CLI as it exists
today (this task runs *before* Task 6's `ProposalService` plumbing change, per Sequencing
hazard 4, so these tests characterize current behavior and must stay green through Task 6).

- [ ] **Step 1: Write the tests**

Read `tests/director/test_cli.py` in full first for its exact env-setup pattern (`_env`
helper, `XDG_CONFIG_HOME`/`NOVELIZER_DB_PATH` seeding) — every new test must use the same
isolated-tmp-story pattern, not a shared/real story dir. Append:

```python
def test_autonomy_command_sets_global_level(tmp_path):
    r = CliRunner().invoke(cli, ["--story", str(_seeded_story(tmp_path)), "autonomy", "gated_all"], env=_env(...))
    assert r.exit_code == 0
    assert "gated_all" in r.output


def test_autonomy_command_rejects_unknown_level_with_friendly_message(tmp_path):
    r = CliRunner().invoke(cli, ["--story", str(_seeded_story(tmp_path)), "autonomy", "not_a_real_level"], env=_env(...))
    assert "Unknown autonomy level" in r.output
    assert "Traceback" not in r.output


def test_autonomy_command_sets_per_agent_override(tmp_path):
    r = CliRunner().invoke(cli, ["--story", str(_seeded_story(tmp_path)), "autonomy", "gated_all", "--agent", "author"], env=_env(...))
    assert r.exit_code == 0
    assert "author" in r.output


def test_proposals_command_lists_no_pending_proposals(tmp_path):
    r = CliRunner().invoke(cli, ["--story", str(_seeded_story(tmp_path)), "proposals"], env=_env(...))
    assert "No pending proposals" in r.output


def test_proposals_command_lists_a_pending_proposal(tmp_path):
    # Seed a proposal.created event directly via the story's EventStore, then invoke.
    ...
    assert "Pending Proposals" in r.output


def test_approve_command_approves_and_reports(tmp_path):
    # Seed a proposal, invoke `approve <id>`, assert output + that the
    # target event now exists (re-open the story's ReadStore).
    ...


def test_approve_command_reports_not_found(tmp_path):
    r = CliRunner().invoke(cli, ["--story", str(_seeded_story(tmp_path)), "approve", "nonexistent-id"], env=_env(...))
    assert "not found" in r.output.lower()


def test_reject_command_rejects_and_reports(tmp_path):
    ...


def test_approve_command_logs_at_info_level(tmp_path, caplog):
    # Assert a caplog record documents the approval action (adjust to
    # whatever log statement the command path already emits, or add one
    # if none exists -- if adding, keep it a one-line logger.info call in
    # commands.approve, not a new logging subsystem).
    ...
```

Fill in the ellipsis blocks by reading `tests/director/test_cli.py`'s existing story-seeding
helpers (`_seeded_story` may not exist yet — check first; if the file only has an inline
`tmp_path / "story"` pattern per test, follow that instead of inventing a new helper).

- [ ] **Step 2: Run tests to verify they fail (where they should)**

Run: `uv run pytest tests/director/test_cli.py -v`
Expected: most pass immediately against current `cli.py` behavior (this is
characterization, not red/green feature work) **except** the caplog test if no log
statement currently exists at that call site — that one test drives a one-line addition.

- [ ] **Step 3: Implement (only if the caplog test needs a log statement)**

Add `logger.info("approved proposal %s (%s)", proposal_id, proposal.target_event_type)`
(and the reject equivalent) to `commands.approve`/`commands.reject` if not already present
— check first, since `commands.py` may already log at a level `caplog` isn't capturing by
default (adjust `caplog.set_level(logging.INFO)` in the test rather than assuming a change
is needed).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/director/test_cli.py -v`, then `uv run pytest tests/ -v` for the
full suite green.

- [ ] **Step 5: Commit**

```bash
git add tests/director/test_cli.py novelizer/director/commands.py
git commit -m "test: CliRunner coverage for autonomy/proposals/approve/reject, Literal-rejection, caplog (M4 backlog closeout)"
```

---

### Task 6: CLI `ProposalService` from the same runtime factory as TUI

**Files:**
- Modify: `novelizer/director/commands.py`, `novelizer/director/cli.py`
- Test: `tests/director/test_cli.py` (Task 5's tests must stay green), `tests/director/test_commands.py` (grep for its existence first)

**Problem:** `commands.approve(events, read, proposal_id)` and `commands.reject(...)`
construct their own `ProposalService(events)` (`commands.py:36,46`) — a second construction
path parallel to `Runtime.start()`'s `self.proposals = ProposalService(self.events)`
(`runtime.py:65`), which is what the TUI's `dispatch()` → `_dispatch_decision()` actually
uses. Two construction paths for the same service is exactly the duplication the M5-finish
spec names.

**Interfaces:**
- `commands.approve(proposals: ProposalService, read, proposal_id: str) -> str` and
  `commands.reject(proposals: ProposalService, read, proposal_id: str) -> str` — signature
  changes from `(events, read, proposal_id)` to `(proposals, read, proposal_id)`, dropping
  the internal `ProposalService(events)` construction; callers now pass an already-
  constructed `ProposalService`.
- `cli.py`'s `approve`/`reject` commands change their call sites from
  `commands.approve(rt.events, rt.read, proposal_id)` to
  `commands.approve(rt.proposals, rt.read, proposal_id)` — `rt.proposals` is populated by
  `Runtime.start()`, but `_with_runtime`'s `_run` helper in `cli.py` currently does NOT call
  `rt.start()` (it calls `rt.events.init()`/`rt.projector.init()`/`rt.read.init()`/
  `rt.projector.catch_up()` directly, skipping agent construction) — **check this first**:
  if `rt.proposals` is `None` at that point (per `Runtime.__init__`'s
  `self.proposals: Optional[ProposalService] = None`), `_with_runtime` needs
  `rt.proposals = ProposalService(rt.events)` added as one line (not a full `rt.start()`,
  which would also construct all seven agents and their runners — unwanted for a CLI
  command that only touches the store), OR expose a narrower `Runtime.init_light()`/
  similar that constructs only `policy`/`committer`/`proposals` without agents. Recommend
  the one-line addition in `_with_runtime` — it's the smallest change that removes the
  duplicate construction path without restructuring `Runtime`.
- `dispatch()`'s `_dispatch_decision` (already using `runtime.proposals`) is unchanged —
  this task only fixes the CLI side.

- [ ] **Step 1: Confirm current behavior, then adjust Task 5's tests if the plumbing
  change alters an observable outcome**

Task 5's tests characterize `approve`/`reject` output text and exit codes — this task
changes *how* the `ProposalService` is constructed, not *what* it does, so Task 5's tests
should need zero changes if this task is done correctly. Run
`uv run pytest tests/director/test_cli.py -v -k "approve or reject"` before touching any
production code to record the current green baseline.

- [ ] **Step 2: Implement**

Change `commands.approve`/`commands.reject` signatures. Update `cli.py`'s two call sites.
Add the `rt.proposals` construction line to `_with_runtime` if confirmed missing (Step 0
above). If `tests/director/test_commands.py` exists and calls `commands.approve`/
`commands.reject` directly with an `events` positional argument, update those call sites
too (construct a throwaway `ProposalService(events)` in the test itself, or better, pass
the test's own already-available `ProposalService` fixture if one exists — check the file).

- [ ] **Step 3: Run tests to verify they pass**

Run: `uv run pytest tests/director/ -v`, then `uv run pytest tests/ -v` for the full suite
green — Task 5's tests must be unchanged and still green (proving this was a pure plumbing
fix, not a behavior change).

- [ ] **Step 4: Commit**

```bash
git add novelizer/director/commands.py novelizer/director/cli.py tests/director/
git commit -m "refactor: CLI approve/reject construct ProposalService from the same Runtime factory the TUI uses, removing the second construction path"
```

---

### Task 7: Causal-graph-in-Author-prompt — recorded verdict (evaluation, not blanket addition)

**Files:**
- Modify: `docs/submilestones/M5-finish.md` (verdict recorded under the M5.3 row or a
  dedicated closeout section)
- No production code change expected unless the verdict is "add it" (see below).

**Problem:** M4's non-goal deferred this with a re-evaluation window "now that real
Causeway data from M4 is in hand." The decomposition demands a verdict, not a repeat
deferral.

**Analysis to perform and record:**
1. What already exists: `causal_flags_note(edges, chapter_order)` in `novelizer/brain/
   context.py` already injects paradox-candidate summaries into the **Editor's** prompt
   (`editor.py`'s `work()` calls it), reusing `find_paradoxes`. The Author currently
   receives **no** causal-graph context at all — `Author.poll()`'s ctx has no
   `causal_edges` key and `_summarize()` never calls `causal_flags_note` or any causal
   summary.
2. The actual question: should the *Author* (the chapter-drafting agent, not the Editor,
   which reviews after the fact) see the causal graph while drafting, to avoid *writing*
   causally-inconsistent chapters in the first place rather than catching them after?
3. Verdict criteria to weigh and write into the doc:
   - **Cost**: one more context block in every Author prompt, on every poll cycle,
     regardless of whether the story has any causal edges yet (early chapters: empty,
     wasted tokens on the "None yet" fallback path, same shape as `stale_threads_note`/
     `known_secrets_note`'s existing empty-string-when-nothing-to-report pattern — so the
     token cost is genuinely zero until edges exist, which softens this concern).
   - **Signal quality**: `find_paradoxes` flags *cause-after-effect* ordering violations —
     useful to an agent about to *declare* a new causal edge (Author's `causal_intents`
     field already exists per M4.2), but Author doesn't currently see existing edges at
     all when deciding what new edge to declare, meaning it's flying blind on ordering
     even before drafting — this is closer to a real gap than the staleness-threshold
     item.
   - **Risk**: prompt bloat / attention dilution on deep-agent tool-calling models (same
     class of concern D3 in M5.2's plan flagged for `ProviderStrategy` switches) is a real
     but different risk — this is prose content, not response-format grammar, so it's
     lower-risk than a `ProviderStrategy` change, but still adds prompt length on every
     Author call, the highest-frequency agent in the room.
   - **Precedent**: `known_secrets_note`/`stale_threads_note` are exactly this shape
     (existing-state summaries injected into the Author's drafting context) and are load-
     bearing for M5.1's whole reliability story — adding a `causal_flags_note`-equivalent
     for the Author is consistent with, not a departure from, the established pattern.
4. **Recommended verdict: adopt, scoped small.** Add `causal_edges` to `Author.poll()`'s
   ctx (`await self._read.list_causal_edges()`, mirroring how `themes`/`threads`/`secrets`
   are already fetched) and call `causal_flags_note(ctx["causal_edges"], [c.id for c in
   ctx["chapters"]])` in `_summarize`, appended via `_guarded_line`-style conditional (empty
   when no edges exist, same as the Editor's usage) — this is a small, precedent-consistent
   addition, not a redesign, and directly closes the "Author declares edges blind to
   existing ones" gap named above. If this plan's executor judges the live-quality
   trade-off differently after implementing and testing it, record the actual verdict
   (adopted / adopted-with-caveats / explicitly deferred-with-reason) in this task's commit
   and in the doc update — this plan's recommendation is not a mandate if evidence during
   implementation contradicts it.

- [ ] **Step 1: Write the failing test**

```python
def test_summarize_includes_causal_flags_when_edges_exist():
    from novelizer.agents.author import _summarize
    ctx = {..., "causal_edges": [make_paradox_edge()], "chapters": [...]}
    out = _summarize(ctx)
    assert "Causal flags:" in out


def test_summarize_omits_causal_flags_block_when_no_edges():
    ctx = {..., "causal_edges": [], "chapters": []}
    out = _summarize(ctx)
    assert "Causal flags:" not in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_author.py -v -k causal`
Expected: FAIL — `KeyError: 'causal_edges'`.

- [ ] **Step 3: Implement**

Add `"causal_edges": await self._read.list_causal_edges()` to `Author.poll()`. Add the
`causal_flags_note` call to `_summarize`, importing it from `novelizer.brain.context`
(already imported by `editor.py`; add the import to `author.py`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_author.py -v`, then `uv run pytest tests/ -v` for the
full suite green.

- [ ] **Step 5: Record the verdict in the doc and commit**

Add a short paragraph under the M5.3 row in `docs/submilestones/M5-finish.md` (or a
dedicated closeout section — match whichever structure M5.1/M5.2's closeout notes used)
recording: verdict = adopted, rationale (the four bullet points above, condensed), and the
one-line implementation summary.

```bash
git add novelizer/agents/author.py tests/agents/test_author.py docs/submilestones/M5-finish.md
git commit -m "feat: causal graph injected into Author's drafting context (M4 re-evaluation verdict: adopt, scoped to existing causal_flags_note reuse)"
```

---

### Task 8: Staleness/sag-spike thresholds → settings, wired into `settings_screen.py`

**Files:**
- Modify: `novelizer/brain/staleness.py`, `novelizer/brain/sag_spike.py`,
  `novelizer/settings/models.py`, `novelizer/settings/layers.py`,
  `novelizer/settings/loader.py`, and every call site that invokes
  `find_stale_threads`/`detect_sag_spike` (grep first — likely `continuity_checker.py`,
  `structure_analyst.py`, and/or a Brain-view widget) to pass the setting value instead of
  relying on the function's default parameter.
- Test: `tests/brain/test_staleness.py`, `tests/brain/test_sag_spike.py`,
  `tests/settings/`

**Interfaces:**
- `EffectiveSettings.staleness_threshold_chapters: int = 3` and
  `EffectiveSettings.sag_spike_delta: float = 0.3` in `models.py`, both added to
  `STORY_OVERRIDABLE_KEYS` (director-tunable per story, same category as
  `prior_chapter_summary_chars`), neither in `RESTART_REQUIRED_KEYS` (read fresh per
  analyzer call, no cached construction to rebuild).
- `GlobalConfig`/`StoryConfig`/`EnvOverrides` each gain the two matching `| None = None`
  fields, following `llm_max_tokens`'s four-file pattern.
- `STALENESS_THRESHOLD_CHAPTERS`/`SAG_SPIKE_DELTA` module constants in `staleness.py`/
  `sag_spike.py` **stay as the function default parameter values** (so any direct caller
  in tests that doesn't pass an explicit threshold keeps today's behavior) — the change is
  that every **production** call site (agents, Brain views) now passes
  `settings.staleness_threshold_chapters`/`settings.sag_spike_delta` explicitly instead of
  relying on the default.

- [ ] **Step 1: Write the failing tests**

```python
def test_find_stale_threads_respects_explicit_threshold():
    # Two chapters since last touch, threshold=1 (stricter than default 3):
    # assert the thread IS flagged stale, where it would NOT be at the
    # default threshold=3.
    ...


def test_detect_sag_spike_respects_explicit_delta():
    # A score delta of 0.15: not flagged at default delta=0.3, but IS
    # flagged when delta=0.1 is passed explicitly.
    ...
```

Plus the settings-layer override-precedence tests mirroring `llm_max_tokens`'s existing
test (grep and copy the shape for both new fields).

Grep the actual call sites first:
```bash
grep -rn "find_stale_threads\|detect_sag_spike\|STALENESS_THRESHOLD_CHAPTERS\|SAG_SPIKE_DELTA" novelizer/
```
and write one characterization test per production call site confirming it currently uses
the hardcoded default, before changing it to pass the setting explicitly.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/brain/ tests/settings/ -v -k "staleness or sag_spike"`
Expected: the explicit-threshold tests already pass (the functions already accept a
`threshold`/`delta` parameter — this is wiring, not new analyzer logic) **except** the
settings-layer precedence tests, which FAIL with `AttributeError` until the fields exist.

- [ ] **Step 3: Implement**

Add the two settings fields across the four files. Update every production call site found
in Step 1's grep to pass `settings.staleness_threshold_chapters`/`settings.sag_spike_delta`
explicitly.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/brain/ tests/settings/ -v`, then `uv run pytest tests/ -v` for the
full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/brain/staleness.py novelizer/brain/sag_spike.py novelizer/settings/models.py novelizer/settings/layers.py novelizer/settings/loader.py tests/brain/ tests/settings/
git commit -m "feat: staleness_threshold_chapters and sag_spike_delta become settings-layer configurable (M4 backlog re-evaluation, wired into settings_screen via auto-discovery)"
```

---

### Task 9: `EmbeddingStore` — injectable `embedding_function`, `themes` collection, near-duplicate theme suggestion

**Files:**
- Modify: `novelizer/store/embeddings.py`, `novelizer/runtime.py` (or wherever
  `EmbeddingStore` is constructed — grep first), `novelizer/agents/editor.py` or a new
  small module for the suggestion logic (decide during implementation which fits better —
  recommend a `novelizer/brain/theme_similarity.py` sibling to `staleness.py`/
  `sag_spike.py`, since this is a Brain-adjacent analyzer, not agent-prompt logic).
- Test: `tests/store/test_embeddings.py`, `tests/brain/test_theme_similarity.py` (new)

**Interfaces:**
- `EmbeddingStore.__init__(self, path, embed_model=..., base_url=..., api_key=...,
  embedding_function=None)`: when `embedding_function` is provided, use it directly instead
  of constructing `OpenAIEmbeddingFunction(...)`; when `None` (default), current behavior
  unchanged. This is the CI-testability seam — the spec explicitly requires no live embed
  endpoint dependency in CI, and the remote endpoint has been observed down.
- A deterministic **fake embedding function** for tests: a simple callable/class matching
  chromadb's `EmbeddingFunction` protocol (`__call__(self, input: list[str]) ->
  list[list[float]]`) that hashes each string into a small fixed-dimension vector
  deterministically (e.g. character n-gram bag hashed into an 8-dim vector, normalized) —
  similar strings must land close in the vector space for the near-duplicate test to be
  meaningful, so a naive `hash(text) % N` one-hot won't work; use something like a bag-of-
  character-trigrams count vector (deterministic, no ML dependency, "similar text → similar
  vector" holds well enough for a fixed test fixture).
- `self._themes = self._client.get_or_create_collection("themes", embedding_function=ef)`
  added to `__init__`, alongside `_world`/`_chars`/`_chapters`.
- `EmbeddingStore.upsert_theme(theme: ThemeRecord) -> None`: embeds `theme.title`, upserts
  by `theme.id`.
- `EmbeddingStore.query_themes(query: str, n: int = 5) -> list[tuple[str, float]]` (id,
  distance) or similar — mirror `query_world_entries`'s shape but return enough to compute
  a near-duplicate suggestion (distance/similarity score), not just hydrated `ThemeRecord`s
  (the caller needs the score to threshold against).
- `novelizer/brain/theme_similarity.py`: `suggest_near_duplicate_theme(embedding_store,
  new_theme: ThemeRecord, threshold: float = <fixed default, document the exact value
  chosen — e.g. 0.15 cosine distance or equivalent for whatever chromadb's default distance
  metric is>) -> Optional[str]` — returns the id of an existing theme the new one may
  duplicate, or `None`. Pure suggestion — **never auto-merges**, per Locked decision 8/the
  non-goals list.
- Wiring: when a `theme.introduced` event is folded (i.e., after `Author`/`Editor` commits
  one via `_commit_theme_intents`), the embedding needs to be upserted and checked. Decide
  during implementation whether this lives in `_commit_theme_intents` itself (simplest —
  add an optional `embedding_store` parameter, no-op if `None`, called right after a
  successful `THEME_INTRODUCED` commit) or as a separate consumer — recommend the former
  for locality with the existing commit call, matching how this codebase keeps side effects
  next to the event that causes them rather than inventing a new listener/subscriber
  mechanism this milestone never asked for. When a near-duplicate is found, commit an
  Editor-facing suggestion via the existing `retcon_request.created` seam (same pattern as
  voice drift) tagged with a new `THEME_SIMILARITY_SOURCE_TAG = "[source:
  theme_similarity]"` constant, description citing both theme ids/titles — **not** a
  silent merge, not a new notification channel.

- [ ] **Step 1: Write the failing tests**

```python
# tests/store/test_embeddings.py
def test_embedding_store_accepts_injectable_embedding_function():
    from novelizer.store.embeddings import EmbeddingStore
    store = EmbeddingStore(path=str(tmp_path), embedding_function=FakeEmbeddingFunction())
    # No network call attempted; construction succeeds.


async def test_upsert_and_query_themes_roundtrip(tmp_path):
    store = EmbeddingStore(path=str(tmp_path), embedding_function=FakeEmbeddingFunction())
    await store.upsert_theme(ThemeRecord(id="loss", title="The Cost of Ambition"))
    results = await store.query_themes("The Cost of Ambition")
    assert results and results[0][0] == "loss"


# tests/brain/test_theme_similarity.py
async def test_suggest_near_duplicate_theme_finds_similar_title(tmp_path):
    store = EmbeddingStore(path=str(tmp_path), embedding_function=FakeEmbeddingFunction())
    await store.upsert_theme(ThemeRecord(id="loss", title="The Cost of Ambition"))
    suggestion = await suggest_near_duplicate_theme(
        store, ThemeRecord(id="loss2", title="The Price of Ambition")
    )
    assert suggestion == "loss"


async def test_suggest_near_duplicate_theme_returns_none_for_dissimilar_title(tmp_path):
    store = EmbeddingStore(path=str(tmp_path), embedding_function=FakeEmbeddingFunction())
    await store.upsert_theme(ThemeRecord(id="loss", title="The Cost of Ambition"))
    suggestion = await suggest_near_duplicate_theme(
        store, ThemeRecord(id="joy2", title="A Blossoming Friendship")
    )
    assert suggestion is None


async def test_commit_theme_intents_introduce_files_similarity_suggestion_retcon(stack):
    # With an EmbeddingStore(embedding_function=FakeEmbeddingFunction()) pre-
    # seeded with a near-duplicate theme, calling _commit_theme_intents with
    # a new "introduce" whose title is near-duplicate should, after the
    # theme.introduced commit, also commit a retcon_request.created tagged
    # THEME_SIMILARITY_SOURCE_TAG. No auto-merge -- the new theme.introduced
    # event still commits as its own distinct id.
    ...
```

Define `FakeEmbeddingFunction` in a shared test fixtures module (`tests/conftest.py` or
`tests/store/conftest.py`) so both test files reuse it.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/store/test_embeddings.py tests/brain/test_theme_similarity.py -v`
Expected: FAIL — `TypeError: unexpected keyword argument 'embedding_function'`, then
`ModuleNotFoundError: No module named 'novelizer.brain.theme_similarity'`.

- [ ] **Step 3: Implement**

In order: `embedding_function` injection param → `themes` collection + `upsert_theme`/
`query_themes` → `theme_similarity.py`'s `suggest_near_duplicate_theme` → the
`_commit_theme_intents` wiring (optional `embedding_store` param, no-op when `None`, so
every existing call site that doesn't pass one is unaffected — grep and confirm no existing
`_commit_theme_intents` call breaks).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/store/ tests/brain/ tests/agents/test_base.py -v`, then
`uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/store/embeddings.py novelizer/brain/theme_similarity.py novelizer/agents/base.py tests/store/ tests/brain/test_theme_similarity.py tests/conftest.py
git commit -m "feat: EmbeddingStore themes collection + injectable embedding_function (CI-safe, no live endpoint dependency) + Editor-facing near-duplicate theme suggestion via tagged retcon"
```

---

### Task 10: `chapter.revised` event — Editor "revise" actually revises in place

**Files:**
- Modify: `novelizer/canon/events.py`, `novelizer/canon/policy.py`,
  `novelizer/canon/projector.py`, `novelizer/agents/editor.py`,
  `novelizer/agents/author.py`, `novelizer/agents/schemas.py` (if a revise-target signal
  needs a typed field — check `DirectorSignal`'s existing shape first)
- Test: `tests/canon/test_events.py` (or wherever event registration is tested),
  `tests/canon/test_projector.py`, `tests/agents/test_editor.py`,
  `tests/agents/test_author.py`

**Problem (per Locked decision 10):** Today, Editor "revise" fires a
`DirectorSignal(kind=note, target_agent="author")` carrying free-text feedback. The Author
has no branch that treats this as "rewrite chapter X" — it just runs its normal
`work()`/`commit()` path next cycle and drafts an entirely new chapter, leaving the flagged
one sitting in `draft` status forever, un-revised, invisible to the reader as ever having
been flagged.

**Interfaces:**
- `EventType.CHAPTER_REVISED = "chapter.revised"` in `events.py`.
- `ChapterRevised(BaseModel)`: `chapter_id: str, prose: str, editor_notes_ref: str = ""` —
  payload per Locked decision 10 exactly (`chapter_id` is the existing id, no minting; no
  new title/character_ids fields — a revision changes prose, not the chapter's identity or
  cast, matching "the id already exists" framing).
- `CHAPTER_REVISED` joins `_CANON_EVENTS` in `policy.py` (same gating class as
  `CHAPTER_CREATED`/`CHAPTER_STATUS_CHANGED` — it rewrites canon prose).
- `Projector._apply` gains a `CHAPTER_REVISED` branch: `SELECT` the existing chapter row by
  `chapter_id`, `model_copy(update={"prose": payload.prose, "editorial_status":
  EditorialStatus.draft})` (a revision re-enters the draft→review cycle — set status back
  to `draft` so the Editor picks it up again next cycle, closing the loop instead of
  leaving it in whatever status it was flagged at), `INSERT OR REPLACE` — if no row exists
  for `chapter_id` (shouldn't happen under correct signal routing), log and no-op, matching
  every other "shouldn't happen" branch's precedent in this codebase.
- **Escape hatch (per the spec's "promotion escape hatch if oversized" wording)**: if the
  revised prose is implausibly large (define a fixed sanity bound, e.g. >4x the original
  chapter's prose length, or reuse whatever length-sanity check exists elsewhere in this
  codebase — grep for one first), do not silently truncate or reject; log a warning and
  commit anyway (event sourcing: the log is the truth, a length anomaly is a signal for a
  human/Retconner to notice via the feed, not something the Projector silently corrects).
  Confirm during implementation whether "promotion escape hatch" instead means "if this
  Editor/Author signal-routing turns out structurally harder than expected, promote the
  whole feature to its own branch" (re-reading Locked decision 10's exact wording — it says
  "an oversized chapter" in the M5.3 spec row context, and separately "if plan review finds
  this crowding out the sweep, it gets promoted to its own branch" as the *scope* escape
  hatch). Implement **both** readings: the small per-event length-anomaly log described
  above (mechanical, cheap), and treat the scope-level "promote to its own branch"
  instruction as this plan's Scope-risk flag (already stated up front) rather than a
  runtime code path.
- **Revise-target signal routing**: `DirectorSignal` needs the Author's revise branch to be
  able to identify *which* chapter to revise. Check `DirectorSignal`'s current fields
  (`kind`, `body`, `target_agent`) — `body` currently carries free-text editor notes. Add a
  `target_entity: str = ""` field to `DirectorSignal` (if not already present — grep first,
  `focus` signals may already use a field like this for the entity string) carrying the
  flagged chapter's id, and a new `SignalKind.revise` value (sibling to `SignalKind.note`/
  `.seed`/`.focus`) so the Author's `poll()`/`commit()` can distinguish "write a new
  chapter" signals from "revise chapter X" signals by `kind`, not by parsing `body` text.
- `Editor.commit()`'s "revise" branch changes from committing a `DirectorSignal(kind=note,
  ...)` to `DirectorSignal(kind=SignalKind.revise, body=verdict.notes,
  target_agent="author", target_entity=ch.id)`.
- `Author.poll()` gains a `revise_signals = [s for s in ctx["signals"] if s.kind ==
  SignalKind.revise]` (or filter within `work()`/`commit()` — decide the cleanest split;
  recommend filtering in `work()` since it changes which schema/output path runs). If a
  revise signal is present, `Author.work()` takes a distinct branch: build a revise-specific
  prompt (existing chapter prose + editor notes + "rewrite this chapter addressing the
  feedback" framing, reusing `_guarded_line`/existing context-building helpers where they
  fit) and request output shaped for a revision — decide whether this reuses `ChapterDraft`
  (title/prose/character_ids/thread_intents/etc. — a revision could still touch threads/
  secrets/themes) or a narrower schema; **recommend reusing `ChapterDraft`** since a
  revision is still "the Author writing chapter prose with the same set of possible
  narrative intents," just targeting an existing id instead of minting one — the only
  behavioral fork is in `Author.commit()`, which for a revise signal commits
  `CHAPTER_REVISED` (payload `chapter_id=revise_signal.target_entity, prose=draft.prose,
  editor_notes_ref=revise_signal.id`) instead of `CHAPTER_CREATED`, and still runs the same
  thread/theme/knowledge/causal intent commits afterward (a revision can still plant/touch/
  develop, same as a fresh chapter) minus re-minting a new chapter id anywhere.
  `Author.commit()` must consume the revise signal via `_consume_signals` same as any other
  signal, so it isn't reprocessed next cycle.

- [ ] **Step 1: Write the failing tests**

```python
# tests/canon/test_projector.py
async def test_chapter_revised_replaces_prose_same_chapter_id(stack):
    events, proj, read, committer = stack
    ch = Chapter(title="T", prose="original")
    await committer.commit("author", EventType.CHAPTER_CREATED, ch.id, ch)
    await proj.catch_up()
    await committer.commit("editor", EventType.CHAPTER_REVISED, ch.id,
                            ChapterRevised(chapter_id=ch.id, prose="revised prose"))
    await proj.catch_up()
    chapters = await read.list_chapters()
    assert len(chapters) == 1  # chapter count unchanged, not a new chapter
    revised = await read.get_chapter(ch.id)
    assert revised.prose == "revised prose"
    assert revised.editorial_status == EditorialStatus.draft  # re-enters review


async def test_chapter_revised_is_canon_gated(stack):
    from novelizer.canon.policy import AutonomyPolicy
    events, proj, read, committer = stack
    policy = AutonomyPolicy(read)
    assert await policy.is_gated("author", EventType.CHAPTER_REVISED) is True  # same class as CHAPTER_CREATED


# tests/agents/test_editor.py
async def test_editor_revise_verdict_commits_revise_signal_with_target_entity(stack):
    # FakeRunner returns EditorVerdict(verdict="revise", notes="fix pacing").
    # run_once(); assert a director_signal.created event with
    # kind=SignalKind.revise, target_agent="author", target_entity=<chapter id>.
    ...


# tests/agents/test_author.py
async def test_author_revise_signal_commits_chapter_revised_not_chapter_created(stack):
    # Seed a chapter, then a director_signal (kind=revise, target_entity=<chapter id>).
    # FakeRunner returns ChapterDraft(title=..., prose="fixed prose", ...).
    # run_once(); assert a chapter.revised event exists for that chapter id,
    # NO new chapter.created event, and chapter count via list_chapters() unchanged.


async def test_author_revise_signal_still_commits_thread_intents(stack):
    # Revision path still processes thread_intents/theme_intents/etc same as
    # a fresh chapter -- assert one of those commits alongside chapter.revised.


async def test_author_revise_signal_is_consumed(stack):
    # After run_once(), the revise signal no longer appears in
    # list_unconsumed_signals(target_agent="author").
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/canon/ tests/agents/test_editor.py tests/agents/test_author.py -v -k revis`
Expected: FAIL — `AttributeError: type object 'EventType' has no attribute 'CHAPTER_REVISED'`,
then cascading failures as each interface is added.

- [ ] **Step 3: Implement**

In order: `EventType.CHAPTER_REVISED` + `ChapterRevised` payload + `_CANON_EVENTS`
registration → `SignalKind.revise` + `DirectorSignal.target_entity` → Projector fold →
`Editor.commit()`'s revise branch → `Author`'s revise-signal detection and commit branch.
Re-audit both `editor.py` and `author.py`'s `ctx[...]`/`ctx.get(...)` usage per Locked
decision 9 while both files are open for this task.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/canon/ tests/agents/ -v`, then `uv run pytest tests/ -v` for the
full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/events.py novelizer/canon/policy.py novelizer/canon/projector.py novelizer/agents/editor.py novelizer/agents/author.py novelizer/agents/schemas.py novelizer/store/models.py tests/canon/ tests/agents/test_editor.py tests/agents/test_author.py
git commit -m "feat: chapter.revised event — Editor revise verdict now rewrites the flagged chapter in place instead of prompting a brand-new chapter (M4 backlog correctness bug, Locked decision 10)"
```

---

### Task 11: `max_concurrent_agents` setting + Scheduler dispatch pool (part 1 — dispatch shape, no live behavior change at pool size 1)

**Files:**
- Modify: `novelizer/scheduler.py`, `novelizer/settings/models.py`,
  `novelizer/settings/layers.py`, `novelizer/settings/loader.py`, `novelizer/runtime.py`
- Test: `tests/test_scheduler.py` (grep for the exact existing filename first)

**Interfaces (per Locked decision 11):**
- `EffectiveSettings.max_concurrent_agents: int = 2` in `models.py` — default 2 per the
  user's explicit direction ("we should be constantly calling inference"), 1 remains
  available as an explicit serial fallback. Add to `STORY_OVERRIDABLE_KEYS`? No — this is a
  runtime-behavior/capacity setting tied to the inference endpoint's concurrency headroom,
  closer to `llm_base_url` than to `author_temperature`; **recommend global-only** (not in
  `STORY_OVERRIDABLE_KEYS`), matching how connection-capacity settings, not per-story
  authorial choices, are scoped in this codebase. Not in `RESTART_REQUIRED_KEYS` either —
  the Scheduler reads it per-`tick()`, no cached construction to rebuild (confirm this
  during implementation; if the Scheduler ends up caching a pool-size-derived structure at
  construction time, add it to `RESTART_REQUIRED_KEYS` instead and note the deviation).
- `GlobalConfig`/`EnvOverrides` gain `max_concurrent_agents: int | None = None` (not
  `StoryConfig`, matching the global-only scoping above).
- `Scheduler.__init__` gains `max_concurrent_agents: int = 2` parameter, stored as
  `self._max_concurrent`.
- `Scheduler._in_flight: dict[str, asyncio.Task]` — tracks agent name → its currently-
  running `run_once()` task. An agent already in `_in_flight` is excluded from
  `eligible` in `tick()` (a fifth exclusion criterion alongside `not a.paused` and
  `a.ready_for_interval(now)`).
- `Scheduler.tick()` rework: instead of picking the single best-scored eligible agent and
  `await`-ing it inline, it fills up to `self._max_concurrent - len(self._in_flight)` free
  pool slots from the readiness-sorted eligible list (highest score first, `override`
  target still takes priority same as today — if an override target is eligible and has a
  free slot, dispatch it first, then fill remaining slots by score), creating an
  `asyncio.create_task(self._run(agent, now))` for each and storing it in `_in_flight`
  keyed by agent name, **without awaiting them** — `tick()` returns promptly (its return
  type changes from `Optional[str]` (single name) to `list[str]` (names dispatched this
  tick) — **check every existing caller of `tick()`'s return value** (grep `\.tick()` across
  `novelizer/` and `tests/`) and update them for the new list-shaped return.
- `Scheduler._run(agent, now)` gains a `finally`-block addition: pop the agent from
  `self._in_flight` (in addition to its existing `agent.mark_ran(now)` — `mark_ran` fires
  on task **completion**, not dispatch, exactly per Locked decision 11's explicit
  requirement, which the current serial `_run` already satisfies structurally since
  `mark_ran` is already in the `finally` block; concurrency changes *when `_run` starts*,
  not `_run`'s own completion-marking behavior, which is why this part of Locked decision
  11 is "preserve," not "add").
- `Scheduler.run()`'s loop: since `tick()` no longer awaits agent completion, the sleep-
  loop cadence (`await asyncio.sleep(self._tick_sleep)`) becomes the *dispatch* cadence, not
  a "wait for the last agent to finish" cadence — this is the actual concurrency
  unlock (today, one slow agent blocks every other agent's turn for its full duration; with
  `_in_flight` tracking, `tick()` polls and fills free slots every `tick_sleep` seconds
  regardless of what's still running). Confirm `run()`'s existing `except Exception:
  logger.exception(...)` still applies correctly — `tick()` itself should not raise from
  a dispatched task's failure (task exceptions surface via `_run`'s own `except`, which
  crash-handles by recording `_last_error` and re-raising *within the task*, not into
  `tick()`'s caller) — this must NOT crash the scheduler loop or leave `_in_flight` with a
  stale entry; confirm the crash-handling precedent from master's external commit
  `c70b8f6` (roster ⚠ / `last_error`) still fires correctly per-task under concurrency, not
  just per-`tick()`.
- `Scheduler.status()` gains: `"running": a.name in self._in_flight` (replacing the current
  single-agent `a.name == self._last_ran` comparison, which was already a minor
  pre-existing inaccuracy under serial execution too — it showed the *last completed*
  agent as "running," not the currently in-flight one; concurrency makes this
  inaccuracy impossible to ignore, so fixing it is in-scope here, not a separate task).

- [ ] **Step 1: Write the failing tests**

Read the existing scheduler test file in full first (`grep -rln "class Scheduler\|from
novelizer.scheduler" tests/`) for its `FakeAgent`/clock-mocking conventions. Append:

```python
async def test_tick_dispatches_up_to_max_concurrent_agents(scheduler_with_pool_2, fake_agents):
    # Two ready agents, pool size 2: tick() dispatches both without
    # awaiting completion -- assert both appear in scheduler._in_flight
    # immediately after tick() returns, before either has finished.
    ...


async def test_tick_does_not_exceed_pool_size(scheduler_with_pool_2, fake_agents):
    # Three ready agents, pool size 2, none currently in flight: tick()
    # dispatches exactly 2 (highest-readiness-scored), the third stays
    # eligible for the next tick.
    ...


async def test_agent_already_in_flight_is_excluded_from_next_tick(scheduler_with_pool_2, slow_fake_agent):
    # Dispatch a slow agent (long-running fake run_once()). Call tick()
    # again before it completes: assert it is NOT re-dispatched (no second
    # task created for the same agent name).
    ...


async def test_status_reflects_agents_currently_in_flight(...):
    # After dispatching, before completion, status() shows running=True
    # for the in-flight agent(s), running=False for others.
    ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_scheduler.py -v -k concurrent`
Expected: FAIL — `AttributeError: 'Scheduler' object has no attribute '_in_flight'`.

- [ ] **Step 3: Implement**

Rework `Scheduler` per the interfaces above. Grep every existing caller of `tick()`'s
return value (`novelizer/`, `tests/`) and update for the `list[str]` shape. Add
`max_concurrent_agents` to `runtime.py`'s `Scheduler(...)` construction
(`max_concurrent_agents=s.max_concurrent_agents`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_scheduler.py -v`, then `uv run pytest tests/ -v` for the full
suite green — **pay close attention to any existing test asserting `tick()`'s return value
is a single string or `None`**; those must be updated to the new list shape, not deleted.

- [ ] **Step 5: Commit**

```bash
git add novelizer/scheduler.py novelizer/settings/models.py novelizer/settings/layers.py novelizer/settings/loader.py novelizer/runtime.py tests/test_scheduler.py
git commit -m "feat: max_concurrent_agents setting (default 2) + Scheduler dispatch pool — tick() fills free in-flight slots by readiness score instead of awaiting one agent to completion"
```

---

### Task 12: Concurrency proof obligation 1 — overlap test (asyncio-clock instrumentation, no live LLM)

**Files:**
- Test: `tests/test_scheduler.py`

**Interfaces:** No new production code — this is the first of Locked decision 11's four
named CI proof obligations, verifying Task 11's dispatch shape actually produces temporal
overlap, not just non-blocking `tick()` returns.

- [ ] **Step 1: Write the test**

```python
async def test_two_slow_agents_run_overlapped_not_sequentially(scheduler_with_pool_2):
    """Two FakeAgents whose run_once() each sleep 0.1s and record
    (name, start_time, end_time) using the event loop's own clock. Dispatch
    both via tick() (pool size >=2), await both in-flight tasks to
    completion (drain _in_flight), then assert agent B's start_time is
    BEFORE agent A's end_time -- proof of genuine overlap, not just fast
    sequential execution. A serial scheduler (pool size 1, or the pre-Task-11
    tick()) would fail this assertion: B's start would always be >= A's end.
    """
    ...
```

Use `asyncio.get_event_loop().time()` (or the scheduler's own injectable `clock`) for
start/end timestamps, not wall-clock `time.time()`, to keep the test fast and deterministic
under `pytest-asyncio`'s event loop.

- [ ] **Step 2: Run to verify it fails against a pool-size-1 configuration (sanity check
  the test actually discriminates)**

Run the same test with `max_concurrent_agents=1` explicitly and confirm it correctly FAILS
(no overlap possible at pool size 1) — this is a meta-check that the test is not a false
positive/vacuously-true assertion. Then run it against the real `scheduler_with_pool_2`
fixture and confirm PASS.

- [ ] **Step 3: N/A** — no production code change expected if Task 11 was implemented
  correctly.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_scheduler.py -v -k overlap`, then `uv run pytest tests/ -v`
for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add tests/test_scheduler.py
git commit -m "test: prove two slow agents genuinely overlap under pool size 2 (Locked decision 11 proof obligation 1), with a pool-size-1 discriminating sanity check"
```

---

### Task 13: Concurrency proof obligations 2 & 3 — no-double-dispatch + pool-1-serial-equivalence

**Files:**
- Test: `tests/test_scheduler.py`

- [ ] **Step 1: Write the tests**

```python
async def test_same_agent_never_double_dispatched_across_ticks(scheduler_with_pool_2, slow_fake_agent):
    """Call tick() repeatedly (faster than the slow agent's run_once()
    completes) while it stays eligible (ready_for_interval keeps returning
    True). Assert only ONE asyncio.Task was ever created for that agent
    name across all the tick() calls -- _in_flight exclusion holds under
    repeated polling, not just a single extra tick()."""
    ...


async def test_pool_size_1_reproduces_todays_serial_ordering_exactly(fake_agents_with_distinct_scores):
    """With max_concurrent_agents=1, dispatch order and one-at-a-time
    execution must exactly match the pre-Task-11 single-best-score
    behavior: given three eligible agents with distinct readiness scores,
    tick() dispatches only the highest-scored one, and it must complete
    (leave _in_flight) before the next tick() dispatches another -- assert
    via the same start/end-time overlap check as Task 12's test, but
    asserting NO overlap at pool size 1, proving pool size 1 is a true
    serial fallback, not concurrency-with-a-cap-of-one that happens to
    rarely overlap."""
    ...
```

- [ ] **Step 2–4:** Same red→green→full-suite pattern as prior tasks. These should largely
  pass immediately if Task 11's `_in_flight` exclusion and pool-size gating are correct —
  if either fails, that names a real gap in Task 11, fix Task 11, do not weaken these
  tests.

- [ ] **Step 5: Commit**

```bash
git add tests/test_scheduler.py
git commit -m "test: prove no-double-dispatch across repeated ticks and pool-size-1 exact serial equivalence (Locked decision 11 proof obligations 2 and 3)"
```

---

### Task 14: Concurrency proof obligation 4 — K-agent concurrent-commit log-integrity property test

**Files:**
- Test: `tests/test_scheduler.py` or `tests/canon/test_scheduler_concurrency_property.py`
  (new file — recommend the new file, since this is a Hypothesis property test over the
  full event-log stack, not scheduler-internals-only like Tasks 12–13)

**Interfaces:** No new production code expected. This is the property test Locked decision
11 explicitly names: "concurrent commits from K fake agents produce a log whose
per-aggregate event ordering is still valid (no interleaving corruption)."

- [ ] **Step 1: Write the failing test**

```python
from hypothesis import given, settings as hyp_settings, strategies as st

@given(k=st.integers(min_value=2, max_value=5), commits_per_agent=st.integers(min_value=1, max_value=5))
@hyp_settings(max_examples=20, deadline=None)
async def test_k_concurrent_agents_produce_valid_per_aggregate_ordering(stack, k, commits_per_agent):
    """Falsification check: K fake agents, each committing a distinct
    number of events to DISTINCT aggregate ids (e.g. each agent owns its
    own chapter id, so cross-agent races on the SAME aggregate aren't the
    thing under test here -- that's a separate, narrower concern already
    covered by SQLite's serialization via aiosqlite's connection lock, per
    Locked decision 11's own rationale), dispatched concurrently via
    asyncio.gather (simulating the Scheduler's pool dispatch without
    needing the full Scheduler class), commit through the real
    Committer/EventStore stack. After catch_up(), assert:
    1. Every committed event for every agent's aggregate id is present
       (no lost writes under concurrent access).
    2. Within each agent's own aggregate id, the events replay in the
       exact order that agent issued them (per-aggregate ordering
       preserved -- concurrency across agents must not interleave a
       single agent's own event sequence out of order).
    3. Total committed event count == k * commits_per_agent (no
       duplication, no silent drops).
    If this test ever finds a violation, that is a real concurrency bug in
    the EventStore/Committer under concurrent access -- do not weaken the
    assertion to make it pass; report it and treat Task 11 as blocked
    until resolved, since aiosqlite's connection-lock serialization is the
    entire safety argument Locked decision 11 rests on.
    """
    ...
```

- [ ] **Step 2: Run tests to verify they fail (or pass — see note)**

Run: `uv run pytest tests/canon/test_scheduler_concurrency_property.py -v`
Expected: this exercises the `EventStore`/`Committer` stack, which predates this plan and
should already be safe under concurrent `await`s per aiosqlite's connection-lock guarantee
— so this test may **pass on first run**, same formality as prior sub-milestones' "property
test of already-correct behavior" precedent. If it fails, that's a real, load-bearing gap:
stop, do not proceed to Task 15, and report it — the entire concurrency feature's safety
argument depends on this holding.

- [ ] **Step 3: N/A (unless Step 2 finds a real bug, in which case fix the EventStore/
  Committer, not this test)**

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/canon/test_scheduler_concurrency_property.py -v`, then
`uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add tests/canon/test_scheduler_concurrency_property.py
git commit -m "test: Hypothesis property proof — K concurrent fake agents produce a log with valid per-aggregate ordering, no lost/duplicated writes (Locked decision 11 proof obligation 4)"
```

---

### Task 15: Optional live observation note — two agents genuinely overlapping against the real endpoint

**Files:**
- Create: `tests/test_scheduler_live_observation.py` (or record as a manual run note only —
  see below; this task is explicitly non-blocking and not part of M5.3's CI-mechanical
  done-when, which per the spec has no live_llm half)

**Rationale:** The spec is explicit that M5.3's done-when is mechanical-only. This task
exists because the user cares specifically about saturation, not because the milestone
requires it — treat it as optional and time-boxed; skip it entirely if the environment's
live endpoint isn't reachable or if Tasks 11–14 already consumed the plan's budget.

- [ ] **Step 1 (optional): Observe real overlap**

Using `load_effective_settings()` and two real agents (e.g. `Author` + `Editor`, or two
independent-aggregate agents unlikely to contend), run the real `Runtime`/`Scheduler` with
`max_concurrent_agents=2` against the configured live endpoint for a short window, with
request-start/request-end logging (or the same in-flight-task timestamp technique from Task
12) added temporarily. Confirm two agents' inference calls are observed genuinely in flight
concurrently against the real endpoint (not just two fast fake calls). This is **not** a
`pytest.mark.live_llm` test that must pass in CI going forward — record the observation
(pass/fail, timestamps, any endpoint-side rejection of concurrent requests) as a short note.

- [ ] **Step 2: Record the observation**

Add a short paragraph to `docs/submilestones/M5-finish.md`'s M5.3 closeout note (Task 16)
recording what was observed, or explicitly "skipped — optional, not attempted, reason:
<time/environment>" if not run. Either outcome is acceptable and reportable.

- [ ] **Step 3: Commit (only if a temporary observation script/test was added and is worth
  keeping as a documented manual-run artifact; otherwise skip — do not commit throwaway
  instrumentation)**

---

### Task 16: Full-suite verification + M5.3 done-when trace (final gate)

**Files:** `docs/submilestones/M5-finish.md` — status update and closeout note.

- [ ] **Step 1: Run the full suite**

```bash
uv run pytest tests/ -v
```
Confirm zero failures, zero errors, `live_llm`-marked tests deselected by default.

- [ ] **Step 2: Trace the M5.3 done-when (a) clause chain literally**

Re-read the M5.3 row's done-when sentence in `docs/submilestones/M5-finish.md` verbatim and
confirm, clause by clause, which task/test proves each:
- "casing normalized at commit boundary" → Task 1.
- "`_guarded_line` produces byte-identical output to the strings it replaces" → Task 2.
- "setting is read from story/env override" (the 200-char window) → Task 3.
- "CLI commands covered by `CliRunner`" → Task 5.
- "Editor 'revise' produces a `chapter.revised` event referencing the original chapter id —
  not a new `chapter.created` — and the read model shows the revised prose under the same
  chapter id with the chapter count unchanged" → Task 10.
- "embedding-similarity suggestion fires above a fixed default threshold in a seeded
  fixture with two near-duplicate theme titles, using an injected deterministic fake
  embedding function — no live embed endpoint in CI" → Task 9.
- (Concurrency, added to the spec by the user-directed 2026-07-18 addendum, not the
  original done-when table cell but Locked decision 11's own explicit proof-obligation
  list) → Tasks 11–14.

If any clause has no corresponding test, stop and add it before declaring the plan
complete — do not close this task on an incomplete trace.

- [ ] **Step 3: Update `docs/submilestones/M5-finish.md`'s M5.3 status and closeout note**

Change the M5.3 row's `Status` cell from `not started` to `complete (CI-proven; mechanical-
only per spec, no live_llm half; concurrency live observation: <recorded outcome from Task
15>)`. Add a closeout note recording: every backlog item's disposition (landed/verdict),
the causal-graph verdict recorded in Task 7, the staleness/sag-spike settings landed in
Task 8, and any deviation any task's executor made from this plan as written (e.g. if
`chapter.revised` or concurrency was promoted to its own branch per the scope-risk flag,
record that here instead of a completion claim).

- [ ] **Step 4: Commit**

```bash
git add docs/submilestones/M5-finish.md
git commit -m "docs: mark M5.3 complete — done-when traced clause by clause, backlog triage disposition recorded"
```
