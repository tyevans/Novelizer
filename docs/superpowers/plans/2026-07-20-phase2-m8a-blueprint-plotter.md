# M8a "Blueprint & Plotter" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The authored story blueprint enters canon (framework + beats + rolling chapter briefs), written and maintained by a new Plotter agent, consumed by the Author — the first half of Phase 2's M8 (docs/MILESTONES.md). M8b (beat-drift/tension-target Brain comparators, Outline board tab, OutlineBackend) follows in its own plan.

**Architecture:** Three new aggregates following the established recipe (events → projection → ReadStore → intents). `blueprint.adopted` is the system's first **always-gated** event (routes to the proposal queue at every autonomy level via a new `_ALWAYS_GATED` first-check in policy). Beats are minted with the blueprint from a built-in template module (`canon/beat_templates.py`) — beat targets are chapter windows derived from `ideal_pct × target_chapter_count`. Briefs are rolling-wave: drafted/superseded/fulfilled, one open brief per future ordinal. The Plotter is a new LLM `BaseAgent` mirroring the StructureAnalyst's shape; the Author consumes the open brief for the next ordinal as a push block (the assignment) and emits `chapter_brief.fulfilled` on commit, mirroring Muse-hand consumption.

**Tech Stack:** Python 3.13, aiosqlite, pydantic v2, Hypothesis, pytest asyncio_mode=auto, deepagents 0.6.12.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-19-authoring-skills-blueprint-design.md` §"Blueprint (story shape)", §"Chapter briefs", §"Write path", §"Agent changes". Roadmap row: docs/MILESTONES.md M8.
- Event-sourcing rules: minted-once ids; later events cite ids; projector no-ops for unknown/terminal ids; **every new table added to `Projector._reset_state_locked`'s tuple** (canon/projector.py ~line 130).
- Gating: `blueprint.adopted` ALWAYS gated (new `_ALWAYS_GATED` set checked before `_NEVER_GATED` in `AutonomyPolicy.is_gated`, canon/policy.py:60-67); `blueprint.retargeted`, `beat.fulfilled`, `chapter_brief.*` go in `_NEVER_GATED`. Do NOT add `blueprint.adopted` to `_CANON_EVENTS` (would un-gate it at full_auto).
- Windows/ordinals: chapters are ordered by rowid; ordinal = 1-based list position; beat target window = `[round(ideal_pct*N) - tol, round(ideal_pct*N) + tol]` clamped to `[1, N]` where `N = target_chapter_count`, `tol = max(1, round(tolerance_pct*N))` — computed in projection-independent pure code (Task 1), reused everywhere, never re-derived.
- Prompt notes: empty-string-when-quiet; the Author's brief block is push (the assignment), not a note.
- Plotter runner: mirror `build_structure_analyst_runner` (agents/structure_analyst.py:80-101): tooled branch via `_tooled`, `ExcludeToolsMiddleware(excluded=frozenset({"write_todos"}))`... EXCEPTION: the Plotter is a planner — `write_todos` plausibly helps it. Give the Plotter todos (NO exclusion middleware), matching the Author. Document this in the builder docstring.
- **Run all tests in this worktree, NEVER the main checkout.** `uv run pytest <path> -v`, synchronous.
- Kishōtenketsu discipline: templates must not require conflict/antagonist fields; `expected_polarity` may be `""`.

---

### Task 1: Beat templates + window math (pure)

**Files:**
- Create: `novelizer/canon/beat_templates.py`
- Test: `tests/canon/test_beat_templates.py`

**Interfaces:**
- Produces:

```python
class TemplateBeat(BaseModel):        # pydantic, frozen
    slug: str            # e.g. "midpoint" — beat ids mint as f"{blueprint_id}-{slug}"
    name: str            # "Midpoint"
    ideal_pct: float     # 0.0-1.0
    tolerance_pct: float # e.g. 0.08
    expected_polarity: str = ""   # "", "up", "down", "flip"

