# M1.2 · Mission Control — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Turn the single-feed M0/M1.1 TUI into the Mission Control dashboard the director chose (layout A): a persistent multi-pane view — activity feed + story browser + agent roster strip + status bar — with a command line for steering and a full-screen "Room" feed toggle. All read live from canon.

**Architecture:** A shared **command layer** (`novelizer/director/commands.py`) holds the director actions (seed, focus, pause, resume) so the CLI and TUI call the same code (DRY; the Direction bounded context's write vocabulary in one place). The TUI composes small, independently-testable widgets: `AgentRoster` (renders scheduler/agent status), `StoryBrowser` (a Tree over chapters/characters/world/retcons), and a detail pane. The `Scheduler` gains a lightweight `status()` read so the roster can show who's running/paused. Pure data→text helpers are unit-tested; widget wiring gets Textual `pilot` smoke tests.

**Tech Stack:** Python 3.13, `textual` (Tree, RichLog, Input, containers, bindings, `run_test` pilot), `click`+`rich`, `pytest`+`pytest-asyncio`.

**Context — current state after M1.1 (on `master`):**
- `novelizer/tui/app.py` — `NovelizerApp(runtime)` with `compose()` yielding `Header`, one `RichLog(id="feed")`, `Footer`; `on_mount` starts `_projector_loop`/`_scheduler_loop`/`_feed_loop` (each guarded via `_report_worker_error`); `messages: list[str]` buffer; pure `format_event(ev)`.
- `novelizer/director/cli.py` — `cli` group (bare launches TUI via `_launch_tui`), `_with_runtime(settings, fn)` helper, commands `seed`/`chapters`/`read`/`retcons`. `seed` appends `DIRECTOR_SIGNAL_CREATED`.
- `novelizer/scheduler.py` — `Scheduler(agents, read_store, tick_sleep=1.0, clock=time.monotonic)`, `tick()` returns the ran agent's name (or None), `pause_agent(name)`/`resume_agent(name)`, `run()`/`stop()`. Agents have `.name`, `.paused`, `.interval`, `ready_for_interval(now)`, `mark_ran(now)`, `readiness()`.
- `novelizer/runtime.py` — `Runtime` exposes `.events/.projector/.read/.committer/.agents/.scheduler` + named agent attrs after `start()`.
- `novelizer/canon/read_store.py` — `list_chapters(status)/get_chapter/list_world_entries(domain)/list_characters/get_character/list_retcon_requests(status)/list_unconsumed_signals(target_agent)`.
- `novelizer/store/models.py` — `Chapter`, `Character`, `WorldEntry`, `RetconRequest`, `DirectorSignal`, `SignalKind`, enums.

## Global Constraints

- **Python** `>=3.13`.
- **Event sourcing:** the TUI/CLI change canon ONLY by appending events (director actions go through the shared command layer, which appends `director_signal.*`); reads come only from `ReadStore`. No projection writes, no direct SQL in the Direction layer.
- **DRY command layer:** seed/focus/pause/resume logic lives once in `novelizer/director/commands.py`; both CLI and TUI call it. No duplicated append/pause logic.
- **Widgets are decomposed and testable:** each widget has a pure data→renderable helper tested without a running app; the app wiring gets `pilot` smoke tests. No business logic embedded in `compose()`.
- **The TUI is a reader of canon:** panes render from `ReadStore`/event tail; they never mutate state except via the command layer.
- **TDD, black-box first:** failing test → fail → implement → pass → commit. Don't weaken pilot assertions to nothing.
- **`asyncio_mode = "auto"`**. Ollama tests stay deselected by default.
- **Autonomy is NOT built here** — the status bar shows a static `full-auto` placeholder; the real dial/approval queue is M1.3. Do not add gating.

---

### Task 1: Shared director command layer

**Files:**
- Create: `novelizer/director/commands.py`
- Modify: `novelizer/director/cli.py` (route `seed` through it)
- Test: `tests/director/test_commands.py`

**Interfaces:**
- Produces (all async, operating on a started/− `Runtime`-like object exposing `.events` and, for pause/resume, `.scheduler`):
  - `async def seed(events, text: str) -> None` — appends `DIRECTOR_SIGNAL_CREATED` (kind=seed).
  - `async def focus(events, entity: str) -> None` — appends `DIRECTOR_SIGNAL_CREATED` (kind=focus, body=entity).
  - `def pause(scheduler, agent_name: str) -> None` / `def resume(scheduler, agent_name: str) -> None` — delegate to `scheduler.pause_agent/resume_agent`.
  - `async def dispatch(runtime, line: str) -> str` — parse a command line (`seed <text>` / `focus <text>` / `pause <agent>` / `resume <agent>`) and perform it; return a human-readable result string (or an error string for unknown commands). Used by the TUI command input.

- [ ] **Step 1: Write the failing test**

`tests/director/test_commands.py`:
```python
import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.events import EventType
from novelizer.store.models import SignalKind
from novelizer.director import commands


class FakeScheduler:
    def __init__(self): self.paused = set()
    def pause_agent(self, n): self.paused.add(n)
    def resume_agent(self, n): self.paused.discard(n)


class FakeRuntime:
    def __init__(self, events, scheduler): self.events = events; self.scheduler = scheduler


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


async def test_seed_appends_signal(stack):
    events, proj, read = stack
    await commands.seed(events, "a storm is coming")
    await proj.catch_up()
    sigs = await read.list_unconsumed_signals()
    assert len(sigs) == 1 and sigs[0].kind == SignalKind.seed and "storm" in sigs[0].body


async def test_focus_appends_focus_signal(stack):
    events, proj, read = stack
    await commands.focus(events, "Mira")
    await proj.catch_up()
    sigs = await read.list_unconsumed_signals()
    assert sigs[0].kind == SignalKind.focus and sigs[0].body == "Mira"


async def test_dispatch_routes_and_reports(stack):
    events, proj, read = stack
    sched = FakeScheduler()
    rt = FakeRuntime(events, sched)
    assert "seed" in (await commands.dispatch(rt, "seed a storm")).lower()
    await proj.catch_up()
    assert len(await read.list_unconsumed_signals()) == 1
    await commands.dispatch(rt, "pause editor")
    assert "editor" in sched.paused
    await commands.dispatch(rt, "resume editor")
    assert "editor" not in sched.paused
    assert "unknown" in (await commands.dispatch(rt, "frobnicate x")).lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/director/test_commands.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

Create `novelizer/director/commands.py`:
```python
from __future__ import annotations
from novelizer.canon.events import EventType
from novelizer.store.models import DirectorSignal, SignalKind


async def seed(events, text: str) -> None:
    sig = DirectorSignal(kind=SignalKind.seed, body=text)
    await events.append(EventType.DIRECTOR_SIGNAL_CREATED, sig.id, sig)


async def focus(events, entity: str) -> None:
    sig = DirectorSignal(kind=SignalKind.focus, body=entity)
    await events.append(EventType.DIRECTOR_SIGNAL_CREATED, sig.id, sig)


def pause(scheduler, agent_name: str) -> None:
    scheduler.pause_agent(agent_name)


def resume(scheduler, agent_name: str) -> None:
    scheduler.resume_agent(agent_name)


async def dispatch(runtime, line: str) -> str:
    parts = line.strip().split(maxsplit=1)
    if not parts:
        return "Empty command."
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""
    if cmd == "seed" and arg:
        await seed(runtime.events, arg)
        return f"Seed injected: {arg}"
    if cmd == "focus" and arg:
        await focus(runtime.events, arg)
        return f"Focus set: {arg}"
    if cmd == "pause" and arg:
        pause(runtime.scheduler, arg)
        return f"Paused: {arg}"
    if cmd == "resume" and arg:
        resume(runtime.scheduler, arg)
        return f"Resumed: {arg}"
    return f"Unknown command: {line.strip()}"
```

Update `novelizer/director/cli.py` `seed` command to route through the layer (replace its body's append with `await commands.seed(rt.events, text)`), adding `from novelizer.director import commands` at the top. Keep the console output line.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/director/test_commands.py tests/director/test_cli.py -v`
Expected: PASS. Then `uv run pytest -q` green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/director/commands.py novelizer/director/cli.py tests/director/test_commands.py
git commit -m "feat: extract shared director command layer (seed/focus/pause/resume/dispatch)"
```

---

### Task 2: Scheduler status read

**Files:**
- Modify: `novelizer/scheduler.py` (track last-ran agent; add `status()`)
- Test: `tests/test_scheduler.py` (add a status test)

**Interfaces:**
- Produces: `Scheduler.status() -> list[dict]` — one dict per agent `{"name": str, "paused": bool, "running": bool}` where `running` marks the most recently run agent. `Scheduler` tracks `self._last_ran: str | None`, set in `_run`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_scheduler.py`:
```python
async def test_status_reports_paused_and_last_ran():
    a = StubAgent("a", 0.2); b = StubAgent("b", 0.9)
    sched = Scheduler([a, b], StubRead(), clock=lambda: 1000.0)
    before = {s["name"]: s for s in sched.status()}
    assert before["a"]["running"] is False and before["b"]["running"] is False
    await sched.tick()  # runs b
    sched.pause_agent("a")
    st = {s["name"]: s for s in sched.status()}
    assert st["b"]["running"] is True
    assert st["a"]["paused"] is True and st["b"]["paused"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_scheduler.py::test_status_reports_paused_and_last_ran -v`
Expected: FAIL (`AttributeError: status`).

- [ ] **Step 3: Implement**

In `novelizer/scheduler.py`: add `self._last_ran: Optional[str] = None` in `__init__`; in `_run`, set `self._last_ran = agent.name` after `agent.mark_ran(now)`; add:
```python
    def status(self) -> list:
        return [
            {"name": a.name, "paused": a.paused, "running": a.name == self._last_ran}
            for a in self._agents
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_scheduler.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add novelizer/scheduler.py tests/test_scheduler.py
git commit -m "feat: add Scheduler.status() for roster display"
```

---

### Task 3: Agent roster widget

**Files:**
- Create: `novelizer/tui/__init__.py` already exists; create `novelizer/tui/widgets/__init__.py` (empty), `novelizer/tui/widgets/roster.py`
- Test: `tests/tui/test_roster.py`

**Interfaces:**
- Produces:
  - `def roster_line(status_row: dict) -> str` — pure: renders one agent status dict (`{name, paused, running}`) to a compact line, e.g. `"● author  running"`, `"● editor  paused"`, `"· retconner  idle"` (running → filled marker + "running"; paused → "paused"; else "idle").
  - `class AgentRoster(Static)` — a Textual `Static` widget with `update_from(status: list[dict])` that renders all rows (joined) into itself.

- [ ] **Step 1: Write the failing test**

`tests/tui/test_roster.py`:
```python
from novelizer.tui.widgets.roster import roster_line


def test_running_agent_marked():
    line = roster_line({"name": "author", "paused": False, "running": True})
    assert "author" in line and "running" in line


def test_paused_agent_marked():
    line = roster_line({"name": "editor", "paused": True, "running": False})
    assert "editor" in line and "paused" in line


def test_idle_agent():
    line = roster_line({"name": "retconner", "paused": False, "running": False})
    assert "retconner" in line and "idle" in line
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tui/test_roster.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

Create `novelizer/tui/widgets/__init__.py` (empty) and `novelizer/tui/widgets/roster.py`:
```python
from __future__ import annotations
from textual.widgets import Static


def roster_line(status_row: dict) -> str:
    name = status_row["name"]
    if status_row.get("paused"):
        return f"· {name}  paused"
    if status_row.get("running"):
        return f"● {name}  running"
    return f"· {name}  idle"


class AgentRoster(Static):
    def update_from(self, status: list) -> None:
        self.update("\n".join(roster_line(s) for s in status) or "no agents")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/tui/test_roster.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/widgets/__init__.py novelizer/tui/widgets/roster.py tests/tui/test_roster.py
git commit -m "feat: add AgentRoster widget with pure roster_line renderer"
```

---

### Task 4: Story browser data model

**Files:**
- Create: `novelizer/tui/widgets/browser_model.py`
- Test: `tests/tui/test_browser_model.py`

**Interfaces:**
- Produces (pure, no Textual — the data that drives the browser Tree and detail pane):
  - `async def browser_sections(read) -> list[dict]` — returns four sections in order: `[{"key":"chapters","label":"Chapters (N)","items":[{"id","label"}...]}, {"key":"characters",...}, {"key":"world",...}, {"key":"retcons",...}]`. Chapter label = `"<title> [<status>]"`; character label = `"<name>"`; world label = `"[<domain>] <title>"`; retcon label = `"<description[:40]>"` (open retcons only).
  - `async def detail_text(read, section_key: str, item_id: str) -> str` — returns a readable multi-line detail for the selected item (chapter → title + prose; character → name/traits/arc/backstory; world → title + body; retcon → description + proposed_resolution). Returns `""` if not found.

- [ ] **Step 1: Write the failing test**

`tests/tui/test_browser_model.py`:
```python
import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.events import EventType
from novelizer.store.models import Chapter, Character, WorldEntry, RetconRequest
from novelizer.tui.widgets.browser_model import browser_sections, detail_text


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


async def test_sections_cover_all_categories(stack):
    events, proj, read = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="It began."))
    await events.append(EventType.CHARACTER_CREATED, "ch1", Character(id="ch1", name="Mira", traits="stoic"))
    await events.append(EventType.WORLD_ENTRY_CREATED, "w1", WorldEntry(id="w1", title="Brinemarsh", body="salt"))
    await events.append(EventType.RETCON_REQUEST_CREATED, "r1", RetconRequest(id="r1", description="scar mismatch", conflicting_entry_ids=[], proposed_resolution="left hand"))
    await proj.catch_up()
    secs = {s["key"]: s for s in await browser_sections(read)}
    assert [s for s in secs] == ["chapters", "characters", "world", "retcons"] or set(secs) == {"chapters","characters","world","retcons"}
    assert secs["chapters"]["items"][0]["label"].startswith("One")
    assert "Mira" in secs["characters"]["items"][0]["label"]
    assert "Brinemarsh" in secs["world"]["items"][0]["label"]
    assert "scar mismatch" in secs["retcons"]["items"][0]["label"]


