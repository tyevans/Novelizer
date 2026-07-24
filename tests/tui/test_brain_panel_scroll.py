import pytest
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll

from novelizer.tui.widgets.brain_panel import BrainPanel


class _Harness(App):
    def compose(self) -> ComposeResult:
        yield BrainPanel()


@pytest.mark.asyncio
async def test_each_brain_body_is_inside_a_scroll_container():
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        for body_id in ("shape_body", "threads_body", "secrets_body",
                        "causeway_body", "outline_body", "arcs_body"):
            body = app.query_one(f"#{body_id}")
            # walk ancestors; a VerticalScroll must be one of them
            anc = body.parent
            found = False
            while anc is not None:
                if isinstance(anc, VerticalScroll):
                    found = True
                    break
                anc = anc.parent
            assert found, f"{body_id} is not inside a VerticalScroll"
