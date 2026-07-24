from __future__ import annotations
import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable
from textual.app import App, ComposeResult
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Header, Footer, RichLog, Static, Tree, Input
from novelizer.canon.events import StoredEvent, EventType
from novelizer.chat.personas import CHAT_PERSONAS, resolve_agent_name
from novelizer.director import commands
from novelizer.telemetry.events import TelemetryEventType, ToolSummaryReady
from novelizer.tui.chat_screen import ChatScreen
from novelizer.tui.tool_summarizer import summarize_tool_call
from novelizer.tui.research_screen import ResearchScreen
from novelizer.settings import StoryDirectory, TOMLFileError, global_config_path, load_effective_settings
from novelizer.tui.widgets.roster import status_strip
from novelizer.tui.widgets.browser import StoryBrowser
from novelizer.tui.widgets.browser_model import detail_view
from novelizer.tui.widgets.proposals_model import banner_line
from novelizer.tui.approval_screen import ApprovalScreen
from novelizer.tui.export_screen import ExportScreen
from novelizer.tui.escalations_screen import EscalationsScreen
from novelizer.tui.widgets.brain_panel import BrainPanel
from novelizer.tui.widgets.feed_model import (
    render_event,
    chapter_rule,
    welcome_lines,
    worker_error_line,
)
from tui_kit.widgets.activity_strip import ActivityStrip
from tui_kit.widgets.engine_room import EngineRoom
from tui_kit.run_model import (
    LiveRunState, apply_bus_item, route_agent, seed_state, seed_states,
    normalize_input_summary,
)
from novelizer.tui.identity import AGENT_NAMES, NOVELIZER_AGENT_THEME
from novelizer.tui.telemetry_adapter import to_contract_event, trace_line, trace_detail

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AppCommand:
    """A zero-arg UI action reachable from both a keybinding and the
    command palette. `callback` takes the running NovelizerApp instance."""

    name: str
    description: str
    callback: Callable[["NovelizerApp"], "Awaitable[None] | None"]


def format_event(ev: StoredEvent) -> str:
    """Plain-text rendering of a feed line — the string surface app.messages
    and the existing tests assert on. Styling lives in render_event."""
    return render_event(ev).plain