async def test_detail_text_for_chapter_and_character(stack):
    events, proj, read = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="It began in salt."))
    await events.append(EventType.CHARACTER_CREATED, "ch1", Character(id="ch1", name="Mira", traits="stoic", arc_status="wary"))
    await proj.catch_up()
    assert "It began in salt." in await detail_text(read, "chapters", "c1")
    d = await detail_text(read, "characters", "ch1")
    assert "Mira" in d and "wary" in d
    assert await detail_text(read, "chapters", "nope") == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tui/test_browser_model.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

Create `novelizer/tui/widgets/browser_model.py`:
```python
from __future__ import annotations


async def browser_sections(read) -> list:
    chapters = await read.list_chapters()
    characters = await read.list_characters()
    world = await read.list_world_entries()
    retcons = await read.list_retcon_requests(status="open")
    return [
        {"key": "chapters", "label": f"Chapters ({len(chapters)})",
         "items": [{"id": c.id, "label": f"{c.title} [{c.editorial_status.value}]"} for c in chapters]},
        {"key": "characters", "label": f"Characters ({len(characters)})",
         "items": [{"id": c.id, "label": c.name} for c in characters]},
        {"key": "world", "label": f"World ({len(world)})",
         "items": [{"id": e.id, "label": f"[{e.domain.value if hasattr(e.domain,'value') else e.domain}] {e.title}"} for e in world]},
        {"key": "retcons", "label": f"Retcons ({len(retcons)})",
         "items": [{"id": r.id, "label": r.description[:40]} for r in retcons]},
    ]


async def detail_text(read, section_key: str, item_id: str) -> str:
    if section_key == "chapters":
        ch = await read.get_chapter(item_id)
        return f"{ch.title}\n\n{ch.prose}" if ch else ""
    if section_key == "characters":
        c = await read.get_character(item_id)
        if not c:
            return ""
        return f"{c.name}\nTraits: {c.traits}\nArc: {c.arc_status}\nMotivations: {c.motivations}\n\n{c.backstory}"
    if section_key == "world":
        for e in await read.list_world_entries():
            if e.id == item_id:
                return f"{e.title}\n\n{e.body}"
        return ""
    if section_key == "retcons":
        for r in await read.list_retcon_requests():
            if r.id == item_id:
                return f"{r.description}\n\nProposed: {r.proposed_resolution}\nStatus: {r.status.value if hasattr(r.status,'value') else r.status}"
        return ""
    return ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/tui/test_browser_model.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/widgets/browser_model.py tests/tui/test_browser_model.py
git commit -m "feat: add story-browser data model (sections + detail text)"
```

