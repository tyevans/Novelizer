from hypothesis import given, strategies as st
from tui_kit.widgets.roster import (
    ALARM_STYLE, ERROR_MARK, IDLE_MARK, PAUSED_MARK, RUNNING_MARK, WAITING_MARK,
    hold_phrase, roster_glyphs, roster_summary,
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


def _row(name, paused=False, running=False, last_error=None, waiting_on_pool=False):
    return {"name": name, "paused": paused, "running": running, "last_error": last_error,
            "waiting_on_pool": waiting_on_pool,
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


def test_agent_queued_on_the_llm_pool_carries_its_own_mark():
    """A run frozen behind the shared LLM permit is neither working nor idle.
    Showing it as a spinner makes a 429 pile-up read as a hung agent; showing it
    as idle hides it entirely."""
    strip = roster_glyphs([_row("author", waiting_on_pool=True), _row("editor")], THEME)
    assert strip.plain == f"✎{WAITING_MARK} §{IDLE_MARK}"


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


# --- hold_phrase: WHY an agent is not producing -----------------------------
#
# The glyph mark says "not running"; the phrase says why, and what the agent is
# waiting on. Same precedence chain as _mark, one string per row, terse enough
# for a dense status pane.

def _held(name, **kw):
    row = _row(name, **{k: v for k, v in kw.items()
                        if k in ("paused", "running", "last_error", "waiting_on_pool")})
    row["hold_reason"] = kw.get("hold_reason")
    row["hold_seconds"] = kw.get("hold_seconds", 0.0)
    return row


def test_a_running_agent_has_no_hold_phrase():
    assert hold_phrase(_held("author", running=True)) == ""


def test_pool_wait_is_named_as_such():
    """The 429 case: the agent was dispatched and is queued behind the shared
    LLM permit. Nothing it does will change that -- the pool has to drain."""
    assert hold_phrase(_held("author", waiting_on_pool=True)) == "waiting on LLM pool permit"


def test_pool_wait_outranks_a_stale_error():
    """last_error is the PREVIOUS run's; a dispatched run queued on a permit is
    the agent's state now."""
    assert hold_phrase(_held("author", waiting_on_pool=True, last_error="boom")) == (
        "waiting on LLM pool permit")


def test_paused_agent_says_paused():
    assert hold_phrase(_held("author", paused=True)) == "paused"


def test_fail_backoff_names_the_error_and_the_retry():
    assert hold_phrase(_held("author", last_error="Timeout: nope",
                             hold_reason="backing off", hold_seconds=12.4)) == (
        "backing off after error · retry in 12s")


def test_idle_ladder_says_what_it_is_awaiting():
    """Not a countdown to a scheduled run -- dispatch is progress-driven. The
    agent is waiting for the story to change; the seconds are only when it will
    next look."""
    assert hold_phrase(_held("author", hold_reason="awaiting progress",
                             hold_seconds=45.0)) == (
        "awaiting story progress · rechecks in 45s")


def test_unheld_agent_is_waiting_for_a_dispatch_slot():
    """Nothing is holding it: the room is simply busier than the dispatch cap,
    which is progress, not a wedge."""
    assert hold_phrase(_held("author")) == "ready · waiting for a dispatch slot"


def test_hold_phrase_tolerates_a_status_row_without_the_hold_fields():
    assert hold_phrase(_row("author")) == "ready · waiting for a dispatch slot"


# --- fleet_hold_summary: the whole fleet's holds in one line -----------------
#
# The per-agent panes are gone, so there is no longer a per-agent surface for
# the hold phrase. One vitals line has to carry it for everyone: a
# rate-limited fleet, a crash loop and a converged fleet must not look alike.

def test_fleet_hold_summary_is_empty_when_everyone_is_running():
    from tui_kit.widgets.roster import fleet_hold_summary
    assert fleet_hold_summary([_held("author", running=True),
                               _held("editor", running=True)]) == ""


def test_fleet_hold_summary_counts_agents_sharing_a_reason():
    from tui_kit.widgets.roster import fleet_hold_summary
    out = fleet_hold_summary([_held("author", paused=True), _held("editor", paused=True),
                              _held("plotter", waiting_on_pool=True)])
    assert "2× paused" in out
    assert "waiting on LLM pool permit" in out


def test_fleet_hold_summary_of_an_empty_roster_is_empty():
    from tui_kit.widgets.roster import fleet_hold_summary
    assert fleet_hold_summary([]) == ""
