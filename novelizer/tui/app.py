from __future__ import annotations
import asyncio
from pathlib import Path
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, Footer, RichLog, Static, Tree, Input
from novelizer.canon.events import StoredEvent, EventType
from novelizer.canon.autonomy import AutonomyState
from novelizer.director import commands
from novelizer.settings import StoryDirectory, TOMLFileError, global_config_path, load_effective_settings
from novelizer.tui.widgets.roster import AgentRoster
from novelizer.tui.widgets.browser import StoryBrowser
from novelizer.tui.widgets.browser_model import detail_text
from novelizer.tui.widgets.proposals_model import pending_lines
from novelizer.tui.widgets.thread_board import ThreadBoard
from novelizer.tui.widgets.story_shape import StoryShape

_LABELS = {
    EventType.CHAPTER_CREATED: "Author",
    EventType.WORLD_ENTRY_CREATED: "Architect",
    EventType.CHARACTER_CREATED: "Keeper",
    EventType.DIRECTOR_SIGNAL_CREATED: "Director",
    EventType.RETCON_REQUEST_CREATED: "Retcon",
    EventType.CHAPTER_STATUS_CHANGED: "Editor",
}

_AGENT_LABELS = {
    "author": "Author",
    "editor": "Editor",
    "world_architect": "Architect",
    "character_keeper": "Keeper",
    "continuity_checker": "Continuity",
    "retconner": "Retconner",
}


def _agent_label(agent_name: str) -> str:
    return _AGENT_LABELS.get(agent_name, agent_name.replace("_", " ").title())


def format_event(ev: StoredEvent) -> str:
    p = ev.payload
    if ev.event_type == EventType.AGENT_REMARKED:
        label = _agent_label(p.get("agent_name", "?"))
        note = p.get("note", "")
        return f'💬 {label}: "{note}"'
    who = _LABELS.get(ev.event_type, "System")
    if ev.event_type == EventType.CHAPTER_CREATED:
        detail = f"new chapter: {p.get('title', '')}"
    elif ev.event_type == EventType.WORLD_ENTRY_CREATED:
        detail = f"lore: {p.get('title', '')}"
    elif ev.event_type == EventType.DIRECTOR_SIGNAL_CREATED:
        detail = f"signal: {p.get('body', '')}"
    elif ev.event_type == EventType.RETCON_REQUEST_CREATED:
        detail = f"retcon: {p.get('description', '')}"
    elif ev.event_type == EventType.CHAPTER_STATUS_CHANGED:
        detail = f"chapter reviewed: {p.get('title', '')}"
    else:
        detail = ev.event_type
    return f"◆ {who} — {detail}"


def _status_line(state: AutonomyState) -> str:
    base = (
        f"AUTONOMY: {state.global_level.value}   ·   :seed <text> · :focus <x> · "
        f":pause <agent> · :autonomy <level> [agent] · :approve/:reject <id>"
    )
    if state.overrides:
        summary = ", ".join(f"{k}={v.value}" for k, v in state.overrides.items())
        base += f"  (overrides: {summary})"
    return base


