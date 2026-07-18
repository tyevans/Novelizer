from novelizer.canon.autonomy import Proposal
from novelizer.tui.widgets.proposals_model import proposal_line, pending_lines


def test_proposal_line_renders_id_agent_and_target():
    p = Proposal(id="abcdef12-0000", proposing_agent="author",
                 target_event_type="chapter.created", target_aggregate_id="c1", payload={})
    line = proposal_line(p)
    assert "abcdef12" in line and "author" in line and "chapter.created" in line


class FakeRead:
    def __init__(self, proposals):
        self._proposals = proposals

    async def list_proposals(self, status=None):
        return [p for p in self._proposals if status is None or p.status.value == status]


async def test_pending_lines_lists_open_proposals():
    p1 = Proposal(proposing_agent="author", target_event_type="chapter.created",
                  target_aggregate_id="c1", payload={})
    p2 = Proposal(proposing_agent="editor", target_event_type="chapter.status_changed",
                  target_aggregate_id="c2", payload={})
    p2 = p2.model_copy(update={"status": p2.status.approved})
    lines = await pending_lines(FakeRead([p1, p2]))
    assert len(lines) == 1
    assert "author" in lines[0]


async def test_pending_lines_empty_when_none_open():
    lines = await pending_lines(FakeRead([]))
    assert lines == []
