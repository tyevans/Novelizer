from __future__ import annotations
import asyncio
import time
from collections import deque
from pathlib import Path
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Header, Footer, RichLog, Static, Tree, Input
from novelizer.canon.events import StoredEvent, EventType
from novelizer.chat.personas import CHAT_PERSONAS, resolve_agent_name
from novelizer.director import commands
from novelizer.tui.chat_screen import ChatScreen
from novelizer.settings import StoryDirectory, TOMLFileError, global_config_path, load_effective_settings
from novelizer.tui.widgets.roster import command_hint, status_strip
from novelizer.tui.widgets.browser import StoryBrowser
from novelizer.tui.widgets.browser_model import detail_view
from novelizer.tui.widgets.proposals_model import banner_line
from novelizer.tui.approval_screen import ApprovalScreen
from novelizer.tui.widgets.brain_panel import BrainPanel
from novelizer.tui.widgets.feed_model import (
    render_event,
    chapter_rule,
    welcome_lines,
    worker_error_line,
)
from novelizer.tui.widgets.activity_strip import ActivityStrip
from novelizer.tui.widgets.engine_room import EngineRoom
from novelizer.tui.widgets.engine_room_model import (
    LiveRunState, apply_bus_item, seed_state, trace_line, trace_detail,
)


def format_event(ev: StoredEvent) -> str:
    """Plain-text rendering of a feed line — the string surface app.messages
    and the existing tests assert on. Styling lives in render_event."""
    return render_event(ev).plain


