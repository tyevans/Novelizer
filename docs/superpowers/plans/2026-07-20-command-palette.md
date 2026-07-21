# Unified Command Palette Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Novelizer TUI's hardcoded colon-command dispatcher and disconnected keybindings with one declarative command registry, surfaced through Textual's native fuzzy `CommandPalette`.

**Architecture:** `novelizer/director/commands.py` gets a `Command` dataclass and a `COMMAND_REGISTRY` list; `dispatch()` becomes a registry lookup instead of an if/elif chain. `novelizer/tui/app.py` gets a parallel `APP_COMMANDS` list for zero-arg UI actions (panel toggles, brain tabs, approvals modal, settings screen, quit), with `action_*` methods becoming one-line wrappers over it. A new `NovelizerCommandProvider(textual.command.Provider)` merges both registries into palette hits. `Ctrl+K` opens the palette (via `COMMAND_PALETTE_BINDING`) instead of focusing an always-visible `Input`; selecting an args-taking command opens a scoped follow-up `Input` pre-filled with `"<name> "`.

**Tech Stack:** Python, Textual >=5.3.0 (`textual.command.Provider`/`Hit`/`DiscoveryHit`/`CommandPalette`), pytest + pytest-asyncio.

## Global Constraints

- Textual version floor: `textual>=5.3.0` (from `pyproject.toml`) — the `Provider`/`Hit`/`DiscoveryHit` API used below is from this version's `textual.command` module (verified against the installed wheel).
- Do not change the behavior/return text of any existing command (`seed`, `focus`, `pause`, `resume`, `autonomy`, `retarget`, `approve`, `reject`, `muse`) — existing tests in `tests/director/test_commands.py` and `tests/tui/test_app_commands.py` (except the placeholder/hint tests, explicitly retired in Task 4) must keep passing unmodified.
- Never run the test suite in the main checkout — this worktree (`novelizer/.claude/worktrees/command-palette-design`) is isolated for that reason; run all `pytest` commands from here.
- `novelizer/director/commands.py` stays UI-framework-agnostic (no `textual` imports) — it is used from both the TUI and (via `seed_story_dir`/`adopt_blueprint_story_dir`) the story picker. Only `novelizer/tui/app.py` may import `textual.command`.

---

### Task 1: Command registry in `novelizer/director/commands.py`

**Files:**
- Modify: `novelizer/director/commands.py` (add `Command` dataclass, `COMMAND_REGISTRY`, refactor `dispatch`)
- Test: `tests/director/test_commands.py` (existing tests must pass unmodified; add registry tests)

**Interfaces:**
- Produces: `Command` dataclass (`name: str`, `description: str`, `callback: Callable[[Any, str], Awaitable[str]]`, `takes_args: bool = True`); `COMMAND_REGISTRY: list[Command]`; `find_command(name: str) -> Command | None`.
- Consumes: nothing new — wraps existing `seed`, `focus`, `pause`, `resume`, `autonomy`, `retarget_blueprint`, `_dispatch_decision`, `muse_status_report` already in this file.

- [ ] **Step 1: Write the failing test for registry completeness and lookup**

Add to `tests/director/test_commands.py` (append at end of file):

```python
def test_registry_has_one_entry_per_dispatch_command():
    names = {c.name for c in commands.COMMAND_REGISTRY}
    assert names == {
        "seed", "focus", "pause", "resume", "autonomy",
        "retarget", "approve", "reject", "muse",
    }


def test_find_command_matches_by_name_and_returns_none_for_unknown():
    assert commands.find_command("seed") is not None
    assert commands.find_command("seed").description
    assert commands.find_command("frobnicate") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/ty/workspace/novelizer/.claude/worktrees/command-palette-design && python -m pytest tests/director/test_commands.py::test_registry_has_one_entry_per_dispatch_command -v`
Expected: FAIL with `AttributeError: module 'novelizer.director.commands' has no attribute 'COMMAND_REGISTRY'`

- [ ] **Step 3: Add the `Command` dataclass and registry-backed callbacks**

