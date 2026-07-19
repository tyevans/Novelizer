from novelizer.tui.widgets.roster import roster_summary


def _row(name, paused=False, running=False, last_error=None):
    return {"name": name, "paused": paused, "running": running, "last_error": last_error}


def test_no_agents():
    assert roster_summary([]) == "no agents"


def test_all_idle_collapses_to_count():
    summary = roster_summary([_row("author"), _row("editor"), _row("retconner")])
    assert summary == "3 agents idle"


def test_running_agent_shown_by_name():
    summary = roster_summary([_row("author", running=True), _row("editor")])
    assert "● author" in summary
    # idle agents are not listed individually
    assert "editor" not in summary


def test_paused_agents_listed():
    summary = roster_summary([_row("author"), _row("editor", paused=True), _row("retconner", paused=True)])
    assert "⏸ editor, retconner" in summary


def test_errored_agent_shown_with_truncated_error():
    summary = roster_summary([_row("author", last_error="x" * 100)])
    assert "⚠ author: " in summary
    assert "x" * 100 not in summary
    assert len(summary) < 80


def test_running_paused_and_error_compose():
    summary = roster_summary([
        _row("author", running=True),
        _row("editor", paused=True),
        _row("continuity_checker", last_error="boom"),
    ])
    assert "● author" in summary
    assert "⏸ editor" in summary
    assert "⚠ continuity_checker: boom" in summary
