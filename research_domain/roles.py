from __future__ import annotations
from substrate import AgentSpec

from research_domain.agents import ExtractorAgent, RetractorAgent, VerifierAgent


def _stub_construct(name: str):
    def _construct(ctx):
        return {"role": name, "context": ctx}
    return _construct


def build_live_agents(runtime, corpus, runner_factory) -> list:
    """Construct the live trio in scheduler order. runner_factory(role_name)
    returns a Runner for that role (real deepagents runner in the CLI,
    fakes in tests)."""
    return [
        ExtractorAgent(runner_factory("extractor"), runtime, corpus),
        VerifierAgent(runner_factory("verifier"), runtime, corpus),
        RetractorAgent(runner_factory("retractor"), runtime),
    ]


ROLE_REGISTRY: list[AgentSpec] = [
    AgentSpec(name="scout", tool_grant=None, construct=_stub_construct("scout")),
    AgentSpec(name="extractor", tool_grant=None, construct=ExtractorAgent),
    AgentSpec(name="verifier", tool_grant=None, construct=VerifierAgent),
    AgentSpec(name="retractor", tool_grant=None, construct=RetractorAgent),
    AgentSpec(name="synthesizer", tool_grant=None, construct=_stub_construct("synthesizer")),
    AgentSpec(name="coverage_analyst", tool_grant=None, construct=_stub_construct("coverage_analyst")),
]