In `novelizer/director/commands.py`, add near the top (after the existing imports, before `logger = ...`):

```python
from dataclasses import dataclass
from typing import Awaitable, Callable


@dataclass(frozen=True)
class Command:
    """One entry point reachable from both the colon-bar and the command
    palette. `callback` takes (runtime, raw_args_string) and returns the
    result text shown in the feed."""

    name: str
    description: str
    callback: Callable[[object, str], Awaitable[str]]
    takes_args: bool = True
```

Then replace the body of `dispatch()` (lines 172-243) with registry-backed
callback functions plus a lookup-based `dispatch`. Each callback below is
the existing if-branch body, extracted verbatim and given a
`(runtime, args)` signature:

```python
async def _cmd_seed(runtime, args: str) -> str:
    if not args:
        return "Unknown command: seed"
    await seed(runtime.events, args)
    return f"Seed injected: {args}"


async def _cmd_focus(runtime, args: str) -> str:
    if not args:
        return "Unknown command: focus"
    await focus(runtime.events, args)
    return f"Focus set: {args}"


async def _cmd_pause(runtime, args: str) -> str:
    parts = args.split(maxsplit=1)
    if not parts:
        return "Unknown command: pause"
    pause(runtime.scheduler, parts[0])
    return f"Paused: {parts[0]}"


async def _cmd_resume(runtime, args: str) -> str:
    parts = args.split(maxsplit=1)
    if not parts:
        return "Unknown command: resume"
    resume(runtime.scheduler, parts[0])
    return f"Resumed: {parts[0]}"


async def _cmd_autonomy(runtime, args: str) -> str:
    parts = args.split(maxsplit=1)
    if not parts:
        return "Unknown command: autonomy"
    level_str = parts[0]
    agent = parts[1] if len(parts) > 1 else None
    try:
        level = AutonomyLevel(level_str)
    except ValueError:
        return f"Unknown autonomy level: {level_str}"
    current = await runtime.read.get_autonomy_state()
    if agent:
        overrides = dict(current.overrides)
        overrides[agent] = level
        next_state = AutonomyState(global_level=current.global_level, overrides=overrides)
        await autonomy(runtime.events, next_state)
        return f"Autonomy for {agent} set to {level.value}"
    next_state = AutonomyState(global_level=level, overrides=current.overrides)
    await autonomy(runtime.events, next_state)
    return f"Global autonomy set to {level.value}"


async def _cmd_retarget(runtime, args: str) -> str:
    parts = args.split(maxsplit=1)
    if not parts:
        return "Unknown command: retarget"
    try:
        n = int(parts[0])
    except ValueError:
        return f"Invalid chapter count: {parts[0]}"
    return await retarget_blueprint(runtime.events, runtime.read, n)


async def _cmd_approve(runtime, args: str) -> str:
    parts = args.split(maxsplit=1)
    if not parts:
        return "Unknown command: approve"
    return await _dispatch_decision(runtime, parts[0], "approve")


async def _cmd_reject(runtime, args: str) -> str:
    parts = args.split(maxsplit=1)
    if not parts:
        return "Unknown command: reject"
    return await _dispatch_decision(runtime, parts[0], "reject")


async def _cmd_muse(runtime, args: str) -> str:
    if args.strip().lower() == "reroll":
        active = await runtime.read.get_active_hand()
        if active is not None:
            await runtime.events.append(
                EventType.INSPIRATION_HAND_SUPERSEDED, active.id,
                InspirationHandSuperseded(hand_id=active.id),
            )
        # Deal without waiting for the projector: deal_fresh_hand doesn't
        # check for an active hand, and the projection sorts itself out
        # (the superseded event lands before the new drawn event).
        # Note: if the Author already holds the old hand mid-draft when a
        # reroll fires, that chapter's eventual consumption no-ops against
        # the (now superseded) hand and its uptake goes untracked. This is
        # an accepted, human-triggered edge case.
        hand = await runtime.muse.deal_fresh_hand()
        return f"Rerolled. New hand: {'; '.join(hand.names)}"
    return muse_status_report(
        await runtime.read.get_active_hand(),
        await runtime.read.list_hands(),
        await runtime.read.list_uptake(),
    )


COMMAND_REGISTRY: list[Command] = [
    Command("seed", "Inject a seed signal for the Author to pick up", _cmd_seed),
    Command("focus", "Set the current focus entity", _cmd_focus),
    Command("pause", "Pause an agent", _cmd_pause),
    Command("resume", "Resume a paused agent", _cmd_resume),
    Command("autonomy", "Set global or per-agent autonomy level", _cmd_autonomy),
    Command("retarget", "Retarget the active blueprint's chapter count", _cmd_retarget),
    Command("approve", "Approve a proposal by id", _cmd_approve),
    Command("reject", "Reject a proposal by id", _cmd_reject),
    Command("muse", "Show muse status, or 'muse reroll' for a fresh hand", _cmd_muse),
]


def find_command(name: str) -> Command | None:
    return next((c for c in COMMAND_REGISTRY if c.name == name), None)


async def dispatch(runtime, line: str) -> str:
    parts = line.strip().split(maxsplit=1)
    if not parts:
        return "Empty command."
    # The status bar advertises colon-prefixed commands (":seed", ":focus"),
    # so accept the prefix as well as the bare form.
    cmd = parts[0].lower().removeprefix(":")
    rest = parts[1] if len(parts) > 1 else ""
    command = find_command(cmd)
    if command is None:
        return f"Unknown command: {line.strip()}"
    return await command.callback(runtime, rest)
```

