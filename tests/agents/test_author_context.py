"""Author context assembly: what is pushed vs what must be pulled.

The Author is asked to return character_ids and to continue from the last
chapter's final moment. Both were unsupported by the pushed context: the cast
block showed names without ids, and prior chapters arrived as a 200-character
head slice that cannot show how a chapter ended.

See docs/agent-prompting/proposal-author.md §3.
"""
from __future__ import annotations

from novelizer.agents.author import _summarize
from novelizer.store.models import Chapter, Character


def _ctx(**over):
    ctx = {
        "world": [], "characters": [], "previous": [], "chapters": [], "signals": [],
        "threads": [], "secrets": [], "knowledge_matrix": {}, "themes": [],
        "causal_edges": [], "promises": [], "hand": None, "brief": None,
    }
    ctx.update(over)
    return ctx


class TestCastBlockCarriesIds:
    def test_character_line_shows_the_id_the_draft_must_cite(self):
        """character_ids is a required output field; without ids in the pushed
        cast the Author has to invent or omit them."""
        chars = [Character(id="mara-vance", name="Mara Vance", traits="wry", arc_status="rising")]
        sent = _summarize(_ctx(characters=chars))
        assert "Mara Vance (id:mara-vance)" in sent

    def test_still_renders_traits_and_arc(self):
        chars = [Character(id="m", name="M", traits="wry", arc_status="rising")]
        sent = _summarize(_ctx(characters=chars))
        assert "wry" in sent and "rising" in sent

    def test_empty_cast_unchanged(self):
        assert "Characters:\nNone yet." in _summarize(_ctx())


class TestPriorChapterFidelity:
    def test_push_mode_shows_the_end_of_the_last_chapter(self):
        """A head slice hides the ending the next chapter must continue from."""
        prose = "OPENING. " + ("middle " * 200) + "FINAL MOMENT."
        chs = [Chapter(id="c1", title="One", prose=prose)]
        sent = _summarize(_ctx(previous=chs, chapters=chs))
        assert "FINAL MOMENT." in sent

    def test_most_recent_chapter_gets_more_room_than_older_ones(self):
        older = Chapter(id="c1", title="One", prose="A" * 4000)
        latest = Chapter(id="c2", title="Two", prose="B" * 4000)
        sent = _summarize(_ctx(previous=[older, latest], chapters=[older, latest]))
        assert sent.count("B") > sent.count("A")

    def test_pull_mode_pushes_the_index_and_no_prose(self):
        chs = [Chapter(id="c1", title="One", prose="SECRET PROSE")]
        sent = _summarize(_ctx(previous=chs, chapters=chs), pull_mode=True)
        assert "SECRET PROSE" not in sent
        assert "Chapter index:" in sent
        assert "ch001" in sent