BEAT_TEMPLATES: dict[str, list[TemplateBeat]]   # keys: "six-position", "kishotenketsu"
def beat_window(ideal_pct: float, tolerance_pct: float, target_chapter_count: int) -> tuple[int, int]
```

- `six-position` template (from the research synthesis): catalyst 0.10, threshold 0.25, midpoint 0.50 (expected_polarity "flip"), low-point 0.75 ("down"), final-turn 0.80 ("up"), climax 0.90 ("up"). tolerance_pct 0.05 for all.
- `kishotenketsu`: ki 0.05, sho 0.40, ten 0.75 ("flip"), ketsu 0.95. tolerance_pct 0.08. No polarity requirements except ten.
- `beat_window`: `center = round(ideal_pct * n)`; `tol = max(1, round(tolerance_pct * n))`; return `(max(1, center - tol), min(n, max(1, center + tol)))`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/canon/test_beat_templates.py
from hypothesis import given
from hypothesis import strategies as st

from novelizer.canon.beat_templates import BEAT_TEMPLATES, beat_window


def test_six_position_template_shape():
    beats = BEAT_TEMPLATES["six-position"]
    assert [b.slug for b in beats] == [
        "catalyst", "threshold", "midpoint", "low-point", "final-turn", "climax"]
    assert beats[2].ideal_pct == 0.50 and beats[2].expected_polarity == "flip"
    assert all(0.0 < b.ideal_pct < 1.0 for b in beats)
    assert [b.ideal_pct for b in beats] == sorted(b.ideal_pct for b in beats)


def test_kishotenketsu_has_no_required_polarity_except_ten():
    beats = BEAT_TEMPLATES["kishotenketsu"]
    assert [b.slug for b in beats] == ["ki", "sho", "ten", "ketsu"]
    assert beats[2].expected_polarity == "flip"
    assert all(b.expected_polarity == "" for i, b in enumerate(beats) if i != 2)


def test_beat_window_basic():
    assert beat_window(0.50, 0.05, 20) == (9, 11)      # center 10, tol 1
    assert beat_window(0.90, 0.05, 20) == (17, 19)


def test_beat_window_clamps_to_book():
    lo, hi = beat_window(0.05, 0.05, 10)
    assert lo >= 1
    lo, hi = beat_window(0.95, 0.10, 10)
    assert hi <= 10


@given(st.floats(0.01, 0.99), st.floats(0.01, 0.2), st.integers(3, 200))
def test_beat_window_invariants(pct, tol, n):
    lo, hi = beat_window(pct, tol, n)
    assert 1 <= lo <= hi <= n
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/canon/test_beat_templates.py -v` — ModuleNotFoundError.

- [ ] **Step 3: Implement** exactly per Interfaces (module docstring: "Built-in structural beat templates. Craft reference data — richer templates arrive as skills packs in M10; adopting a framework mints Beat rows from one of these lists. Kishōtenketsu is deliberately conflict-optional: templates must never require an antagonist or non-empty polarity.").

- [ ] **Step 4: Run to verify pass** — all PASS.

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon/beat_templates.py tests/canon/test_beat_templates.py
git commit -m "feat(canon): built-in beat templates + window math"
```

---

### Task 2: Events + payloads

**Files:**
- Modify: `novelizer/canon/events.py` (constants after the M7 promise block; payloads at end)
- Test: `tests/canon/test_events.py` (append)

**Interfaces:**
- Produces constants: `BLUEPRINT_ADOPTED = "blueprint.adopted"`, `BLUEPRINT_RETARGETED = "blueprint.retargeted"`, `BEAT_FULFILLED = "beat.fulfilled"`, `CHAPTER_BRIEF_DRAFTED = "chapter_brief.drafted"`, `CHAPTER_BRIEF_SUPERSEDED = "chapter_brief.superseded"`, `CHAPTER_BRIEF_FULFILLED = "chapter_brief.fulfilled"`.
- Payload models:

```python
class BeatSpec(BaseModel):
    """One beat minted with a blueprint. beat_id = f"{blueprint_id}-{slug}"
    — minted once at adoption; beat.fulfilled cites it exactly."""
    beat_id: str
    slug: str
    name: str
    ideal_pct: float
    tolerance_pct: float
    expected_polarity: str = ""