Delete the old `dispatch()` body (the if/elif chain) — it is fully replaced
by the code above. Leave `seed`, `focus`, `pause`, `resume`, `autonomy`,
`retarget_blueprint`, `plan_thread_resolution`, `plan_secret_reveal`,
`approve`, `reject`, `_dispatch_decision`, `seed_story_dir`,
`adopt_blueprint_story_dir` exactly as they are — only `dispatch()` itself
is replaced, and the new `_cmd_*` wrappers are added alongside the
functions they call.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/ty/workspace/novelizer/.claude/worktrees/command-palette-design && python -m pytest tests/director/test_commands.py -v`
Expected: PASS — all existing tests plus the two new ones.

- [ ] **Step 5: Commit**

```bash
cd /home/ty/workspace/novelizer/.claude/worktrees/command-palette-design
git add novelizer/director/commands.py tests/director/test_commands.py
git commit -m "refactor(director): replace commands.dispatch if/elif chain with a Command registry"
```

---

### Task 2: App-level zero-arg command registry in `novelizer/tui/app.py`

**Files:**
- Modify: `novelizer/tui/app.py`
- Test: `tests/tui/test_app_commands.py`

**Interfaces:**
- Consumes: `commands.COMMAND_REGISTRY`, `commands.Command` from Task 1.
- Produces: `AppCommand` dataclass (`name: str`, `description: str`, `callback: Callable[["NovelizerApp"], Awaitable[None] | None]`) and `APP_COMMANDS: list[AppCommand]` (module-level in `app.py`), consumed by Task 3's palette provider.

- [ ] **Step 1: Write the failing test**

Add to `tests/tui/test_app_commands.py` (append at end of file):

```python
@pytest.mark.asyncio
async def test_app_commands_cover_every_binding_action():
    from novelizer.tui.app import APP_COMMANDS, NovelizerApp

    covered = {c.name for c in APP_COMMANDS}
    # Every non-command, non-quit BINDINGS action must have a same-named
    # entry in APP_COMMANDS so the palette can reach it.
    expected = {
        "approvals", "toggle_room", "toggle_engine", "toggle_prompt",
        "toggle_reading", "quit", "settings",
        "brain_tab_shape", "brain_tab_threads", "brain_tab_secrets",
        "brain_tab_causeway", "brain_tab_outline", "brain_tab_arcs",
    }
    assert covered == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/ty/workspace/novelizer/.claude/worktrees/command-palette-design && python -m pytest tests/tui/test_app_commands.py::test_app_commands_cover_every_binding_action -v`
Expected: FAIL with `ImportError: cannot import name 'APP_COMMANDS'`

- [ ] **Step 3: Add `AppCommand`, `APP_COMMANDS`, and route action methods through it**

In `novelizer/tui/app.py`, add near the top, after the existing imports and
before `def format_event(...)`:

```python
from dataclasses import dataclass
from typing import Awaitable, Callable


