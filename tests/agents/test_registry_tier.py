"""Every agent declares its cognitive tier, in the registry, next to its
tooling grants.

The alternative -- each build_*_runner quietly picking its own temperature and
model -- is what the fleet had, and it drifted: five call sites had five
definitions of "cheap". A declared field means the fleet's cost profile is
readable in one file and sweepable by a test.
"""
from __future__ import annotations

import dataclasses

import pytest

from novelizer.agents.registry import AGENT_REGISTRY
from novelizer.agents.registry_types import AgentSpec, AgentTier

# Light = the work is deterministic shaping of text someone else wrote, and
# the pass has no tools to choose between. Full = the work is judgment.
#
# summarizer and kg extraction are deliberately NOT light: they read prose and
# decide what matters, which is judgment even though the output is short. This
# list is the whole argument, so changing it should be a visible diff.
EXPECTED_LIGHT = {"flaglabeler"}


def test_every_spec_declares_a_tier_explicitly():
    """No default: agent fourteen must state which tier it is, rather than
    inheriting whichever one happened to be cheaper to leave out."""
    assert "tier" not in {
        f.name for f in dataclasses.fields(AgentSpec) if f.default is not dataclasses.MISSING
    }
    for spec in AGENT_REGISTRY:
        assert isinstance(spec.tier, AgentTier), spec.name


def test_the_light_fleet_is_exactly_the_declared_set():
    light = {spec.name for spec in AGENT_REGISTRY if spec.tier is AgentTier.LIGHT}
    assert light == EXPECTED_LIGHT


def test_light_agents_have_no_tools():
    """A light agent runs a graph-free single call, so it has nowhere to put a
    tool. Granting one would silently do nothing -- fail loudly instead."""
    for spec in AGENT_REGISTRY:
        if spec.tier is AgentTier.LIGHT:
            assert spec.tool_grant is None, spec.name
            assert spec.subagent_grant is None, spec.name


def test_light_agents_rebuild_when_the_light_tier_settings_change():
    """The settings-reload trap: a runner caches its model, so an agent that
    reads light_model must name it in rebuild_on or a live change is ignored."""
    for spec in AGENT_REGISTRY:
        if spec.tier is AgentTier.LIGHT:
            assert "light_model" in spec.rebuild_on, spec.name
            assert "light_reasoning" in spec.rebuild_on, spec.name


@pytest.mark.parametrize("spec", AGENT_REGISTRY, ids=lambda s: s.name)
def test_full_agents_do_not_claim_light_settings(spec):
    """Inverse guard: a full agent naming light_model in rebuild_on would
    rebuild on a setting it never reads."""
    if spec.tier is AgentTier.FULL:
        assert "light_model" not in spec.rebuild_on
