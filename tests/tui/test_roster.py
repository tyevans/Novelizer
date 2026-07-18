from novelizer.tui.widgets.roster import roster_line


def test_running_agent_marked():
    line = roster_line({"name": "author", "paused": False, "running": True})
    assert "author" in line and "running" in line


def test_paused_agent_marked():
    line = roster_line({"name": "editor", "paused": True, "running": False})
    assert "editor" in line and "paused" in line


def test_idle_agent():
    line = roster_line({"name": "retconner", "paused": False, "running": False})
    assert "retconner" in line and "idle" in line
