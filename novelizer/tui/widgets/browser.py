from __future__ import annotations
from textual.widgets import Tree
from novelizer.tui.widgets.browser_model import browser_sections


class StoryBrowser(Tree):
    async def refresh_sections(self, read) -> None:
        expanded = {str(n.label) for n in self.root.children if n.is_expanded}
        self.root.remove_children()
        self.root.expand()
        for sec in await browser_sections(read):
            node = self.root.add(sec["label"], data={"section": sec["key"], "id": None})
            for item in sec["items"]:
                node.add_leaf(item["label"], data={"section": sec["key"], "id": item["id"]})
            if sec["label"] in expanded:
                node.expand()
