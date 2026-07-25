"""The secrets-and-reveals craft pack is registered, not merely on disk.

This project has already shipped guidance no agent ever saw: six tooled agents
could read `/skills/` but no builder passed `skills=`, so the packs existed and
were undiscoverable. A pack directory added under novelizer/skills_packs/ is
inert until deepagents' `SkillsMiddleware` can list it through the real
composite backend AND every tooled agent registers that container. This module
asserts both halves for the secrets pack specifically, end to end -- listing via
deepagents' own coroutine against the real backend, and the builder sweep
derived from AGENT_REGISTRY (via tests/agents/tooled_builders.py) rather than a
hand-listed roster, for the same reason the decisiveness and rejection-feedback
sweeps derive theirs: the Curator once shipped unswept because a hand copy
drifted.

Content is pinned only where being wrong would mislead an agent: the pack must
route reveal-window guidance to the Plotter without ever offering it planting,
which `PlotterOutput` has no field to express.
"""
from __future__ import annotations

import importlib.resources

import deepagents
import pytest
from deepagents.backends import CompositeBackend, StateBackend
from deepagents.middleware.skills import _alist_skills_with_errors

from novelizer.canon_fs.backend import CanonBackend
from novelizer.canon_fs.skills_route import CRAFT_SKILLS, build_skills_backend
from tests.agents.tooled_builders import TOOLED_BUILDERS

PACK = "secrets-and-reveals"


def _pack_files() -> list[str]:
    root = importlib.resources.files("novelizer.skills_packs") / PACK
    return [p.name for p in root.iterdir()]


def _skills_composite() -> CompositeBackend:
    """The `/skills/` route exactly as Runtime._phase_a_toolkit builds it;
    the other routes are irrelevant to skill listing."""
    return CompositeBackend(
        default=StateBackend(),
        routes={"/skills/": build_skills_backend()},
    )


class _FakeSettings:
    agent_model = "gpt-4o-mini"
    author_model = "gpt-4o-mini"
    llm_base_url = None
    llm_api_key = "test-key"
    agent_temperature = 0.7
    author_temperature = 0.8
    llm_max_tokens = None


class _FakeGraph:
    def with_config(self, config):
        return self


@pytest.fixture
def captured_kwargs(monkeypatch):
    captured: dict = {}

    def fake_create_deep_agent(*args, **kwargs):
        captured.update(kwargs)
        return _FakeGraph()

    monkeypatch.setattr(deepagents, "create_deep_agent", fake_create_deep_agent)
    return captured


def _skill_text() -> str:
    return (
        importlib.resources.files("novelizer.skills_packs") / PACK / "SKILL.md"
    ).read_text()


# -- registration: the pack loads through the real seam --


async def test_pack_loads_through_the_real_skills_container():
    """Fails if the pack directory is missing, misnamed, has no SKILL.md, or
    has no frontmatter description -- i.e. every way a pack can be unregistered
    while still sitting on disk."""
    skills, error = await _alist_skills_with_errors(_skills_composite(), CRAFT_SKILLS[0])
    assert error is None, f"skills source failed to load: {error}"
    by_name = {s["name"]: s for s in skills}
    assert PACK in by_name, (
        f"{PACK} is not listed by the skills container; agents cannot discover it. "
        f"listed: {sorted(by_name)}"
    )
    assert by_name[PACK]["description"].strip(), "pack has an empty description"


@pytest.mark.parametrize("module_name,func_name", TOOLED_BUILDERS)
async def test_every_tooled_agent_receives_the_pack(
    module_name, func_name, captured_kwargs
):
    """The other half of registration: a listed pack still reaches nobody
    unless each agent registers the container that lists it. Resolving the
    captured sources against the real backend closes the loop -- a builder
    passing some other source set fails here even though the pack loads."""
    module = importlib.import_module(module_name)
    getattr(module, func_name)(
        _FakeSettings(), backend=CanonBackend(read_store=None), tools=[],
    )
    sources = captured_kwargs.get("skills")
    assert sources, f"{func_name} registers no skills at all"
    names: set[str] = set()
    for source in sources:
        skills, error = await _alist_skills_with_errors(_skills_composite(), source)
        assert error is None, f"{func_name}'s source {source} failed to load: {error}"
        names |= {s["name"] for s in skills}
    assert PACK in names, (
        f"{func_name} registers {sources}, which does not surface {PACK}"
    )


# -- content: the capability boundary the schema split just made structural --


def test_pack_has_a_reference_file_like_its_siblings():
    assert "SKILL.md" in _pack_files()
    refs = importlib.resources.files("novelizer.skills_packs") / PACK / "references"
    assert [p.name for p in refs.iterdir()], "pack ships no reference file"


def test_pack_teaches_the_irony_ledger_by_path_and_by_field():
    """Naming the file is not teaching it. `live_chapters` is the field that
    turns the ledger from a report into an instrument, so the pack must name
    it."""
    text = _skill_text()
    assert "/secrets/_dramatic-irony.md" in text
    assert "live_chapters" in text


def _sentences(text: str) -> list[str]:
    """Sentence-ish units, so the assertions below survive prose re-wrapping
    (a line-based split would depend on where the paragraph happens to fold)."""
    flat = " ".join(text.split())
    return [s for s in flat.replace("; ", ". ").split(". ") if s]


def test_plotter_guidance_is_windows_not_planting():
    """PlotterOutput carries resolution_plan_intents and no secret field of any
    kind. Handing the Plotter planting advice in prose would re-open a boundary
    the schema split closed."""
    text = _skill_text()
    assert "resolution_plan_intents" in text, (
        "the pack never names the one secret-adjacent slot the Plotter has"
    )
    plotter = [s for s in _sentences(text) if "Plotter" in s]
    assert plotter, "the pack never addresses the Plotter's capability"
    offers_planting = [s for s in plotter if "secret_plants" in s or "plant a secret" in s]
    assert not offers_planting, (
        "the pack offers the Plotter planting, which PlotterOutput cannot "
        f"express: {offers_planting}"
    )
