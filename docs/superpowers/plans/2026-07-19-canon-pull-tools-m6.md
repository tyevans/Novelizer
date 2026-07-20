# CPT-M6: Phase-b Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The remaining five scheduled agents (World Architect, Character Keeper, Editor, Retconner, Structure Analyst) get canon tools — completing the rollout. Muse (non-LLM-canon consumer) and the checker's mining runner stay untouched.

**Architecture:** Pure phase-b flag flips per the ladder: five settings flags, five builders mirroring `build_author_runner`'s reviewed pattern (graph-scope callbacks + `recursion_limit: 50`, constructor-callback-free model with explicit `streaming`, retrieval note when backend given), runtime `_tooled` wiring with rebuild parity. **No prompt diets in M6** — each agent's push is deliberate: Editor reviews ONE chapter in full; Keeper mines recent prose (its truncation was just tuned on main); Analyst's 400-char whole-book skim IS its scoring algorithm; Architect/Retconner push world-entry bodies they act on. Tools are additive research capacity.

**Two spec deviations to document, not implement:**
1. `write_todos` stays available to ALL agents — deepagents hardcodes `TodoListMiddleware` in the stack; exclusion requires a `HarnessProfile` entry-point plugin, disproportionate to the schema-weight savings.
2. The retrieval note is SPLIT: phase-a agents' note references "the chapter list below" (their map); phase-b agents have no map, so they get a base note without that sentence.

## Global Constraints

- Red/green TDD; tests ONLY in this worktree; `uv run pytest` prefix.
- Flags OFF ⇒ byte-identical prompts/construction per agent; existing tests pass unmodified.
- Phase-a prompts must remain BYTE-IDENTICAL after the note split (compose, don't rewrite).
- Flag names: `world_architect_tools_enabled`, `character_keeper_tools_enabled`, `editor_tools_enabled`, `retconner_tools_enabled`, `structure_analyst_tools_enabled` — all `bool = True`, plumbed everywhere `author_tools_enabled` is.
- Builders mirror `novelizer/agents/author.py`'s `build_author_runner` exactly (including the `streaming=callbacks is not None` lesson — see chat/runners.py's M5 fix).

---

### Task 1: Five settings flags

Mirror `author_tools_enabled` at every settings site (models.py field + STORY_OVERRIDABLE_KEYS, layers.py both classes, loader.py EnvOverrides) for all five names; tests mirror the default + story-override pattern (one parametrized test over the five names is fine).

- [ ] Failing tests → implement → `uv run pytest tests/settings -v` green
- [ ] Commit: `feat(settings): phase-b per-agent tools_enabled flags`

### Task 2: Retrieval-note split

**Files:** `novelizer/agents/author.py` (+ any file importing RETRIEVAL_NOTE), tests in `tests/agents/`.

Split the constant: `RETRIEVAL_NOTE_BASE` = the note minus the "The chapter list below is an index — read any chapter or canon file you need in full before writing." sentence (keep the tools sentence and the cite-ids sentence); `RETRIEVAL_NOTE = RETRIEVAL_NOTE_BASE`-composed-with-the-map-sentence such that the final string is byte-identical to today's (test: literal equality against the current full text, plus existing author/checker/chat tests unmodified).

- [ ] Failing test (literal-equality pin) → implement → `uv run pytest tests/agents tests/chat -v` green
- [ ] Commit: `refactor(agents): split retrieval note into base + map sentence`

### Task 3: Five tooled builders

**Files:** `novelizer/agents/world_architect.py`, `character_keeper.py`, `editor.py`, `retconner.py`, `structure_analyst.py`; tests appended per agent's existing test file.

Each `build_*_runner(settings, callbacks=None)` becomes `(settings, callbacks=None, backend=None, tools=None)`:
- backend given ⇒ append `RETRIEVAL_NOTE_BASE` to that agent's SYSTEM_PROMPT, pass backend/tools to create_deep_agent, model built with `callbacks=None, streaming=callbacks is not None`, graph `.with_config` carrying callbacks + `recursion_limit: 50` (exact author composition).
- bare call ⇒ byte-identical construction (constructor callbacks as today).

Per-agent tests (parametrize where the file structure allows): bare unchanged; tooled has base note (and NOT the map sentence) + recursion_limit 50; smoke with `CanonBackend(read_store=None)`.

- [ ] Failing tests → implement all five → `uv run pytest tests/agents -v` green, existing tests unmodified
- [ ] Commit: `feat(agents): phase-b tooled builders — architect, keeper, editor, retconner, analyst`

### Task 4: Runtime wiring + rebuild parity

**Files:** `novelizer/runtime.py`, `tests/test_runtime.py`.

READ runtime.py's `_tooled` helper and the M4/M5 wiring first. Extend construction of the five agents in `start()` to route their builders through `_tooled(builder, self.settings.<agent>_tools_enabled)`-style closures (match the existing mechanism precisely). Rebuild parity: the `apply_settings` temperature-rebuild path rebuilds agents via builders — ensure the five follow the same pinned-at-start pattern as author/checker (M4's fix; M5 documented flips as inert-until-restart — keep that contract: pin each agent's tooling at start, e.g. store per-agent tooled state or reuse the pull_mode-pinning pattern via a small `self._tooling_pinned: dict[str, bool]` set at start).

Tests: spy-pattern (as in the M4/M5 runtime tests) — flags on ⇒ each of the five real builders receives backend/tools; flags off ⇒ bare; rebuild after temperature change keeps tooling pinned.

- [ ] Failing tests → implement → `uv run pytest tests/test_runtime.py tests/test_apply_settings.py -v` green
- [ ] Commit: `feat(runtime): phase-b toolkit wiring with pinned rebuilds`

### Task 5: Docs + gate

**Files:** `docs/superpowers/plans/2026-07-19-canon-pull-tools-milestones.md`, `docs/superpowers/specs/2026-07-19-canon-pull-tools-design.md`, `docs/QUICKSTART.md`.

- [ ] Milestone doc: CPT-M6 delivered note (rollout complete; the two deviations above; Muse + mining untouched; no prompt diets with the per-agent rationale).
- [ ] Spec: annotate the Wiring section — `write_todos` scoping not implemented (HarnessProfile plugin disproportionate); note-split delivered.
- [ ] QUICKSTART: document the eight `*_tools_enabled` flags (author/checker/chat + five) with one line on what turning one off does (legacy push prompts, no canon tools; restart required).
- [ ] Full-suite gate: `uv run pytest -q` — zero failures (known load-only flakes: rerun isolated before treating as real).
- [ ] Commit: `docs: CPT-M6 delivered — canon pull tools rollout complete`
