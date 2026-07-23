from tui_kit.contracts import AgentTheme, RunStarted, TokenDelta, ToolCallStarted


class _FakeTheme:
    def glyph(self, agent_name: str) -> str:
        return "@"

    def label(self, agent_name: str) -> str:
        return agent_name.title()

    def style(self, agent_name: str) -> str:
        return "bold"

    def verb(self, agent_name: str) -> str:
        return "working"


def test_fake_theme_satisfies_the_agent_theme_protocol():
    theme: AgentTheme = _FakeTheme()
    assert theme.glyph("author") == "@"
    assert theme.label("author") == "Author"
    assert theme.style("author") == "bold"
    assert theme.verb("author") == "working"


def test_contract_events_are_frozen_dataclasses_with_expected_fields():
    started = RunStarted(run_id="r1", agent_name="author")
    assert started.run_id == "r1" and started.agent_name == "author"
    delta = TokenDelta(run_id="r1", agent_name="author", text="hi")
    assert delta.kind == "text"
    tool = ToolCallStarted(run_id="r1", agent_name="author", tool_name="search",
                           input_summary="q")
    assert tool.delegate == ""
