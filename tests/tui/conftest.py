"""Shared helpers for the pilot tests: a complete runner roster, and settling.

Both exist for the same reason -- these tests drive a REAL app against a REAL
Runtime, so anything the test leaves unspecified gets built for real, and
anything it waits a fixed time for is a race.
"""
from __future__ import annotations

import time

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


DEFAULT_SETTLE_TIMEOUT = 5.0


async def settle(pilot, predicate, timeout: float = DEFAULT_SETTLE_TIMEOUT) -> bool:
    """Pump the event loop until `predicate()` holds, or `timeout` elapses.

    Replaces `await pilot.pause(0.8)`-style fixed sleeps. A fixed sleep is
    wrong in both directions: it burns its full duration even when the app
    settled immediately, and it still loses the race on a loaded machine --
    "0.8s wasn't enough this time" is precisely how these tests fail under
    parallel load.

    Returns whether the predicate held, so callers can assert on it directly;
    the caller's own assertion still runs either way, so a genuinely broken
    expectation fails with its own message rather than a timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await pilot.pause()
        try:
            if predicate():
                return True
        except Exception:
            # A widget the predicate reaches for may not be mounted yet; that
            # is a "not settled yet" condition, not a test failure.
            pass
    return False
