def test_curator_registered_and_settings_present():
    from novelizer.agents.registry import AGENT_REGISTRY
    from novelizer.settings.models import EffectiveSettings

    names = [spec.name for spec in AGENT_REGISTRY]
    assert "curator" in names

    s = EffectiveSettings()
    assert s.curator_tools_enabled is True
    assert s.curator_subagent_enabled is False
