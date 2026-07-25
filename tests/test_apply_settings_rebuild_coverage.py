"""A live temperature change must reach every agent that depends on it.

`Runtime.apply_settings` reports `agent_temperature` in its `applied` list, which
is a promise: the running fleet is now using the new value. It kept that promise
via a hand-written block naming seven agents, so an agent added later (or one
whose builder started reading the setting later) silently kept the old
temperature until the next restart -- while apply_settings still claimed the key
was applied.

The coverage check is derived from the agents themselves rather than restating
the list, so it fails for a *newly added* agent too, which is the whole point.
"""
from __future__ import annotations

import importlib
import inspect

import pytest

from novelizer.agents.registry import AGENT_REGISTRY
from novelizer.runtime import Runtime
from novelizer.settings import EffectiveSettings as Settings


def _agents_reading(setting: str) -> set[str]:
    """Names of registry agents whose module builds a model from `setting`."""
    found = set()
    for spec in AGENT_REGISTRY:
        module = importlib.import_module(spec.construct.__module__)
        if f"settings.{setting}" in inspect.getsource(module):
            found.add(spec.name)
    return found


def test_agent_temperature_readers_are_a_known_set():
    """Documents which agents the live-apply path has to cover."""
    readers = _agents_reading("agent_temperature")
    # If this fails, an agent started or stopped depending on agent_temperature.
    # That is fine -- but check the rebuild coverage test below still passes.
    assert readers == {
        "world_architect", "character_keeper", "editor", "continuity_checker",
        "retconner", "curator", "structure_analyst", "plotter", "triage",
    }, readers


@pytest.fixture
def settings(tmp_path):
    return Settings(db_path=str(tmp_path / "world.db"))


async def test_rebuild_keeps_the_subagent_grant(tmp_path, monkeypatch):
    """A rebuild must not quietly downgrade a subagent-enabled agent.

    The old hand-written rebuild called `_tooled(builder, pinned)` with two
    positional args, so `subagent_enabled` fell back to False: a live
    temperature change stripped the researcher subagent from every agent that
    had one, with nothing reporting it. Rebuilding through the agent's own
    construct() carries the grant, because that is where the grant is read.
    """
    settings = Settings(
        db_path=str(tmp_path / "world.db"),
    ).model_copy(update={
        "world_architect_tools_enabled": True,
        "world_architect_subagent_enabled": True,
    })
    rt = Runtime(settings)
    await rt.start()
    try:
        seen: list[dict] = []

        class _R:
            async def ainvoke(self, inputs):
                raise AssertionError("not used")

        def _spy(settings, callbacks=None, backend=None, tools=None, subagents=None):
            seen.append({"subagents": subagents, "backend": backend})
            return _R()

        monkeypatch.setattr(
            "novelizer.agents.world_architect.build_world_architect_runner", _spy
        )
        rt.apply_settings(
            rt.settings.model_copy(update={"agent_temperature": rt.settings.agent_temperature + 0.2})
        )

        assert len(seen) == 1
        assert seen[0]["backend"] is rt._canon_backend
        assert seen[0]["subagents"], (
            "rebuild dropped the subagent grant -- the agent keeps pull_mode and its "
            "tools but silently loses its researcher until restart"
        )
    finally:
        await rt.close()


async def test_changing_agent_temperature_rebuilds_every_dependent_runner(settings):
    # No runner/runners injected: this is the path where apply_settings owns
    # rebuilding, which is the path a real run takes.
    rt = Runtime(settings)
    await rt.start()
    try:
        before = {name: rt.agents_by_name[name]._runner for name in _agents_reading("agent_temperature")}
        result = rt.apply_settings(
            settings.model_copy(update={"agent_temperature": settings.agent_temperature + 0.15})
        )
        assert "agent_temperature" in result["applied"]

        stale = [
            name for name, runner in before.items()
            if rt.agents_by_name[name]._runner is runner
        ]
        assert not stale, (
            f"apply_settings reported agent_temperature applied, but these agents kept "
            f"their old runner (and so the old temperature) until restart: {sorted(stale)}"
        )
    finally:
        await rt.close()
