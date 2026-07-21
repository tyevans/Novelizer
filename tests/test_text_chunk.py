from novelizer.text_chunk import chunk_prose


def test_short_text_is_a_single_chunk():
    assert chunk_prose("short text", chunk_chars=100, overlap=10) == ["short text"]


def test_long_text_splits_into_overlapping_chunks():
    text = "a" * 250
    chunks = chunk_prose(text, chunk_chars=100, overlap=20)
    assert len(chunks) > 1
    assert all(len(c) <= 100 for c in chunks)
    # overlap: end of one chunk reappears at the start of the next
    assert chunks[0][-20:] == chunks[1][:20]
    # reassembling with overlap removed reconstructs the original text
    assert chunks[0] + "".join(c[20:] for c in chunks[1:]) == text


def test_chunk_exactly_at_boundary_is_a_single_chunk():
    text = "a" * 100
    assert chunk_prose(text, chunk_chars=100, overlap=10) == [text]