class NovelizerApp(App):
    TITLE = "Novelizer — Mission Control"
    CSS_PATH = "app.tcss"
    SETTINGS_POLL_INTERVAL: float = 1.0

    # Note: Textual 5.3.0 does not accept "colon" as a key name for BINDINGS,
    # so "ctrl+k" is used to focus the command input instead.
    BINDINGS = [
        ("ctrl+k", "focus_command", "Command"),
        ("a", "approvals", "Approve"),
        ("r", "toggle_room", "Room"),
        ("e", "toggle_engine", "Engine Room"),
        ("p", "toggle_prompt", "Prompt"),
        ("v", "toggle_reading", "Reading"),
        ("1", "brain_tab('tab_shape')", "Shape"),
        ("2", "brain_tab('tab_threads')", "Threads"),
        ("3", "brain_tab('tab_secrets')", "Secrets"),
        ("4", "brain_tab('tab_causeway')", "Cause"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, runtime, hint_index: int = 0) -> None:
        super().__init__()
        self.runtime = runtime
        self._hint_index = hint_index
        self._last_seq = 0
        self._chapter_count = 0
        self.messages: list[str] = []
        self._live_state = LiveRunState()
        self._trace_events: deque = deque(maxlen=200)

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            with Vertical(id="left"):
                feed = RichLog(highlight=False, markup=False, id="feed")
                feed.border_title = "THE ROOM"
                yield feed
                # hidden by default via the stylesheet; _proposals_loop toggles
                # the .open class so mode rules (#body.engine) can still win
                yield Static(id="proposals_banner")
                brain = BrainPanel(id="brain")
                brain.border_title = "STORY BRAIN"
                yield brain
                yield EngineRoom(id="engine_room")
            with Vertical(id="right"):
                browser = StoryBrowser("Story", id="browser")
                browser.border_title = "STORY"
                yield browser
                with VerticalScroll(id="detail_scroll") as detail_scroll:
                    detail_scroll.border_title = "DETAIL"
                    yield Static("Select an item to view details.", id="detail")
        yield Static("loading…", id="statusbar")
        yield ActivityStrip("idle", id="activity_strip")
        # compact=True drops Input's default tall border, which would consume
        # both edges of the single row #command gets and leave 0 content lines.
        yield Input(id="command", placeholder=command_hint(self._hint_index), compact=True)
        yield Footer()

    async def on_mount(self) -> None:
        self.run_worker(self._projector_loop(), exclusive=False)
        self.run_worker(self._scheduler_loop(), exclusive=False)
        self.run_worker(self._feed_loop(), exclusive=False)
        self.run_worker(self._browser_loop(), exclusive=False)
        self.run_worker(self._proposals_loop(), exclusive=False)
        self.run_worker(self._statusbar_loop(), exclusive=False)
        self.run_worker(self._brain_loop(), exclusive=False)
        self.run_worker(self._settings_watch_loop(), exclusive=False)
        self.run_worker(self._telemetry_bus_loop(), exclusive=False)
        self.run_worker(self._telemetry_refresh_loop(), exclusive=False)

    def _report_worker_error(self, worker_name: str, e: Exception) -> None:
        line = worker_error_line(worker_name, e)
        try:
            log = self.query_one("#feed", RichLog)
            log.write(line)
        except Exception:
            pass
        self.messages.append(line.plain)

    async def _projector_loop(self) -> None:
        while True:
            try:
                await self.runtime.projector.catch_up()
            except Exception as e:
                self._report_worker_error("projector", e)
            try:
                await self.runtime.index_catch_up()
            except Exception as e:
                self._report_worker_error("indexer", e)
            await asyncio.sleep(self.runtime.settings.projector_interval)

    async def _scheduler_loop(self) -> None:
        # Dispatched agents now run concurrently as background tasks, so a
        # crashing agent no longer raises synchronously out of tick() (that
        # would defeat the whole point of not awaiting dispatch) -- it's
        # recorded in Scheduler.status()'s last_error instead. Poll for newly
        # completed failing runs each cycle and surface them the same way a
        # direct tick() exception would have been reported before concurrency.
        # Dedup by run_count, not error text: repeated identical failures
        # (e.g. the same agent crashing the same way every cycle) must each
        # still be reported once per completed run.
        reported_run_count: dict[str, int] = {}
        while True:
            try:
                await self.runtime.scheduler.tick()
                for s in self.runtime.scheduler.status():
                    err = s["last_error"]
                    if err and reported_run_count.get(s["name"]) != s["run_count"]:
                        reported_run_count[s["name"]] = s["run_count"]
                        self._report_worker_error("scheduler", RuntimeError(f"{s['name']}: {err}"))
            except Exception as e:
                self._report_worker_error("scheduler", e)
            await asyncio.sleep(self.runtime.settings.projector_interval)

    async def _feed_loop(self) -> None:
        log = self.query_one("#feed", RichLog)
        try:
            if not await self.runtime.events.events_since(0):
                for line in welcome_lines():
                    log.write(line)
                    self.messages.append(line.plain)
        except Exception as e:
            self._report_worker_error("feed", e)
        while True:
            try:
                events = await self.runtime.events.events_since(self._last_seq)
                for ev in events:
                    self._last_seq = ev.sequence
                    if ev.event_type == EventType.CHAT_USER_MESSAGED:
                        continue
                    if ev.event_type == EventType.CHAPTER_CREATED:
                        self._chapter_count += 1
                        rule = chapter_rule(self._chapter_count, ev.payload.get("title", ""))
                        log.write(rule)
                        self.messages.append(rule.plain)
                    rendered = render_event(ev)
                    log.write(rendered)
                    self.messages.append(rendered.plain)
            except Exception as e:
                self._report_worker_error("feed", e)
            await asyncio.sleep(0.3)

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

    async def _proposals_loop(self) -> None:
        while True:
            try:
                open_count = len(await self.runtime.read.list_proposals(status="open"))
                banner = self.query_one("#proposals_banner", Static)
                if open_count:
                    banner.update(banner_line(open_count))
                banner.set_class(bool(open_count), "open")
            except Exception as e:
                self._report_worker_error("proposals", e)
            await asyncio.sleep(0.5)

    async def _statusbar_loop(self) -> None:
        while True:
            try:
                state = await self.runtime.read.get_autonomy_state()
                strip = status_strip(self.runtime.scheduler.status(), state)
                self.query_one("#statusbar", Static).update(strip)
            except Exception as e:
                self._report_worker_error("statusbar", e)
            await asyncio.sleep(0.5)

    async def _brain_loop(self) -> None:
        while True:
            try:
                await self.query_one("#brain", BrainPanel).refresh_from(
                    self.runtime.read,
                    threshold=self.runtime.settings.staleness_threshold_chapters,
                    delta=self.runtime.settings.sag_spike_delta,
                )
            except Exception as e:
                self._report_worker_error("brain", e)
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

    def _next_hint(self) -> str:
        try:
            rows = [r for r in self.runtime.scheduler.status() if not r["paused"]]
            if not rows:
                return ""
            soonest = min(rows, key=lambda r: r["next_ready_in"])
            return f"next: {soonest['name']} in {int(soonest['next_ready_in'])}s"
        except Exception:
            return ""

    def _refresh_strip(self) -> None:
        strip = self.query_one("#activity_strip", ActivityStrip)
        strip.render_state(self._live_state, time.monotonic(), self._next_hint())

    def _refresh_trace(self) -> None:
        rows = [(ev.id, trace_line(ev)) for ev in reversed(self._trace_events)]
        self.query_one("#engine_room", EngineRoom).set_trace_rows(rows)

    async def _telemetry_bus_loop(self) -> None:
        # Seed from the durable log first so a restart never shows a blank view.
        try:
            recent = await self.runtime.telemetry_store.events_tail(200)
            self._trace_events.extend(recent)
            self._live_state = seed_state(recent[-50:], time.monotonic())
            self._refresh_strip()
            self.query_one("#engine_room", EngineRoom).render_live(self._live_state)
            self._refresh_trace()
        except Exception as e:
            self._report_worker_error("telemetry-seed", e)
        q = self.runtime.telemetry_bus.subscribe()
        while True:
            try:
                item = await q.get()
                self._live_state = apply_bus_item(self._live_state, item, time.monotonic())
                if isinstance(item, StoredEvent):
                    self._trace_events.append(item)
                    self._refresh_trace()
                self._refresh_strip()
                self.query_one("#engine_room", EngineRoom).render_live(self._live_state)
            except Exception as e:
                self._report_worker_error("telemetry", e)

    async def _telemetry_refresh_loop(self) -> None:
        while True:
            try:
                self._refresh_strip()
                self.query_one("#engine_room", EngineRoom).render_live(self._live_state)
            except Exception as e:
                self._report_worker_error("telemetry-refresh", e)
            await asyncio.sleep(0.5)

    def action_focus_command(self) -> None:
        self.set_focus(self.query_one("#command", Input))

    async def action_approvals(self) -> None:
        # Guard: never stack the modal over itself or over another pushed
        # screen (e.g. SettingsScreen). App bindings still fire while a modal
        # is up for keys the modal doesn't consume, so this must be checked.
        if self.screen is not self.default_screen:
            return
        if not await self.runtime.read.list_proposals(status="open"):
            return
        self.push_screen(ApprovalScreen(self.runtime))

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

    def action_toggle_engine(self) -> None:
        self.query_one("#body").toggle_class("engine")

    def action_toggle_prompt(self) -> None:
        if self.query_one("#body").has_class("engine"):
            self.query_one("#engine_room", EngineRoom).toggle_prompt()

    def action_brain_tab(self, pane_id: str) -> None:
        self.query_one("#brain", BrainPanel).activate_tab(pane_id)

    async def _run_command(self, line: str) -> None:
        stripped = line.strip()
        if stripped.startswith("@"):
            token, _, text = stripped[1:].partition(" ")
            agent = resolve_agent_name(token)
            if agent is None:
                known = ", ".join(f"@{n}" for n in CHAT_PERSONAS)
                msg = f"» unknown agent @{token} — try: {known}"
                self.query_one("#feed", RichLog).write(msg)
                self.messages.append(msg)
                return
            await self._open_chat(agent, text.strip())
            return
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

    async def _open_chat(self, agent_name: str, text: str) -> None:
        if isinstance(self.screen, ChatScreen):
            await self.screen.set_current(agent_name)
        else:
            await self.push_screen(ChatScreen(self.runtime, agent_name))
        if text:
            await self.send_chat_message(agent_name, text)

    async def send_chat_message(self, agent_name: str, text: str) -> None:
        """Send a chat message and schedule reply generation (completed in the
        chat-routing change; ChatScreen calls this)."""
        message_id = await self.runtime.chat.send(agent_name, text)
        self.run_worker(self._chat_reply_worker(agent_name, message_id), exclusive=False)

    async def _chat_reply_worker(self, agent_name: str, replying_to: str) -> None:
        try:
            await self.runtime.chat.generate_reply(agent_name, replying_to)
        except Exception as e:
            line = f"⚠ {agent_name} reply failed: {e}"
            try:
                self.query_one("#feed", RichLog).write(line)
            except Exception:
                pass
            self.messages.append(line)
            if isinstance(self.screen, ChatScreen):
                self.screen.add_error(agent_name, line)

    async def on_input_submitted(self, event) -> None:
        if event.input.id == "command":
            await self._run_command(event.value)
            event.input.value = ""
            self.set_focus(None)

    async def on_tree_node_selected(self, event) -> None:
        data = event.node.data
        if not data or not data.get("id"):
            return
        view = await detail_view(self.runtime.read, data["section"], data["id"])
        if view is None:
            self._update_detail("(no detail)")
        else:
            self._update_detail(view.body, view.title)

    def _update_detail(self, content, title: str = "") -> None:
        self.query_one("#detail", Static).update(content)
        # The pane self-labels: border title is the selected item's
        # UPPERCASED title, reset to DETAIL when nothing is selected.
        scroll = self.query_one("#detail_scroll", VerticalScroll)
        scroll.border_title = title.upper() if title else "DETAIL"
        # New selection: start reading at the top, not wherever the previous
        # entry was scrolled to.
        scroll.scroll_home(animate=False)

    async def on_data_table_row_selected(self, event) -> None:
        if event.data_table.id != "er_trace":
            return
        key = event.row_key.value
        ev = next((e for e in self._trace_events if e.id == key), None)
        if ev is None:
            return
        run_id = ev.payload.get("run_id")
        produced = await self.runtime.events.events_for_run(run_id) if run_id else []
        self.query_one("#engine_room", EngineRoom).show_detail(trace_detail(ev, produced))
