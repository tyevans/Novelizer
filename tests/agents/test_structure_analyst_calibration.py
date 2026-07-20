"""The Structure Analyst has to score on a stable scale across passes.

Its scores feed detect_sag_spike, which flags any chapter far from the running
average of ALL scores, and the Editor acts on those flags. So drift in the
Analyst's internal scale between passes manufactures pacing alarms about
chapters nobody wrote badly. It can only hold a scale it can see, so already-
scored chapters are pushed as calibration anchors.

See docs/agent-prompting/proposal-structure-analyst.md §1, §3.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from novelizer.agents.schemas import StructureAnalystOutput
from novelizer.agents.structure_analyst import StructureAnalyst
from novelizer.canon.committer import Committer
from novelizer.canon.event_store import EventStore
from novelizer.canon.events import AnnotationStructureScored, EventType
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.store.models import Chapter


class FakeRunner:
    def __init__(self):
        self.calls = []

    async def ainvoke(self, inputs):
        self.calls.append(inputs)
        return {"structured_response": StructureAnalystOutput()}


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    events = EventStore(path)
    await events.init()
    proj = Projector(events, path)
    await proj.init()
    read = ReadStore(path)
    await read.init()
    yield events, proj, read, Committer(events)
    await read.close()
    await proj.close()
    await events.close()
    os.unlink(path)


async def _prompt(read, committer, runner, pull_mode=False):
    analyst = StructureAnalyst(runner, read, committer, pull_mode=pull_mode)
    ctx = await analyst.poll()
    await analyst.work(ctx)
    return runner.calls[-1]["messages"][0]["content"]


class TestCalibrationAnchors:
    async def test_previously_scored_chapters_are_pushed_as_anchors(self, stack):
        events, proj, read, committer = stack
        for i in (1, 2):
            await events.append(
                EventType.CHAPTER_CREATED, f"c{i}", Chapter(id=f"c{i}", title=f"T{i}", prose="p"),
            )
        await events.append(
            EventType.ANNOTATION_STRUCTURE_SCORED, "c1",
            AnnotationStructureScored(chapter_id="c1", tension=0.4, pacing_label="rising"),
        )
        await proj.catch_up()
        sent = await _prompt(read, committer, FakeRunner())
        assert "0.4" in sent and "rising" in sent
        assert "T1" in sent

    async def test_no_anchor_block_on_the_first_pass(self, stack):
        events, proj, read, committer = stack
        await events.append(
            EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="T1", prose="p"),
        )
        await proj.catch_up()
        sent = await _prompt(read, committer, FakeRunner())
        assert "Already scored" not in sent

    async def test_anchors_are_capped_to_the_nearest_few(self, stack):
        """Anchors are for calibration, not a transcript of every score."""
        events, proj, read, committer = stack
        for i in range(1, 12):
            await events.append(
                EventType.CHAPTER_CREATED, f"c{i}", Chapter(id=f"c{i}", title=f"T{i}", prose="p"),
            )
            if i <= 9:
                await events.append(
                    EventType.ANNOTATION_STRUCTURE_SCORED, f"c{i}",
                    AnnotationStructureScored(chapter_id=f"c{i}", tension=0.5, pacing_label="steady"),
                )
        await proj.catch_up()
        sent = await _prompt(read, committer, FakeRunner())
        anchor_block = sent.split("Already scored")[1] if "Already scored" in sent else ""
        assert anchor_block.count("tension") <= 5


class TestPullMode:
    async def test_pull_mode_pushes_titles_not_prose(self, stack):
        events, proj, read, committer = stack
        await events.append(
            EventType.CHAPTER_CREATED, "c1",
            Chapter(id="c1", title="T1", prose="THE WHOLE ARC IS IN HERE"),
        )
        await proj.catch_up()
        sent = await _prompt(read, committer, FakeRunner(), pull_mode=True)
        assert "THE WHOLE ARC" not in sent
        assert "c1" in sent

    async def test_push_mode_still_inlines_an_excerpt(self, stack):
        events, proj, read, committer = stack
        await events.append(
            EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="T1", prose="EXCERPT HERE"),
        )
        await proj.catch_up()
        sent = await _prompt(read, committer, FakeRunner())
        assert "EXCERPT HERE" in sent


class TestRubricIsInThePrompt:
    def test_prompt_anchors_the_scale_with_bands(self):
        from novelizer.agents.structure_analyst import SYSTEM_PROMPT

        for band in ("0.0-0.2", "0.3-0.4", "0.5-0.6", "0.7-0.8", "0.9-1.0"):
            assert band in SYSTEM_PROMPT

    def test_prompt_warns_against_scoring_length(self):
        from novelizer.agents.structure_analyst import SYSTEM_PROMPT

        assert "not tenser" in SYSTEM_PROMPT or "word count" in SYSTEM_PROMPT