class BlueprintAdopted(BaseModel):
    """Payload for blueprint.adopted — mints the story's structural frame.

    ALWAYS routed through the proposal queue regardless of autonomy level
    (Locked decision #1: adopting a shape re-frames the whole book; the
    Director signs off). One blueprint is active at a time: adoption
    supersedes any prior blueprint in projection (Locked decision #2).
    `blueprint_id` is minted by the proposing side (uuid); beats are minted
    with it from a template (canon/beat_templates.py)."""
    blueprint_id: str
    framework: str
    target_chapter_count: int
    genre: str = ""
    beats: list[BeatSpec] = Field(default_factory=list)
    obligatory_scenes: list[str] = Field(default_factory=list)
    note: str = ""


class BlueprintRetargeted(BaseModel):
    """Payload for blueprint.retargeted — the book is running long/short;
    beat windows recompute from the new count in read-side logic. Cites the
    active blueprint id; unknown/superseded ids are projection no-ops."""
    blueprint_id: str
    target_chapter_count: int


class BeatFulfilled(BaseModel):
    """Payload for beat.fulfilled — the Plotter judges a drafted chapter
    carried the beat, cited by beat_id. Re-emission supersedes (the room may
    re-judge which chapter truly carried the midpoint). chapter_id="" clears
    a fulfillment."""
    beat_id: str
    chapter_id: str = ""
    note: str = ""


class ChapterBriefDrafted(BaseModel):
    """Payload for chapter_brief.drafted — the plan for a near-future
    chapter; the Plotter's main output, the Author's assignment.

    `brief_id` minted once (uuid) at draft. `target_ordinal` is a 1-based
    future chapter ordinal; briefs for already-drafted ordinals are dropped
    at commit (plan the future, not the past). Cited thread/beat/promise ids
    are validated at commit; unknown ids are dropped from the lists with a
    warning, never fail the brief."""
    brief_id: str
    target_ordinal: int
    goal: str
    pov_character_id: str = ""
    threads_to_touch: list[str] = Field(default_factory=list)
    beats_to_hit: list[str] = Field(default_factory=list)
    promises_to_progress: list[str] = Field(default_factory=list)
    value_shift: str = ""        # e.g. "trust: + -> -"
    planned_outcome: str = ""    # yes | yes_but | no_and | no
    synopsis: str = ""


class ChapterBriefSuperseded(BaseModel):
    """Payload for chapter_brief.superseded — terminal; the replacing brief
    (if any) is its own drafted event."""
    brief_id: str
    superseded_by_brief_id: str = ""


class ChapterBriefFulfilled(BaseModel):
    """Payload for chapter_brief.fulfilled — the Author drafted against this
    brief; terminal and absorbing."""
    brief_id: str
    chapter_id: str
```

- [ ] **Step 1: failing tests** — append to tests/canon/test_events.py, same shape as the M7 promise-payload test: construct each with minimal args, assert defaults (`BlueprintAdopted(blueprint_id="b", framework="six-position", target_chapter_count=24).genre == ""`; `BeatFulfilled(beat_id="x").chapter_id == ""`; `ChapterBriefDrafted(brief_id="r", target_ordinal=3, goal="g").planned_outcome == ""`), assert the six constant strings.
- [ ] **Step 2: verify failure** (ImportError). **Step 3: implement.** **Step 4:** `uv run pytest tests/canon/test_events.py -q` PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(canon): blueprint, beat, and chapter-brief event types"`

---

### Task 3: Read models

**Files:**
- Modify: `novelizer/store/models.py`
- Test: `tests/canon/test_blueprint_models.py` (new)

**Interfaces:**

