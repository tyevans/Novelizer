# M11 "Landing" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A book can *finish*. Completion criteria over the blueprint become a first-class, detectable state (`book.completed`), the Plotter steers the endgame and retargets when the book runs long or short, a story is framed at creation, and Mission Control opens on the plan — completing Phase 2 (docs/MILESTONES.md M11).

**Architecture:** Completion is a pure Brain faculty (`brain/completion.py`) over blueprint/beats/promises/arcs/chapters, consumed by the Plotter (which emits the one-shot `book.completed` event), the TUI (Outline board + a progress element), and prompt notes. **Completion is informational, not load-bearing on dispatch** — the scheduler stays free of canon knowledge (it holds a ReadStore but deliberately no domain logic); the room quiesces naturally as readiness drops. `blueprint.retargeted` already exists end-to-end (event, payload, projection, `_NEVER_GATED`, tests) with **no emitter** — M11 adds Director (CLI + in-TUI verb) and Plotter emitters only. The Frame step goes in the **story picker** (per-story creation), not the setup wizard (global, once-per-machine).

**Tech Stack:** Python 3.13, Textual, pydantic v2, Click, pytest asyncio_mode=auto.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-19-authoring-skills-blueprint-design.md` §"Land" (pipeline step 9) + §Milestones M11. M11 acceptance: a seeded story runs to a **finished** novel and the system declares it done.
- Established invariants: minted-once ids; absorbing terminals; new tables in `_reset_state_locked`; notes empty-when-quiet; TUI reuses faculties, never re-derives; bare (non-tooled) builder paths byte-identical.
- **Locked-decision amendment (deliberate, must be documented in code):** `BLUEPRINT_ADOPTED` is `_ALWAYS_GATED` because *agent-proposed* adoption needs Director sign-off. The Frame step appends it directly at story creation, before any Runtime exists — the Director choosing a framework in the creation form and clicking Create **is** the sign-off. Task 5 amends the `BlueprintAdopted` docstring to state this exception explicitly so the code does not lie about its own rule. No other path may bypass the gate.
- `book.completed` is emitted **once** per blueprint (projection guards re-emission); it is a statement about the current active blueprint, and adopting a new blueprint clears it.
- **Run all tests in this worktree, NEVER the main checkout.** Synchronous only — implementers/reviewers NEVER use background runs or monitors.

---

### Task 1: `completion` faculty + note

**Files:** Create `novelizer/brain/completion.py`; modify `novelizer/brain/context.py`; tests `tests/brain/test_completion.py`, `tests/brain/test_context.py` (append).

**Interfaces:**

```python
@dataclass(frozen=True)
class CompletionStatus:
    complete: bool
    beats_total: int
    beats_fulfilled: int
    promises_open: int          # state == open
    arcs_unresolved: int        # active and not resolved
    chapters: int
    target_chapters: int
    blockers: list[str]         # human lines naming what remains; empty iff complete

def completion_status(blueprint, beats, promises, arcs, chapters) -> CompletionStatus | None
    # blueprint None -> None (a story with no adopted shape can never be "complete")
    # complete iff: beats non-empty AND every beat has fulfilled_by_chapter_id
    #   AND no promise in state open
    #   AND no ACTIVE arc with resolved False
    #   (chapters/target are reported for context, never gating — a book may land
    #    early or late; the blueprint's shape is the criterion, not its length)
    # blockers: one line per unmet criterion, e.g.
    #   "2 of 6 beats unfulfilled: midpoint, climax"
    #   "3 promises still open"
    #   "1 arc unresolved: Mara"   (names via the arcs' character ids — callers pass
    #                               characters if they want names; keep ids here and
    #                               let context.py map names)
