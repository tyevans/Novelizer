from __future__ import annotations
import asyncio
import logging
import time
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Footer, Input, RichLog, Tab, Tabs

from tui_kit.run_model import LiveRunState, apply_bus_item, route_agent
from tui_kit.widgets.live_stream_panel import LiveStreamPanel
from novelizer.tui.telemetry_adapter import to_contract_event
from novelizer.tui.identity import NOVELIZER_AGENT_THEME

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 0.5


class ChatScreen(Screen):
    """Full-screen chat with one agent, with a tab strip over every existing
    conversation. Transcript state comes from the read model; pending and
    unread are session-only UI state."""

    BINDINGS = [
        ("escape", "back", "Mission Control"),
        ("ctrl+pageup", "prev_chat", "Prev chat"),
        ("ctrl+pagedown", "next_chat", "Next chat"),
    ]

    def __init__(self, runtime, agent_name: str) -> None:
        super().__init__()
        self.runtime = runtime
        self.agent_name = agent_name
        self._agents: list[str] = [agent_name]
        self._seen: dict[str, int] = {}
        self._errors: dict[str, list[str]] = {}
        self._last_render_key: tuple = ()
        self._live_state = LiveRunState()

    def compose(self) -> ComposeResult:
        yield Tabs(Tab(f"@{self.agent_name}", id=f"chat-{self.agent_name}"), id="chat_tabs")
        yield LiveStreamPanel(theme=NOVELIZER_AGENT_THEME, id="chat_live")
        yield RichLog(highlight=False, markup=False, id="chat_log")
        yield Input(id="chat_input", placeholder=f"message @{self.agent_name}…", compact=True)
        yield Footer()

    async def on_mount(self) -> None:
        self.run_worker(self._poll_loop(), exclusive=False)
        self.run_worker(self._telemetry_loop(), exclusive=False, group="telemetry")
        self.set_focus(self.query_one("#chat_input", Input))

    async def _telemetry_loop(self) -> None:
        q = self.runtime.telemetry_bus.subscribe()
        try:
            while True:
                item = await q.get()
                contract_item = to_contract_event(item)
                if contract_item is None or route_agent(contract_item) != f"chat:{self.agent_name}":
                    continue
                self._live_state = apply_bus_item(self._live_state, contract_item, time.monotonic())
                self.query_one(LiveStreamPanel).render(self._live_state)
        finally:
            self.runtime.telemetry_bus.unsubscribe(q)

    # -- public API used by the app --------------------------------------

    async def set_current(self, agent_name: str) -> None:
        """Switch the screen to another agent's conversation (used by @mention
        routing while the screen is already open)."""
        self.agent_name = agent_name
        self._live_state = LiveRunState()
        self.query_one(LiveStreamPanel).render(self._live_state)
        if agent_name not in self._agents:
            self._agents.append(agent_name)
        await self._sync_tabs()
        tabs = self.query_one("#chat_tabs", Tabs)
        tabs.active = f"chat-{agent_name}"
        self.query_one("#chat_input", Input).placeholder = f"message @{agent_name}…"

    def add_error(self, agent_name: str, line: str) -> None:
        self._errors.setdefault(agent_name, []).append(line)
        self._last_render_key = ()  # force re-render on next poll

    # -- internals ---------------------------------------------------------

    async def _sync_tabs(self) -> None:
        tabs = self.query_one("#chat_tabs", Tabs)
        existing = {t.id for t in tabs.query(Tab)}
        for agent in self._agents:
            tab_id = f"chat-{agent}"
            if tab_id not in existing:
                await tabs.add_tab(Tab(f"@{agent}", id=tab_id))

    def _tab_label(self, agent: str, count: int) -> str:
        unread = agent != self.agent_name and count > self._seen.get(agent, 0)
        return f"@{agent} ●" if unread else f"@{agent}"

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self._refresh()
            except Exception as e:
                logger.warning("chat screen refresh failed: %s", e)
            await asyncio.sleep(_POLL_INTERVAL)

    async def _refresh(self) -> None:
        conversations = await self.runtime.read.list_chat_conversations()
        for agent in conversations:
            if agent not in self._agents:
                self._agents.append(agent)
        await self._sync_tabs()
        tabs = self.query_one("#chat_tabs", Tabs)
        counts: dict[str, int] = {}
        for agent in self._agents:
            counts[agent] = await self.runtime.read.count_chat_messages(agent)
            tab = tabs.query_one(f"#chat-{agent}", Tab)
            tab.label = self._tab_label(agent, counts[agent])
        self._seen[self.agent_name] = counts.get(self.agent_name, 0)
        pending = self.runtime.chat.pending(self.agent_name) if self.runtime.chat else False
        render_key = (self.agent_name, counts.get(self.agent_name, 0), pending,
                      len(self._errors.get(self.agent_name, [])))
        if render_key == self._last_render_key:
            return
        self._last_render_key = render_key
        log = self.query_one("#chat_log", RichLog)
        log.clear()
        for m in await self.runtime.read.list_chat_messages(self.agent_name):
            who = "you" if m.role == "user" else self.agent_name
            log.write(f"{who}: {m.text}")
        for err in self._errors.get(self.agent_name, []):
            log.write(err)
        if pending:
            log.write(f"… {self.agent_name} is thinking")

    # -- events ------------------------------------------------------------

    async def on_input_submitted(self, event) -> None:
        if event.input.id != "chat_input":
            return
        text = event.value.strip()
        event.input.value = ""
        if text:
            await self.app.send_chat_message(self.agent_name, text)
            self._last_render_key = ()

    def on_tabs_tab_activated(self, event) -> None:
        tab_id = event.tab.id or ""
        if tab_id.startswith("chat-"):
            agent = tab_id.removeprefix("chat-")
            if agent != self.agent_name:
                self.agent_name = agent
                self._live_state = LiveRunState()
                self.query_one(LiveStreamPanel).render(self._live_state)
                self._last_render_key = ()
                self.query_one("#chat_input", Input).placeholder = f"message @{agent}…"

    def action_back(self) -> None:
        self.app.pop_screen()

    async def _cycle(self, step: int) -> None:
        if len(self._agents) < 2:
            return
        idx = (self._agents.index(self.agent_name) + step) % len(self._agents)
        await self.set_current(self._agents[idx])

    async def action_prev_chat(self) -> None:
        await self._cycle(-1)

    async def action_next_chat(self) -> None:
        await self._cycle(1)