---

### Task 5: Story browser widget (Tree)

**Files:**
- Create: `novelizer/tui/widgets/browser.py`
- Test: `tests/tui/test_browser_widget.py`

**Interfaces:**
- Consumes: `browser_sections` (Task 4).
- Produces: `class StoryBrowser(Tree)` — a Textual `Tree[dict]`. `async def refresh_sections(self, read) -> None` rebuilds the tree: root children are the four sections; each section's children are its items, each leaf's `data` = `{"section": key, "id": item_id}`. Exposes the selected leaf's `data` via Textual's `NodeSelected` message (consumers read `event.node.data`).

- [ ] **Step 1: Write the failing test**

`tests/tui/test_browser_widget.py`:
```python
import os
import tempfile
import pytest
from textual.app import App, ComposeResult
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.events import EventType
from novelizer.store.models import Chapter
from novelizer.tui.widgets.browser import StoryBrowser


class _Host(App):
    def __init__(self, read): super().__init__(); self._read = read
    def compose(self) -> ComposeResult:
        yield StoryBrowser("Story", id="browser")
    async def on_mount(self):
        await self.query_one(StoryBrowser).refresh_sections(self._read)


@pytest.mark.asyncio
async def test_browser_lists_sections_and_items():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    try:
        await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
        await proj.catch_up()
        app = _Host(read)
        async with app.run_test():
            tree = app.query_one(StoryBrowser)
            labels = [str(n.label) for n in tree.root.children]
            assert any("Chapters" in l for l in labels)
            chapters_node = next(n for n in tree.root.children if "Chapters" in str(n.label))
            assert any("One" in str(c.label) for c in chapters_node.children)
    finally:
        await read.close(); await proj.close(); await events.close(); os.unlink(path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tui/test_browser_widget.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

Create `novelizer/tui/widgets/browser.py`:
```python
from __future__ import annotations
from textual.widgets import Tree
from novelizer.tui.widgets.browser_model import browser_sections