```

- `context.py` gains `completion_note(blueprint, beats, promises, arcs, chapters, characters) -> str`: `""` when there is no blueprint OR when the story is far from done (more than one blocker category) — this note is *endgame steering*, so it fires only when the book is CLOSE: exactly one blocker category remaining, or complete. Complete → "The blueprint is satisfied: every beat fulfilled, every promise settled, every arc resolved. Write the ending — then the room is done." Near-complete → names precisely what remains ("Everything is settled except 2 promises: <names>. Steer the remaining chapters at them.").

- [ ] Steps 1-5: faculty tests (each criterion independently blocking; complete case; no-blueprint None; beats-empty never complete; resolved-but-inactive arcs ignored; released promises don't block) + note tests (quiet when far, fires when near/complete); implement; `uv run pytest tests/brain/ -q`; commit `feat(brain): completion faculty and endgame note`.

---

### Task 2: `book.completed` event, projection, read, and Plotter emission

**Files:** Modify `novelizer/canon/events.py`, `novelizer/canon/projector.py`, `novelizer/canon/read_store.py`, `novelizer/canon/policy.py`, `novelizer/agents/plotter.py`; tests `tests/canon/test_events.py`, `tests/canon/test_projector.py`, `tests/canon/test_policy.py`, `tests/agents/test_plotter.py`.

**Interfaces:**

```python
BOOK_COMPLETED = "book.completed"

class BookCompleted(BaseModel):
    """Payload for book.completed — the room declares the blueprint satisfied:
    every beat fulfilled, every promise paid or released, every active arc
    resolved (see novelizer.brain.completion).

    Informational and one-shot per blueprint: the projection ignores a repeat
    while the same blueprint is active, and adopting a new blueprint clears
    the flag (Locked decision: completion describes the CURRENT shape). It
    does not stop the scheduler — the room quiesces on readiness, and the
    Director decides when to close the story."""
    blueprint_id: str
    chapter_id: str = ""     # the last chapter at declaration time
    note: str = ""
