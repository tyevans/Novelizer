import pytest
from novelizer.store.models import (
    ArcRecord, BeatRecord, BlueprintRecord, CausalEdgeRecord, Chapter, PromiseRecord, PromiseState,
    SecretReferenceRecord, ThreadRecord, ThreadState,
)
from novelizer.research import tools


class _FakeReadStore:
    def __init__(
        self, *, threads=None, chapters=None, secret_refs=None, matrix=None, edges=None,
        promises=None, blueprint=None, beats=None, arcs=None,
    ):
        self._threads = threads or []
        self._chapters = chapters or []
        self._secret_refs = secret_refs or []
        self._matrix = matrix or {}
        self._edges = edges or []
        self._promises = promises or []
        self._blueprint = blueprint
        self._beats = beats or []
        self._arcs = arcs or []

    async def list_threads(self): return self._threads
    async def list_chapters(self, status=None): return self._chapters
    async def list_secret_references(self, secret_id=None): return self._secret_refs
    async def knowledge_matrix(self): return self._matrix
    async def list_causal_edges(self): return self._edges
    async def list_promises(self): return self._promises
    async def get_active_blueprint(self): return self._blueprint
    async def list_beats(self): return self._beats
    async def list_arcs(self, active_only=False): return self._arcs


def _chapters(n):
    return [Chapter(id=f"c{i}", title=str(i), prose="p") for i in range(n)]


@pytest.mark.asyncio
async def test_check_stale_threads_reports_none_when_nothing_stale():
    read = _FakeReadStore(chapters=_chapters(1))
    result = await tools.check_stale_threads(read)
    assert result == "No stale threads."


@pytest.mark.asyncio
async def test_check_stale_threads_lists_stale_thread_ids():
    chs = _chapters(5)
    thread = ThreadRecord(id="t1", name="The Debt", state=ThreadState.planted, last_chapter_id="c0")
    read = _FakeReadStore(threads=[thread], chapters=chs)
    result = await tools.check_stale_threads(read)
    assert "t1" in result and "The Debt" in result


@pytest.mark.asyncio
async def test_check_leaks_reports_none_when_no_leaks():
    read = _FakeReadStore()
    result = await tools.check_leaks(read)
    assert result == "No leaks found."


@pytest.mark.asyncio
async def test_check_leaks_lists_a_leak():
    ref = SecretReferenceRecord(secret_id="s1", character_id="char1", chapter_id="c1")
    read = _FakeReadStore(secret_refs=[ref], matrix={})
    result = await tools.check_leaks(read)
    assert "s1" in result and "char1" in result


@pytest.mark.asyncio
async def test_check_paradoxes_reports_none_when_no_paradoxes():
    read = _FakeReadStore()
    result = await tools.check_paradoxes(read)
    assert result == "No paradoxes found."


@pytest.mark.asyncio
async def test_check_paradoxes_lists_an_ordering_violation():
    chs = _chapters(3)  # c0, c1, c2
    edge = CausalEdgeRecord(cause_chapter_id="c2", effect_chapter_id="c0")
    read = _FakeReadStore(edges=[edge], chapters=chs)
    result = await tools.check_paradoxes(read)
    assert "c2" in result and "c0" in result


@pytest.mark.asyncio
async def test_check_promise_ledger_reports_none_when_empty():
    read = _FakeReadStore(chapters=_chapters(1))
    result = await tools.check_promise_ledger(read)
    assert result == "No overdue or due promises."


@pytest.mark.asyncio
async def test_check_promise_ledger_lists_overdue_promise():
    chs = _chapters(10)
    promise = PromiseRecord(
        id="p1", name="The Letter", state=PromiseState.open, window_lo=1, window_hi=3,
    )
    read = _FakeReadStore(promises=[promise], chapters=chs)
    result = await tools.check_promise_ledger(read)
    assert "p1" in result and "OVERDUE" in result


@pytest.mark.asyncio
async def test_check_beat_drift_reports_none_without_a_blueprint():
    read = _FakeReadStore(chapters=_chapters(1))
    result = await tools.check_beat_drift(read)
    assert result == "No beat drift (no adopted blueprint)."


@pytest.mark.asyncio
async def test_check_beat_drift_lists_a_late_beat():
    chs = _chapters(20)
    blueprint = BlueprintRecord(id="bp1", framework="three-act", target_chapter_count=20)
    beat = BeatRecord(
        id="b1", blueprint_id="bp1", slug="midpoint", name="Midpoint",
        ideal_pct=0.1, tolerance_pct=0.05,
    )
    read = _FakeReadStore(blueprint=blueprint, beats=[beat], chapters=chs)
    result = await tools.check_beat_drift(read)
    assert "Midpoint" in result and "late" in result.lower()


@pytest.mark.asyncio
async def test_check_completion_reports_no_blueprint():
    read = _FakeReadStore()
    result = await tools.check_completion(read)
    assert result == "No adopted blueprint yet."


@pytest.mark.asyncio
async def test_check_completion_reports_incomplete_status():
    chs = _chapters(3)
    blueprint = BlueprintRecord(id="bp1", framework="three-act", target_chapter_count=10)
    beat = BeatRecord(
        id="b1", blueprint_id="bp1", slug="midpoint", name="Midpoint",
        ideal_pct=0.5, tolerance_pct=0.1,
    )
    read = _FakeReadStore(blueprint=blueprint, beats=[beat], chapters=chs)
    result = await tools.check_completion(read)
    assert "not complete" in result.lower()
    assert "Midpoint" in result
