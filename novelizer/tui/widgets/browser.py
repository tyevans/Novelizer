from __future__ import annotations
from textual.widgets import Tree
from novelizer.tui.widgets.browser_model import browser_sections


class StoryBrowser(Tree):
    _last_sections = None

    async def refresh_sections(self, read) -> None:
        sections = await browser_sections(read)
        if sections == self._last_sections:
            # Nothing changed: skip the rebuild so the user's cursor position
            # (and expansion state) is preserved in the common steady state.
            return
        # Key expansion state on the stable section key (not the label, which
        # embeds item counts and therefore changes whenever an item is added).
        expanded = {
            n.data["section"] for n in self.root.children
            if n.is_expanded and n.data
        }
        self._last_sections = sections
        self.root.remove_children()
        self.root.expand()
        # Note: cursor position is not restored across an actual data-change
        # rebuild (out of scope - Textual API rabbit hole); only the
        # skip-when-unchanged path above preserves it.
        for sec in sections:
            node = self.root.add(sec["label"], data={"section": sec["key"], "id": None})
            for item in sec["items"]:
                node.add_leaf(item["label"], data={"section": sec["key"], "id": item["id"]})
            if sec["key"] in expanded:
                node.expand()
