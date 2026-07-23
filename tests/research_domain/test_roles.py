from research_domain.agents import ExtractorAgent, RetractorAgent, VerifierAgent
from research_domain.roles import ROLE_REGISTRY, build_live_agents


def test_role_registry_has_six_roles_in_declared_order():
    names = [spec.name for spec in ROLE_REGISTRY]
    assert names == ["scout", "extractor", "verifier", "retractor", "synthesizer", "coverage_analyst"]


def test_every_stub_role_construct_is_callable_and_returns_something():
    stub_names = {"scout", "synthesizer", "coverage_analyst"}
    for spec in ROLE_REGISTRY:
        if spec.name in stub_names:
            result = spec.construct(None)
            assert result is not None


def test_no_role_has_a_tool_grant_in_this_proof():
    assert all(spec.tool_grant is None for spec in ROLE_REGISTRY)


def test_live_trio_construct_is_the_real_agent_class():
    by_name = {spec.name: spec for spec in ROLE_REGISTRY}
    assert by_name["extractor"].construct is ExtractorAgent
    assert by_name["verifier"].construct is VerifierAgent
    assert by_name["retractor"].construct is RetractorAgent


class _NullRunner:
    async def ainvoke(self, inputs: dict) -> dict:
        return {}


def test_build_live_agents_returns_wired_trio(tmp_path):
    class StubRuntime:  # construction wiring only, no I/O
        pass
    agents = build_live_agents(StubRuntime(), object(), lambda role: _NullRunner())
    assert [type(a) for a in agents] == [ExtractorAgent, VerifierAgent, RetractorAgent]
    assert [a.name for a in agents] == ["extractor", "verifier", "retractor"]