@dataclass(frozen=True)
class AppCommand:
    """A zero-arg UI action reachable from both a keybinding and the
    command palette. `callback` takes the running NovelizerApp instance."""

    name: str
    description: str
    callback: Callable[["NovelizerApp"], "Awaitable[None] | None"]
```

Then, after the `NovelizerApp` class body's existing `action_*` methods
(i.e. after `action_brain_tab`, currently ending at line 355), change each
`action_*` method to delegate to a small module-level function. Replace
the block from `def action_approvals` (line 325) through `def
action_brain_tab` (lines 354-355) — leave `action_focus_command` (line
322) and the `("ctrl+k", "focus_command", "Command")` `BINDINGS` entry
untouched for now, Task 3 removes both when it wires the palette — with:

```python
    async def action_approvals(self) -> None:
        await _app_open_approvals(self)

    def action_toggle_room(self) -> None:
        _app_toggle_room(self)

    def action_toggle_reading(self) -> None:
        _app_toggle_reading(self)

    def action_toggle_engine(self) -> None:
        _app_toggle_engine(self)

    def action_toggle_prompt(self) -> None:
        _app_toggle_prompt(self)

    def action_brain_tab(self, pane_id: str) -> None:
        self.query_one("#brain", BrainPanel).activate_tab(pane_id)
```

Add the module-level action functions and `APP_COMMANDS` list at the
bottom of the file (after the `NovelizerApp` class):

```python
async def _app_open_approvals(app: NovelizerApp) -> None:
    # Guard: never stack the modal over itself or over another pushed
    # screen (e.g. SettingsScreen). App bindings still fire while a modal
    # is up for keys the modal doesn't consume, so this must be checked.
    if app.screen is not app.default_screen:
        return
    if not await app.runtime.read.list_proposals(status="open"):
        return
    app.push_screen(ApprovalScreen(app.runtime))


def _app_toggle_room(app: NovelizerApp) -> None:
    # Room and reading are mutually exclusive: room hides #right, reading
    # hides #left -- both at once would blank the whole body.
    body = app.query_one("#body")
    body.remove_class("reading")
    body.toggle_class("room")


def _app_toggle_reading(app: NovelizerApp) -> None:
    body = app.query_one("#body")
    body.remove_class("room")
    body.toggle_class("reading")


def _app_toggle_engine(app: NovelizerApp) -> None:
    app.query_one("#body").toggle_class("engine")


def _app_toggle_prompt(app: NovelizerApp) -> None:
    if app.query_one("#body").has_class("engine"):
        app.query_one("#engine_room", EngineRoom).toggle_prompt()


def _app_open_settings(app: NovelizerApp) -> None:
    from novelizer.tui.settings_screen import SettingsScreen

    story_dir = StoryDirectory(root=Path(app.runtime.settings.db_path).parent)
    app.push_screen(SettingsScreen(story_dir, lambda: app.runtime.settings))


def _app_quit(app: NovelizerApp) -> None:
    app.exit()


