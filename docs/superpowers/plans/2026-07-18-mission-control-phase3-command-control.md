# Mission Control Phase 3 — Command & Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **NEVER create a `.env` file at any point in this plan.** Any scratch/temp file goes in
> the job's tmp dir, never the repo. No live-LLM tests are involved in this plan — every
> test here is pure-function or pilot-harness with stub runners.

**Goal:** Spec Staging item 3 ("Command & control"): the resident `#proposals` pane is removed and replaced by a one-line high-contrast **proposals banner** (visible only when open proposals exist) plus an **approval modal** (`a` key: id-free queue rows, full payload context, approve/reject/dismiss); the statusbar's `roster_summary` becomes a **glyph strip** built from `identity.py`; `_status_line`'s command cheatsheet is replaced by the **autonomy dial meter** (`AUTONOMY ▮▮▯▯ gated_canon`) and a rotating-on-mount Input placeholder hint; the browser gains **state cues** (chapter status dots, retcons `⚠`, a new Threads section with stale count) and the detail pane gets **typography** (bold title, dim metadata with render-time word count, dynamic UPPERCASED border title).

**Architecture:** All new rendering logic is pure functions in the existing `*_model.py`-seam modules: `roster.py` (which stays the pure statusbar module it is — glyph strip, dial meter, placeholder hints), `proposals_model.py` (banner line, modal rows, payload context), `browser_model.py` (section cues, `DetailView`). A thin `ApprovalScreen(ModalScreen)` (**new** `novelizer/tui/approval_screen.py`) composes `OptionList` + `Static` and consumes the pure functions. Approve/reject go **only** through `novelizer.director.commands.dispatch` — the modal calls the app's existing `_run_command`, so the result line lands in the feed and `app.messages` exactly like a typed command; `ProposalService`/`Committer` are never called from TUI code. Staleness in the browser is **never re-derived**: `is_thread_stale` + `settings.staleness_threshold_chapters`, the same source the brain panel uses. No new events, projections, or read-model changes — the chapter word count is computed from prose at render time. EngineRoom / ActivityStrip / telemetry loops are untouched (the one permitted engine-rule edit is a member swap in tcss, below). Folded-in cleanups: the dead `TAB_IDS` constant in `brain_panel.py` is deleted, and the vestigial `#body.engine #roster` tcss member goes with the pane-era rules.

**Tech Stack:** Python 3.13, uv, Textual 5.3.0 (`ModalScreen`, `OptionList`/`Option`, `Static`, `Vertical`, `Input`), `rich.text.Text`, pytest + pytest-asyncio pilot harness, Hypothesis for property tests.

## Global Constraints

