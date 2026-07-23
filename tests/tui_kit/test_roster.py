from hypothesis import given, strategies as st
from tui_kit.widgets.roster import (
    ALARM_STYLE, ERROR_MARK, IDLE_MARK, PAUSED_MARK, RUNNING_MARK,
    roster_glyphs, roster_summary,
)


class _FakeTheme:
    _STYLES = {"author": "gold3", "editor": "medium_purple"}
    _GLYPHS = {"author": "✎", "editor": "§"}

    def glyph(self, agent_name):
        return self._GLYPHS.get(agent_name, "?")

    def label(self, agent_name):
        return agent_name.title()

    def style(self, agent_name):
        return self._STYLES.get(agent_name, "dim")

    def verb(self, agent_name):
        return "working"


THEME = _FakeTheme()
CAST = ("author", "editor")


def _row(name, paused=False, running=False, last_error=None):
    return {"name": name, "paused": paused, "running": running, "last_error": last_error,
            "last_completed": False, "run_count": 0, "next_ready_in": 0.0}


def test_no_agents_renders_dim_placeholder():
    strip = roster_glyphs([], THEME)
    assert strip.plain == "no agents"
    assert str(strip.style) == "dim"
    assert roster_summary([], THEME) == "no agents"


def test_idle_cast_renders_every_glyph_with_idle_mark():
    strip = roster_glyphs([_row(n) for n in CAST], THEME)
    assert strip.plain == "✎· §·"


def test_running_agent_carries_spinner_mark():
    strip = roster_glyphs([_row("author", running=True), _row("editor")], THEME)
    assert strip.plain == f"✎{RUNNING_MARK} §{IDLE_MARK}"


def test_paused_agent_carries_pause_mark():
    strip = roster_glyphs([_row("editor", paused=True)], THEME)
    assert strip.plain == f"§{PAUSED_MARK}"


def test_errored_agent_carries_alarm_mark_without_error_text():
    strip = roster_glyphs([_row("author", last_error="RuntimeError: boom" * 10)], THEME)
    assert strip.plain == f"✎{ERROR_MARK}"
    assert "boom" not in strip.plain


def test_error_wins_over_paused_and_running():
    strip = roster_glyphs([_row("author", paused=True, running=True, last_error="x")], THEME)
    assert strip.plain == f"✎{ERROR_MARK}"


def test_paused_wins_over_running():
    strip = roster_glyphs([_row("author", paused=True, running=True)], THEME)
    assert strip.plain == f"✎{PAUSED_MARK}"


def test_glyph_takes_agent_style_and_error_mark_takes_alarm_style():
    strip = roster_glyphs([_row("author", last_error="x")], THEME)
    styles = [(strip.plain[s.start:s.end], str(s.style)) for s in strip.spans]
    assert ("✎", "gold3") in styles
    assert (ERROR_MARK, ALARM_STYLE) in styles


@given(
    st.lists(
        st.tuples(st.sampled_from(CAST), st.booleans(), st.booleans(),
                  st.one_of(st.none(), st.just("err"))),
        max_size=8,
    )
)
def test_summary_is_the_plain_strip_and_one_cell_pair_per_agent(rows):
    status = [_row(n, paused=p, running=r, last_error=e) for n, p, r, e in rows]
    strip = roster_glyphs(status, THEME)
    assert roster_summary(status, THEME) == strip.plain
    if status:
        assert len(strip.plain) == 3 * len(status) - 1