APP_COMMANDS: list[AppCommand] = [
    AppCommand("approvals", "Open the approvals screen", _app_open_approvals),
    AppCommand("toggle_room", "Toggle Room view", _app_toggle_room),
    AppCommand("toggle_engine", "Toggle Engine Room view", _app_toggle_engine),
    AppCommand("toggle_prompt", "Toggle the Engine Room prompt panel", _app_toggle_prompt),
    AppCommand("toggle_reading", "Toggle Reading view", _app_toggle_reading),
    AppCommand("settings", "Open settings", _app_open_settings),
    AppCommand("quit", "Quit Novelizer", _app_quit),
    AppCommand(
        "brain_tab_shape", "Story Brain: Shape tab",
        lambda app: app.query_one("#brain", BrainPanel).activate_tab("tab_shape"),
    ),
    AppCommand(
        "brain_tab_threads", "Story Brain: Threads tab",
        lambda app: app.query_one("#brain", BrainPanel).activate_tab("tab_threads"),
    ),
    AppCommand(
        "brain_tab_secrets", "Story Brain: Secrets tab",
        lambda app: app.query_one("#brain", BrainPanel).activate_tab("tab_secrets"),
    ),
    AppCommand(
        "brain_tab_causeway", "Story Brain: Cause tab",
        lambda app: app.query_one("#brain", BrainPanel).activate_tab("tab_causeway"),
    ),
    AppCommand(
        "brain_tab_outline", "Story Brain: Outline tab",
        lambda app: app.query_one("#brain", BrainPanel).activate_tab("tab_outline"),
    ),
    AppCommand(
        "brain_tab_arcs", "Story Brain: Arcs tab",
        lambda app: app.query_one("#brain", BrainPanel).activate_tab("tab_arcs"),
    ),
]
```

`action_open_settings` and the removal of `action_focus_command`/the
`ctrl+k` binding are finished in Task 3, since they're tied to palette
wiring — `_app_open_settings` and `AppCommand("settings", ...)` above are
enough for this task; nothing yet calls `_app_open_settings` from a
keybinding, which is correct (there was never a keybinding for settings,
only the `:settings` colon-command handled separately in `_run_command`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/ty/workspace/novelizer/.claude/worktrees/command-palette-design && python -m pytest tests/tui/test_app_commands.py -v`
Expected: PASS (existing tests unaffected; new coverage test passes).

- [ ] **Step 5: Commit**

```bash
cd /home/ty/workspace/novelizer/.claude/worktrees/command-palette-design
git add novelizer/tui/app.py tests/tui/test_app_commands.py
git commit -m "refactor(tui): introduce APP_COMMANDS registry for zero-arg UI actions"
```

---

### Task 3: `NovelizerCommandProvider` and palette wiring

**Files:**
- Modify: `novelizer/tui/app.py`
- Test: `tests/tui/test_app_commands.py`

**Interfaces:**
- Consumes: `commands.COMMAND_REGISTRY` (Task 1), `APP_COMMANDS` (Task 2).
- Produces: `NovelizerCommandProvider(textual.command.Provider)` class, registered in `NovelizerApp.COMMANDS`.

- [ ] **Step 1: Write the failing test**

Add to `tests/tui/test_app_commands.py`:

```python
@pytest.mark.asyncio
async def test_command_provider_discovers_every_registered_command():
    from novelizer.tui.app import APP_COMMANDS, NovelizerApp, NovelizerCommandProvider
    from novelizer.director import commands as director_commands

    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1)
    rt = Runtime(settings, runners=_runners())
    await rt.start()
    app = NovelizerApp(rt)
    try:
        async with app.run_test() as pilot:
            provider = NovelizerCommandProvider(app.screen)
            hits = [hit async for hit in provider.discover()]
            names = {hit.text for hit in hits}
            expected = {c.name for c in director_commands.COMMAND_REGISTRY} | {
                c.name for c in APP_COMMANDS
            }
            assert names == expected
    finally:
        await rt.close(); os.unlink(path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/ty/workspace/novelizer/.claude/worktrees/command-palette-design && python -m pytest tests/tui/test_app_commands.py::test_command_provider_discovers_every_registered_command -v`
Expected: FAIL with `ImportError: cannot import name 'NovelizerCommandProvider'`

- [ ] **Step 3: Implement the provider and wire it into the App**

Add this import near the top of `novelizer/tui/app.py`, alongside the
other `textual` imports:

```python
from textual.command import DiscoveryHit, Hit, Hits, Provider
```

Add the provider class after `APP_COMMANDS` (bottom of file, from Task 2):

