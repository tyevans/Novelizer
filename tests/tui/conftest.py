"""Shared helpers for the pilot tests.

These tests drive a REAL app against a REAL Runtime, so anything a test leaves
unspecified gets built for real -- which is what stub_runners exists to stop.
"""
from __future__ import annotations

from novelizer.agents.registry import AGENT_REGISTRY


class _NullRunner:
    """A runner that answers every invocation with an empty structured response.

    Stands in for agents a test does not care about. Returning None rather than
    a typed draft is deliberate: the agents treat it as "nothing to do", which
    is exactly what an agent outside the test's subject should do.
    """

    async def ainvoke(self, inputs):
        return {"structured_response": None}


def stub_runners(**overrides):
    """A runner for EVERY registered agent, with `overrides` layered on top.

    `Runtime._runner_for` falls back to BUILDING THE REAL RUNNER for any name a
    test did not inject (novelizer/runtime.py, see its own comment about this
    having previously made TUI tests hang on live connection attempts). A real
    runner compiles a deep-agent graph and, for the tooled agents, wires a chat
    model -- so each missing name is real work on every single boot.

    Every pilot file used to hand-maintain its own 8-name dict while
    AGENT_REGISTRY holds 13, leaving 5 agents built for real per Runtime.
    Measured: Runtime.start() 0.82s with the partial roster, 0.46s with a
    complete one, across ~100 Runtime-booting pilot tests.

    Deriving from the registry means a newly registered agent is stubbed by
    construction rather than quietly becoming a real build -- the same drift
    that left AGENT_NAMES at 9 of 13.
    """
    runners = {spec.name: _NullRunner() for spec in AGENT_REGISTRY}
    runners.update(overrides)
    return runners