class StoryBrowser(Tree):
    async def refresh_sections(self, read) -> None:
        expanded = {str(n.label) for n in self.root.children if n.is_expanded}
        self.root.remove_children()
        self.root.expand()
        for sec in await browser_sections(read):
            node = self.root.add(sec["label"], data={"section": sec["key"], "id": None})
            for item in sec["items"]:
                node.add_leaf(item["label"], data={"section": sec["key"], "id": item["id"]})
            if sec["label"] in expanded:
                node.expand()
```
Note: if the running Textual version raises on `add_leaf`/`data=` kwarg or `remove_children`, adapt to the available Tree API (e.g. `add(..., allow_expand=False)`); keep the same node/leaf structure and `data` payload so the test's assertions hold.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/tui/test_browser_widget.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/widgets/browser.py tests/tui/test_browser_widget.py
git commit -m "feat: add StoryBrowser Tree widget"
```

---

### Task 6: Mission Control layout + panes wired to live data

**Files:**
- Modify: `novelizer/tui/app.py` (recompose into the multi-pane layout; add a browser-refresh loop, roster-refresh loop, detail pane)
- Create: `novelizer/tui/app.tcss` (layout CSS)
- Test: `tests/tui/test_app_layout.py`

**Interfaces:**
- Produces: `NovelizerApp` composed as Mission Control: `Header`; a horizontal body with a left column (`RichLog#feed` large + `AgentRoster#roster` strip beneath) and a right column (`StoryBrowser#browser` + `Static#detail`); a `Static#statusbar` (shows `AUTONOMY: full-auto` placeholder + hint) above `Footer`. `on_mount` additionally starts a `_roster_loop` (calls `scheduler.status()` → `roster.update_from`) and a `_browser_loop` (periodic `browser.refresh_sections(read)`). Selecting a browser leaf updates `#detail` via `detail_text`.

