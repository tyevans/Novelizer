import pytest
from novelizer.tui.widgets import browser_model


class _Read:
    def __init__(self, *, blueprint=None, beats=None, briefs=None, **empty):
        self._blueprint = blueprint
        self._beats = beats or []
        self._briefs = briefs or []

    async def get_active_blueprint(self):
        return self._blueprint

    async def list_beats(self):
        return self._beats

    async def list_briefs(self, status=None):
        if status:
            return [b for b in self._briefs if b.status == status]
        return self._briefs

    # everything else the sections list touches -> empty
    async def list_chapters(self, status=None): return []
    async def list_characters(self): return []
    async def list_world_entries(self): return []
    async def list_flags(self, status=None): return []
    async def list_threads(self): return []
    async def list_themes(self): return []


class _BP:
    framework = "six-position"; target_chapter_count = 24; genre = "noir"


class _Beat:
    def __init__(self, id, name):
        self.id = id; self.name = name; self.ideal_pct = 0.25
        self.tolerance_pct = 0.1; self.expected_polarity = "positive"
        self.fulfilled_by_chapter_id = ""


class _Brief:
    def __init__(self, id, ordinal, goal):
        self.id = id; self.target_ordinal = ordinal; self.goal = goal
        self.synopsis = "syn"; self.status = "open"; self.pov_character_id = ""
        self.threads_to_touch = []; self.beats_to_hit = []
        self.promises_to_progress = []; self.value_shift = ""; self.planned_outcome = ""


@pytest.mark.asyncio
async def test_outline_section_present_with_blueprint():
    read = _Read(blueprint=_BP(), beats=[_Beat("b1", "Hook")],
                 briefs=[_Brief("br1", 3, "raise the stakes")])
    sections = await browser_model.browser_sections(read, staleness_threshold=3)
    outline = next(s for s in sections if s["key"] == "outline")
    ids = [i["id"] for i in outline["items"]]
    assert "blueprint" in ids
    assert "beat:b1" in ids
    assert "brief:br1" in ids


@pytest.mark.asyncio
async def test_outline_absent_without_blueprint():
    sections = await browser_model.browser_sections(_Read(blueprint=None), staleness_threshold=3)
    assert all(s["key"] != "outline" for s in sections)


@pytest.mark.asyncio
async def test_detail_view_for_beat_and_blueprint():
    read = _Read(blueprint=_BP(), beats=[_Beat("b1", "Hook")], briefs=[])
    bp_view = await browser_model.detail_view(read, "outline", "blueprint")
    assert bp_view is not None and "six-position" in bp_view.body.plain
    beat_view = await browser_model.detail_view(read, "outline", "beat:b1")
    assert beat_view is not None and "Hook" in beat_view.body.plain


@pytest.mark.asyncio
async def test_detail_view_for_brief():
    read = _Read(blueprint=_BP(), beats=[], briefs=[_Brief("br1", 3, "raise the stakes")])
    brief_view = await browser_model.detail_view(read, "outline", "brief:br1")
    assert brief_view is not None and "raise the stakes" in brief_view.body.plain


@pytest.mark.asyncio
async def test_detail_view_missing_returns_none():
    read = _Read(blueprint=_BP(), beats=[], briefs=[])
    assert await browser_model.detail_view(read, "outline", "beat:nope") is None
