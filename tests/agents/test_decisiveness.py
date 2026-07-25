"""Every registered LLM agent carries decisiveness guidance in its prompt.

A measured fleet review found agents re-litigating choices they had already
made with evidence: one World Architect run named the entries it meant to write
and then spent the rest of its pass re-asking whether to write them. The
complementary half of that fix (the dominant half is a retrieval bug, fixed
elsewhere) is per-agent text naming the stopping condition for deliberation.

The sweep derives from AGENT_REGISTRY rather than a hand-listed roster -- the
same reason tests/agents/tooled_builders.py does: a hand-maintained copy is how
the Curator once shipped unswept. An agent registered without either a
DECISIVENESS_NOTE or an entry in DECISIVENESS_EXEMPT fails here.
"""
from __future__ import annotations

import importlib

import pytest

from novelizer.agents.prompts import DECISIVENESS_EXEMPT
from novelizer.agents.registry import AGENT_REGISTRY

# Convention every agent module follows: the prompt constant is SYSTEM_PROMPT,
# except the two whose prompts predate that convention.
_PROMPT_ATTRS = ("SYSTEM_PROMPT", "AUTHOR_SYSTEM_PROMPT", "PLOTTER_SYSTEM_PROMPT")

GUIDED = [spec.name for spec in AGENT_REGISTRY if spec.name not in DECISIVENESS_EXEMPT]


def _module(name: str):
    return importlib.import_module(f"novelizer.agents.{name}")


def _system_prompt(module) -> str:
    for attr in _PROMPT_ATTRS:
        prompt = getattr(module, attr, None)
        if prompt is not None:
            return prompt
    raise AssertionError(f"{module.__name__} exposes none of {_PROMPT_ATTRS}")


def test_every_registered_agent_is_guided_or_exempt():
    """The point of the sweep: a new agent cannot join the fleet silently."""
    assert GUIDED, "registry produced no guided agents -- derivation is broken"
    for spec in AGENT_REGISTRY:
        assert spec.name in DECISIVENESS_EXEMPT or getattr(
            _module(spec.name), "DECISIVENESS_NOTE", None
        ), (
            f"{spec.name} is registered with no DECISIVENESS_NOTE and no justified "
            "entry in DECISIVENESS_EXEMPT"
        )


@pytest.mark.parametrize("name", GUIDED)
def test_note_reaches_the_system_prompt(name):
    """A constant nobody concatenates is guidance the model never sees."""
    module = _module(name)
    assert module.DECISIVENESS_NOTE in _system_prompt(module)


@pytest.mark.parametrize("name", GUIDED)
def test_note_names_a_stopping_condition(name):
    """"Be decisive" is exhortation. The useful shape is when deliberation is
    over, so each note must open its own section and say what settles it."""
    note = _module(name).DECISIVENESS_NOTE
    assert note.startswith("\n\n## "), "notes are appended sections, not loose sentences"
    assert 200 < len(note) < 1200, f"{name}'s note is {len(note)} chars -- too terse or a lecture"


def test_notes_are_tailored_not_boilerplate():
    """The failure mode differs by lane: a generative agent should execute the
    choice it made, while a judgement agent's hesitation before escalating to a
    human is sometimes correct. One pasted line cannot say both."""
    notes = [_module(name).DECISIVENESS_NOTE for name in GUIDED]
    assert len(set(notes)) == len(notes), "at least two agents share a note verbatim"


def test_exemptions_are_justified_and_current():
    for name, reason in DECISIVENESS_EXEMPT.items():
        assert name in {spec.name for spec in AGENT_REGISTRY}, (
            f"{name} is exempt but no longer registered -- stale exemption"
        )
        assert len(reason) > 40, f"{name}'s exemption is asserted, not reasoned"
