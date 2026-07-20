from __future__ import annotations
import pytest
from novelizer.agents.registry_types import AgentContext, AgentSpec, ToolGrant


class _Settings:
    editor_tools_enabled = True
    checker_tools_enabled = False


def test_tool_grant_reads_named_setting_true():
    grant = ToolGrant(enabled_setting="editor_tools_enabled")
    assert grant.is_enabled(_Settings()) is True


def test_tool_grant_reads_named_setting_false():
    grant = ToolGrant(enabled_setting="checker_tools_enabled")
    assert grant.is_enabled(_Settings()) is False


def test_agent_context_holds_shared_construction_state():
    ctx = AgentContext(
        read="read_store", committer="committer", events="event_store",
        settings=_Settings(), casting_note="terse", personalities={"author": "wry"},
        provenance={"model": "x"}, tooled=lambda b, e: b, runner_for=lambda n, b, fallback_name=None: b,
    )
    assert ctx.casting_note == "terse"
    assert ctx.personalities["author"] == "wry"
    assert ctx.tooled(lambda: None, True)() is None


def test_agent_spec_is_frozen_and_holds_construct_callable():
    called = {}

    def construct(ctx):
        called["ctx"] = ctx
        return "agent-instance"

    spec = AgentSpec(name="author", tool_grant=None, construct=construct)
    assert spec.name == "author"
    result = spec.construct("fake-ctx")
    assert result == "agent-instance"
    assert called["ctx"] == "fake-ctx"
    with pytest.raises(Exception):
        spec.name = "editor"  # frozen dataclass rejects mutation
