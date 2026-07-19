import pytest
from novelizer.canon_fs.backend import READ_ONLY_ERROR, CanonBackend


def test_write_and_edit_refuse_with_intent_message():
    backend = CanonBackend(read_store=None)
    w = backend.write("/chapters/001-x.md", "prose")
    assert w.error == READ_ONLY_ERROR and w.path is None
    e = backend.edit("/chapters/001-x.md", "old", "new")
    assert e.error == READ_ONLY_ERROR and e.path is None


async def test_async_write_and_edit_refuse_too():
    backend = CanonBackend(read_store=None)
    assert (await backend.awrite("/x.md", "c")).error == READ_ONLY_ERROR
    assert (await backend.aedit("/x.md", "a", "b")).error == READ_ONLY_ERROR


def test_upload_download_refuse_per_file():
    backend = CanonBackend(read_store=None)
    ups = backend.upload_files([("/a.md", b"x"), ("/b.md", b"y")])
    assert [u.path for u in ups] == ["/a.md", "/b.md"]
    assert all(u.error == "permission_denied" for u in ups)
    downs = backend.download_files(["/a.md"])
    assert downs[0].error == "permission_denied" and downs[0].content is None


def test_sync_read_surface_names_async_path():
    backend = CanonBackend(read_store=None)
    for method, args in (("ls", ("/",)), ("read", ("/x.md",)),
                         ("grep", ("q",)), ("glob", ("*.md",))):
        with pytest.raises(NotImplementedError, match="a" + method):
            getattr(backend, method)(*args)
