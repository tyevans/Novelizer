from __future__ import annotations
import asyncio
from pathlib import Path
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Header, Footer, RichLog, Static, Tree, Input
from novelizer.canon.events import StoredEvent, EventType
from novelizer.canon.autonomy import AutonomyState
from novelizer.director import commands
from novelizer.settings import StoryDirectory, TOMLFileError, global_config_path, load_effective_settings
from novelizer.tui.widgets.roster import roster_summary
from novelizer.tui.widgets.browser import StoryBrowser
from novelizer.tui.widgets.browser_model import detail_text
from novelizer.tui.widgets.proposals_model import pending_lines
from novelizer.tui.widgets.thread_board import ThreadBoard
from novelizer.tui.widgets.story_shape import StoryShape
from novelizer.tui.widgets.who_knows_what import WhoKnowsWhat
from novelizer.tui.widgets.causeway import Causeway
from novelizer.tui.widgets.feed_model import render_event


def format_event(ev: StoredEvent) -> str:
    """Plain-text rendering of a feed line — the string surface app.messages
    and the existing tests assert on. Styling lives in render_event."""
    return render_event(ev).plain


def _status_line(state: AutonomyState) -> str:
    base = (
        f"AUTONOMY: {state.global_level.value}   ·   :seed <text> · :focus <x> · "
        f":pause <agent> · :autonomy <level> [agent] · :approve/:reject <id> · :settings"
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
        ("v", "toggle_reading", "Reading"),
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
                yield Static("no pending proposals", id="proposals")
                yield ThreadBoard("no threads yet", id="thread_board")
                yield StoryShape("no chapters scored yet", id="story_shape")
                yield WhoKnowsWhat("no secrets yet", id="who_knows_what")
                yield Causeway("no causal edges yet", id="causeway")
            with Vertical(id="right"):
                yield StoryBrowser("Story", id="browser")
                with VerticalScroll(id="detail_scroll"):
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
        self.run_worker(self._browser_loop(), exclusive=False)
        self.run_worker(self._proposals_loop(), exclusive=False)
        self.run_worker(self._statusbar_loop(), exclusive=False)
        self.run_worker(self._thread_board_loop(), exclusive=False)
        self.run_worker(self._story_shape_loop(), exclusive=False)
        self.run_worker(self._settings_watch_loop(), exclusive=False)
        self.run_worker(self._who_knows_what_loop(), exclusive=False)
        self.run_worker(self._causeway_loop(), exclusive=False)

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
                agents = roster_summary(self.runtime.scheduler.status())
                self.query_one("#statusbar", Static).update(f"{agents}   |   {_status_line(state)}")
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
            if result.get("errors"):
                line = f"⚙ settings error: {'; '.join(result['errors'])}"
                log.write(line)
                self.messages.append(line)

    async def _who_knows_what_loop(self) -> None:
        while True:
            try:
                await self.query_one("#who_knows_what", WhoKnowsWhat).refresh_from(self.runtime.read)
            except Exception as e:
                self._report_worker_error("who_knows_what", e)
            await asyncio.sleep(1.0)

    async def _causeway_loop(self) -> None:
        while True:
            try:
                await self.query_one("#causeway", Causeway).refresh_from(self.runtime.read)
            except Exception as e:
                self._report_worker_error("causeway", e)
            await asyncio.sleep(1.0)

    def action_focus_command(self) -> None:
        self.set_focus(self.query_one("#command", Input))

    def action_toggle_room(self) -> None:
        # Room and reading are mutually exclusive: room hides #right, reading
        # hides #left — both at once would blank the whole body.
        body = self.query_one("#body")
        body.remove_class("reading")
        body.toggle_class("room")

    def action_toggle_reading(self) -> None:
        body = self.query_one("#body")
        body.remove_class("room")
        body.toggle_class("reading")

    async def _run_command(self, line: str) -> None:
        cmd = line.strip().lstrip(":").split(maxsplit=1)
        if cmd and cmd[0].lower() == "settings":
            from novelizer.tui.settings_screen import SettingsScreen

            story_dir = StoryDirectory(root=Path(self.runtime.settings.db_path).parent)
            self.push_screen(SettingsScreen(story_dir, lambda: self.runtime.settings))
            return
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
        self._update_detail(text or "(no detail)")

    def _update_detail(self, text: str) -> None:
        self.query_one("#detail", Static).update(text)
        # New selection: start reading at the top, not wherever the previous
        # entry was scrolled to.
        self.query_one("#detail_scroll", VerticalScroll).scroll_home(animate=False)
