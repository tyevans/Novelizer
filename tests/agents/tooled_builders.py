"""The tooled-builder sweep, derived from AGENT_REGISTRY rather than hand-listed.

Both fleet-wide prompt-surface tests (skills wiring, output-conventions note)
used to keep their own copy of this list. Both copies drifted: the Curator was
added to the fleet with tools and subagents but appeared in neither list, so it
shipped without `skills=` and nothing failed. Deriving from the registry means a
new tooled agent is swept the day it is registered.

An agent is "tooled" iff its AgentSpec carries a `tool_grant` -- that is exactly
the set whose builders take a `backend` kwarg and can reach `/skills/`.
"""
from __future__ import annotations

from novelizer.agents.registry import AGENT_REGISTRY

# Convention every tooled agent follows: module novelizer.agents.<name> exposes
# build_<name>_runner. Asserted below rather than assumed.
TOOLED_BUILDERS = [
    (f"novelizer.agents.{spec.name}", f"build_{spec.name}_runner")
    for spec in AGENT_REGISTRY
    if spec.tool_grant is not None
]


def test_every_tooled_agent_has_a_discoverable_builder():
    import importlib

    assert TOOLED_BUILDERS, "registry produced no tooled agents -- derivation is broken"
    for module_name, func_name in TOOLED_BUILDERS:
        module = importlib.import_module(module_name)
        assert callable(getattr(module, func_name, None)), (
            f"{module_name} has a tool_grant but no {func_name}; the fleet-wide "
            "prompt-surface sweeps silently skip it"
        )