- [ ] **Step 1: Write the failing test**

`tests/tui/test_app_layout.py`:
```python
import os
import tempfile
import pytest
from novelizer.config import Settings
from novelizer.runtime import Runtime
from novelizer.tui.app import NovelizerApp
from novelizer.agents.schemas import WorldEntriesDraft, WorldEntryDraft, KeeperOutput, EditorVerdict, ContinuityOutput, RetconAmendments
from novelizer.agents.base import ChapterDraft


class _R:
    def __init__(self, out): self._out = out
    async def ainvoke(self, inputs): return {"structured_response": self._out}


def _runners():
    return {
        "world_architect": _R(WorldEntriesDraft(entries=[WorldEntryDraft(title="Brinemarsh", body="salt")])),
        "author": _R(ChapterDraft(title="Chapter One", prose="It began.")),
        "character_keeper": _R(KeeperOutput()),
        "editor": _R(EditorVerdict(verdict="approve", notes="ok")),
        "continuity_checker": _R(ContinuityOutput()),
        "retconner": _R(RetconAmendments()),
    }


@pytest.mark.asyncio
async def test_mission_control_panes_present_and_populate():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, author_interval=1, projector_interval=0.1, default_agent_interval=1, continuity_interval=1)
    rt = Runtime(settings, runners=_runners())
    await rt.start()
    app = NovelizerApp(rt)
    try:
        async with app.run_test() as pilot:
            from textual.widgets import RichLog, Tree, Static
            assert app.query_one("#feed", RichLog) is not None
            assert app.query_one("#browser", Tree) is not None
            assert app.query_one("#roster", Static) is not None
            assert app.query_one("#statusbar", Static) is not None
            await pilot.pause(0.8)
            # roster shows agent names; browser shows the authored chapter
            roster_text = str(app.query_one("#roster", Static).renderable)
            assert "author" in roster_text
            tree = app.query_one("#browser", Tree)
            all_labels = [str(n.label) for n in tree.root.children] + [str(c.label) for n in tree.root.children for c in n.children]
            assert any("Chapter One" in l for l in all_labels) or any("Chapters (1" in l for l in all_labels)
    finally:
        await rt.close(); os.unlink(path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tui/test_app_layout.py -v`