```

- Projection: fold `completed=True` + `completed_chapter_id`/`completed_note` into the cited blueprint row **only if it is the active one and not already completed**; `BLUEPRINT_ADOPTED` already replaces the row, so a new blueprint starts uncompleted (verify: the adoption branch builds a fresh `BlueprintRecord` — completion fields default False/""). `BlueprintRecord` gains `completed: bool = False`, `completed_chapter_id: str = ""`, `completed_note: str = ""` (back-compat defaults).
- ReadStore: no new query needed (`get_active_blueprint()` carries the fields).
- Policy: `BOOK_COMPLETED` → `_NEVER_GATED` (the room reporting a derived fact, like a thread touch).
- Plotter: `commit()` — after its other intent commits, if `ctx["blueprint"]` is active and NOT already `completed`, compute `completion_status(...)` from ctx; when `complete`, commit `BOOK_COMPLETED(blueprint_id=..., chapter_id=last chapter id, note=out.feed_note[:200] or "")`. No LLM involvement — a mechanical, deterministic declaration (mirrors the M8b stale-brief reap: it must run even when the LLM returned nothing useful, so place it beside/above the `out is None` guard the way the reap is).
- Plotter `poll()` already loads blueprint/beats/promises/arcs/chapters — confirm all five are present; add whichever is missing.

- [ ] Steps 1-5: event/payload defaults test; projector tests (fold on active; ignore repeat; ignore non-active; new adoption resets completed); policy test; Plotter end-to-end (a story satisfying every criterion → `book.completed` projected once; running it twice → still one event; an unsatisfied story → none); implement; `uv run pytest tests/canon/ tests/agents/test_plotter.py -q`; commit `feat(canon): book.completed — the room declares the blueprint satisfied`.

---

### Task 3: Retarget emitters (CLI + in-TUI verb + Plotter intent)

**Files:** Modify `novelizer/director/commands.py`, `novelizer/director/cli.py`, `novelizer/agents/schemas.py`, `novelizer/agents/intents.py`, `novelizer/agents/base.py`, `novelizer/agents/plotter.py`; tests `tests/director/test_commands.py`, `tests/director/test_cli.py`, `tests/agents/test_intents.py`, `tests/agents/test_plotter.py`.

The event/payload/projection/gating for `blueprint.retargeted` ALREADY EXIST and are tested — this task adds emitters only.

- `commands.retarget_blueprint(events, read, target_chapter_count: int) -> str`: no active blueprint → error string; `target_chapter_count < 3` → error string; else append `BLUEPRINT_RETARGETED(blueprint_id=active.id, target_chapter_count=...)` and return `f"blueprint retargeted to {n} chapters"`. Follow the `plan_thread_resolution` shape but **return a typed result if the file's siblings allow it cheaply** — the CLI currently colorizes by string prefix with an explicit fragility comment (cli.py ~line 349); if introducing a typed return would ripple, keep strings and document the success prefix at the call site as the siblings do.
- CLI: `@cli.command("retarget")` taking `TARGET_CHAPTERS` (int), mirroring `plan-resolution`'s body shape; yellow on rejection, green on success (the M8b convention).
- In-TUI: `commands.dispatch` gains `retarget <n>` beside `seed`/`focus`/`autonomy`; update `PLACEHOLDER_HINTS` (`novelizer/tui/widgets/roster.py`) to advertise it.
- Plotter: `RetargetIntent(target_chapter_count: int, reason: str = "")` in schemas; `PlotterOutput.retarget_intent: RetargetIntent | None = None`; `commit_retarget_intent(committer, agent_name, intent, blueprint)` helper (drop when no active blueprint, when `< 3`, or when equal to the current count — the last is the important guard against churn); BaseAgent wrapper; Plotter commits it. Plotter prompt gains one sentence: it may retarget when the story clearly needs more or fewer chapters than the blueprint assumes.

- [ ] Steps 1-5: command tests (happy/no-blueprint/too-small); CLI test (mirroring `test_autonomy_command_sets_global_level`); dispatch test; intent tests (happy/no-blueprint/too-small/no-change-drop); Plotter end-to-end; implement; `uv run pytest tests/director/ tests/agents/ -q`; commit `feat(director): retarget the blueprint from CLI, TUI, and the Plotter`.

---

### Task 4: Finale-window convergence note

**Files:** Modify `novelizer/brain/context.py` (or a small addition to `completion.py` if the computation belongs with the faculty — implementer's judgment, but the note function lives in context.py like its siblings); modify `novelizer/agents/plotter.py` (prompt assembly); tests `tests/brain/test_context.py`, `tests/agents/test_plotter.py`.

- `finale_convergence_note(blueprint, beats, promises, arcs, chapters) -> str`: `""` unless the story has entered the **finale window** — defined as `len(chapters) >= round(0.80 * target_chapter_count)` (the final-turn position; reuse `beat_window` for the climax beat when present and prefer its `window_lo` as the threshold, falling back to the 0.80 rule when no climax beat exists). Inside the window it lists what must converge before the end: unfulfilled beats, open promises (with overdue flagged), unresolved arcs — capped at a handful of names each, with steering guidance ("everything still open must land in the next N chapters").
- Wired into the Plotter's `_summarize` only (it owns convergence), placed near the ledger/pacing notes.

- [ ] Steps 1-5: note tests (quiet before the window; fires inside; names what remains; quiet when nothing remains — that's `completion_note`'s job); Plotter prompt-inclusion test; implement; `uv run pytest tests/brain/ tests/agents/test_plotter.py -q`; commit `feat(brain): finale convergence note`.

---

### Task 5: Frame step at story creation

**Files:** Modify `novelizer/tui/story_picker.py`, `novelizer/director/commands.py`, `novelizer/canon/events.py` (docstring amendment only); tests `tests/tui/test_story_picker.py`, `tests/director/test_commands.py`.

- `commands.adopt_blueprint_story_dir(story: StoryDirectory, framework: str, target_chapter_count: int, genre: str = "") -> None`: sibling of `seed_story_dir` (standalone EventStore, no Runtime). Validates `framework in BEAT_TEMPLATES` and `target_chapter_count >= 3` (raises `ValueError` on bad input — the caller is a form with a Select, so this is a programming-error guard, not a user path); mints `blueprint_id = str(uuid.uuid4())` and the `BeatSpec` list from the template exactly as `commit_blueprint_plan` does (extract the minting into a shared helper if it is a verbatim duplicate — DRY, but only if the extraction is clean); appends `BLUEPRINT_ADOPTED` directly.
- `events.py`: amend the `BlueprintAdopted` docstring with the sanctioned exception (Director-authored at story creation via the picker's Frame step, before any Runtime/GatingCommitter exists — the creation form IS the sign-off; agent-proposed adoption remains always-gated).
- `story_picker.py` new-story form gains, after the premise/voice fields: `Select#new_framework` (options from `BEAT_TEMPLATES` keys, blank allowed = skip framing), `Input#new_target_chapters` (placeholder "target chapters, e.g. 24"), `Select#new_genre` or a free `Input#new_genre` (implementer's call; a free Input is simpler and genre is free text in the model). `_create()` — after the existing seed step — if a framework was chosen: parse the target (default 24 when blank; on unparseable input show `"✗ target chapters must be a number"` and return WITHOUT exiting, mirroring the seed-failure branch) and call `adopt_blueprint_story_dir(...)`; failures render `"✗ story created, but framing failed: {e}"` and return, exactly like the seed branch. Skipping framing leaves the story exactly as today.