class NovelizerApp(App):
    TITLE = "Novelizer — Mission Control"
    CSS_PATH = "app.tcss"
    SETTINGS_POLL_INTERVAL: float = 1.0

    BINDINGS = [
        ("a", "approvals", "Approve"),
        ("r", "toggle_room", "Room"),
        ("e", "toggle_engine", "Engine Room"),
        ("p", "toggle_prompt", "Prompt"),
        ("P", "pause_all", "Pause All"),
        ("v", "toggle_reading", "Reading"),
        ("1", "brain_tab('tab_shape')", "Shape"),
        ("2", "brain_tab('tab_threads')", "Threads"),
        ("3", "brain_tab('tab_secrets')", "Secrets"),
        ("4", "brain_tab('tab_causeway')", "Cause"),
        ("5", "brain_tab('tab_outline')", "Outline"),
        ("6", "brain_tab('tab_arcs')", "Arcs"),
        ("ctrl+r", "talk_to_project", "Talk to Project"),
        ("ctrl+e", "open_escalations", "Escalations"),
        ("q", "quit", "Quit"),
    ]
    # COMMANDS is set below, after NovelizerCommandProvider is defined at
    # module scope (it must exist before we can reference it here).
    COMMAND_PALETTE_BINDING = "ctrl+k"

    def __init__(self, runtime) -> None:
        super().__init__()
        self.runtime = runtime
        self._last_seq = 0
        self._chapter_count = 0
        self.messages: list[str] = []
        self._live_state = LiveRunState()
        self._agent_live_states: dict[str, LiveRunState] = {}
        self._trace_events: deque = deque(maxlen=200)
        self._paused_by_toggle: list[str] | None = None

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
                yield EngineRoom(agent_names=list(AGENT_NAMES), theme=NOVELIZER_AGENT_THEME,
                                 id="engine_room")
            with Vertical(id="right"):
                browser = StoryBrowser("Story", id="browser")
                browser.border_title = "STORY"
                yield browser
                with VerticalScroll(id="detail_scroll") as detail_scroll:
                    detail_scroll.border_title = "DETAIL"
                    yield Static("Select an item to view details.", id="detail")
        yield Static("loading…", id="statusbar")
        yield ActivityStrip("idle", theme=NOVELIZER_AGENT_THEME, id="activity_strip")
        # Hidden by default; open_command_followup() reveals it, pre-filled,
        # when an args-taking palette command is selected. compact=True
        # drops Input's default tall border so the single row it gets has
        # at least one visible content line.
        followup = Input(id="command_followup", compact=True)
        followup.display = False
        yield followup
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
            try:
                await self.runtime.kg_catch_up()
            except Exception as e:
                self._report_worker_error("kg", e)
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
                # Combined background lag across BOTH drains (embedding indexer
                # AND KG projector), not just the indexer. The strict background
                # gate freezes every agent while EITHER side lags, so a readout
                # that watched only indexer.lag() would show "caught up" through
                # a KG-endpoint stall -- an unexplained freeze. background_progress()
                # sums both and never raises (see Runtime.background_progress).
                progress = await self.runtime.background_progress()
                await self.query_one("#brain", BrainPanel).refresh_from(
                    self.runtime.read,
                    threshold=self.runtime.settings.staleness_threshold_chapters,
                    delta=self.runtime.settings.sag_spike_delta,
                    lag=progress.total,
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
            now = time.monotonic()
            contract_recent = [c for c in (to_contract_event(e) for e in recent[-50:]) if c is not None]
            self._live_state = seed_state(contract_recent, now)
            self._agent_live_states = seed_states(contract_recent, now)
            self._refresh_strip()
            engine_room = self.query_one("#engine_room", EngineRoom)
            engine_room.render_live(self._live_state)
            for agent, state in self._agent_live_states.items():
                if agent in AGENT_NAMES:
                    engine_room.render_agent_live(agent, state)
            self._refresh_trace()
        except Exception as e:
            self._report_worker_error("telemetry-seed", e)
        q = self.runtime.telemetry_bus.subscribe()
        while True:
            try:
                item = await q.get()
                now = time.monotonic()
                contract_item = to_contract_event(item)
                agent = None
                if contract_item is not None:
                    self._live_state = apply_bus_item(self._live_state, contract_item, now)
                    agent = route_agent(contract_item)
                    if agent:
                        self._agent_live_states[agent] = apply_bus_item(
                            self._agent_live_states.get(agent, LiveRunState()), contract_item, now)
                if isinstance(item, StoredEvent):
                    self._trace_events.append(item)
                    self._refresh_trace()
                if isinstance(item, StoredEvent) and item.event_type in (
                        TelemetryEventType.TOOL_CALL_FINISHED, TelemetryEventType.TOOL_CALL_FAILED):
                    self.run_worker(self._summarize_tool_call(item), exclusive=False, group="tool-summary")
                self._refresh_strip()
                engine_room = self.query_one("#engine_room", EngineRoom)
                engine_room.render_live(self._live_state)
                if agent in AGENT_NAMES:
                    engine_room.render_agent_live(agent, self._agent_live_states[agent], now)
            except Exception as e:
                self._report_worker_error("telemetry", e)

    async def _summarize_tool_call(self, ev: StoredEvent) -> None:
        p = ev.payload
        tool_name = p.get("tool_name", "?")
        # Normalize identically to apply_bus_item's TOOL_CALL_STARTED handler:
        # this string is both the LLM prompt's input_summary context and the
        # ToolSummaryReady match key, so it must match the tool block exactly.
        input_summary = normalize_input_summary(p.get("input_summary", ""))
        if ev.event_type == TelemetryEventType.TOOL_CALL_FINISHED:
            # Cap what feeds the cheap-LLM synopsis prompt — the full output
            # is already rendered verbatim in the block itself (see
            # engine_room_model.Block.output); this is just prompt hygiene.
            output_summary, error = p.get("output_summary", "")[:1000], ""
        else:
            output_summary = ""
            error = f"{p.get('error_type', '?')}: {p.get('error_message', '')}"
        try:
            summary = await summarize_tool_call(
                self.runtime.settings, tool_name, input_summary, output_summary, error)
        except Exception as e:
            logger.warning("tool-call summarization failed: %s", e)
            return
        self.runtime.telemetry_bus.publish(ToolSummaryReady(
            run_id=p.get("run_id", ""), agent_name=p.get("agent_name", ""),
            tool_name=tool_name, input_summary=input_summary, summary=summary))

    async def _telemetry_refresh_loop(self) -> None:
        while True:
            try:
                self._refresh_strip()
                engine_room = self.query_one("#engine_room", EngineRoom)
                engine_room.render_live(self._live_state)
                now = time.monotonic()
                for agent, state in self._agent_live_states.items():
                    if agent in AGENT_NAMES:
                        engine_room.render_agent_live(agent, state, now)
            except Exception as e:
                self._report_worker_error("telemetry-refresh", e)
            await asyncio.sleep(0.5)

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

    def action_pause_all(self) -> None:
        _app_pause_all(self)

    def action_talk_to_project(self) -> None:
        _app_open_research(self)

    def action_open_escalations(self) -> None:
        _app_open_escalations(self)

    def action_brain_tab(self, pane_id: str) -> None:
        _app_brain_tab(self, pane_id)

    def open_command_followup(self, name: str) -> None:
        box = self.query_one("#command_followup", Input)
        box.value = f"{name} "
        box.display = True
        self.set_focus(box)
        box.action_end()

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
            _app_open_settings(self)
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
        if event.input.id == "command_followup":
            await self._run_command(event.value)
            event.input.value = ""
            event.input.display = False
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


def _app_pause_all(app: NovelizerApp) -> None:
    # Toggle: first press pauses every not-already-paused agent and
    # remembers which ones it paused; second press resumes only those,
    # leaving agents that were individually paused beforehand untouched.
    if app._paused_by_toggle is None:
        app._paused_by_toggle = app.runtime.scheduler.pause_all()
    else:
        app.runtime.scheduler.resume_agents(app._paused_by_toggle)
        app._paused_by_toggle = None


def _app_brain_tab(app: NovelizerApp, pane_id: str) -> None:
    app.query_one("#brain", BrainPanel).activate_tab(pane_id)


def _app_open_settings(app: NovelizerApp) -> None:
    from novelizer.tui.settings_screen import SettingsScreen

    story_dir = StoryDirectory(root=Path(app.runtime.settings.db_path).parent)
    app.push_screen(SettingsScreen(story_dir, lambda: app.runtime.settings))


def _app_open_export(app: NovelizerApp) -> None:
    if app.screen is not app.default_screen:
        return
    app.push_screen(ExportScreen(app.runtime))


def _app_quit(app: NovelizerApp) -> None:
    app.exit()


def _app_open_research(app: NovelizerApp) -> None:
    app.push_screen(ResearchScreen(app.runtime))


def _app_open_escalations(app: NovelizerApp) -> None:
    app.push_screen(EscalationsScreen(app.runtime))


APP_COMMANDS: list[AppCommand] = [
    AppCommand("approvals", "Open the approvals screen", _app_open_approvals),
    AppCommand("toggle_room", "Toggle Room view", _app_toggle_room),
    AppCommand("toggle_engine", "Toggle Engine Room view", _app_toggle_engine),
    AppCommand("toggle_prompt", "Toggle the Engine Room prompt panel", _app_toggle_prompt),
    AppCommand("pause_all", "Pause/unpause all agents", _app_pause_all),
    AppCommand("toggle_reading", "Toggle Reading view", _app_toggle_reading),
    AppCommand("settings", "Open settings", _app_open_settings),
    AppCommand("export_epub", "Export EPUB", _app_open_export),
    AppCommand("talk_to_project", "Talk to the Project (research)", _app_open_research),
    AppCommand("open_escalations", "Review escalated flags", _app_open_escalations),
    AppCommand("quit", "Quit Novelizer", _app_quit),
    AppCommand(
        "brain_tab_shape", "Story Brain: Shape tab",
        lambda app, pid="tab_shape": _app_brain_tab(app, pid),
    ),
    AppCommand(
        "brain_tab_threads", "Story Brain: Threads tab",
        lambda app, pid="tab_threads": _app_brain_tab(app, pid),
    ),
    AppCommand(
        "brain_tab_secrets", "Story Brain: Secrets tab",
        lambda app, pid="tab_secrets": _app_brain_tab(app, pid),
    ),
    AppCommand(
        "brain_tab_causeway", "Story Brain: Cause tab",
        lambda app, pid="tab_causeway": _app_brain_tab(app, pid),
    ),
    AppCommand(
        "brain_tab_outline", "Story Brain: Outline tab",
        lambda app, pid="tab_outline": _app_brain_tab(app, pid),
    ),
    AppCommand(
        "brain_tab_arcs", "Story Brain: Arcs tab",
        lambda app, pid="tab_arcs": _app_brain_tab(app, pid),
    ),
]


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
                    text=name,
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


# NovelizerApp.COMMANDS references NovelizerCommandProvider, which must be
# defined first (it in turn depends on APP_COMMANDS, defined above) — so it
# is assigned here rather than in the class body.
NovelizerApp.COMMANDS = {NovelizerCommandProvider}
