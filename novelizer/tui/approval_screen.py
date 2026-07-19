"""The approval queue as a modal drill-in (spec Zone 3). Thin shell: every
rendered row and context comes from the pure proposals_model functions, and
approve/reject go ONLY through commands.dispatch — via the app's
_run_command, so the result line lands in the feed and app.messages exactly
like a typed ':approve <id>'. ProposalService/Committer are never called
from here."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from novelizer.tui.widgets.proposals_model import proposal_context, proposal_row


class ApprovalScreen(ModalScreen):
    """List of open proposals + full payload context for the highlighted row.
    enter = approve (OptionList's select), x = reject, escape = close."""

    BINDINGS = [
        ("escape", "close", "Close"),
        ("x", "reject", "Reject"),
    ]

    def __init__(self, runtime) -> None:
        super().__init__()
        self.runtime = runtime
        self._by_id: dict = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="approval_box") as box:
            box.border_title = "APPROVALS"
            yield OptionList(id="approval_list")
            yield Static("", id="approval_context")

    async def on_mount(self) -> None:
        await self._reload()

    async def _reload(self) -> None:
        props = await self.runtime.read.list_proposals(status="open")
        if not props:
            self.dismiss()
            return
        self._by_id = {p.id: p for p in props}
        options = self.query_one("#approval_list", OptionList)
        options.clear_options()
        for p in props:
            options.add_option(Option(proposal_row(p), id=p.id))
        options.highlighted = 0
        options.focus()

    def on_option_list_option_highlighted(self, event) -> None:
        proposal = self._by_id.get(event.option.id)
        if proposal is not None:
            self.query_one("#approval_context", Static).update(proposal_context(proposal))

    async def on_option_list_option_selected(self, event) -> None:
        await self._decide("approve", event.option.id)

    async def action_reject(self) -> None:
        options = self.query_one("#approval_list", OptionList)
        if options.highlighted is None:
            return
        await self._decide("reject", options.get_option_at_index(options.highlighted).id)

    def action_close(self) -> None:
        self.dismiss()

    async def _decide(self, verb: str, proposal_id: str) -> None:
        # The one seam: commands.dispatch via the app's command runner, so
        # the '» Approved/Rejected proposal …' line lands in the feed exactly
        # like a typed command. Then catch the projector up before reloading:
        # the read store only learns the proposal left 'open' after
        # projection, and reloading early would re-show it (and let a second
        # enter double-commit the target event).
        await self.app._run_command(f"{verb} {proposal_id}")
        await self.runtime.projector.catch_up()
        await self._reload()