- **Approve/reject only via the command seam (non-negotiable):** the modal's decisions run `await self.app._run_command(f"approve {proposal_id}")` / `f"reject {proposal_id}"`, which calls `commands.dispatch(runtime, line)` and writes `» {result}` to the feed + `app.messages` — identical to typing the command. The dispatch verbs are `approve`/`reject` (bare or `:`-prefixed) and the response strings are `Approved proposal {id} ({target_event_type})` / `Rejected proposal {id} ({target_event_type})` / `Proposal not found: {id}` / `Proposal {id} is already {status}.` (from `novelizer/director/commands.py::_dispatch_decision`). `ProposalService`, `Committer`, and `runtime.proposals` are never imported or called from any TUI module.
- **Post-decision projector catch-up:** after a decision the modal awaits `self.runtime.projector.catch_up()` before reloading its list. The read store only learns a proposal left the `open` state after projection; without the catch-up the reloaded queue would re-show the just-decided proposal (and a second `enter` would double-commit the target event). `catch_up()` is idempotent and is the same call the app's `_projector_loop` already makes.
- **Modal guard (non-negotiable):** `NovelizerApp.action_approvals` returns without pushing when `self.screen is not self.default_screen` (Textual 5.3's `App.default_screen` property — `App.query_one` also queries the default screen, so feed writes from the modal work). This guards against stacking a second `ApprovalScreen` *and* against opening the modal over `SettingsScreen`. It also returns without pushing when there are no open proposals.
- **Exact banner text:** `▼ {n} proposals awaiting approval — press a` for n ≥ 2, `▼ 1 proposal awaiting approval — press a` for n = 1. Style `BANNER_STYLE = "bold black on gold3"` (the one high-contrast line on the dashboard). The `#proposals_banner` widget's `display` is `False` whenever the open-proposal count is 0 — zero rows spent, no border, no title.
- **Modal keys:** `enter` = approve highlighted row (via `OptionList.OptionSelected` — OptionList owns enter), `x` = reject highlighted row, `escape` = close. Bindings live on `ApprovalScreen` (ModalScreen bindings take precedence over the App's).
- **Id-free modal rows:** each row is `{glyph} {label}` (agent color, `ljust(SPEAKER_WIDTH)`) + `→ {target_event_type}` + dim `  {payload_summary}`. `payload_summary` = first non-empty of payload keys `("title", "name", "note", "description", "body")` (feed_model's fallback order), whitespace-collapsed, clipped to 60 chars with `…`. The context pane shows a bold `{label} proposes {target_event_type}` header then one `key: value` line per payload field, **skipping** `id`, `*_id`, `*_ids`, `created_at`, and `provenance` keys and empty values (names, not ids); values collapsed and clipped to 160 chars.
- **Roster glyph strip, exact marks:** every agent renders as `{glyph}{mark}`, glyph in the agent's `identity.py` color; marks: running `⠋` (static per render, agent color), idle `·` (dim), paused `‖` (dim), errored `!` (`ALARM_STYLE`); precedence errored > paused > running > idle; agents joined by a single space — e.g. `✎⠋ §· ⌂· ♥· ⚖· ↺· ∿·`. Empty status → dim `no agents`. Error *text* never appears in the strip (errors land in the feed as alarm lines); the scheduler's `last_completed`/`run_count`/`next_ready_in` M5.3 status fields are accepted and ignored. `roster_summary(status)` stays as the plain-string variant (`roster_glyphs(status).plain`).
- **Autonomy dial, exact rendering:** `AUTONOMY ▮▮▯▯ gated_canon` — glyphs `▮` (filled) / `▯` (empty), 4 segments; filled = trust position on the ladder (the real `AutonomyLevel` enum order): `full_auto` → 4, `gated_retcons` → 3, `gated_canon` → 2, `gated_all` (most conservative) → 1. Filled segments and the level label share one style stepping with trust: `full_auto` `green3`, `gated_retcons` `gold3`, `gated_canon` `dark_orange`, `gated_all` `red`; `AUTONOMY ` prefix and empty segments are dim. Overrides append compact dim ` ({agent}={level}, …)`. The command cheatsheet is gone from the statusbar; `_status_line` is deleted.
- **Placeholder hints, verbatim (4), chosen once per app start:**
  1. `:seed a lighthouse at the end of the world`
  2. `:focus the storm that never lands`
  3. `:pause author — let the room breathe`
  4. `:autonomy gated_canon — take the wheel yourself`
  `command_hint(index)` = `PLACEHOLDER_HINTS[index % 4]`; `NovelizerApp(runtime, hint_index: int = 0)` — default 0 so every existing test construction is deterministic; only `cli._launch_tui` passes `random.randrange(len(PLACEHOLDER_HINTS))`. No time/random anywhere in the TUI module or tests.
- **Chapter status dots** (real `EditorialStatus` enum is `draft | reviewed | final` — the spec sketch's approved/draft/revising names map by pipeline position): `◌` draft, `◐` reviewed, `●` final; defensive fallback `·` for an unrecognized value. Chapter rows: `{dot} {title}` — the `[EditorialStatus.X]` text is gone.
- **Browser section labels:** Retcons: `Retcons ({n}) ⚠` when n ≥ 1 open, `Retcons (0)` otherwise. New Threads section (between Retcons and Themes, matching the spec mockup order): label `Threads ({open} · {stale} stale)` when stale ≥ 1, `Threads ({open})` otherwise, where open = non-terminal count and stale via `is_thread_stale(t, chapters, staleness_threshold)`; rows `⚠ {name} · stale` / `· {name} · {state}` / `✓ {name} · {state}` (terminal). Themes label unchanged. **No ids/slugs in any label.**
- **Staleness single-sourcing (M5.3 pattern):** `browser_sections(read, *, staleness_threshold)` and `StoryBrowser.refresh_sections(read, *, staleness_threshold)` are keyword-only with **no default**; `_browser_loop` reads `self.runtime.settings.staleness_threshold_chapters` **every cycle** — exactly how `_brain_loop` feeds the brain panel. The literal `3` is never typed in the new code.
- **Detail pane:** `detail_text` is replaced by `detail_view(read, section_key, item_id) -> DetailView` (frozen dataclass `title: str`, `body: Text`). Body typography: bold title line, dim metadata line, dim-labeled fields, blank line, prose with paragraphs preserved. Chapter metadata: `{status} · {n:,} words` with `word_count(prose) = len(prose.split())` computed at render time (never stored). Threads handler: name (bold), meta `{state} · touched {n}x · last touch: ch N "Title"` (or `last touch: —` when the chapter is unknown — no raw ids), then `last_note`. `#detail_scroll.border_title` = `view.title.upper()`, reset to `"DETAIL"` when nothing is selected / nothing found; `_update_detail(content, title="")` owns the reset. `#detail` gets `max-width: 80` (spec's readable measure). Reading mode ('v') inherits all of it — same widget.
- **Layout & tcss:** `#proposals_banner` (height 1, `padding: 0 1`) sits between `#feed` and `#brain` in `#left`. The `#proposals` Static, its tcss rules, its border-title entries, `pending_lines`, and `proposal_line` are deleted in the same task that adds the banner. The `#body.engine` hide rule's members become `#feed, #proposals_banner, #brain` (the vestigial `#roster` member is dropped — cleanup). `#body.room` / `#body.reading` rules untouched.
- **Folded-in cleanup:** the dead `TAB_IDS` constant in `brain_panel.py` (grep-verified: zero references anywhere) is deleted in Task 3 (the first task that edits app-layout surfaces).
- **`app.messages` surface untouched for feed writes** — the only new writer is `_run_command`'s existing `» {result}` path (invoked by the modal). Banner/statusbar/browser never append to `app.messages`. EngineRoom / ActivityStrip / telemetry loops, `format_event`, `render_event`, and the Footer's mechanism stay exactly as merged (the Footer gains the `a` binding's entry automatically).
- Full suite must pass with `uv run pytest -q`, **zero warnings**, before the final commit.

## Existing-test inventory (what breaks, where it is fixed)

| Test | References | Fixed in |
|---|---|---|
| `tests/tui/test_roster.py` (all 8 tests on the old `roster_summary` name/count format, incl. master's `last_completed` fallback tests) | old summary strings | Task 1: file rewritten for the glyph strip (the `last_completed` sticky marker is obsolete — the strip always shows the whole cast) |
| `tests/tui/test_app.py::test_status_line_shows_real_autonomy_level`, `::test_status_line_summarizes_overrides` | deleted `_status_line` | Task 2: rewritten against `dial_meter` (same assertions: real level value shown, `full-auto` absent, overrides summarized) |
| `tests/tui/test_app_layout.py::test_mission_control_panes_present_and_populate` | statusbar named-agent assertions (`_roster_names`), old `AUTONOMY:` format | Task 2: rewritten — waits for/asserts a cast glyph (`✎`) and `AUTONOMY ▮` in the statusbar |
| `tests/tui/test_app_layout.py::test_approval_queue_pane_shows_pending_proposal_and_approve_via_command` | `#proposals` Static, `proposal_id[:8]` visible | Task 3: rewritten as `test_proposals_banner_appears_and_approve_via_command_clears_it` (id-free banner; approve still via `_run_command`) |
| `tests/tui/test_app_layout.py::test_every_pane_has_its_border_title` | `"#proposals": "PROPOSALS"` entry | Task 3: entry removed (5 entries → 4; the banner is title-less by design) |
| `tests/tui/test_proposals_model.py` (3 tests on `proposal_line`/`pending_lines`) | deleted helpers | Task 3: file rewritten for `banner_line`; Task 4 appends `payload_summary`/`proposal_row`/`proposal_context` tests |
| `tests/tui/test_browser_model.py` (7 tests incl. section order, `[status]` chapter labels, `detail_text` strings) | `detail_text` → `detail_view`, new Threads section, dot labels | Task 5: file rewritten (`.plain`/`.title` assertions, `threads` in section-keys list, dot + label assertions) |
| `tests/tui/test_browser_widget.py` (2 tests; `_Host.refresh_sections(read)`) | new keyword-only `staleness_threshold` | Task 5: host + calls pass `staleness_threshold=3` explicitly |
| `tests/tui/test_detail_pane_scroll.py` (`app._update_detail("short text")`) | `_update_detail` signature | unaffected — the new signature is `_update_detail(content, title="")`; a str positional call still works and resets the title to `DETAIL` |
| `tests/tui/test_app_commands.py`, `test_app_smoke.py`, `test_app_resilience.py`, `test_feed_wiring.py`, `test_feed_model.py`, `test_identity.py`, `test_brain_model.py`, `test_brain_panel.py`, `test_reading_mode.py`, `test_engine_room*.py`, `test_settings*.py`, `test_story_picker.py`, `test_setup_wizard.py` | none of the changed surfaces (grep-verified: no `_status_line`/`roster_summary`/`#proposals`/`detail_text`/`TAB_IDS` references) | not touched (Task 2 *adds* one placeholder test to `test_app_commands.py`) |

---

### Task 1: Roster glyph strip (`roster.py` rewrite + `test_roster.py` rewrite)

**Files:**
- Rewrite: `novelizer/tui/widgets/roster.py`
- Rewrite: `tests/tui/test_roster.py`

**Interfaces:**
- Consumes: `identity_for` (`novelizer.tui.identity`), `ALARM_STYLE` (`novelizer.tui.widgets.feed_model`), scheduler status dicts (`novelizer/scheduler.py::status()` shape: `name`, `paused`, `running`, `last_error`, plus M5.3's `last_completed`, `run_count`, `next_ready_in` — accepted, ignored).
- Produces: `RUNNING_MARK`/`IDLE_MARK`/`PAUSED_MARK`/`ERROR_MARK`, `roster_glyphs(status: list) -> Text`, `roster_summary(status: list) -> str` (plain variant). Task 2 appends `dial_meter`/`status_strip`/hints to this same module.

Design decisions locked here: mark precedence errored > paused > running > idle; running mark takes the agent's color (motion reads as the agent acting), idle/paused marks are dim, error mark is `ALARM_STYLE`; no error text and no agent names in the strip; the M5.3 `last_completed` sticky fallback is dropped (the strip shows the whole cast at all times, so there is no "empty bar" case to fill).

- [ ] **Step 1: Write the failing test** (replace `tests/tui/test_roster.py` wholesale)

```python
from hypothesis import given, strategies as st

from novelizer.tui.identity import identity_for
from novelizer.tui.widgets.feed_model import ALARM_STYLE
from novelizer.tui.widgets.roster import (
    ERROR_MARK,
    IDLE_MARK,
    PAUSED_MARK,
    RUNNING_MARK,
    roster_glyphs,
    roster_summary,
)

CAST = (
    "author", "editor", "world_architect", "character_keeper",
    "continuity_checker", "retconner", "structure_analyst",
)


def _row(name, paused=False, running=False, last_error=None):
    # Real Scheduler.status() shape incl. the M5.3 fields the strip ignores.
    return {
        "name": name, "paused": paused, "running": running, "last_error": last_error,
        "last_completed": False, "run_count": 0, "next_ready_in": 0.0,
    }


def test_no_agents_renders_dim_placeholder():
    strip = roster_glyphs([])
    assert strip.plain == "no agents"
    assert str(strip.style) == "dim"
    assert roster_summary([]) == "no agents"


def test_idle_cast_renders_every_glyph_with_idle_mark():
    strip = roster_glyphs([_row(n) for n in CAST])
    assert strip.plain == "✎· §· ⌂· ♥· ⚖· ↺· ∿·"


def test_running_agent_carries_spinner_mark():
    strip = roster_glyphs([_row("author", running=True), _row("editor")])
    assert strip.plain == f"✎{RUNNING_MARK} §{IDLE_MARK}"


def test_paused_agent_carries_pause_mark():
    strip = roster_glyphs([_row("editor", paused=True)])
    assert strip.plain == f"§{PAUSED_MARK}"


def test_errored_agent_carries_alarm_mark_without_error_text():
    strip = roster_glyphs([_row("author", last_error="RuntimeError: boom" * 10)])
    assert strip.plain == f"✎{ERROR_MARK}"
    assert "boom" not in strip.plain  # errors land in the feed, not the bar


def test_error_wins_over_paused_and_running():
    strip = roster_glyphs([_row("author", paused=True, running=True, last_error="x")])
    assert strip.plain == f"✎{ERROR_MARK}"


def test_paused_wins_over_running():
    strip = roster_glyphs([_row("author", paused=True, running=True)])
    assert strip.plain == f"✎{PAUSED_MARK}"


def test_glyph_takes_agent_color_and_error_mark_takes_alarm_style():
    strip = roster_glyphs([_row("author", last_error="x")])
    styles = [(strip.plain[s.start:s.end], str(s.style)) for s in strip.spans]
    assert ("✎", identity_for("author").style) in styles
    assert (ERROR_MARK, ALARM_STYLE) in styles


def test_running_mark_takes_the_agent_color():
    strip = roster_glyphs([_row("retconner", running=True)])
    styles = [(strip.plain[s.start:s.end], str(s.style)) for s in strip.spans]
    assert (RUNNING_MARK, identity_for("retconner").style) in styles


@given(
    st.lists(
        st.tuples(st.sampled_from(CAST), st.booleans(), st.booleans(),
                  st.one_of(st.none(), st.just("err"))),
        max_size=8,
    )
)
def test_summary_is_the_plain_strip_and_one_cell_pair_per_agent(rows):
    status = [_row(n, paused=p, running=r, last_error=e) for n, p, r, e in rows]
    strip = roster_glyphs(status)
    assert roster_summary(status) == strip.plain
    if status:
        # glyph+mark per agent, single-space-joined: 2 cells per agent + gaps
        assert len(strip.plain) == 3 * len(status) - 1
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/tui/test_roster.py -v
```
Expected: `ImportError: cannot import name 'ERROR_MARK' from 'novelizer.tui.widgets.roster'` (collection error).

- [ ] **Step 3: Write minimal implementation** (replace `novelizer/tui/widgets/roster.py` wholesale)

```python
"""Pure Zone-5 statusbar rendering: scheduler status (+ Task 2: autonomy
state, command hints) -> Rich Text. Same seam as the *_model.py modules —
no Textual imports, no I/O, unit-testable without a terminal.

The old named-summary format ("● author  ⏸ editor  ⚠ x: err") is replaced by
the spec's glyph strip: the whole cast, always visible, one glyph+mark pair
per agent in the agent's identity color. Error text never renders here —
errors land in the feed as alarm lines (spec Zone 5)."""
from __future__ import annotations

from rich.text import Text

from novelizer.tui.identity import identity_for
from novelizer.tui.widgets.feed_model import ALARM_STYLE

DIM = "dim"

# State marks appended to each agent's glyph. Precedence: errored > paused >
# running > idle. The spinner is static per render (any spinner char is fine).
RUNNING_MARK = "⠋"
IDLE_MARK = "·"
PAUSED_MARK = "‖"
ERROR_MARK = "!"


def _mark(s: dict) -> tuple[str, str | None]:
    """(mark, style) for one status row; style None means 'agent color'."""
    if s.get("last_error"):
        return ERROR_MARK, ALARM_STYLE
    if s.get("paused"):
        return PAUSED_MARK, DIM
    if s.get("running"):
        return RUNNING_MARK, None
    return IDLE_MARK, DIM


def roster_glyphs(status: list) -> Text:
    """The cast as a glyph strip — '✎⠋ §· ⌂· ♥· ⚖· ↺· ∿·'. Glyph in the
    agent's color; mark carries state. M5.3 status fields the strip does not
    need (last_completed, run_count, next_ready_in) are accepted and ignored."""
    if not status:
        return Text("no agents", style=DIM)
    strip = Text()
    for i, s in enumerate(status):
        if i:
            strip.append(" ")
        ident = identity_for(s["name"])
        strip.append(ident.glyph, style=ident.style)
        mark, style = _mark(s)
        strip.append(mark, style=ident.style if style is None else style)
    return strip


def roster_summary(status: list) -> str:
    """Plain-string variant of the glyph strip for string-surface needs."""
    return roster_glyphs(status).plain
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/tui/test_roster.py -v
```
Expected: 10 passed (the Hypothesis test counts as one). Note: `tests/tui/test_app_layout.py::test_mission_control_panes_present_and_populate` now fails (statusbar no longer names agents) — that is Task 2's rewrite; do not run the full suite between Tasks 1 and 2.

- [ ] **Step 5: Commit**

```
git add novelizer/tui/widgets/roster.py tests/tui/test_roster.py
git commit -m "feat: roster glyph strip — whole cast as identity-colored glyph+state marks, replacing the named summary"
```

---

### Task 2: Autonomy dial meter, statusbar strip, placeholder hints

**Files:**
- Modify: `novelizer/tui/widgets/roster.py` (append)
- Modify: `novelizer/tui/app.py` (delete `_status_line`, rewire `_statusbar_loop`, `hint_index`, placeholder)
- Modify: `novelizer/director/cli.py` (`_launch_tui` passes a random hint index)
- Modify: `tests/tui/test_roster.py` (append), `tests/tui/test_app.py` (rewrite the two `_status_line` tests), `tests/tui/test_app_layout.py` (rewrite statusbar assertions), `tests/tui/test_app_commands.py` (append placeholder test)

**Interfaces:**
- Consumes: `AutonomyLevel`, `AutonomyState` (`novelizer.canon.autonomy` — the real ladder: `full_auto`, `gated_retcons`, `gated_canon`, `gated_all`), Task 1's `roster_glyphs`, `DIM`.
- Produces: `DIAL_SEGMENTS = 4`, `DIAL_FILLED = "▮"`, `DIAL_EMPTY = "▯"`, `DIAL_LEVELS`, `DIAL_STYLES`, `dial_meter(state: AutonomyState) -> Text`, `status_strip(status: list, state: AutonomyState) -> Text`, `PLACEHOLDER_HINTS: tuple[str, ...]`, `command_hint(index: int) -> str`. The app consumes `status_strip` and `command_hint`; `cli.py` consumes `PLACEHOLDER_HINTS`.

Design decisions locked here: filled segments = trust (spec: "steps with trust level") — `full_auto` 4 / `gated_retcons` 3 / `gated_canon` 2 / `gated_all` 1, matching the mockup's `▮▮▯▯ gated:canon` for gated_canon; label is the enum value verbatim (`gated_canon`, keeping `test_status_line_shows_real_autonomy_level`'s spirit); dial colors green3 → gold3 → dark_orange → red; `NovelizerApp.__init__` gains `hint_index: int = 0` (deterministic default for every existing test; only the CLI randomizes).

- [ ] **Step 1: Write the failing tests**

Append to `tests/tui/test_roster.py`:

```python
from novelizer.canon.autonomy import AutonomyLevel, AutonomyState
from novelizer.tui.widgets.roster import (
    PLACEHOLDER_HINTS,
    command_hint,
    dial_meter,
    status_strip,
)


def test_dial_meter_gated_canon_is_two_filled_segments():
    meter = dial_meter(AutonomyState(global_level=AutonomyLevel.gated_canon))
    assert meter.plain == "AUTONOMY ▮▮▯▯ gated_canon"


def test_dial_meter_full_auto_all_filled_and_gated_all_one_filled():
    full = dial_meter(AutonomyState(global_level=AutonomyLevel.full_auto))
    assert full.plain == "AUTONOMY ▮▮▮▮ full_auto"
    floor = dial_meter(AutonomyState(global_level=AutonomyLevel.gated_all))
    assert floor.plain == "AUTONOMY ▮▯▯▯ gated_all"
    mid = dial_meter(AutonomyState(global_level=AutonomyLevel.gated_retcons))
    assert mid.plain == "AUTONOMY ▮▮▮▯ gated_retcons"


def test_dial_meter_color_steps_with_trust():
    full = dial_meter(AutonomyState(global_level=AutonomyLevel.full_auto))
    styles = [(full.plain[s.start:s.end], str(s.style)) for s in full.spans]
    assert ("▮▮▮▮", "green3") in styles
    floor = dial_meter(AutonomyState(global_level=AutonomyLevel.gated_all))
    styles = [(floor.plain[s.start:s.end], str(s.style)) for s in floor.spans]
    assert ("▮", "red") in styles


def test_dial_meter_summarizes_overrides_compactly():
    meter = dial_meter(AutonomyState(
        global_level=AutonomyLevel.full_auto,
        overrides={"retconner": AutonomyLevel.gated_all},
    ))
    assert meter.plain == "AUTONOMY ▮▮▮▮ full_auto (retconner=gated_all)"


def test_status_strip_composes_roster_then_dial():
    strip = status_strip([_row("author")], AutonomyState(global_level=AutonomyLevel.gated_canon))
    assert strip.plain == "✎·    AUTONOMY ▮▮▯▯ gated_canon"


def test_command_hint_is_deterministic_and_wraps():
    assert command_hint(0) == PLACEHOLDER_HINTS[0]
    assert command_hint(len(PLACEHOLDER_HINTS)) == PLACEHOLDER_HINTS[0]
    assert command_hint(2) == PLACEHOLDER_HINTS[2]
    assert len(PLACEHOLDER_HINTS) == 4
    assert all(h.startswith(":") for h in PLACEHOLDER_HINTS)
```

Replace the two `_status_line` tests in `tests/tui/test_app.py` (delete `test_status_line_shows_real_autonomy_level` and `test_status_line_summarizes_overrides`, add):

```python
def test_dial_shows_real_autonomy_level_never_a_guess():
    from novelizer.tui.widgets.roster import dial_meter
    from novelizer.canon.autonomy import AutonomyLevel, AutonomyState
    line = dial_meter(AutonomyState(global_level=AutonomyLevel.gated_canon)).plain
    assert "gated_canon" in line
    assert "full-auto" not in line


def test_dial_summarizes_overrides():
    from novelizer.tui.widgets.roster import dial_meter
    from novelizer.canon.autonomy import AutonomyLevel, AutonomyState
    line = dial_meter(AutonomyState(global_level=AutonomyLevel.full_auto,
                                    overrides={"retconner": AutonomyLevel.gated_all})).plain
    assert "retconner=gated_all" in line
```

In `tests/tui/test_app_layout.py::test_mission_control_panes_present_and_populate`, replace the statusbar-assertion machinery (the `_roster_names` tuple, the `any(n in statusbar_text ...)` wait condition and the two final statusbar asserts) so the body reads:

```python
            import time
            deadline = time.monotonic() + 5.0
            statusbar_text = ""
            all_labels = []
            # The statusbar is now the glyph strip + dial: the cast's glyphs
            # are always present once _statusbar_loop has run, and the dial
            # renders 'AUTONOMY ▮...' — no agent names appear by design.
            while time.monotonic() < deadline:
                await pilot.pause(0.2)
                statusbar_text = str(app.query_one("#statusbar", Static).renderable)
                tree = app.query_one("#browser", Tree)
                all_labels = [str(n.label) for n in tree.root.children] + [str(c.label) for n in tree.root.children for c in n.children]
                if "AUTONOMY" in statusbar_text and (
                    any("Chapter One" in l for l in all_labels) or any("Chapters (1" in l for l in all_labels)
                ):
                    break
            # statusbar shows the cast glyph strip and the dial; browser shows the authored chapter
            assert "✎" in statusbar_text and "∿" in statusbar_text
            assert "AUTONOMY ▮" in statusbar_text
            assert any("Chapter One" in l for l in all_labels) or any("Chapters (1" in l for l in all_labels)
```

Append to `tests/tui/test_app_commands.py`:

```python
@pytest.mark.asyncio
async def test_command_placeholder_is_hint_zero_by_default():
    from novelizer.tui.widgets.roster import PLACEHOLDER_HINTS

    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1)
    rt = Runtime(settings, runners=_runners())
    await rt.start()
    app = NovelizerApp(rt)  # default hint_index=0: deterministic under test
    try:
        async with app.run_test():
            from textual.widgets import Input
            assert app.query_one("#command", Input).placeholder == PLACEHOLDER_HINTS[0]
    finally:
        await rt.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_command_placeholder_rotates_with_hint_index():
    from novelizer.tui.widgets.roster import PLACEHOLDER_HINTS

    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1)
    rt = Runtime(settings, runners=_runners())
    await rt.start()
    app = NovelizerApp(rt, hint_index=2)
    try:
        async with app.run_test():
            from textual.widgets import Input
            assert app.query_one("#command", Input).placeholder == PLACEHOLDER_HINTS[2]
    finally:
        await rt.close(); os.unlink(path)
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/tui/test_roster.py tests/tui/test_app.py tests/tui/test_app_commands.py -v
```
Expected: `ImportError: cannot import name 'dial_meter' from 'novelizer.tui.widgets.roster'` (collection error on test_roster); after that resolves, `test_app.py`'s new tests fail the same way and the placeholder tests fail with `TypeError: NovelizerApp.__init__() got an unexpected keyword argument 'hint_index'` / placeholder mismatch.

- [ ] **Step 3: Write minimal implementation**

Append to `novelizer/tui/widgets/roster.py` (and add `from novelizer.canon.autonomy import AutonomyLevel, AutonomyState` to its imports):

```python
DIAL_SEGMENTS = 4
DIAL_FILLED = "▮"
DIAL_EMPTY = "▯"

# Trust ladder from the real AutonomyLevel enum: filled segments = trust.
# full_auto is the wide-open dial; gated_all is the floor (1 filled).
DIAL_LEVELS: dict[AutonomyLevel, int] = {
    AutonomyLevel.full_auto: 4,
    AutonomyLevel.gated_retcons: 3,
    AutonomyLevel.gated_canon: 2,
    AutonomyLevel.gated_all: 1,
}
# Color steps with trust (spec Zone 5: green → amber; red at the floor).
DIAL_STYLES: dict[AutonomyLevel, str] = {
    AutonomyLevel.full_auto: "green3",
    AutonomyLevel.gated_retcons: "gold3",
    AutonomyLevel.gated_canon: "dark_orange",
    AutonomyLevel.gated_all: "red",
}


def dial_meter(state: AutonomyState) -> Text:
    """The dial: 'AUTONOMY ▮▮▯▯ gated_canon' — filled segments = trust
    position on the ladder, per-agent overrides folded to a dim suffix."""
    filled = DIAL_LEVELS[state.global_level]
    style = DIAL_STYLES[state.global_level]
    meter = Text()  # no base style: each part styles itself via spans
    meter.append("AUTONOMY ", style=DIM)
    meter.append(DIAL_FILLED * filled, style=style)
    if filled < DIAL_SEGMENTS:
        meter.append(DIAL_EMPTY * (DIAL_SEGMENTS - filled), style=DIM)
    meter.append(f" {state.global_level.value}", style=style)
    if state.overrides:
        summary = ", ".join(f"{k}={v.value}" for k, v in state.overrides.items())
        meter.append(f" ({summary})", style=DIM)
    return meter


def status_strip(status: list, state: AutonomyState) -> Text:
    """The whole Zone-5 statusbar: roster glyph strip + autonomy dial.
    The command cheatsheet is gone — commands live in the Input's hint
    placeholder and :help."""
    strip = roster_glyphs(status)
    strip.append("    ")
    strip.append_text(dial_meter(state))
    return strip


# One hint chosen per app start (command_hint(index)); index 0 under test —
# deterministic, no time/random in the TUI module. Only the CLI randomizes.
PLACEHOLDER_HINTS: tuple[str, ...] = (
    ":seed a lighthouse at the end of the world",
    ":focus the storm that never lands",
    ":pause author — let the room breathe",
    ":autonomy gated_canon — take the wheel yourself",
)


def command_hint(index: int) -> str:
    return PLACEHOLDER_HINTS[index % len(PLACEHOLDER_HINTS)]
```

In `novelizer/tui/app.py`:

1. Replace the import line `from novelizer.tui.widgets.roster import roster_summary` with `from novelizer.tui.widgets.roster import command_hint, status_strip`, and delete the now-unused `from novelizer.canon.autonomy import AutonomyState` import.
2. Delete the whole `_status_line` function (lines 37–45).
3. Extend `__init__`:

```python
    def __init__(self, runtime, hint_index: int = 0) -> None:
        super().__init__()
        self.runtime = runtime
        self._hint_index = hint_index
        self._last_seq = 0
        self._chapter_count = 0
        self.messages: list[str] = []
        self._live_state = LiveRunState()
        self._trace_events: deque = deque(maxlen=200)
```

4. In `compose()`, replace the `Input` line with:

```python
        yield Input(id="command", placeholder=command_hint(self._hint_index), compact=True)
```

5. Replace `_statusbar_loop`:

```python
    async def _statusbar_loop(self) -> None:
        while True:
            try:
                state = await self.runtime.read.get_autonomy_state()
                strip = status_strip(self.runtime.scheduler.status(), state)
                self.query_one("#statusbar", Static).update(strip)
            except Exception as e:
                self._report_worker_error("statusbar", e)
            await asyncio.sleep(0.5)
```

6. Also change `yield Static("AUTONOMY: loading…", id="statusbar")` to `yield Static("loading…", id="statusbar")` (the old string implied the dead format).

In `novelizer/director/cli.py::_launch_tui`, randomize the hint per real app start:

```python
def _launch_tui(settings: EffectiveSettings) -> None:
    import random

    from novelizer.tui.app import NovelizerApp
    from novelizer.tui.widgets.roster import PLACEHOLDER_HINTS

    async def _boot():
        rt = Runtime(settings)
        await rt.start()
        app = NovelizerApp(rt, hint_index=random.randrange(len(PLACEHOLDER_HINTS)))
        try:
            await app.run_async()
        finally:
            await rt.close()

    asyncio.run(_boot())
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/tui/test_roster.py tests/tui/test_app.py tests/tui/test_app_commands.py tests/tui/test_app_layout.py -v
```
Expected: all pass — test_roster 16, test_app 10 (8 feed-format + 2 rewritten dial), test_app_commands 5 (3 old + 2 placeholder), test_app_layout 5 (populate test rewritten; the other four untouched and still green).

- [ ] **Step 5: Commit**

```
git add novelizer/tui/widgets/roster.py novelizer/tui/app.py novelizer/director/cli.py \
        tests/tui/test_roster.py tests/tui/test_app.py tests/tui/test_app_commands.py tests/tui/test_app_layout.py
git commit -m "feat: autonomy dial meter + glyph-strip statusbar; command cheatsheet moves to rotating Input hints"
```

---

### Task 3: Proposals banner — pure line, pane removal, app wiring, tcss, TAB_IDS cleanup

**Files:**
- Rewrite: `novelizer/tui/widgets/proposals_model.py`
- Modify: `novelizer/tui/app.py` (compose swap, `_proposals_loop`), `novelizer/tui/app.tcss`, `novelizer/tui/widgets/brain_panel.py` (delete `TAB_IDS`)
- Rewrite: `tests/tui/test_proposals_model.py`; modify `tests/tui/test_app_layout.py`

**Interfaces:**
- Consumes: nothing new (pure string/Text construction).
- Produces: `BANNER_STYLE = "bold black on gold3"`, `banner_line(count: int) -> Text`. Task 4 appends the modal-facing pure functions to this module. The app consumes `banner_line`; `proposal_line` and `pending_lines` are deleted (grep-verified: `app.py` and `test_proposals_model.py` are their only consumers).

Design decisions locked here: the banner widget carries no border and no title (it *is* one line); visibility is `widget.display`, toggled by the existing 0.5 s `_proposals_loop`; `banner_line` requires `count >= 1` (the app never calls it at 0). The `#body.engine` rule's `#proposals` member becomes `#proposals_banner` (engine mode must hide the banner exactly as it hid the pane) and the vestigial `#roster` member is dropped. `TAB_IDS` in `brain_panel.py` is deleted here — this is the first task on the app-layout surface, and grep shows zero references.

- [ ] **Step 1: Write the failing tests**

Replace `tests/tui/test_proposals_model.py` wholesale:

```python
from hypothesis import given, strategies as st

from novelizer.tui.widgets.proposals_model import BANNER_STYLE, banner_line


def test_banner_line_singular():
    line = banner_line(1)
    assert line.plain == "▼ 1 proposal awaiting approval — press a"
    assert str(line.style) == BANNER_STYLE


def test_banner_line_plural_matches_spec_mockup():
    assert banner_line(2).plain == "▼ 2 proposals awaiting approval — press a"


@given(st.integers(min_value=1, max_value=99))
def test_banner_line_always_counts_and_always_high_contrast(n):
    line = banner_line(n)
    assert line.plain.startswith(f"▼ {n} proposal")
    assert line.plain.endswith("— press a")
    assert str(line.style) == BANNER_STYLE
```

In `tests/tui/test_app_layout.py`:

1. In `test_every_pane_has_its_border_title`, delete the `"#proposals": "PROPOSALS",` entry (the banner is title-less by design; 5 entries → 4).
2. Replace `test_approval_queue_pane_shows_pending_proposal_and_approve_via_command` wholesale:

```python
@pytest.mark.asyncio
async def test_proposals_banner_appears_and_approve_via_command_clears_it():
    from textual.widgets import Static
    from novelizer.canon.events import EventType
    from novelizer.canon.autonomy import AutonomyLevel, AutonomyState
    from novelizer.store.models import Chapter

    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1)
    rt = Runtime(settings, runners=_runners())
    await rt.start()
    # Pause all background agents to ensure deterministic test (only the intended proposal exists)
    for name in ["world_architect", "character_keeper", "author", "editor",
                 "continuity_checker", "retconner", "structure_analyst"]:
        rt.scheduler.pause_agent(name)
    try:
        await rt.events.append(EventType.AUTONOMY_CHANGED, "singleton",
                                AutonomyState(global_level=AutonomyLevel.gated_canon))
        await rt.projector.catch_up()
        ch = Chapter(id="c1", title="Pending One", prose="p")
        await rt.committer.commit("author", EventType.CHAPTER_CREATED, ch.id, ch)
        await rt.projector.catch_up()
        app = NovelizerApp(rt)
        async with app.run_test() as pilot:
            await pilot.pause(0.7)  # let _proposals_loop cycle
            banner = app.query_one("#proposals_banner", Static)
            assert banner.display, "banner must be visible while a proposal is open"
            banner_text = str(banner.renderable)
            assert banner_text == "▼ 1 proposal awaiting approval — press a"
            pending = await rt.read.list_proposals(status="open")
            assert len(pending) == 1
            assert pending[0].id[:8] not in banner_text   # id-free dashboard
            # the resident pane is gone
            assert not app.query("#proposals")
            # approving through the command seam still works and empties the queue
            await app._run_command(f"approve {pending[0].id}")
            await rt.projector.catch_up()
            chapters = await rt.read.list_chapters()
            assert len(chapters) == 1 and chapters[0].title == "Pending One"
            await pilot.pause(0.7)
            assert not app.query_one("#proposals_banner", Static).display
    finally:
        await rt.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_proposals_banner_hidden_on_a_quiet_story():
    from textual.widgets import Static

    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1)
    rt = Runtime(settings, runners=_runners())
    await rt.start()
    for name in ["world_architect", "character_keeper", "author", "editor",
                 "continuity_checker", "retconner", "structure_analyst"]:
        rt.scheduler.pause_agent(name)
    app = NovelizerApp(rt)
    try:
        async with app.run_test() as pilot:
            await pilot.pause(0.7)
            assert not app.query_one("#proposals_banner", Static).display
    finally:
        await rt.close(); os.unlink(path)
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/tui/test_proposals_model.py tests/tui/test_app_layout.py -v
```
Expected: `ImportError: cannot import name 'BANNER_STYLE' from 'novelizer.tui.widgets.proposals_model'` (collection error); the layout tests fail on `#proposals_banner` not existing.

- [ ] **Step 3: Write minimal implementation**

Replace `novelizer/tui/widgets/proposals_model.py` wholesale:

```python
"""Pure proposal rendering: open-proposal records -> the banner line and
(Task 4) the approval modal's rows and payload context. No Textual imports,
no I/O — same seam as the other *_model.py modules."""
from __future__ import annotations

from rich.text import Text

# The one high-contrast line on the dashboard (spec Zone 3: "then it is the
# most visible thing on screen").
BANNER_STYLE = "bold black on gold3"


def banner_line(count: int) -> Text:
    """'▼ 2 proposals awaiting approval — press a'. Only called with
    count >= 1 — the app hides the banner widget entirely when the queue is
    empty (zero rows spent)."""
    noun = "proposal" if count == 1 else "proposals"
    return Text(f"▼ {count} {noun} awaiting approval — press a", style=BANNER_STYLE)
```

In `novelizer/tui/app.py`:

1. Replace the import `from novelizer.tui.widgets.proposals_model import pending_lines` with `from novelizer.tui.widgets.proposals_model import banner_line`.
2. In `compose()`, replace the three-line `#proposals` block with the banner (still between feed and brain):

```python
                banner = Static(id="proposals_banner")
                banner.display = False
                yield banner
```

3. Replace `_proposals_loop`:

```python
    async def _proposals_loop(self) -> None:
        while True:
            try:
                open_count = len(await self.runtime.read.list_proposals(status="open"))
                banner = self.query_one("#proposals_banner", Static)
                if open_count:
                    banner.update(banner_line(open_count))
                banner.display = bool(open_count)
            except Exception as e:
                self._report_worker_error("proposals", e)
            await asyncio.sleep(0.5)
```

In `novelizer/tui/app.tcss`:

1. Replace the `#proposals` rule with: `#proposals_banner { height: 1; padding: 0 1; }`
2. Replace the engine hide rule (dropping the vestigial `#roster` member — cleanup):

```
#body.engine #feed, #body.engine #proposals_banner,
#body.engine #brain { display: none; }
```

3. Change the border-title-color rule to `#brain, #detail_scroll { border-title-color: $text-muted; }` (drop `#proposals`).

In `novelizer/tui/widgets/brain_panel.py`, delete the dead constant line:

```python
TAB_IDS = ("tab_shape", "tab_threads", "tab_secrets", "tab_causeway")
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/tui/test_proposals_model.py tests/tui/test_app_layout.py tests/tui/test_brain_panel.py tests/tui/test_engine_room.py -v
```
Expected: all pass — test_proposals_model 3, test_app_layout 6 (4 kept + 2 banner tests), and the untouched brain-panel/engine-room suites confirm the tcss member swap broke nothing.

- [ ] **Step 5: Commit**

```
git add novelizer/tui/widgets/proposals_model.py novelizer/tui/widgets/brain_panel.py \
        novelizer/tui/app.py novelizer/tui/app.tcss \
        tests/tui/test_proposals_model.py tests/tui/test_app_layout.py
git commit -m "feat: proposals banner — one high-contrast line replaces the resident pane (hidden when quiet); drop dead TAB_IDS"
```

---

### Task 4: Approval modal — pure rows/context + `ApprovalScreen` + `a` binding with guard

**Files:**
- Modify: `novelizer/tui/widgets/proposals_model.py` (append)
- Create: `novelizer/tui/approval_screen.py`
- Modify: `novelizer/tui/app.py` (binding + `action_approvals`), `novelizer/tui/app.tcss` (modal rules)
- Modify: `tests/tui/test_proposals_model.py` (append); Create: `tests/tui/test_approval_screen.py`

**Interfaces:**
- Consumes: `Proposal` (`novelizer.canon.autonomy` — fields `id`, `proposing_agent`, `target_event_type`, `target_aggregate_id`, `payload: dict`, `status`), `identity_for`, `SPEAKER_WIDTH` (`novelizer.tui.identity`), `ReadStore.list_proposals(status="open")`, `commands.dispatch` **via** `NovelizerApp._run_command`, `runtime.projector.catch_up()`, Textual `ModalScreen`/`OptionList`/`Option`.
- Produces: `payload_summary(payload: dict) -> str`, `proposal_row(p: Proposal) -> Text`, `proposal_context(p: Proposal) -> Text`, `ApprovalScreen(ModalScreen)` (constructor takes `runtime`), app binding `("a", "approvals", "Approve")` + `async def action_approvals`.

Design decisions locked here: `enter` approves via `OptionList.OptionSelected` (OptionList owns the enter key; no separate binding), `x`/`escape` are `ApprovalScreen` BINDINGS; after each decision the screen awaits `projector.catch_up()` then reloads, dismissing itself when the queue empties; `action_approvals` never pushes when a non-default screen is up (`self.screen is not self.default_screen`) or when the queue is empty; the context pane skips id-ish/bookkeeping payload keys (`id`, `*_id`, `*_ids`, `created_at`, `provenance`) and empty values.

- [ ] **Step 1: Write the failing tests**

Append to `tests/tui/test_proposals_model.py`:

```python
from novelizer.canon.autonomy import Proposal
from novelizer.tui.identity import identity_for
from novelizer.tui.widgets.proposals_model import (
    payload_summary,
    proposal_context,
    proposal_row,
)


def _proposal(**payload):
    return Proposal(id="abcdef12-0000-0000-0000-000000000000",
                    proposing_agent="author", target_event_type="chapter.created",
                    target_aggregate_id="c1", payload=payload)


def test_payload_summary_prefers_title_then_falls_through_the_human_keys():
    assert payload_summary({"title": "The Salt Road", "body": "x"}) == "The Salt Road"
    assert payload_summary({"name": "Elara"}) == "Elara"
    assert payload_summary({"note": "a note"}) == "a note"
    assert payload_summary({"description": "scar mismatch"}) == "scar mismatch"
    assert payload_summary({"body": "prose here"}) == "prose here"
    assert payload_summary({}) == ""
    assert payload_summary({"tension": 0.5}) == ""


def test_payload_summary_collapses_whitespace_and_clips_to_60():
    summary = payload_summary({"title": "word " * 40})
    assert "\n" not in summary
    assert len(summary) == 60 and summary.endswith("…")


def test_proposal_row_is_id_free_and_names_agent_and_target():
    row = proposal_row(_proposal(title="Pending One"))
    assert "abcdef12" not in row.plain
    assert row.plain == "✎ Author    → chapter.created  Pending One"
    styles = [(row.plain[s.start:s.end], str(s.style)) for s in row.spans]
    assert ("✎ Author    ", identity_for("author").style) in styles


def test_proposal_row_without_summary_has_no_trailing_gap():
    row = proposal_row(_proposal())
    assert row.plain == "✎ Author    → chapter.created"


def test_proposal_context_bold_header_and_payload_fields():
    ctx = proposal_context(_proposal(title="Pending One", prose="It began."))
    lines = ctx.plain.splitlines()
    assert lines[0] == "Author proposes chapter.created"
    assert "title: Pending One" in lines
    assert "prose: It began." in lines
    styles = [(ctx.plain[s.start:s.end], str(s.style)) for s in ctx.spans]
    assert ("Author proposes chapter.created", "bold") in styles


def test_proposal_context_skips_id_like_and_bookkeeping_keys_and_empties():
    ctx = proposal_context(_proposal(
        id="025bae36", supersedes_id="x", event_ids=["a"], character_ids=[],
        created_at="2026-07-18", provenance={"model": "m"},
        title="Kept", editor_notes=None, prose="",
    ))
    assert "025bae36" not in ctx.plain
    assert "supersedes_id" not in ctx.plain and "event_ids" not in ctx.plain
    assert "created_at" not in ctx.plain and "provenance" not in ctx.plain
    assert "editor_notes" not in ctx.plain and "prose:" not in ctx.plain
    assert "title: Kept" in ctx.plain
```

Create `tests/tui/test_approval_screen.py`:

```python
import os
import tempfile
import pytest
from novelizer.settings import EffectiveSettings as Settings
from novelizer.runtime import Runtime
from novelizer.tui.app import NovelizerApp
from novelizer.tui.approval_screen import ApprovalScreen
from novelizer.canon.events import EventType
from novelizer.canon.autonomy import AutonomyLevel, AutonomyState
from novelizer.store.models import Chapter
from novelizer.agents.schemas import (
    WorldEntriesDraft, KeeperOutput, EditorVerdict, ContinuityOutput, RetconAmendments, StructureAnalystOutput,
)
from novelizer.agents.base import ChapterDraft


class _R:
    def __init__(self, out): self._out = out
    async def ainvoke(self, inputs): return {"structured_response": self._out}


def _runners():
    return {k: _R(v) for k, v in {
        "world_architect": WorldEntriesDraft(), "author": ChapterDraft(title="X", prose="y"),
        "character_keeper": KeeperOutput(), "editor": EditorVerdict(), "continuity_checker": ContinuityOutput(),
        "retconner": RetconAmendments(),
        "structure_analyst": StructureAnalystOutput(),
    }.items()}


async def _gated_app(n_proposals: int = 1):
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1)
    rt = Runtime(settings, runners=_runners())
    await rt.start()
    for a in rt.scheduler.status():
        rt.scheduler.pause_agent(a["name"])
    await rt.events.append(EventType.AUTONOMY_CHANGED, "singleton",
                           AutonomyState(global_level=AutonomyLevel.gated_canon))
    await rt.projector.catch_up()
    for i in range(n_proposals):
        ch = Chapter(id=f"c{i + 1}", title=f"Pending {i + 1}", prose="It waits.")
        await rt.committer.commit("author", EventType.CHAPTER_CREATED, ch.id, ch)
    await rt.projector.catch_up()
    return NovelizerApp(rt), rt, path


@pytest.mark.asyncio
async def test_a_key_opens_modal_with_id_free_rows_and_context():
    app, rt, path = await _gated_app()
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            assert isinstance(app.screen, ApprovalScreen)
            from textual.widgets import OptionList, Static
            options = app.screen.query_one("#approval_list", OptionList)
            assert options.option_count == 1
            row = options.get_option_at_index(0).prompt.plain
            assert "Author" in row and "chapter.created" in row and "Pending 1" in row
            pending = await rt.read.list_proposals(status="open")
            assert pending[0].id[:8] not in row
            context = str(app.screen.query_one("#approval_context", Static).renderable)
            assert "Author proposes chapter.created" in context
            assert "It waits." in context
    finally:
        await rt.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_a_key_does_nothing_when_no_open_proposals():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1)
    rt = Runtime(settings, runners=_runners())
    await rt.start()
    for a in rt.scheduler.status():
        rt.scheduler.pause_agent(a["name"])
    app = NovelizerApp(rt)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            assert not isinstance(app.screen, ApprovalScreen)
            assert len(app.screen_stack) == 1
    finally:
        await rt.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_a_key_never_stacks_a_second_modal():
    app, rt, path = await _gated_app()
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            await pilot.press("a")   # falls through the modal to the app binding
            await pilot.pause()
            assert len(app.screen_stack) == 2  # default + one modal, never more
    finally:
        await rt.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_enter_approves_through_dispatch_and_dismisses_when_queue_empties():
    app, rt, path = await _gated_app()
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause(0.3)
            # the target event was committed for real
            chapters = await rt.read.list_chapters()
            assert len(chapters) == 1 and chapters[0].title == "Pending 1"
            # the result line landed in the feed like a typed command
            assert any(m.startswith("» Approved proposal") for m in app.messages)
            # queue empty -> modal dismissed itself
            assert not isinstance(app.screen, ApprovalScreen)
    finally:
        await rt.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_x_rejects_and_second_proposal_stays_listed():
    app, rt, path = await _gated_app(n_proposals=2)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            await pilot.press("x")
            await pilot.pause(0.3)
            assert any(m.startswith("» Rejected proposal") for m in app.messages)
            assert await rt.read.list_chapters() == []          # nothing committed
            assert isinstance(app.screen, ApprovalScreen)       # one proposal left
            from textual.widgets import OptionList
            assert app.screen.query_one("#approval_list", OptionList).option_count == 1
            assert len(await rt.read.list_proposals(status="open")) == 1
    finally:
        await rt.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_escape_closes_without_deciding():
    app, rt, path = await _gated_app()
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, ApprovalScreen)
            assert len(await rt.read.list_proposals(status="open")) == 1
    finally:
        await rt.close(); os.unlink(path)
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/tui/test_proposals_model.py tests/tui/test_approval_screen.py -v
```
Expected: `ImportError: cannot import name 'payload_summary'` on test_proposals_model; `ModuleNotFoundError: No module named 'novelizer.tui.approval_screen'` on test_approval_screen (collection errors).

- [ ] **Step 3: Write minimal implementation**

Append to `novelizer/tui/widgets/proposals_model.py` (and add `from novelizer.canon.autonomy import Proposal` and `from novelizer.tui.identity import SPEAKER_WIDTH, identity_for` to its imports):

```python
DIM = "dim"

# Human payload fields, in feed_model's fallback order; ids never summarize.
_SUMMARY_KEYS = ("title", "name", "note", "description", "body")
SUMMARY_WIDTH = 60
_VALUE_WIDTH = 160
_SKIP_CONTEXT_KEYS = {"created_at", "provenance"}


def _one_line(value: object, width: int) -> str:
    collapsed = " ".join(str(value).split())
    return collapsed if len(collapsed) <= width else collapsed[: width - 1] + "…"


def payload_summary(payload: dict) -> str:
    """One collapsed, clipped line naming the proposed payload — first
    non-empty human field, or '' when the payload has none."""
    for key in _SUMMARY_KEYS:
        if payload.get(key):
            return _one_line(payload[key], SUMMARY_WIDTH)
    return ""


def proposal_row(p: Proposal) -> Text:
    """One id-free queue row: proposing agent (glyph + label in the agent's
    color, feed-aligned) → target event type, dim payload summary."""
    ident = identity_for(p.proposing_agent)
    row = Text()  # spans, not a base style — the tests assert on row.spans
    row.append(f"{ident.glyph} {ident.label}".ljust(SPEAKER_WIDTH), style=ident.style)
    row.append(f"→ {p.target_event_type}")
    summary = payload_summary(p.payload)
    if summary:
        row.append(f"  {summary}", style=DIM)
    return row


def _is_bookkeeping(key: str) -> bool:
    return (
        key == "id"
        or key.endswith("_id")
        or key.endswith("_ids")
        or key in _SKIP_CONTEXT_KEYS
    )


def proposal_context(p: Proposal) -> Text:
    """Full payload context for the highlighted row: bold header, then one
    'key: value' line per human payload field. Ids/slugs and bookkeeping
    fields are skipped — names, not ids — and empty values are omitted."""
    ident = identity_for(p.proposing_agent)
    ctx = Text()  # spans, not a base style — bold must not bleed into values
    ctx.append(f"{ident.label} proposes {p.target_event_type}", style="bold")
    for key, val in p.payload.items():
        if _is_bookkeeping(key) or val in (None, "", [], {}):
            continue
        ctx.append("\n")
        ctx.append(f"{key}: ", style=DIM)
        ctx.append(_one_line(val, _VALUE_WIDTH))
    return ctx
```

Create `novelizer/tui/approval_screen.py`:

```python
"""The approval queue as a modal drill-in (spec Zone 3). Thin shell: every
rendered row and context comes from the pure proposals_model functions, and
approve/reject go ONLY through commands.dispatch — via the app's
_run_command, so the result line lands in the feed and app.messages exactly
like a typed ':approve <id>'. ProposalService/Committer are never called
from here."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from novelizer.tui.widgets.proposals_model import proposal_context, proposal_row


class ApprovalScreen(ModalScreen):
    """List of open proposals + full payload context for the highlighted row.
    enter = approve (OptionList's select), x = reject, escape = close."""

    BINDINGS = [
        ("escape", "close", "Close"),
        ("x", "reject", "Reject"),
    ]

    def __init__(self, runtime) -> None:
        super().__init__()
        self.runtime = runtime
        self._by_id: dict = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="approval_box") as box:
            box.border_title = "APPROVALS"
            yield OptionList(id="approval_list")
            yield Static("", id="approval_context")

    async def on_mount(self) -> None:
        await self._reload()

    async def _reload(self) -> None:
        props = await self.runtime.read.list_proposals(status="open")
        if not props:
            self.dismiss()
            return
        self._by_id = {p.id: p for p in props}
        options = self.query_one("#approval_list", OptionList)
        options.clear_options()
        for p in props:
            options.add_option(Option(proposal_row(p), id=p.id))
        options.highlighted = 0
        options.focus()

    def on_option_list_option_highlighted(self, event) -> None:
        proposal = self._by_id.get(event.option.id)
        if proposal is not None:
            self.query_one("#approval_context", Static).update(proposal_context(proposal))

    async def on_option_list_option_selected(self, event) -> None:
        await self._decide("approve", event.option.id)

    async def action_reject(self) -> None:
        options = self.query_one("#approval_list", OptionList)
        if options.highlighted is None:
            return
        await self._decide("reject", options.get_option_at_index(options.highlighted).id)

    def action_close(self) -> None:
        self.dismiss()

    async def _decide(self, verb: str, proposal_id: str) -> None:
        # The one seam: commands.dispatch via the app's command runner, so
        # the '» Approved/Rejected proposal …' line lands in the feed exactly
        # like a typed command. Then catch the projector up before reloading:
        # the read store only learns the proposal left 'open' after
        # projection, and reloading early would re-show it (and let a second
        # enter double-commit the target event).
        await self.app._run_command(f"{verb} {proposal_id}")
        await self.runtime.projector.catch_up()
        await self._reload()
```

In `novelizer/tui/app.py`:

1. Add `from novelizer.tui.approval_screen import ApprovalScreen` to the imports.
2. Add the binding to `BINDINGS`, after `("ctrl+k", ...)`:

```python
        ("a", "approvals", "Approve"),
```

3. Add the action (near `action_focus_command`):

```python
    async def action_approvals(self) -> None:
        # Guard: never stack the modal over itself or over another pushed
        # screen (e.g. SettingsScreen). App bindings still fire while a modal
        # is up for keys the modal doesn't consume, so this must be checked.
        if self.screen is not self.default_screen:
            return
        if not await self.runtime.read.list_proposals(status="open"):
            return
        self.push_screen(ApprovalScreen(self.runtime))
```

Append to `novelizer/tui/app.tcss`:

```
ApprovalScreen { align: center middle; }
#approval_box { width: 80%; max-width: 100; height: 70%; border: round $primary; background: $surface; border-title-style: bold; }
#approval_list { height: 1fr; }
#approval_context { height: auto; max-height: 10; padding: 0 1; }
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/tui/test_proposals_model.py tests/tui/test_approval_screen.py -v
```
Expected: all pass — test_proposals_model 9 (3 banner + 6 modal-pure), test_approval_screen 6.

- [ ] **Step 5: Commit**

```
git add novelizer/tui/widgets/proposals_model.py novelizer/tui/approval_screen.py \
        novelizer/tui/app.py novelizer/tui/app.tcss \
        tests/tui/test_proposals_model.py tests/tui/test_approval_screen.py
git commit -m "feat: approval modal — id-free queue + payload context; enter/x/escape decide through commands.dispatch only"
```

---

### Task 5: Browser state cues, Threads section, detail typography, dynamic detail title

**Files:**
- Rewrite: `novelizer/tui/widgets/browser_model.py`
- Modify: `novelizer/tui/widgets/browser.py` (`refresh_sections` signature), `novelizer/tui/app.py` (`_browser_loop`, `on_tree_node_selected`, `_update_detail`), `novelizer/tui/app.tcss` (`#detail` max-width)
- Rewrite: `tests/tui/test_browser_model.py`; modify `tests/tui/test_browser_widget.py`; append to `tests/tui/test_app_layout.py`

**Interfaces:**
- Consumes: `is_thread_stale` (`novelizer.brain.staleness` — single-sourcing, same as the brain panel), `TERMINAL_STATES` (`novelizer.canon.threads`), `EditorialStatus` (`novelizer.store.models`), `chapter_label`, `chapter_number` (`novelizer.tui.widgets.brain_model` — the existing title helpers, never re-implemented), `ReadStore.get_thread`/`list_threads`, `rich.text.Text`.
- Produces: `STATUS_DOTS: dict[EditorialStatus, str]`, `word_count(prose: str) -> int`, `browser_sections(read, *, staleness_threshold: int) -> list`, `DetailView` (frozen dataclass: `title: str`, `body: Text`), `detail_view(read, section_key: str, item_id: str) -> DetailView`. `detail_text` is deleted (grep-verified consumers: `app.py`, `test_browser_model.py` — both updated here). `StoryBrowser.refresh_sections(read, *, staleness_threshold)`. `_update_detail(content, title="")`.

Design decisions locked here: section order becomes chapters, characters, world, retcons, **threads**, themes (spec mockup order); threads items list *all* threads (`⚠`/`·`/`✓` prefixes carry state) while the label counts open (+ stale); `staleness_threshold` is keyword-only with no default at both the model and widget seam — the app is the only production caller and reads `settings.staleness_threshold_chapters` every cycle; empty detail fields are skipped (no `Motivations: ` ghost lines); the thread detail's last-touch uses `chapter_label` only when the chapter is known, else `—` (no raw-id fallback on this surface); `_update_detail` keeps accepting a plain str positionally so `test_detail_pane_scroll` is untouched.

- [ ] **Step 1: Write the failing tests**

Replace `tests/tui/test_browser_model.py` wholesale:

```python
import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.events import EventType, ThemeIntroduced, ThreadPlanted, ThreadTouched
from novelizer.store.models import (
    Chapter, Character, EditorialStatus, RetconRequest, RetconStatus, WorldEntry,
)
from novelizer.tui.widgets.browser_model import (
    STATUS_DOTS,
    browser_sections,
    detail_view,
    word_count,
)

THRESHOLD = 3  # tests pin the explicit keyword; production passes settings


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


async def test_sections_cover_all_categories_including_threads(stack):
    events, proj, read = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="It began."))
    await events.append(EventType.CHARACTER_CREATED, "ch1", Character(id="ch1", name="Mira", traits="stoic"))
    await events.append(EventType.WORLD_ENTRY_CREATED, "w1", WorldEntry(id="w1", title="Brinemarsh", body="salt"))
    await events.append(EventType.RETCON_REQUEST_CREATED, "r1", RetconRequest(id="r1", description="scar mismatch", conflicting_entry_ids=[], proposed_resolution="left hand"))
    await events.append(EventType.THREAD_PLANTED, "the-locket", ThreadPlanted(id="the-locket", name="The Locket"))
    await proj.catch_up()
    secs = await browser_sections(read, staleness_threshold=THRESHOLD)
    assert [s["key"] for s in secs] == ["chapters", "characters", "world", "retcons", "threads", "themes"]
    assert "Mira" in secs[1]["items"][0]["label"]
    assert "Brinemarsh" in secs[2]["items"][0]["label"]
    assert "scar mismatch" in secs[3]["items"][0]["label"]
    assert "The Locket" in secs[4]["items"][0]["label"]
    assert "the-locket" not in secs[4]["items"][0]["label"]   # no slugs anywhere


def test_status_dots_cover_the_real_editorial_statuses():
    # Real enum values are draft/reviewed/final — the spec sketch's
    # approved/draft/revising names map by pipeline position.
    assert STATUS_DOTS == {
        EditorialStatus.draft: "◌",
        EditorialStatus.reviewed: "◐",
        EditorialStatus.final: "●",
    }


async def test_chapter_rows_show_status_dot_not_enum_text(stack):
    events, proj, read = stack
    await events.append(EventType.CHAPTER_CREATED, "c1",
                        Chapter(id="c1", title="One", prose="p"))
    await events.append(EventType.CHAPTER_CREATED, "c2",
                        Chapter(id="c2", title="Two", prose="p", editorial_status=EditorialStatus.final))
    await proj.catch_up()
    secs = await browser_sections(read, staleness_threshold=THRESHOLD)
    chapter_labels = [i["label"] for i in secs[0]["items"]]
    assert chapter_labels == ["◌ One", "● Two"]
    assert not any("EditorialStatus" in l or "[" in l for l in chapter_labels)


async def test_retcons_label_gains_alarm_mark_only_when_open(stack):
    events, proj, read = stack
    secs = await browser_sections(read, staleness_threshold=THRESHOLD)
    assert [s for s in secs if s["key"] == "retcons"][0]["label"] == "Retcons (0)"
    await events.append(EventType.RETCON_REQUEST_CREATED, "r1",
                        RetconRequest(id="r1", description="open change", conflicting_entry_ids=[], proposed_resolution="fix"))
    r2 = RetconRequest(id="r2", description="resolved change", conflicting_entry_ids=[], proposed_resolution="fix")
    await events.append(EventType.RETCON_REQUEST_CREATED, "r2", r2)
    await events.append(EventType.RETCON_REQUEST_RESOLVED, "r2",
                        r2.model_copy(update={"status": RetconStatus.resolved}))
    await proj.catch_up()
    secs = await browser_sections(read, staleness_threshold=THRESHOLD)
    retcons = [s for s in secs if s["key"] == "retcons"][0]
    assert retcons["label"] == "Retcons (1) ⚠"
    assert len(retcons["items"]) == 1 and retcons["items"][0]["id"] == "r1"


async def test_threads_label_counts_open_and_stale_via_explicit_threshold(stack):
    events, proj, read = stack
    await events.append(EventType.THREAD_PLANTED, "t1", ThreadPlanted(id="t1", name="Stale One"))
    for i in range(3):
        await events.append(EventType.CHAPTER_CREATED, f"c{i}", Chapter(id=f"c{i}", title=f"Ch{i}", prose="p"))
    await events.append(EventType.THREAD_PLANTED, "t2", ThreadPlanted(id="t2", name="Fresh Two"))
    await events.append(EventType.THREAD_TOUCHED, "t2", ThreadTouched(id="t2", chapter_id="c2"))
    await proj.catch_up()
    secs = await browser_sections(read, staleness_threshold=THRESHOLD)
    threads = [s for s in secs if s["key"] == "threads"][0]
    assert threads["label"] == "Threads (2 · 1 stale)"
    labels = [i["label"] for i in threads["items"]]
    assert "⚠ Stale One · stale" in labels
    assert any(l.startswith("· Fresh Two") for l in labels)
    # the SAME data with a looser threshold is quiet — staleness is a
    # parameter fed from settings, never re-typed
    loose = await browser_sections(read, staleness_threshold=99)
    assert [s for s in loose if s["key"] == "threads"][0]["label"] == "Threads (2)"


def test_word_count_is_computed_from_prose():
    assert word_count("") == 0
    assert word_count("It began in salt.") == 4


async def test_detail_view_chapter_typography_title_meta_prose(stack):
    events, proj, read = stack
    prose = "It began in salt.\n\nAnd it ended there."
    await events.append(EventType.CHAPTER_CREATED, "c1",
                        Chapter(id="c1", title="One", prose=prose, editorial_status=EditorialStatus.final))
    await proj.catch_up()
    view = await detail_view(read, "chapters", "c1")
    assert view.title == "One"
    lines = view.body.plain.splitlines()
    assert lines[0] == "One"
    assert lines[1] == "final · 8 words"
    assert "It began in salt." in view.body.plain
    assert "And it ended there." in view.body.plain          # paragraphs preserved
    styles = [(view.body.plain[s.start:s.end], str(s.style)) for s in view.body.spans]
    assert ("One", "bold") in styles
    assert ("final · 8 words", "dim") in styles


async def test_detail_view_character_fields_and_voice(stack):
    events, proj, read = stack
    await events.append(EventType.CHARACTER_CREATED, "ch1",
                        Character(id="ch1", name="Mira", traits="stoic", arc_status="wary",
                                  voice="Speaks in short, clipped sentences.", backstory="Born at sea."))
    await proj.catch_up()
    view = await detail_view(read, "characters", "ch1")
    assert view.title == "Mira"
    d = view.body.plain
    assert "Mira" in d and "Traits: stoic" in d and "Arc: wary" in d
    assert "Voice: Speaks in short, clipped sentences." in d
    assert "Born at sea." in d


async def test_detail_view_character_omits_empty_fields(stack):
    events, proj, read = stack
    await events.append(EventType.CHARACTER_CREATED, "ch1", Character(id="ch1", name="Mira", traits="stoic"))
    await proj.catch_up()
    d = (await detail_view(read, "characters", "ch1")).body.plain
    assert "Voice:" not in d and "Motivations:" not in d and "Arc:" not in d


async def test_detail_view_thread_names_state_and_last_touch_no_ids(stack):
    events, proj, read = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="The Gift", prose="p"))
    await events.append(EventType.THREAD_PLANTED, "t1", ThreadPlanted(id="t1", name="The Locket"))
    await events.append(EventType.THREAD_TOUCHED, "t1",
                        ThreadTouched(id="t1", chapter_id="c1", note="left at the tideline"))
    await proj.catch_up()
    view = await detail_view(read, "threads", "t1")
    assert view.title == "The Locket"
    d = view.body.plain
    assert 'touched' in d and 'last touch: ch 1 "The Gift"' in d
    assert "left at the tideline" in d
    assert "t1" not in d and "c1" not in d


async def test_detail_view_thread_unknown_chapter_shows_dash_not_id(stack):
    events, proj, read = stack
    await events.append(EventType.THREAD_PLANTED, "t1", ThreadPlanted(id="t1", name="The Locket"))
    await proj.catch_up()
    d = (await detail_view(read, "threads", "t1")).body.plain
    assert "last touch: —" in d


async def test_detail_view_theme_world_and_retcon(stack):
    events, proj, read = stack
    await events.append(EventType.THEME_INTRODUCED, "loss", ThemeIntroduced(id="loss", title="Loss of Innocence"))
    await events.append(EventType.WORLD_ENTRY_CREATED, "w1", WorldEntry(id="w1", title="Brinemarsh", body="salt"))
    await events.append(EventType.RETCON_REQUEST_CREATED, "r1",
                        RetconRequest(id="r1", description="scar mismatch", conflicting_entry_ids=[], proposed_resolution="left hand"))
    await proj.catch_up()
    theme = await detail_view(read, "themes", "loss")
    assert theme.title == "Loss of Innocence" and "touched 0x" in theme.body.plain
    world = await detail_view(read, "world", "w1")
    assert world.title == "Brinemarsh" and "salt" in world.body.plain
    retcon = await detail_view(read, "retcons", "r1")
    assert retcon.title == "scar mismatch" and "Proposed: left hand" in retcon.body.plain


async def test_detail_view_not_found_is_empty_titled(stack):
    events, proj, read = stack
    for section in ("chapters", "characters", "world", "retcons", "threads", "themes", "nope"):
        view = await detail_view(read, section, "ghost")
        assert view.title == "" and view.body.plain == ""
```

In `tests/tui/test_browser_widget.py`, update `_Host` and the direct call (both tests):

```python
class _Host(App):
    def __init__(self, read): super().__init__(); self._read = read
    def compose(self) -> ComposeResult:
        yield StoryBrowser("Story", id="browser")
    async def on_mount(self):
        await self.query_one(StoryBrowser).refresh_sections(self._read, staleness_threshold=3)
```

and in `test_expansion_preserved_when_item_count_changes` the mid-test refresh becomes:

```python
            await tree.refresh_sections(read, staleness_threshold=3)
```

Append to `tests/tui/test_app_layout.py`:

```python
@pytest.mark.asyncio
async def test_detail_border_title_follows_selection_and_resets():
    from types import SimpleNamespace
    from textual.containers import VerticalScroll
    from novelizer.canon.events import EventType
    from novelizer.store.models import Chapter

    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1)
    rt = Runtime(settings, runners=_runners())
    await rt.start()
    for name in ["world_architect", "character_keeper", "author", "editor",
                 "continuity_checker", "retconner", "structure_analyst"]:
        rt.scheduler.pause_agent(name)
    try:
        await rt.events.append(EventType.CHAPTER_CREATED, "c1",
                               Chapter(id="c1", title="The Name in the Wind", prose="wind words"))
        await rt.projector.catch_up()
        app = NovelizerApp(rt)
        async with app.run_test() as pilot:
            await pilot.pause()
            scroll = app.query_one("#detail_scroll", VerticalScroll)
            assert str(scroll.border_title) == "DETAIL"
            event = SimpleNamespace(node=SimpleNamespace(data={"section": "chapters", "id": "c1"}))
            await app.on_tree_node_selected(event)
            await pilot.pause()
            assert str(scroll.border_title) == "THE NAME IN THE WIND"
            # a miss resets the pane to its quiet label
            event = SimpleNamespace(node=SimpleNamespace(data={"section": "chapters", "id": "ghost"}))
            await app.on_tree_node_selected(event)
            await pilot.pause()
            assert str(scroll.border_title) == "DETAIL"
    finally:
        await rt.close(); os.unlink(path)
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/tui/test_browser_model.py tests/tui/test_browser_widget.py tests/tui/test_app_layout.py -v
```
Expected: `ImportError: cannot import name 'STATUS_DOTS' from 'novelizer.tui.widgets.browser_model'` (collection error); the widget tests fail with `TypeError: refresh_sections() got an unexpected keyword argument 'staleness_threshold'`; the layout title test fails on `detail_text` still being the seam.

- [ ] **Step 3: Write minimal implementation**

Replace `novelizer/tui/widgets/browser_model.py` wholesale:

```python
"""Pure browser rendering: ReadStore records -> tree section dicts and the
detail pane's DetailView. Same seam as the other *_model.py modules — the
async functions here do read-only ReadStore calls plus pure string/Text
construction; no Textual imports.

State cues (spec Zone 4): chapter rows carry an editorial-status dot, the
Retcons label carries ⚠ when open items exist, and the Threads section
carries the stale count. Staleness is never re-derived: is_thread_stale +
the settings-fed staleness_threshold, the SAME pair the brain panel uses.
No ids/slugs in any label."""
from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text

from novelizer.brain.staleness import is_thread_stale
from novelizer.canon.threads import TERMINAL_STATES
from novelizer.store.models import EditorialStatus
from novelizer.tui.widgets.brain_model import chapter_label, chapter_number

DIM = "dim"

# Editorial status -> state dot. Real enum values are draft/reviewed/final —
# the spec sketch's approved/draft/revising names map by pipeline position:
# ● final (done), ◐ reviewed (mid-pipeline), ◌ draft.
STATUS_DOTS: dict[EditorialStatus, str] = {
    EditorialStatus.draft: "◌",
    EditorialStatus.reviewed: "◐",
    EditorialStatus.final: "●",
}


def _enum_val(v):
    """Safely extract enum value, handling both enum and non-enum types."""
    return v.value if hasattr(v, "value") else v


def _status_dot(status) -> str:
    try:
        return STATUS_DOTS[EditorialStatus(_enum_val(status))]
    except (KeyError, ValueError):
        return "·"


def word_count(prose: str) -> int:
    """Render-time word count — computed from prose, never stored (spec
    non-goal: no new events/projections)."""
    return len(prose.split())


def _thread_row_label(thread, chapters, threshold: int) -> str:
    if is_thread_stale(thread, chapters, threshold):
        return f"⚠ {thread.name} · stale"
    if thread.state.value in TERMINAL_STATES:
        return f"✓ {thread.name} · {thread.state.value}"
    return f"· {thread.name} · {thread.state.value}"


async def browser_sections(read, *, staleness_threshold: int) -> list:
    """Tree sections in the spec mockup's order. `staleness_threshold` is
    keyword-only with no default: the app reads
    settings.staleness_threshold_chapters every cycle and passes it in —
    the same M5.3 settings -> pure-param flow the brain panel uses."""
    chapters = await read.list_chapters()
    characters = await read.list_characters()
    world = await read.list_world_entries()
    retcons = await read.list_retcon_requests(status="open")
    threads = await read.list_threads()
    themes = await read.list_themes()
    open_threads = [t for t in threads if t.state.value not in TERMINAL_STATES]
    stale_count = sum(
        1 for t in threads if is_thread_stale(t, chapters, staleness_threshold)
    )
    threads_label = (
        f"Threads ({len(open_threads)} · {stale_count} stale)"
        if stale_count
        else f"Threads ({len(open_threads)})"
    )
    retcons_label = f"Retcons ({len(retcons)}) ⚠" if retcons else "Retcons (0)"
    return [
        {"key": "chapters", "label": f"Chapters ({len(chapters)})",
         "items": [{"id": c.id, "label": f"{_status_dot(c.editorial_status)} {c.title}"} for c in chapters]},
        {"key": "characters", "label": f"Characters ({len(characters)})",
         "items": [{"id": c.id, "label": c.name} for c in characters]},
        {"key": "world", "label": f"World ({len(world)})",
         "items": [{"id": e.id, "label": f"[{_enum_val(e.domain)}] {e.title}"} for e in world]},
        {"key": "retcons", "label": retcons_label,
         "items": [{"id": r.id, "label": r.description[:40]} for r in retcons]},
        {"key": "threads", "label": threads_label,
         "items": [{"id": t.id, "label": _thread_row_label(t, chapters, staleness_threshold)} for t in threads]},
        {"key": "themes", "label": f"Themes ({len(themes)})",
         "items": [{"id": t.id, "label": t.title} for t in themes]},
    ]


@dataclass(frozen=True)
class DetailView:
    title: str   # plain; the app uppercases it into #detail_scroll's border title
    body: Text


_EMPTY_VIEW = DetailView("", Text(""))


def _view(title: str, meta: str, prose: str = "",
          fields: list[tuple[str, str]] | None = None) -> DetailView:
    """Detail typography: bold title line, dim metadata line, dim-labeled
    fields, then prose with its paragraphs preserved."""
    body = Text()  # spans, not a base style — bold must not bleed into prose
    body.append(title, style="bold")
    if meta:
        body.append("\n")
        body.append(meta, style=DIM)
    for label, val in fields or []:
        if not val:
            continue
        body.append("\n")
        body.append(f"{label}: ", style=DIM)
        body.append(val)
    if prose:
        body.append("\n\n")
        body.append(prose)
    return DetailView(title, body)


async def detail_view(read, section_key: str, item_id: str) -> DetailView:
    if section_key == "chapters":
        ch = await read.get_chapter(item_id)
        if not ch:
            return _EMPTY_VIEW
        meta = f"{_enum_val(ch.editorial_status)} · {word_count(ch.prose):,} words"
        return _view(ch.title, meta, ch.prose)
    if section_key == "characters":
        c = await read.get_character(item_id)
        if not c:
            return _EMPTY_VIEW
        fields = [("Traits", c.traits), ("Arc", c.arc_status), ("Motivations", c.motivations)]
        if c.voice:
            fields.append(("Voice", c.voice))
        return _view(c.name, "", c.backstory, fields)
    if section_key == "world":
        for e in await read.list_world_entries():
            if e.id == item_id:
                return _view(e.title, _enum_val(e.domain), e.body)
        return _EMPTY_VIEW
    if section_key == "retcons":
        for r in await read.list_retcon_requests():
            if r.id == item_id:
                return _view(r.description, f"status: {_enum_val(r.status)}", "",
                             [("Proposed", r.proposed_resolution)])
        return _EMPTY_VIEW
    if section_key == "threads":
        t = await read.get_thread(item_id)
        if not t:
            return _EMPTY_VIEW
        chapters = await read.list_chapters()
        known = chapter_number(t.last_chapter_id, chapters) is not None
        last = chapter_label(t.last_chapter_id, chapters) if known else "—"
        meta = f"{t.state.value} · touched {t.touch_count}x · last touch: {last}"
        return _view(t.name, meta, t.last_note)
    if section_key == "themes":
        theme = await read.get_theme(item_id)
        if not theme:
            return _EMPTY_VIEW
        return _view(theme.title, f"touched {theme.touch_count}x", theme.last_note)
    return _EMPTY_VIEW
```

In `novelizer/tui/widgets/browser.py`, change the signature and forward the keyword:

```python
    async def refresh_sections(self, read, *, staleness_threshold: int) -> None:
        sections = await browser_sections(read, staleness_threshold=staleness_threshold)
```

(the rest of the method body is unchanged).

In `novelizer/tui/app.py`:

1. Replace the import `from novelizer.tui.widgets.browser_model import detail_text` with `from novelizer.tui.widgets.browser_model import detail_view`.
2. `_browser_loop` reads the settings threshold every cycle:

```python
    async def _browser_loop(self) -> None:
        while True:
            try:
                await self.query_one("#browser", StoryBrowser).refresh_sections(
                    self.runtime.read,
                    staleness_threshold=self.runtime.settings.staleness_threshold_chapters,
                )
            except Exception as e:
                self._report_worker_error("browser", e)
            await asyncio.sleep(1.0)
```

3. Replace `on_tree_node_selected` and `_update_detail`:

```python
    async def on_tree_node_selected(self, event) -> None:
        data = event.node.data
        if not data or not data.get("id"):
            return
        view = await detail_view(self.runtime.read, data["section"], data["id"])
        if view.title:
            self._update_detail(view.body, view.title)
        else:
            self._update_detail("(no detail)")

    def _update_detail(self, content, title: str = "") -> None:
        self.query_one("#detail", Static).update(content)
        # The pane self-labels: border title is the selected item's
        # UPPERCASED title, reset to DETAIL when nothing is selected.
        scroll = self.query_one("#detail_scroll", VerticalScroll)
        scroll.border_title = title.upper() if title else "DETAIL"
        # New selection: start reading at the top, not wherever the previous
        # entry was scrolled to.
        scroll.scroll_home(animate=False)
```

In `novelizer/tui/app.tcss`, change the `#detail` rule to add the readable measure:

```
#detail { height: auto; max-width: 80; }
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/tui/test_browser_model.py tests/tui/test_browser_widget.py tests/tui/test_app_layout.py tests/tui/test_detail_pane_scroll.py tests/tui/test_reading_mode.py -v
```
Expected: all pass — test_browser_model 13, test_browser_widget 2, test_app_layout 7 (6 from Tasks 2–3 + the new title test), and the untouched detail-scroll and reading-mode suites confirm `_update_detail`'s str-compatible signature and that reading mode inherits the typography without changes.

- [ ] **Step 5: Commit**

```
git add novelizer/tui/widgets/browser_model.py novelizer/tui/widgets/browser.py \
        novelizer/tui/app.py novelizer/tui/app.tcss \
        tests/tui/test_browser_model.py tests/tui/test_browser_widget.py tests/tui/test_app_layout.py
git commit -m "feat: browser state cues (status dots, retcon alarm, Threads section) + detail typography with dynamic pane title"
```

---

### Task 6: Full-suite verification, zero warnings

**Files:**
- Modify: only whatever the suite run reveals (expected: nothing — Tasks 1–5 migrated every known cross-reference; the inventory table above is the checklist).

**Interfaces:**
- Consumes: everything above. Produces: a green suite.

- [ ] **Step 1: Run the full suite**

```
uv run pytest -q
```
Expected: all tests pass, `0 warnings` in the summary line. (Live-LLM tests skip themselves without the env; that's normal.)

- [ ] **Step 2: If anything fails, fix it** — expected fallout classes: a stray assertion on the old statusbar strings, `#proposals`, `proposal_line`, `detail_text`, or `_status_line` somewhere the inventory missed. Fix by migrating the assertion to the new surface (`roster_glyphs`/`dial_meter`/`banner_line`/`detail_view`), never by resurrecting a deleted helper and never by weakening the assertion to bare truthiness. If a *warning* appears, fix its source (e.g. an unawaited coroutine, an un-dismissed screen, or an un-closed pilot), not the filter config.

- [ ] **Step 3: Re-run to verify green + zero warnings**

```
uv run pytest -q
```
Expected: `... passed` with no warnings summary block.

- [ ] **Step 4: Sanity-run the TUI subset alone**

```
uv run pytest tests/tui -q
```
Expected: green.

- [ ] **Step 5: Commit** (only if Step 2 changed anything)

```
git add -A tests/
git commit -m "test: migrate remaining assertions to the command-and-control surfaces"
```

---

## Self-review notes (spec coverage)

- Spec Phase 3 item ↔ task map: roster glyph strip with state marks → Task 1; autonomy dial meter + cheatsheet-out-of-statusbar + rotating placeholder hint → Task 2; proposals banner (resident pane removed, loud only when pending) → Task 3; approval modal drill-in (list + full context + approve/reject on the selected row, "no more typing `:approve 025bae36`") → Task 4; browser state cues (status dots, retcons ⚠, threads stale count) + detail typography + self-labeling pane → Task 5; suite green, zero warnings → Task 6.
- Seam discipline checked: the only mutation path from any new TUI code is `commands.dispatch` via `_run_command` (Task 4's `_decide`); `ProposalService`, `Committer`, and `runtime.proposals` appear nowhere in `novelizer/tui/`. Feed writes go through the existing `» {result}` path, so `app.messages` keeps receiving plain strings unchanged.
- Names-not-ids checked per surface: banner (count only), modal rows (`proposal_row` asserts `id[:8]` absent), modal context (skips `id`/`*_id`/`*_ids`), thread rows/labels (name + state), thread detail (`—` fallback instead of `chapter_label`'s raw-id fallback), chapter rows (dot + title). The dispatch *result* line (`Approved proposal {id} …`) does contain the id — it is command output in the feed, the same line typing the command produces, and is out of scope for the "no ids on the dashboard" rule.
- Single-sourcing checked: staleness only via `is_thread_stale` + `settings.staleness_threshold_chapters` (keyword-only, no default, read every `_browser_loop` cycle — mirroring `_brain_loop`); chapter titles only via the existing `chapter_label`/`chapter_number`; alarm color only via the imported `ALARM_STYLE`; agent glyph/color only via `identity_for`. The dial reads the real `AutonomyLevel` ladder; no level list is re-typed outside `DIAL_LEVELS`/`DIAL_STYLES` (which are keyed by the enum, so a new level fails loudly at the dict lookup).
- Determinism checked: `hint_index` defaults to 0 (all existing `NovelizerApp(rt)` constructions untouched and deterministic); randomness lives only in `cli._launch_tui`; the spinner mark is a static char.
- Modal correctness checked: guard `self.screen is not self.default_screen` covers self-stacking and SettingsScreen; `a` with an empty queue is a no-op; post-decision `projector.catch_up()` prevents the stale-open double-commit; `App.query_one`/`_run_command` target the default screen (verified against Textual 5.3's `App._get_dom_base`), so feed writes work while the modal is up.
- EngineRoom/ActivityStrip/telemetry untouched except the tcss member swap (`#proposals` → `#proposals_banner`) and the vestigial `#roster` member removal; `#body.room`/`#body.reading` rules untouched; Footer mechanism untouched (it gains `a` from BINDINGS automatically); reading mode inherits detail typography by sharing the widget.
