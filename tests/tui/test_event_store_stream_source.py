import pytest
from novelizer.canon.event_store import EventStore
from novelizer.telemetry.events import TelemetryEventType
from novelizer.tui.event_store_stream_source import EventStoreStreamSource
from novelizer.tui.telemetry_adapter import to_contract_event


@pytest.mark.asyncio
async def test_fetch_output_reads_the_full_output_back_off_disk(tmp_path):
    store = EventStore(str(tmp_path / "telemetry.db"))
    await store.init()
    try:
        stored = await store.append_raw(
            TelemetryEventType.TOOL_CALL_FINISHED, "r1",
            {"run_id": "r1", "agent_name": "author", "tool_name": "read_file",
             "duration_s": 1.0, "input_summary": "ch1.md", "output_summary": "y" * 9000})
        src = EventStoreStreamSource(store, to_contract_event)
        assert await src.fetch_output(stored.sequence) == "y" * 9000
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_page_before_folds_stored_events_into_blocks(tmp_path):
    store = EventStore(str(tmp_path / "telemetry.db"))
    await store.init()
    try:
        await store.append_raw(
            TelemetryEventType.TOOL_CALL_STARTED, "r1",
            {"run_id": "r1", "agent_name": "author", "tool_name": "read_file",
             "input_summary": "ch1.md"})
        last = await store.append_raw(
            TelemetryEventType.TOOL_CALL_FINISHED, "r1",
            {"run_id": "r1", "agent_name": "author", "tool_name": "read_file",
             "duration_s": 1.0, "input_summary": "ch1.md", "output_summary": "done"})
        src = EventStoreStreamSource(store, to_contract_event)
        blocks = await src.page_before(last.sequence + 1, limit=50)
        assert any(getattr(b, "tool_name", "") == "read_file" for b in blocks)
    finally:
        await store.close()
