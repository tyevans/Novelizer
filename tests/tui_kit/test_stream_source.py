import pytest
from tui_kit.run_model import ProseBlock, ToolBlock
from tui_kit.stream_source import InMemoryStreamSource, StreamSource


def test_in_memory_source_satisfies_the_protocol():
    assert isinstance(InMemoryStreamSource([], {}), StreamSource)


@pytest.mark.asyncio
async def test_page_before_returns_blocks_below_the_cursor():
    blocks = [ToolBlock(tool_name="t", input_summary="", sequence=s) for s in (1, 2, 3, 4)]
    src = InMemoryStreamSource(blocks, {})
    assert [b.sequence for b in await src.page_before(4, limit=2)] == [2, 3]


@pytest.mark.asyncio
async def test_page_before_at_the_beginning_is_empty():
    src = InMemoryStreamSource([ToolBlock(tool_name="t", input_summary="", sequence=1)], {})
    assert await src.page_before(1, limit=10) == []


@pytest.mark.asyncio
async def test_fetch_output_returns_the_full_untruncated_payload():
    src = InMemoryStreamSource([], {7: "x" * 9000})
    assert await src.fetch_output(7) == "x" * 9000


@pytest.mark.asyncio
async def test_fetch_output_of_an_unknown_sequence_is_empty():
    assert await InMemoryStreamSource([], {}).fetch_output(99) == ""
