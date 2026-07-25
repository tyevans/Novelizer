from __future__ import annotations
import pytest
from novelizer.agents.registry_types import AgentTier, AgentContext, AgentSpec, ToolGrant, SubagentGrant


class _Settings:
    editor_tools_enabled = True
    checker_tools_enabled = False
    editor_subagent_enabled = True
    checker_subagent_enabled = False


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

    spec = AgentSpec(name="author", tool_grant=None, construct=construct, tier=AgentTier.FULL)
    assert spec.name == "author"
    result = spec.construct("fake-ctx")
    assert result == "agent-instance"
    assert called["ctx"] == "fake-ctx"
    with pytest.raises(Exception):
        spec.name = "editor"  # frozen dataclass rejects mutation


def test_subagent_grant_reads_named_setting_true():
    grant = SubagentGrant(enabled_setting="editor_subagent_enabled")
    assert grant.is_enabled(_Settings()) is True


def test_subagent_grant_reads_named_setting_false():
    grant = SubagentGrant(enabled_setting="checker_subagent_enabled")
    assert grant.is_enabled(_Settings()) is False


def test_agent_spec_subagent_grant_defaults_to_none():
    spec = AgentSpec(name="author", tool_grant=None, construct=lambda ctx: None, tier=AgentTier.FULL)
    assert spec.subagent_grant is None


def test_agent_spec_accepts_explicit_subagent_grant():
    grant = SubagentGrant(enabled_setting="editor_subagent_enabled")
    spec = AgentSpec(name="editor", tool_grant=None, construct=lambda ctx: None, tier=AgentTier.FULL, subagent_grant=grant)
    assert spec.subagent_grant is grant