```python
class NovelizerCommandProvider(Provider):
    """Fuzzy-searches director commands (which need typed arguments, so
    selecting one opens the follow-up Input) and zero-arg app commands
    (which run immediately)."""

    def _candidates(self) -> list[tuple[str, str, bool]]:
        # (name, description, takes_args)
        director_entries = [
            (c.name, c.description, True) for c in commands.COMMAND_REGISTRY
        ]
        app_entries = [(c.name, c.description, False) for c in APP_COMMANDS]
        return director_entries + app_entries

    def _run(self, name: str, takes_args: bool) -> None:
        app: NovelizerApp = self.app  # type: ignore[assignment]
        if takes_args:
            app.open_command_followup(name)
            return
        command = next(c for c in APP_COMMANDS if c.name == name)
        result = command.callback(app)
        if result is not None:
            app.call_next(lambda: app.run_worker(result, exclusive=False))

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for name, description, takes_args in self._candidates():
            text = f"{name} — {description}"
            score = matcher.match(text)
            if score > 0:
                yield Hit(
                    score,
                    matcher.highlight(text),
                    lambda n=name, a=takes_args: self._run(n, a),
                    text=text,
                    help=description,
                )

    async def discover(self) -> Hits:
        for name, description, takes_args in self._candidates():
            text = f"{name} — {description}"
            yield DiscoveryHit(
                text,
                lambda n=name, a=takes_args: self._run(n, a),
                text=name,
                help=description,
            )
```