- [ ] Steps 1-5: command tests (adopts + projects an active blueprint with the template's beats; bad framework/target raise); picker tests (form fields present; create-with-framing projects a blueprint; create-without-framing projects none — mirror the existing picker test style, which drives the app via Textual's pilot); implement; `uv run pytest tests/tui/test_story_picker.py tests/director/ -q`; commit `feat(picker): frame a story at creation`.

---

### Task 6: Outline as the home tab + progress element

**Files:** Modify `novelizer/tui/widgets/brain_panel.py`, `novelizer/tui/widgets/brain_model.py`, `novelizer/tui/app.py`; tests `tests/tui/test_brain_panel.py`, `tests/tui/test_brain_model.py`.

- Home tab: add `on_mount` to `BrainPanel` setting `self.query_one("#brain_tabs", TabbedContent).active = "tab_outline"`. **Do NOT reorder the panes or remap keys** — the labels carry hardcoded digits and the key bindings are pinned by tests; opening on Outline while `1`–`6` keep their meanings is the minimal honest change. Update `tests/tui/test_brain_panel.py:34`'s assertion (`tab_shape` → `tab_outline`) and re-check the key-cycle test at `:46-65` (pressing `1` must still reach Shape).
- Progress element on the Outline header (`outline_tab`, brain_model.py ~line 547): the header line gains chapter progress — `{framework} · ch {len(chapters)}/{target} · {genre}` — and, when the blueprint is `completed`, a leading `✓ COMPLETE` segment (ALARM-free; use a distinct success style if one exists in the module, else DIM+bold). Reuse `completion_status` for the complete flag ONLY if the tab already receives what it needs; the blueprint record's `completed` field is authoritative and cheaper — prefer it.
- `outline_tab` signature gains whatever it needs (it already takes blueprint/beats/briefs/threads/chapters).

- [ ] Steps 1-5: panel home-tab test; key-cycle test still green; model tests (progress segment renders with the right counts; `✓ COMPLETE` appears when the blueprint record is completed and not otherwise); implement; `uv run pytest tests/tui/ -q`; commit `feat(tui): Mission Control opens on the Outline board with story progress`.

---

### Task 7: M10 queue smalls

**Files:** Modify `novelizer/canon_fs/skills_route.py`, `novelizer/store/indexer.py`; tests in their existing homes.

1. Nested `__pycache__` glob leak: the hidden-entry filter matches on basename, so `aglob("**/*", "/skills")` still returns `/skills/__pycache__/*.pyc`. Filter on any path **segment** in the hidden set (`als` and `aglob`). Test with a `**/*.pyc` pattern.
2. `CanonIndexer`'s trailing `else: get_arc(...)` catch-all would silently treat any future unmapped kind as an arc — make it an explicit `elif kind == "arc"` with a logged `else` (unknown kind → warning, skip).

- [ ] Steps 1-5: failing tests; implement; `uv run pytest tests/canon_fs/ tests/store/ -q`; commit `fix: skills glob hygiene + explicit indexer kind dispatch`.

---

### Task 8: Docs, Phase 2 closeout, full-suite gate

**Files:** `docs/MILESTONES.md` (M11 row → ✅ complete pending review; add a short Phase 2 closeout paragraph under the table stating what the phase delivered and naming the standing deferrals: relationship arcs, per-advance pivot history, per-agent skill selectivity, live acceptance run), `docs/QUICKSTART.md` (§13: framing a story at creation, what "complete" means and where it shows, `retarget`, the Outline home view).

- [ ] Doc edits; `uv run pytest -q` full suite; commit `docs: M11 Landing delivered — Phase 2 complete`.
