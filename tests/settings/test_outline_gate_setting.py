from novelizer.settings.models import EffectiveSettings


def test_outline_gate_enabled_defaults_true():
    assert EffectiveSettings().outline_gate_enabled is True


def test_outline_gate_can_be_disabled():
    assert EffectiveSettings(outline_gate_enabled=False).outline_gate_enabled is False