No new import is needed for `commands.COMMAND_REGISTRY` — `app.py` already
has `from novelizer.director import commands` on line 11 (used to build
`_run_command`'s `commands.dispatch(...)` call), and `_candidates` above
reuses that same `commands` module reference.

Now wire the provider and the follow-up-input flow onto `NovelizerApp`.
Replace the `BINDINGS` list's `("ctrl+k", "focus_command", "Command")` line
with nothing (delete it) — Textual auto-binds `COMMAND_PALETTE_BINDING` for
you (see class-var addition below) as long as no existing binding's action
is `"command_palette"`. `BINDINGS` becomes:

```python
    BINDINGS = [
        ("a", "approvals", "Approve"),
        ("r", "toggle_room", "Room"),
        ("e", "toggle_engine", "Engine Room"),
        ("p", "toggle_prompt", "Prompt"),
        ("v", "toggle_reading", "Reading"),
        ("1", "brain_tab('tab_shape')", "Shape"),
        ("2", "brain_tab('tab_threads')", "Threads"),
        ("3", "brain_tab('tab_secrets')", "Secrets"),
        ("4", "brain_tab('tab_causeway')", "Cause"),
        ("5", "brain_tab('tab_outline')", "Outline"),
        ("6", "brain_tab('tab_arcs')", "Arcs"),
        ("q", "quit", "Quit"),
    ]
```

Add two class vars right below `BINDINGS`, and remove
`action_focus_command` and `action_command_palette_open` entirely (both
were Task 2's placeholder / the old bar-focus action):

```python
    COMMANDS = {NovelizerCommandProvider}
    COMMAND_PALETTE_BINDING = "ctrl+k"
```

Add `open_command_followup`, called by the provider for args-taking
commands, as a method on `NovelizerApp` (place it near `_run_command`):

```python
    def open_command_followup(self, name: str) -> None:
        box = self.query_one("#command_followup", Input)
        box.value = f"{name} "
        box.display = True
        self.set_focus(box)
        box.action_end()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/ty/workspace/novelizer/.claude/worktrees/command-palette-design && python -m pytest tests/tui/test_app_commands.py::test_command_provider_discovers_every_registered_command -v`
Expected: PASS

(This step will still fail until Task 4 adds the `#command_followup`
widget to `compose()` — if running this task in isolation, expect
`open_command_followup` to be untested until then; the `discover()` test
above does not exercise it. Proceed to Task 4 before running the full
suite.)

- [ ] **Step 5: Commit**

```bash
cd /home/ty/workspace/novelizer/.claude/worktrees/command-palette-design
git add novelizer/tui/app.py tests/tui/test_app_commands.py
git commit -m "feat(tui): add NovelizerCommandProvider and wire Ctrl+K to the command palette"
```

---

### Task 4: Remove the always-visible colon-bar; add scoped follow-up input

**Files:**
- Modify: `novelizer/tui/app.py`
- Modify: `novelizer/tui/widgets/roster.py` (remove `PLACEHOLDER_HINTS`, `command_hint`)
- Modify: `novelizer/director/cli.py` (remove `hint_index`/`PLACEHOLDER_HINTS` plumbing)
- Test: `tests/tui/test_app_commands.py`, `tests/tui/test_roster.py` (remove now-obsolete hint tests, add follow-up-input test)

**Interfaces:**
- Consumes: `open_command_followup` (Task 3), `commands.dispatch` (Task 1).
- Produces: `#command_followup` `Input` widget in `compose()`, replacing `#command`.

- [ ] **Step 1: Write the failing test**

Add to `tests/tui/test_app_commands.py`:

```python
@pytest.mark.asyncio
async def test_followup_input_prefills_and_dispatches_on_submit():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1)
    rt = Runtime(settings, runners=_runners())
    await rt.start()
    app = NovelizerApp(rt)
    try:
        async with app.run_test() as pilot:
            from textual.widgets import Input
            app.open_command_followup("seed")
            await pilot.pause()
            box = app.query_one("#command_followup", Input)
            assert box.value == "seed "
            assert box.display is True
            box.value = "seed a storm is coming"
            await pilot.press("enter")
            await pilot.pause(0.3)
            log = await rt.events.events_since(0)
            created = [
                e for e in log
                if e.event_type == EventType.DIRECTOR_SIGNAL_CREATED
                and "storm" in e.payload.get("body", "")
            ]
            assert created
            # Submitting hides the box again and clears it for next time.
            assert box.display is False
            assert box.value == ""
    finally:
        await rt.close(); os.unlink(path)
```

Remove the now-obsolete placeholder/hint tests from
`tests/tui/test_app_commands.py`: delete
`test_command_input_has_visible_content_row`,
`test_command_placeholder_is_hint_zero_by_default`, and
`test_command_placeholder_rotates_with_hint_index` (they assert on the
`#command` Input and `PLACEHOLDER_HINTS`, both removed by this task).
Update `test_command_input_seeds_via_dispatch` to call
`app._run_command("seed a storm is coming")` unchanged (that method still
exists and still works — it is the shared landing point for both the
old bar and the new follow-up input).

Remove the hint tests from `tests/tui/test_roster.py`: delete
`test_command_hint_is_deterministic_and_wraps` and its `PLACEHOLDER_HINTS`
/`command_hint` import at the top of that file.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/ty/workspace/novelizer/.claude/worktrees/command-palette-design && python -m pytest tests/tui/test_app_commands.py::test_followup_input_prefills_and_dispatches_on_submit -v`
Expected: FAIL with `textual.css.query.NoMatches` (no `#command_followup` widget yet).

- [ ] **Step 3: Replace the `#command` Input with a hidden `#command_followup` Input**

In `novelizer/tui/app.py`, `compose()`: replace

```python
        # compact=True drops Input's default tall border, which would consume
        # both edges of the single row #command gets and leave 0 content lines.
        yield Input(id="command", placeholder=command_hint(self._hint_index), compact=True)
```

with

```python
        # Hidden by default; open_command_followup() reveals it, pre-filled,
        # when an args-taking palette command is selected. compact=True
        # drops Input's default tall border so the single row it gets has
        # at least one visible content line.
        followup = Input(id="command_followup", compact=True)
        followup.display = False
        yield followup
```

Remove `command_hint` from the `from novelizer.tui.widgets.roster import
command_hint, status_strip` import — it becomes
`from novelizer.tui.widgets.roster import status_strip`.

Remove the `hint_index` parameter from `__init__`:

```python
    def __init__(self, runtime) -> None:
        super().__init__()
        self.runtime = runtime
        self._last_seq = 0
        self._chapter_count = 0
        self.messages: list[str] = []
        self._live_state = LiveRunState()
        self._trace_events: deque = deque(maxlen=200)
```

(removing the `self._hint_index = hint_index` line and the `hint_index:
int = 0` parameter).

Update `on_input_submitted` to key off `#command_followup` and hide the
box again on submit:

```python
    async def on_input_submitted(self, event) -> None:
        if event.input.id == "command_followup":
            await self._run_command(event.value)
            event.input.value = ""
            event.input.display = False
            self.set_focus(None)
```

In `novelizer/tui/widgets/roster.py`, delete `PLACEHOLDER_HINTS` and
`command_hint` (lines 108-120) and their leading comment.

In `novelizer/director/cli.py`, `_launch_tui`, remove the hint plumbing:

```python
def _launch_tui(settings: EffectiveSettings) -> None:
    from novelizer.tui.app import NovelizerApp

    async def _boot():
        rt = Runtime(settings)
        await rt.start()
        app = NovelizerApp(rt)
        try:
            await app.run_async()
        finally:
            await rt.close()

    asyncio.run(_boot())
```

(dropping the `import random` and `from novelizer.tui.widgets.roster
import PLACEHOLDER_HINTS` lines, and the `hint_index=...` argument).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/ty/workspace/novelizer/.claude/worktrees/command-palette-design && python -m pytest tests/tui/test_app_commands.py tests/tui/test_roster.py tests/director/test_commands.py -v`
Expected: PASS — all tests, including the new follow-up-input test and the earlier registry/provider tests from Tasks 1-3.

- [ ] **Step 5: Commit**

```bash
cd /home/ty/workspace/novelizer/.claude/worktrees/command-palette-design
git add novelizer/tui/app.py novelizer/tui/widgets/roster.py novelizer/director/cli.py tests/tui/test_app_commands.py tests/tui/test_roster.py
git commit -m "feat(tui): remove always-visible colon-bar in favor of scoped follow-up input"
```

---

### Task 5: Full-suite verification and stray-reference sweep

**Files:**
- Test: whole `tests/tui/` and `tests/director/` directories; grep sweep across the repo.

**Interfaces:**
- Consumes: everything from Tasks 1-4.
- Produces: nothing new — this task only verifies.

- [ ] **Step 1: Grep for any remaining references to removed names**

Run: `cd /home/ty/workspace/novelizer/.claude/worktrees/command-palette-design && grep -rn "PLACEHOLDER_HINTS\|command_hint\|hint_index\|action_focus_command\|id=\"command\"\|#command\b" --include="*.py" novelizer tests`
Expected: no output (or only matches inside `#command_followup`, which is
expected and fine — check each hit by eye since `#command\b` will also
match as a substring of `#command_followup` under some greps; if so,
confirm the only survivors are `#command_followup`).

- [ ] **Step 2: Run the full TUI and director test suites**

Run: `cd /home/ty/workspace/novelizer/.claude/worktrees/command-palette-design && python -m pytest tests/tui/ tests/director/ -v`
Expected: PASS, 0 failures.

- [ ] **Step 3: Manually smoke-check CLI boot still imports cleanly**

Run: `cd /home/ty/workspace/novelizer/.claude/worktrees/command-palette-design && python -c "from novelizer.director.cli import _launch_tui; from novelizer.tui.app import NovelizerApp, NovelizerCommandProvider, APP_COMMANDS; from novelizer.director.commands import COMMAND_REGISTRY; print('ok', len(APP_COMMANDS), len(COMMAND_REGISTRY))"`
Expected: `ok 7 9` (7 `APP_COMMANDS` entries, 9 `COMMAND_REGISTRY` entries) with no import errors.

- [ ] **Step 4: Commit if Step 1's grep required any cleanup**

If Step 1 turned up stray references needing a fix, fix them, re-run Steps
1-3, then:

```bash
cd /home/ty/workspace/novelizer/.claude/worktrees/command-palette-design
git add -A
git commit -m "chore(tui): sweep stray references to the removed colon-bar/hint system"
```

If Step 1 found nothing to clean up, skip the commit — Task 4's commit
already covers everything.
