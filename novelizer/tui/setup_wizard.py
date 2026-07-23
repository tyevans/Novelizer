from __future__ import annotations

from collections.abc import Iterator

from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widget import Widget
from textual.widgets import Button, Footer, Header, Input, Label, Select, Static

from novelizer.settings.setup_core import build_global_config_data, probe_endpoint

_MODEL_SELECT_IDS = ("author_model", "agent_model", "embed_model")


def _field(label: str, widget: Widget, help_text: str) -> Iterator[Widget]:
    """A labelled form field: visible label, the widget, dim help line.
    Labels must not live in placeholders — those vanish once a value is set."""
    yield Label(label, classes="field-label")
    yield widget
    yield Static(help_text, classes="field-help")


class SetupWizardApp(App[dict | None]):
    """First-run setup: endpoint -> live connectivity test -> model picks.

    run() returns the global-config dict to write, or None if the user quit.
    TUI shell only — probing and config assembly live in settings.setup_core.
    """

    TITLE = "Novelizer — First-run setup"
    BINDINGS = [("q", "quit", "Quit")]
    CSS = """
    #wizard {
        padding: 1 2;
    }
    #wizard .field-label {
        text-style: bold;
    }
    #wizard .field-help {
        color: $text-muted;
        margin-bottom: 1;
    }
    #wizard #probe {
        margin-bottom: 1;
    }
    """

    def __init__(self, probe=probe_endpoint) -> None:
        super().__init__()
        self._probe = probe

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="wizard"):
            yield Static("Point novelizer at your OpenAI-compatible LLM endpoint.")
            yield from _field(
                "LLM base URL",
                Input(value="http://localhost:8080/v1", id="base_url"),
                "OpenAI-compatible endpoint (llama.cpp, vLLM, Ollama, LM Studio, "
                "OpenRouter…). Include the /v1 suffix.",
            )
            yield from _field(
                "API key",
                Input(id="api_key", password=True),
                "Sent as a Bearer token. Leave blank for local endpoints that don't need auth.",
            )
            yield from _field(
                "Stories directory",
                Input(value="stories", id="stories_dir"),
                "Where your stories live. ~ expands; relative paths resolve from "
                "where you launch novelizer.",
            )
            yield Button("Test connection", id="probe")
            yield Static("", id="probe_result")
            yield from _field(
                "Author model",
                Select([], prompt="run Test connection first", id="author_model", disabled=True),
                "Writes the prose — pick your strongest model.",
            )
            yield from _field(
                "Agent model",
                Select([], prompt="run Test connection first", id="agent_model", disabled=True),
                "Runs the support agents (editor, continuity, plotting…). "
                "A faster model works well.",
            )
            yield from _field(
                "Embedding model",
                Select([], prompt="run Test connection first", id="embed_model", disabled=True),
                "Builds the semantic index used for canon search. Must be an embedding model.",
            )
            with Horizontal(id="wizard_actions"):
                yield Button("Save & continue", id="save", variant="success", disabled=True)
                yield Button("Skip model picks — save endpoint only", id="skip")
        yield Footer()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "probe":
            await self._run_probe()
        elif event.button.id == "save":
            self._finish(with_models=True)
        elif event.button.id == "skip":
            self._finish(with_models=False)

    async def _run_probe(self) -> None:
        base_url = self.query_one("#base_url", Input).value
        api_key = self.query_one("#api_key", Input).value.strip() or "not-needed"
        result = await self._probe(base_url.strip(), api_key=api_key)
        out = self.query_one("#probe_result", Static)
        if not result.ok:
            out.update(f"✗ {result.error}")
            return
        out.update(f"✓ connected — models: {', '.join(result.models) or '(none reported)'}")
        options = [(m, m) for m in result.models]
        for select_id in _MODEL_SELECT_IDS:
            select = self.query_one(f"#{select_id}", Select)
            select.set_options(options)
            select.disabled = not options
            if options:
                select.value = result.models[0]
        self.query_one("#save", Button).disabled = not options

    def _selected(self, select_id: str) -> str:
        value = self.query_one(f"#{select_id}", Select).value
        return "" if value in (None, Select.BLANK) else str(value)

    def _finish(self, with_models: bool) -> None:
        try:
            data = build_global_config_data(
                base_url=self.query_one("#base_url", Input).value,
                api_key=self.query_one("#api_key", Input).value,
                stories_dir=self.query_one("#stories_dir", Input).value,
                author_model=self._selected("author_model") if with_models else "",
                agent_model=self._selected("agent_model") if with_models else "",
                embed_model=self._selected("embed_model") if with_models else "",
            )
        except ValueError as e:
            self.query_one("#probe_result", Static).update(f"✗ {e}")
            return
        self.exit(data)