class NovelizerApp(App):
    TITLE = "Novelizer — Mission Control"
    CSS_PATH = "app.tcss"
    SETTINGS_POLL_INTERVAL: float = 1.0

    # Note: Textual 5.3.0 does not accept "colon" as a key name for BINDINGS,
    # so "ctrl+k" is used to focus the command input instead.
    BINDINGS = [
        ("ctrl+k", "focus_command", "Command"),
        ("r", "toggle_room", "Room"),
        ("q", "quit", "Quit"),
    ]

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
                yield Static("no pending proposals", id="proposals")
                yield ThreadBoard("no threads yet", id="thread_board")
                yield StoryShape("no chapters scored yet", id="story_shape")
            with Vertical(id="right"):
                yield StoryBrowser("Story", id="browser")
                yield Static("Select an item to view details.", id="detail")
        yield Static("AUTONOMY: loading…", id="statusbar")
        # compact=True drops Input's default tall border, which would consume
        # both edges of the single row #command gets and leave 0 content lines.
        yield Input(id="command", placeholder="command… (seed/focus/pause/resume)", compact=True)
        yield Footer()

    async def on_mount(self) -> None:
        self.run_worker(self._projector_loop(), exclusive=False)
        self.run_worker(self._scheduler_loop(), exclusive=False)
        self.run_worker(self._feed_loop(), exclusive=False)
        self.run_worker(self._roster_loop(), exclusive=False)
        self.run_worker(self._browser_loop(), exclusive=False)
        self.run_worker(self._proposals_loop(), exclusive=False)
        self.run_worker(self._statusbar_loop(), exclusive=False)
        self.run_worker(self._thread_board_loop(), exclusive=False)
        self.run_worker(self._story_shape_loop(), exclusive=False)
        self.run_worker(self._settings_watch_loop(), exclusive=False)

    def _report_worker_error(self, worker_name: str, e: Exception) -> None:
        line = f"⚠ {worker_name} error: {e}"
        try:
            log = self.query_one("#feed", RichLog)
            log.write(line)
        except Exception:
            pass
        self.messages.append(line)

    async def _projector_loop(self) -> None:
        while True:
            try:
                await self.runtime.projector.catch_up()
            except Exception as e:
                self._report_worker_error("projector", e)
            await asyncio.sleep(self.runtime.settings.projector_interval)

    async def _scheduler_loop(self) -> None:
        while True:
            try:
                await self.runtime.scheduler.tick()
            except Exception as e:
                self._report_worker_error("scheduler", e)
            await asyncio.sleep(self.runtime.settings.projector_interval)

    async def _feed_loop(self) -> None:
        log = self.query_one("#feed", RichLog)
        while True:
            try:
                events = await self.runtime.events.events_since(self._last_seq)
                for ev in events:
                    rendered = format_event(ev)
                    log.write(rendered)
                    self.messages.append(rendered)
                    self._last_seq = ev.sequence
            except Exception as e:
                self._report_worker_error("feed", e)
            await asyncio.sleep(0.3)

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

    async def _proposals_loop(self) -> None:
        while True:
            try:
                lines = await pending_lines(self.runtime.read)
                self.query_one("#proposals", Static).update("\n".join(lines) or "no pending proposals")
            except Exception as e:
                self._report_worker_error("proposals", e)
            await asyncio.sleep(0.5)

    async def _statusbar_loop(self) -> None:
        while True:
            try:
                state = await self.runtime.read.get_autonomy_state()
                self.query_one("#statusbar", Static).update(_status_line(state))
            except Exception as e:
                self._report_worker_error("statusbar", e)
            await asyncio.sleep(0.5)

    async def _thread_board_loop(self) -> None:
        while True:
            try:
                await self.query_one("#thread_board", ThreadBoard).refresh_from(self.runtime.read)
            except Exception as e:
                self._report_worker_error("thread_board", e)
            await asyncio.sleep(1.0)

    async def _story_shape_loop(self) -> None:
        while True:
            try:
                await self.query_one("#story_shape", StoryShape).refresh_from(self.runtime.read)
            except Exception as e:
                self._report_worker_error("story_shape", e)
            await asyncio.sleep(1.0)

    async def _settings_watch_loop(self) -> None:
        story_dir = StoryDirectory(root=Path(self.runtime.settings.db_path).parent)
        watched = [story_dir.story_toml, global_config_path()]

        def snapshot() -> tuple:
            return tuple(p.stat().st_mtime if p.exists() else 0.0 for p in watched)

        last = snapshot()
        while True:
            await asyncio.sleep(self.SETTINGS_POLL_INTERVAL)
            current = snapshot()
            if current == last:
                continue
            last = current
            try:
                new_settings = load_effective_settings(story_dir=story_dir)
            except TOMLFileError as e:
                self._report_worker_error("settings", e)
                continue
            try:
                result = self.runtime.apply_settings(new_settings)
            except Exception as e:
                self._report_worker_error("settings", e)
                continue
            log = self.query_one("#feed", RichLog)
            if result["applied"]:
                line = f"⚙ settings applied: {', '.join(result['applied'])}"
                log.write(line)
                self.messages.append(line)
            if result["restart_required"]:
                line = f"⚙ restart required: {', '.join(result['restart_required'])}"
                log.write(line)
                self.messages.append(line)

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

    async def on_tree_node_selected(self, event) -> None:
        data = event.node.data
        if not data or not data.get("id"):
            return
        text = await detail_text(self.runtime.read, data["section"], data["id"])
        self.query_one("#detail", Static).update(text or "(no detail)")
