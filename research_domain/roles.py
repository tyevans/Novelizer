from __future__ import annotations
from substrate.agent_registry import AgentSpec


def _stub_construct(name: str):
    def _construct(ctx):
        return {"role": name, "context": ctx}
    return _construct


ROLE_REGISTRY: list[AgentSpec] = [
    AgentSpec(name="scout", tool_grant=None, construct=_stub_construct("scout")),
    AgentSpec(name="extractor", tool_grant=None, construct=_stub_construct("extractor")),
    AgentSpec(name="verifier", tool_grant=None, construct=_stub_construct("verifier")),
    AgentSpec(name="retractor", tool_grant=None, construct=_stub_construct("retractor")),
    AgentSpec(name="synthesizer", tool_grant=None, construct=_stub_construct("synthesizer")),
    AgentSpec(name="coverage_analyst", tool_grant=None, construct=_stub_construct("coverage_analyst")),
]
