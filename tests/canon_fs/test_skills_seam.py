"""TDD seam test: prove skills actually load through the REAL composite
backend using deepagents' own SkillsMiddleware -- not our composite/backend
unit tests, which only check ls/read at the routing layer and never
exercised the middleware's actual listing path (`_alist_skills_with_errors`
via `abefore_agent`), which is exactly the path that was broken:

(a) SkillsMiddleware calls `adownload_files` to fetch each candidate
    SKILL.md. `ReadOnlyBackend` refused all downloads outright (treating a
    bulk read as if it were a write), so every candidate failed silently.
(b) SkillsMiddleware treats each `sources` entry as a CONTAINER directory
    listing skill subdirectories (it lists the source, keeps `is_dir`
    entries, then reads `<subdir>/SKILL.md`). Our packs are individual
    directories (`/skills/outlining`), not subdirectories of a shared
    container passed per-agent -- the container the middleware expects is
    `/skills` itself.

This test builds the exact composite Runtime._phase_a_toolkit builds,
constructs a real `SkillsMiddleware` against it exactly as the tooled
builders do, and asserts all packs load with descriptions and no
errors.
"""

from __future__ import annotations

import pytest
from deepagents.backends import CompositeBackend, StateBackend
from deepagents.middleware.skills import SkillsMiddleware, _alist_skills_with_errors

from novelizer.canon_fs.skills_route import CRAFT_SKILLS, build_skills_backend

EXPECTED_PACKS = {
    "outlining",
    "promise-payoff",
    "character-arcs",
    "scene-sequel",
    "pacing",
    "output-conventions",
}


def _real_composite() -> CompositeBackend:
    """Mirror Runtime._phase_a_toolkit's composite shape for the /skills/
    route -- default/outline routes are irrelevant to this seam, so they're
    omitted; only the skills route and its container shape matter here."""
    return CompositeBackend(
        default=StateBackend(),
        routes={"/skills/": build_skills_backend()},
    )


async def test_skills_middleware_loads_all_five_packs_via_container_source():
    """The exact seam the M10 review found broken: deepagents' own listing
    coroutine, against our real backend, using the container source shape
    every tooled builder now passes."""
    backend = _real_composite()
    assert CRAFT_SKILLS == ["/skills"]
    skills, error = await _alist_skills_with_errors(backend, CRAFT_SKILLS[0])
    assert error is None, f"skills source failed to load: {error}"
    names = {s["name"] for s in skills}
    assert names == EXPECTED_PACKS, f"expected all packs, got {names}"
    for skill in skills:
        assert skill["description"].strip(), f"{skill['name']} has an empty description"


async def test_skills_middleware_abefore_agent_populates_state():
    """Exercise the actual middleware entrypoint (not just the listing
    helper) to prove the full seam -- construction through abefore_agent --
    works end to end with our backend and source shape."""
    backend = _real_composite()
    middleware = SkillsMiddleware(backend=backend, sources=CRAFT_SKILLS)

    class _FakeRuntime:
        context = None

    state = {"messages": []}
    result = await middleware.abefore_agent(state, _FakeRuntime(), {})
    assert result is not None
    assert "skills_load_errors" not in result, result.get("skills_load_errors")
    skills_metadata = result["skills_metadata"]
    names = {s["name"] for s in skills_metadata}
    assert EXPECTED_PACKS <= names, f"expected all packs reachable, got {names}"
