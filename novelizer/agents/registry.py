from __future__ import annotations
from novelizer.agents import (
    author, attributor, world_architect, character_keeper, editor,
    continuity_checker, retconner, curator, structure_analyst, summarizer, plotter, muse, triage,
    flaglabeler,
)
from novelizer.agents.registry_types import AgentSpec

# List order is scheduling order -- the same order Runtime.start() built
# self.agents in before this registry existed. The planner (plotter) ticks
# before the writer (author) in a fresh room; keep that ordering intact.
# The Attributor sits immediately after the Author and before the Editor:
# nothing that treats prose as final may see markup.
AGENT_REGISTRY: list[AgentSpec] = [
    world_architect.SPEC, character_keeper.SPEC, muse.SPEC,
    plotter.SPEC, author.SPEC, attributor.SPEC,
    editor.SPEC, continuity_checker.SPEC, retconner.SPEC, curator.SPEC, structure_analyst.SPEC,
    summarizer.SPEC,
    triage.SPEC,
    flaglabeler.SPEC,
]