Expected: FAIL (panes missing).

- [ ] **Step 3: Implement**

Recompose `novelizer/tui/app.py`. Keep `format_event`, `messages`, `_report_worker_error`, and the projector/scheduler/feed loops. Change `compose()` and `on_mount`, add roster/browser/detail loops and a leaf-selection handler. Reference implementation:
```python
from __future__ import annotations
import asyncio
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, Footer, RichLog, Static, Tree
from novelizer.canon.events import StoredEvent, EventType
from novelizer.tui.widgets.roster import AgentRoster
from novelizer.tui.widgets.browser import StoryBrowser
from novelizer.tui.widgets.browser_model import detail_text

# ... keep _LABELS and format_event unchanged ...


class NovelizerApp(App):
    TITLE = "Novelizer — Mission Control"
    CSS_PATH = "app.tcss"

    def __init__(self, runtime) -> None:
        super().__init__()
        self.runtime = runtime
        self._last_seq = 0
        self.messages: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            with Vertical(id="left"):
                yield RichLog(highlight=False, markup=False, id="feed")
                yield AgentRoster(id="roster")
            with Vertical(id="right"):
                yield StoryBrowser("Story", id="browser")
                yield Static("Select an item to view details.", id="detail")
        yield Static("AUTONOMY: full-auto   ·   :seed <text> · :focus <x> · :pause <agent>", id="statusbar")
        yield Footer()

    async def on_mount(self) -> None:
        self.run_worker(self._projector_loop(), exclusive=False)
        self.run_worker(self._scheduler_loop(), exclusive=False)
        self.run_worker(self._feed_loop(), exclusive=False)
        self.run_worker(self._roster_loop(), exclusive=False)
        self.run_worker(self._browser_loop(), exclusive=False)

    # ... keep _report_worker_error, _projector_loop, _scheduler_loop, _feed_loop ...

    async def _roster_loop(self) -> None:
        while True:
            try:
                self.query_one("#roster", AgentRoster).update_from(self.runtime.scheduler.status())
            except Exception as e:
                self._report_worker_error("roster", e)
            await asyncio.sleep(0.5)

    async def _browser_loop(self) -> None:
        while True:
            try:
                await self.query_one("#browser", StoryBrowser).refresh_sections(self.runtime.read)
            except Exception as e:
                self._report_worker_error("browser", e)
            await asyncio.sleep(1.0)

    async def on_tree_node_selected(self, event) -> None:
        data = event.node.data
        if not data or not data.get("id"):
            return
        text = await detail_text(self.runtime.read, data["section"], data["id"])
        self.query_one("#detail", Static).update(text or "(no detail)")
```
Create `novelizer/tui/app.tcss`:
```css
#body { height: 1fr; }
#left { width: 3fr; }
#right { width: 2fr; }
#feed { height: 3fr; border: round $primary; }
#roster { height: 1fr; border: round $secondary; }
#browser { height: 2fr; border: round $primary; }
#detail { height: 1fr; border: round $secondary; padding: 0 1; }
#statusbar { height: 1; background: $panel; color: $text; }
```
If the Textual version rejects a CSS token, simplify that rule; keep the four panes + statusbar present with the given ids (the test only asserts ids + population, not exact styling).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/tui/test_app_layout.py -v`
Expected: PASS. Also run `tests/tui/test_app_smoke.py` and `test_app_resilience.py` — they may need selector updates if they queried the single old feed; keep their assertions but adjust queries to the new `#feed`. Then `uv run pytest -q` green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/app.py novelizer/tui/app.tcss tests/tui/test_app_layout.py tests/tui/test_app_smoke.py tests/tui/test_app_resilience.py
git commit -m "feat: recompose TUI into Mission Control multi-pane layout (feed + roster + browser + detail + statusbar)"
```

---

### Task 7: Command input + key bindings + Room toggle

**Files:**
- Modify: `novelizer/tui/app.py` (add a command `Input`, bindings, and a feed-only "Room" toggle)
- Test: `tests/tui/test_app_commands.py`

**Interfaces:**
- Produces: `NovelizerApp.BINDINGS` includes `:`/`ctrl+k` → focus the command input; `r` → toggle Room mode (hide `#right`, enlarge feed); `q` → quit. A hidden `Input#command` at the bottom; on submit, the line is passed to `commands.dispatch(runtime, line)` and the result written to the feed; the input clears and unfocuses. Room toggle adds/removes a `room` CSS class on `#body` that hides `#right`.

