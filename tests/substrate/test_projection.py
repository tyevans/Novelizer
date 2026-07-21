import pytest
from substrate.projection import ProjectionCatalog, ProjectionSpec


class _FakeEvent:
    def __init__(self, fingerprint: str, chapter_id: str) -> None:
        self.fingerprint = fingerprint
        self.chapter_id = chapter_id


def test_register_and_invalidate_marks_key_dirty_for_recompute():
    catalog = ProjectionCatalog()
    recomputed_calls = []

    def _recompute(key: str):
        recomputed_calls.append(key)
        return f"view-for-{key}"

    catalog.register(ProjectionSpec(
        name="kg_shape",
        invalidation_key=lambda event: event.fingerprint,
        recompute=_recompute,
    ))
    catalog.invalidate("kg_shape", _FakeEvent(fingerprint="fp-1", chapter_id="ch-1"))
    result = catalog.recompute_dirty("kg_shape")
    assert result == {"fp-1": "view-for-fp-1"}
    assert recomputed_calls == ["fp-1"]


def test_recompute_dirty_clears_dirtiness_so_second_call_is_empty():
    catalog = ProjectionCatalog()
    catalog.register(ProjectionSpec(
        name="canon_fs_shape",
        invalidation_key=lambda event: event.chapter_id,
        recompute=lambda key: f"render-of-{key}",
    ))
    catalog.invalidate("canon_fs_shape", _FakeEvent(fingerprint="fp-2", chapter_id="ch-2"))
    first = catalog.recompute_dirty("canon_fs_shape")
    second = catalog.recompute_dirty("canon_fs_shape")
    assert first == {"ch-2": "render-of-ch-2"}
    assert second == {}


def test_two_invalidations_of_same_key_only_recompute_once():
    catalog = ProjectionCatalog()
    calls = []
    catalog.register(ProjectionSpec(
        name="canon_fs_shape",
        invalidation_key=lambda event: event.chapter_id,
        recompute=lambda key: calls.append(key) or f"render-of-{key}",
    ))
    catalog.invalidate("canon_fs_shape", _FakeEvent(fingerprint="fp-3", chapter_id="ch-3"))
    catalog.invalidate("canon_fs_shape", _FakeEvent(fingerprint="fp-4", chapter_id="ch-3"))
    result = catalog.recompute_dirty("canon_fs_shape")
    assert result == {"ch-3": "render-of-ch-3"}
    assert calls == ["ch-3"]


def test_invalidate_unregistered_projection_raises_keyerror():
    catalog = ProjectionCatalog()
    with pytest.raises(KeyError):
        catalog.invalidate("nope", _FakeEvent(fingerprint="fp-5", chapter_id="ch-5"))


def test_two_projections_track_dirtiness_independently():
    catalog = ProjectionCatalog()
    catalog.register(ProjectionSpec(
        name="kg_shape", invalidation_key=lambda e: e.fingerprint, recompute=lambda k: k,
    ))
    catalog.register(ProjectionSpec(
        name="canon_fs_shape", invalidation_key=lambda e: e.chapter_id, recompute=lambda k: k,
    ))
    catalog.invalidate("kg_shape", _FakeEvent(fingerprint="fp-6", chapter_id="ch-6"))
    assert catalog.recompute_dirty("canon_fs_shape") == {}
    assert catalog.recompute_dirty("kg_shape") == {"fp-6": "fp-6"}