```python
class BriefStatus(StrEnum):
    open = "open"
    superseded = "superseded"
    fulfilled = "fulfilled"


class BeatRecord(BaseModel):
    id: str                  # beat_id
    blueprint_id: str
    slug: str
    name: str
    ideal_pct: float
    tolerance_pct: float
    expected_polarity: str = ""
    fulfilled_by_chapter_id: str = ""
    note: str = ""


class BlueprintRecord(BaseModel):
    id: str
    framework: str
    target_chapter_count: int
    genre: str = ""
    obligatory_scenes: list[str] = Field(default_factory=list)
    active: bool = True      # adoption supersedes the prior active blueprint
    note: str = ""


class ChapterBriefRecord(BaseModel):
    id: str
    target_ordinal: int
    goal: str
    pov_character_id: str = ""
    threads_to_touch: list[str] = Field(default_factory=list)
    beats_to_hit: list[str] = Field(default_factory=list)
    promises_to_progress: list[str] = Field(default_factory=list)
    value_shift: str = ""
    planned_outcome: str = ""
    synopsis: str = ""
    status: BriefStatus = BriefStatus.open
    superseded_by_brief_id: str = ""
    fulfilled_by_chapter_id: str = ""
```

- [ ] Steps 1-5 as usual: defaults test + round-trip test (mirror M7 Task 2's), verify failure, implement beside the M7 records, `uv run pytest tests/canon/ -q`, commit `feat(store): blueprint, beat, and chapter-brief read models`.

---

### Task 4: Projection + ReadStore + property tests

**Files:**
- Modify: `novelizer/canon/projector.py` (tables `blueprints (id, data, active)`, `beats (id, data)`, `chapter_briefs (id, data, status)`; reset tuple; branches)
- Modify: `novelizer/canon/read_store.py`
- Test: `tests/canon/test_briefs_projection_property.py` (new), `tests/canon/test_projector.py` (append blueprint/beat tests)

**Interfaces:**
- ReadStore additions (mirror the M7 promise pair):

```python
async def get_active_blueprint(self) -> Optional[BlueprintRecord]      # WHERE active=1, newest rowid
async def list_beats(self) -> list[BeatRecord]                         # ORDER BY rowid (active blueprint's beats only — see projection rule)
async def list_briefs(self, status: str | None = None) -> list[ChapterBriefRecord]
async def get_open_brief_for_ordinal(self, ordinal: int) -> Optional[ChapterBriefRecord]
```

- Projection semantics:
  - `BLUEPRINT_ADOPTED`: set `active=0` on all existing blueprint rows; **delete all rows in `beats`** (beats belong to the active blueprint — superseded blueprints keep their record for audit, their beats do not survive as live targets); insert the blueprint (active=1) and one `beats` row per BeatSpec.
  - `BLUEPRINT_RETARGETED`: fold `target_chapter_count` into the cited blueprint row only if it is the active one; else no-op.
  - `BEAT_FULFILLED`: fold `fulfilled_by_chapter_id`/`note` into the cited beat row; unknown id no-op; re-emission overwrites (supersedes).
  - `CHAPTER_BRIEF_DRAFTED`: first-mint-wins on brief_id.
  - `CHAPTER_BRIEF_SUPERSEDED` / `CHAPTER_BRIEF_FULFILLED`: terminal, absorbing (guard `status == open`), fold `superseded_by_brief_id` / `fulfilled_by_chapter_id`.

- [ ] **Step 1: failing tests.** Property test `test_briefs_projection_property.py`: copy the M7 promises property file's structure verbatim (oracle + fresh-Projector rebuild-equivalence) with actions `["drafted", "superseded", "fulfilled"]` over brief id "r1" — oracle: first `drafted` creates open; `superseded`/`fulfilled` only transition from open; everything after terminal is a no-op. Projector example tests (append, mirroring M7's window-folding tests): adoption supersedes prior blueprint AND clears old beats (adopt A with 2 beats, adopt B with 1 beat → `get_active_blueprint().id == B`, `len(list_beats()) == 1`); retarget non-active blueprint no-ops; beat fulfillment folds + re-emission overwrites + unknown no-ops; `get_open_brief_for_ordinal` returns the open brief and ignores superseded/fulfilled ones.
- [ ] **Step 2: verify failure.** **Step 3: implement** (branches mirror M7's exactly; remember all three tables in the reset tuple). **Step 4:** `uv run pytest tests/canon/ -q` all PASS. **Step 5: commit** `feat(canon): blueprint/beat/brief projections with rebuild-equivalence property tests`.

---

### Task 5: Policy — `_ALWAYS_GATED`

**Files:**
- Modify: `novelizer/canon/policy.py`
- Test: `tests/canon/test_policy.py` (append)

- [ ] **Step 1: failing tests** (mirror the M7 policy test's real API):

```python
async def test_blueprint_adopted_is_gated_at_every_level_including_full_auto(...):
    # for every AutonomyLevel: is_gated(..., EventType.BLUEPRINT_ADOPTED) is True

async def test_other_blueprint_events_are_never_gated(...):
    # BLUEPRINT_RETARGETED, BEAT_FULFILLED, CHAPTER_BRIEF_DRAFTED/SUPERSEDED/FULFILLED
    # ungated at every level

async def test_committer_routes_blueprint_adoption_to_proposal_even_at_full_auto(...):
    # GatingCommitter at full_auto: commit(BLUEPRINT_ADOPTED payload) appends
    # PROPOSAL_CREATED, not BLUEPRINT_ADOPTED; ProposalService.approve replays it
    # (mirror tests/canon/test_committer.py's fixture)
```

- [ ] **Step 2: verify failure.** **Step 3: implement**:

```python
_ALWAYS_GATED = {EventType.BLUEPRINT_ADOPTED}
```

checked FIRST in `is_gated` (before the `_NEVER_GATED` short-circuit), with a comment: "adopting a shape re-frames the whole book — the Director signs off at every autonomy level." Add the five other new types to `_NEVER_GATED`.
- [ ] **Step 4:** policy + committer suites PASS. **Step 5: commit** `feat(policy): blueprint adoption always routes to proposals`.

---

### Task 6: Intents + commit helpers

**Files:**
- Modify: `novelizer/agents/schemas.py` (BlueprintPlan, BriefIntent, BeatIntent, ResolutionPlanIntent)
- Modify: `novelizer/agents/intents.py` (commit helpers)
- Modify: `novelizer/agents/base.py` (wrappers)
- Test: `tests/agents/test_intents.py` (append)

**Interfaces:**

```python
class BlueprintPlan(BaseModel):
    """The Plotter's blueprint proposal. framework must name a built-in
    template; commit mints blueprint_id + beat ids and routes through the
    (always-gated) commit path."""
    framework: str
    target_chapter_count: int
    genre: str = ""
    obligatory_scenes: list[str] = Field(default_factory=list)
    note: str = ""


class BriefIntent(BaseModel):
    action: Literal["draft", "supersede"]
    id: str = ""                     # cited for supersede
    target_ordinal: int = 0
    goal: str = ""
    pov_character_id: str = ""
    threads_to_touch: list[str] = Field(default_factory=list)
    beats_to_hit: list[str] = Field(default_factory=list)
    promises_to_progress: list[str] = Field(default_factory=list)
    value_shift: str = ""
    planned_outcome: Literal["", "yes", "yes_but", "no_and", "no"] = ""
    synopsis: str = ""


class BeatIntent(BaseModel):
    action: Literal["fulfill"]
    beat_id: str
    chapter_id: str = ""             # "" clears
    note: str = ""


class ResolutionPlanIntent(BaseModel):
    kind: Literal["thread", "secret"]
    id: str
    window_lo: int = 0
    window_hi: int = 0
    note: str = ""
```

Helpers in intents.py (mirror the M7 promise helper's shape, logging, `_normalize_id`):

```python
async def commit_blueprint_plan(committer, agent_name, plan: BlueprintPlan | None) -> None
    # None → no-op. Unknown framework → warning, drop. target_chapter_count < 3 → warning, drop.
    # Mint blueprint_id = str(uuid.uuid4()); beats = [BeatSpec(beat_id=f"{blueprint_id}-{t.slug}", ...)
    # for t in BEAT_TEMPLATES[plan.framework]]; commit BLUEPRINT_ADOPTED (GatingCommitter → proposal).

async def commit_brief_intents(committer, agent_name, intents, open_brief_ids: set[str],
                               drafted_chapter_count: int, active_thread_ids: set[str],
                               active_beat_ids: set[str], active_promise_ids: set[str]) -> None
    # draft: target_ordinal <= drafted_chapter_count or <= 0 → warning, drop (plan the future);
    #   goal blank → drop; cited ids filtered per-list against the active sets (drop unknowns
    #   with one warning per list, keep the brief); if an OPEN brief already targets that
    #   ordinal, first commit CHAPTER_BRIEF_SUPERSEDED for it (superseded_by = new brief id) —
    #   one-open-brief-per-ordinal is a helper-enforced invariant;
    #   mint brief_id = str(uuid.uuid4()); commit CHAPTER_BRIEF_DRAFTED.
    # supersede: id not in open_brief_ids → warning, drop; else commit CHAPTER_BRIEF_SUPERSEDED
    #   with superseded_by_brief_id="".

async def commit_beat_intents(committer, agent_name, intents, active_beat_ids: set[str],
                              valid_chapter_ids: set[str]) -> None
    # beat_id not active → drop; chapter_id given but unknown → drop with warning;
    # commit BEAT_FULFILLED.

async def commit_resolution_plan_intents(committer, agent_name, intents,
                                         active_thread_ids: set[str],
                                         unrevealed_secret_ids: set[str]) -> None
    # window invalid (not 0,0 and not 1<=lo<=hi) → warning, drop; unknown id → drop;
    # thread → THREAD_RESOLUTION_PLANNED, secret → SECRET_REVEAL_PLANNED.
```

BaseAgent wrappers `_commit_blueprint_plan`, `_commit_brief_intents`, `_commit_beat_intents`, `_commit_resolution_plan_intents` mirroring the existing ones.

- [ ] **Step 1: failing tests** — one behavior per test, mirroring the M7 promise-intent tests (FakeCommitter capture): blueprint plan happy (BLUEPRINT_ADOPTED payload carries minted beats from template) / unknown framework dropped / tiny count dropped; brief draft happy / past-ordinal dropped / blank goal dropped / unknown cited ids filtered but brief kept / duplicate-ordinal supersedes the old open brief / supersede unknown dropped; beat fulfill happy / unknown beat dropped / unknown chapter dropped; resolution plan happy thread + secret / invalid window dropped / unknown id dropped.
- [ ] **Step 2: verify failure.** **Step 3: implement.** **Step 4:** `uv run pytest tests/agents/test_intents.py -q` PASS. **Step 5: commit** `feat(agents): blueprint, brief, beat, and resolution-plan intents`.

---

### Task 7: The Plotter agent

**Files:**
- Create: `novelizer/agents/plotter.py`
- Test: `tests/agents/test_plotter.py` (new)

**Interfaces:**

```python
class PlotterOutput(BaseModel):
    blueprint_plan: BlueprintPlan | None = None
    brief_intents: list[BriefIntent] = Field(default_factory=list)
    beat_intents: list[BeatIntent] = Field(default_factory=list)
    resolution_plan_intents: list[ResolutionPlanIntent] = Field(default_factory=list)
    promise_intents: list[PromiseIntent] = Field(default_factory=list)
    feed_note: str = ""

class Plotter(BaseAgent):            # name="plotter"
def build_plotter_runner(settings, callbacks=None, backend=None, tools=None)
```

Behavior:
- `readiness()`: `0.0` if no chapters AND no world entries (nothing to plan yet). `1.0` if chapters exist and there is no active blueprint (the frame is missing). Else `min(1.0, needed / 2)` where `needed = max(0, 2 - open_briefs_ahead)` and `open_briefs_ahead` = count of open briefs with `target_ordinal > len(chapters)` — keep 1–3 briefs ahead, readiness rises as the runway shrinks. (Wants: `list_chapters`, `get_active_blueprint`, `list_briefs("open")`, `list_world_entries`.)
- `poll()`: chapters, world (first 10), characters, blueprint, beats, open briefs, threads, secrets, promises, signals (target_agent="plotter"), active hand, knowledge matrix NOT needed. Include the M7 brain notes: `ledger_note`, `resolution_pacing_note`, `stale_threads_note`.
- `_summarize(ctx)` (module fn like the analyst's): chapter index via `chapter_map_note`; blueprint block ("No blueprint adopted — propose one." or framework/target/beat table with windows via `beat_window` + fulfillment status); open briefs block; threads/promises with windows; brain notes; director signals; Muse hand sparks (`inspiration_note`). System prompt:

```python
PLOTTER_SYSTEM_PROMPT = """You are the Plotter — the writers' room's showrunner. You do not write prose.
You keep the story aimed at a shape: propose a blueprint when none exists (pick a framework
and target length that fit the world and genre), keep 1-3 chapter briefs drafted ahead of the
Author (each with a goal, threads to touch, beats to hit, a value shift, and a planned outcome
biased toward yes_but/no_and), judge when a drafted chapter fulfilled a beat, plan resolution
windows for threads and secret reveals, and plant or re-window promises. Revise briefs freely;
supersede rather than contradict. Cite every id exactly as shown. Prefer steering the story
toward overdue payoffs and dark threads over introducing new material."""
```

- `work()`: standard single `ainvoke` → `structured_response`.
- `commit(out, ctx)`: `_commit_blueprint_plan(out.blueprint_plan)` ONLY when ctx has no active blueprint (a plan while one is active is dropped with a warning — retargeting is M8b/M11 territory); brief/beat/resolution/promise intents via wrappers with active-id sets from ctx (`drafted_chapter_count=len(ctx["chapters"])`); `_remark(out.feed_note)`; `_consume_signals`.
- `build_plotter_runner`: mirror the analyst builder BUT no todos-exclusion middleware (planner keeps `write_todos` — docstring notes the spec's "where it plausibly helps" clause) and `response_format=PlotterOutput`; tooled branch appends `RETRIEVAL_NOTE` (import shape per the analyst's current code — check whether it uses `RETRIEVAL_NOTE` or `RETRIEVAL_NOTE_BASE` and mirror the tooled analyst exactly).

- [ ] **Step 1: failing tests** (stack fixture + FakeRunner, mirroring tests/agents/test_structure_analyst.py):
  - readiness ladder: empty world → 0.0; chapters + no blueprint → 1.0; blueprint + 0 open future briefs → 1.0; 2 open future briefs → 0.0.
  - run_once with `PlotterOutput(blueprint_plan=BlueprintPlan(framework="six-position", target_chapter_count=12))` on a story WITH chapters but no blueprint → a PROPOSAL_CREATED event exists whose `target_event_type == "blueprint.adopted"` (use GatingCommitter + full_auto autonomy state to prove always-gating end-to-end); approving it via ProposalService yields `get_active_blueprint()` with 6 beats.
  - run_once with a brief draft intent → open brief projected for its ordinal; blueprint_plan while one is active → dropped (no second proposal).
  - prompt content: no-blueprint ctx → "propose one" block present; with blueprint → beat table lines with windows.
- [ ] **Step 2: verify failure.** **Step 3: implement.** **Step 4:** `uv run pytest tests/agents/test_plotter.py tests/agents/ -q` PASS. **Step 5: commit** `feat(agents): the Plotter — showrunner agent for blueprint and briefs`.

---

### Task 8: Runtime, scheduler, settings, voice, identity, fixtures

**Files:**
- Modify: `novelizer/runtime.py` (import, `self.plotter=None`, construction via `_tooled`+`_runner_for`, agents list, `_tooling_pinned` if applicable, `apply_settings` interval_map + temperature rebuild)
- Modify: `novelizer/settings/models.py` + `loader.py` + `layers.py` (`plotter_interval: int = 240`, `plotter_tools_enabled: bool = True`, both in STORY_OVERRIDABLE_KEYS, layer/None fields)
- Modify: `novelizer/voices/default.toml` (`plotter = "Structural, forward-looking; speaks in acts and payoffs; allergic to dropped threads."`)
- Modify: `novelizer/tui/identity.py` (`"plotter": AgentIdentity("plotter", "Plotter", "⌖", "P", <style matching the dict's existing entries' style-string format>)`)
- Test: `tests/test_runtime.py` (fixture `_all_fake_runners` gains `"plotter"`; roster assertion gains it; flag on/off wiring test mirroring the phase-b parametrized test), `tests/settings/` (flag/interval tests), `tests/test_apply_settings.py` (interval live-apply + temperature rebuild keeps tooling)

- [ ] **Step 1: failing tests** — mirror the existing per-agent patterns exactly (the phase-b flag test at tests/test_runtime.py is parametrized — extend its list with `("build_plotter_runner", "plotter_tools_enabled")`; add plotter to `_all_fake_runners` and the roster-set assertion; settings tests mirror M5/M7 flag tests; apply_settings interval test mirrors the interval_map tests).
- [ ] **Step 2: verify failures.** **Step 3: implement** per §1 of the ground-truth conventions (construction between structure_analyst and muse; agents list order: insert plotter after muse, before author — the planner should tick before the writer in a fresh room; document the choice inline).
- [ ] **Step 4:** `uv run pytest tests/test_runtime.py tests/settings/ tests/test_apply_settings.py tests/tui/ -q` PASS.
- [ ] **Step 5: commit** `feat(runtime): the Plotter joins the room`.

---

### Task 9: Author brief consumption

**Files:**
- Modify: `novelizer/agents/author.py` (poll gains `"brief"`; `_summarize` gains the assignment block; commit emits CHAPTER_BRIEF_FULFILLED; system prompt sentence)
- Test: `tests/agents/test_author.py` (append)

Behavior:
- `poll()` gains `"brief": await self._read.get_open_brief_for_ordinal(len(chapters) + 1)`.
- `_summarize` — when `ctx.get("brief")` is not None, insert an assignment block IMMEDIATELY before the closing "Write the next chapter." line:

```
Chapter brief (your assignment from the Plotter — honor it, or deviate deliberately and say why in your feed note):
Goal: {goal}
POV: {pov_character_id or 'your choice'}
Touch threads: {', '.join(threads_to_touch) or 'your choice'}
Hit beats: {', '.join(beats_to_hit) or 'none targeted'}
Progress promises: {', '.join(promises_to_progress) or 'none targeted'}
Value shift: {value_shift or 'unspecified'} · Planned outcome: {planned_outcome or 'unspecified'}
Synopsis: {synopsis}
```

  No brief → block absent (prompt byte-identical to pre-M8a — pinned).
- `commit()` — in the NEW-chapter path only (not revise), after `chapter.created`: if `ctx.get("brief")` is not None, commit `CHAPTER_BRIEF_FULFILLED(brief_id=brief.id, chapter_id=chapter.id)`.
- `AUTHOR_SYSTEM_PROMPT` gains: "When a chapter brief is present it is your assignment: honor it, or deviate deliberately and explain the deviation in your feed note."

- [ ] **Step 1: failing tests**: with an open brief for ordinal N+1 → prompt contains "Chapter brief" + the goal text; run_once → CHAPTER_BRIEF_FULFILLED projected (brief status fulfilled, fulfilled_by = new chapter id); no brief → prompt lacks "Chapter brief" (byte-identity with a pre-change captured prompt, following the existing byte-identical test pattern); revise path with an open brief → brief NOT consumed.
- [ ] **Step 2: verify failure.** **Step 3: implement.** **Step 4:** `uv run pytest tests/agents/test_author.py tests/agents/ -q` PASS. **Step 5: commit** `feat(author): drafts against the Plotter's chapter brief`.

---

### Task 10: Docs + full-suite gate

**Files:** `docs/MILESTONES.md` (M8 row status → `◐ M8a landed (blueprint/briefs/Plotter; M8b — comparators, Outline board, OutlineBackend — next)`), `docs/QUICKSTART.md` (short §10: the Plotter, blueprint proposals arriving in the approval queue at every autonomy level, briefs as Author assignments).

- [ ] **Step 1:** doc edits. **Step 2:** `uv run pytest -q` full suite — all pass. **Step 3: commit** `docs: M8a delivered — the room gains a Plotter and an authored blueprint`.
