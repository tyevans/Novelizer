from research_domain.roles import ROLE_REGISTRY


def test_role_registry_has_six_roles_in_declared_order():
    names = [spec.name for spec in ROLE_REGISTRY]
    assert names == ["scout", "extractor", "verifier", "retractor", "synthesizer", "coverage_analyst"]


def test_every_role_construct_is_callable_and_returns_something():
    for spec in ROLE_REGISTRY:
        result = spec.construct(None)
        assert result is not None


def test_no_role_has_a_tool_grant_in_this_proof():
    assert all(spec.tool_grant is None for spec in ROLE_REGISTRY)
