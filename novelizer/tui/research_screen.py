from __future__ import annotations
import logging
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Footer, Input, RichLog

from novelizer.research.service import ResearchAnswerError

logger = logging.getLogger(__name__)


class ResearchScreen(Screen):
    """Session-only, single-conversation research REPL: submit a question,
    the input disables while the research agent works, the answer appends
    when it's ready. No persistence — this screen's transcript is its own
    in-memory state."""

    BINDINGS = [("escape", "back", "Mission Control")]

    def __init__(self, runtime) -> None:
        super().__init__()
        self.runtime = runtime
        self._history: list[tuple[str, str]] = []
        self._pending = False

    def compose(self) -> ComposeResult:
        log = RichLog(highlight=False, markup=False, id="research_log")
        log.border_title = "TALK TO THE PROJECT"
        yield log
        yield Input(id="research_input", placeholder="ask about the project…", compact=True)
        yield Footer()

    async def on_mount(self) -> None:
        self.set_focus(self.query_one("#research_input", Input))

    async def on_input_submitted(self, event) -> None:
        if event.input.id != "research_input":
            return
        if self._pending:
            return  # one turn at a time — drop a second submit while busy
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        self._pending = True
        event.input.disabled = True
        log = self.query_one("#research_log", RichLog)
        log.write(f"You: {text}")
        log.write("… researching")
        self.run_worker(self._ask(text), exclusive=True)

    async def _ask(self, question: str) -> None:
        log = self.query_one("#research_log", RichLog)
        input_widget = self.query_one("#research_input", Input)
        try:
            answer = await self.runtime.research.ask(question, self._history)
            self._history.append(("you", question))
            self._history.append(("project", answer))
            log.write(f"Project: {answer}")
        except ResearchAnswerError as e:
            log.write(f"⚠ research failed: {e}")
        except Exception as e:
            logger.warning("research turn failed: %s", e)
            log.write(f"⚠ research failed: {e}")
        finally:
            self._pending = False
            input_widget.disabled = False
            self.set_focus(input_widget)

    def action_back(self) -> None:
        self.app.pop_screen()
