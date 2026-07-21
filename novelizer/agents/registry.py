from __future__ import annotations
from novelizer.agents import (
    author, world_architect, character_keeper, editor,
    continuity_checker, retconner, structure_analyst, plotter, muse, triage,
)
from novelizer.agents.registry_types import AgentSpec

# List order is scheduling order -- the same order Runtime.start() built
# self.agents in before this registry existed. The planner (plotter) ticks
# before the writer (author) in a fresh room; keep that ordering intact.
AGENT_REGISTRY: list[AgentSpec] = [
    world_architect.SPEC, character_keeper.SPEC, muse.SPEC,
    plotter.SPEC, author.SPEC,
    editor.SPEC, continuity_checker.SPEC, retconner.SPEC, structure_analyst.SPEC,
    triage.SPEC,
]