- [ ] **Step 1: Write the failing test**

`tests/tui/test_app_commands.py`:
```python
import os
import tempfile
import pytest
from novelizer.config import Settings
from novelizer.runtime import Runtime
from novelizer.tui.app import NovelizerApp
from novelizer.agents.schemas import WorldEntriesDraft, KeeperOutput, EditorVerdict, ContinuityOutput, RetconAmendments
from novelizer.agents.base import ChapterDraft


class _R:
    def __init__(self, out): self._out = out
    async def ainvoke(self, inputs): return {"structured_response": self._out}


def _runners():
    return {k: _R(v) for k, v in {
        "world_architect": WorldEntriesDraft(), "author": ChapterDraft(title="X", prose="y"),
        "character_keeper": KeeperOutput(), "editor": EditorVerdict(), "continuity_checker": ContinuityOutput(),
        "retconner": RetconAmendments(),
    }.items()}


@pytest.mark.asyncio
async def test_command_input_seeds_via_dispatch():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1)
    rt = Runtime(settings, runners=_runners())
    await rt.start()
    app = NovelizerApp(rt)
    try:
        async with app.run_test() as pilot:
            from textual.widgets import Input
            inp = app.query_one("#command", Input)
            inp.value = "seed a storm is coming"
            await inp.action_submit() if hasattr(inp, "action_submit") else app.set_focus(inp)
            # Fallback: call the handler directly for determinism
            await app._run_command("seed a storm is coming")
            await pilot.pause(0.3)
            sigs = await rt.read.list_unconsumed_signals()
            assert any("storm" in s.body for s in sigs)
    finally:
        await rt.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_room_toggle_hides_right_column():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1)
    rt = Runtime(settings, runners=_runners())
    await rt.start()
    app = NovelizerApp(rt)
    try:
        async with app.run_test() as pilot:
            app.action_toggle_room()
            await pilot.pause()
            assert app.query_one("#body").has_class("room")
            app.action_toggle_room()
            await pilot.pause()
            assert not app.query_one("#body").has_class("room")
    finally:
        await rt.close(); os.unlink(path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tui/test_app_commands.py -v`
Expected: FAIL (no `#command` / `_run_command` / `action_toggle_room`).

- [ ] **Step 3: Implement**

