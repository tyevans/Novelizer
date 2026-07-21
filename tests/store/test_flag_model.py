from novelizer.store.models import Flag


def test_flag_defaults_severity_escalated_failed_attempts():
    flag = Flag(category="contradiction", description="x")
    assert flag.severity is None
    assert flag.escalated is False
    assert flag.failed_attempts == 0


def test_flag_severity_accepts_known_values():
    for sev in ("minor", "major", "critical"):
        flag = Flag(category="contradiction", description="x", severity=sev)
        assert flag.severity == sev