In `novelizer/tui/app.py`: import `Input` and `from novelizer.director import commands`. Add to the class:
```python
    BINDINGS = [
        ("colon", "focus_command", "Command"),
        ("r", "toggle_room", "Room"),
        ("q", "quit", "Quit"),
    ]
```
Add an `Input(id="command", placeholder="command… (seed/focus/pause/resume)")` to `compose()` just above the statusbar. Add methods:
```python
    def action_focus_command(self) -> None:
        self.set_focus(self.query_one("#command", Input))

    def action_toggle_room(self) -> None:
        self.query_one("#body").toggle_class("room")

    async def _run_command(self, line: str) -> None:
        result = await commands.dispatch(self.runtime, line)
        log = self.query_one("#feed", RichLog)
        log.write(f"» {result}")
        self.messages.append(f"» {result}")

    async def on_input_submitted(self, event) -> None:
        if event.input.id == "command":
            await self._run_command(event.value)
            event.input.value = ""
            self.set_focus(None)
```
In `app.tcss`, add: `#command { height: 1; }` and `#body.room #right { display: none; }`. (`colon` is Textual's key name for `:`; if the version differs, bind `ctrl+k` instead and note it — the test drives `_run_command`/`action_toggle_room` directly so bindings needn't be simulated.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/tui/test_app_commands.py -v`
Expected: PASS. Then `uv run pytest -q` green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/app.py novelizer/tui/app.tcss tests/tui/test_app_commands.py
git commit -m "feat: add TUI command input, key bindings, and Room (feed-only) toggle"
```

---

### Task 8: Feed labels for keeper-filed retcons + docs + full-suite green

**Files:**
- Modify: `novelizer/tui/app.py` (`format_event`: label chapters/characters created by any agent correctly; note retcon provenance)
- Modify: `docs/submilestones/M1-the-room-assembles.md` (mark M1.2 complete)
- Modify: `README.md` (Mission Control usage)
- Test: `tests/tui/test_app.py` (strengthen the label assertions the M1.1 review flagged)

**Interfaces:**
- Produces: `format_event` retcon line is provenance-neutral (retcons come from Continuity OR CharacterKeeper) — change its label from the hard-coded "Continuity" to "Retcon" (avoid mislabeling keeper-filed retcons, per the M1.1 review finding). Strengthen `tests/tui/test_app.py` retcon/status label tests to assert BOTH the detail text and the (now provenance-neutral) label.

- [ ] **Step 1: Write/strengthen the failing tests**

Replace the two weak label tests in `tests/tui/test_app.py` (add if absent):
```python
def test_format_retcon_created_labels_retcon():
    from novelizer.tui.app import format_event
    from novelizer.canon.events import StoredEvent, EventType
    ev = StoredEvent(sequence=1, id="e", event_type=EventType.RETCON_REQUEST_CREATED,
                     aggregate_id="r1", payload={"description": "scar mismatch"}, created_at="t")
    line = format_event(ev)
    assert "scar mismatch" in line and "Retcon" in line


def test_format_chapter_status_changed_labels_editor():
    from novelizer.tui.app import format_event
    from novelizer.canon.events import StoredEvent, EventType
    ev = StoredEvent(sequence=2, id="e", event_type=EventType.CHAPTER_STATUS_CHANGED,
                     aggregate_id="c1", payload={"title": "One", "editorial_status": "reviewed"}, created_at="t")
    line = format_event(ev)
    assert "One" in line and "Editor" in line
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tui/test_app.py -v`
Expected: FAIL on `"Retcon" in line` (currently labeled "Continuity").

- [ ] **Step 3: Implement**

In `novelizer/tui/app.py`, change `_LABELS[EventType.RETCON_REQUEST_CREATED]` from `"Continuity"` to `"Retcon"`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/tui/test_app.py -v`
Expected: PASS. Then `uv run pytest -q` — full suite green.

- [ ] **Step 5: Docs + mark complete + commit**

In `docs/submilestones/M1-the-room-assembles.md`, set the M1.2 row to `✅ complete`. In `README.md`, add a short "Mission Control" subsection: the panes (feed / roster / story browser + detail / status bar), the command line (`:` then `seed`/`focus`/`pause`/`resume`), and the `r` Room toggle.
```bash
git add novelizer/tui/app.py docs/submilestones/M1-the-room-assembles.md README.md tests/tui/test_app.py
git commit -m "feat: provenance-neutral retcon feed label; docs; mark M1.2 complete"
```

---

## Self-Review

**Spec coverage (against the M1.2 row + chosen layout A):**
- Activity feed → retained `#feed` RichLog (Task 6). ✓
- Story browser (chapters/characters/world/retcons) + detail → Tasks 4–6. ✓
- Agent roster strip → Tasks 2–3, 6. ✓
- Status bar → Task 6 (`#statusbar`, autonomy placeholder). ✓
- Command palette / steering → Tasks 1, 7 (shared command layer + input + bindings). ✓
- "Room" (layout B) drill-in available → Task 7 toggle. ✓
- Fixes the M1.1 retcon-mislabel finding → Task 8. ✓
- Deferred to M1.3 (NOT gaps): the real autonomy dial + approval queue; the status bar shows a static placeholder. ✓

**Placeholder scan:** none. Textual-version fragility (Tree API, CSS tokens, key names) is called out with concrete fallbacks in Tasks 5–7, and the tests drive handlers directly to stay deterministic. ✓

**Type/interface consistency:** `commands.dispatch(runtime, line)` used by CLI (Task 1) and TUI (Task 7). `Scheduler.status()` shape `{name,paused,running}` produced in Task 2, consumed by `roster_line` (Task 3) and `_roster_loop` (Task 6). `browser_sections`/`detail_text` (Task 4) consumed by `StoryBrowser` (Task 5) and the detail handler (Task 6). Widget ids (`#feed/#roster/#browser/#detail/#statusbar/#command`) consistent across Tasks 6–8. ✓

**DDD/SOLID:** the Direction context's write actions are centralized in `commands.py`; widgets depend only on `ReadStore`/`Scheduler` reads; no canon mutation outside the command layer. ✓
