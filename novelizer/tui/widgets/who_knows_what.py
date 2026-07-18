from __future__ import annotations
from textual.widgets import Static
from novelizer.canon.secrets import knowledge_cell_state
from novelizer.store.models import Character, SecretRecord


def who_knows_what_line(secret: SecretRecord, characters: list[Character], matrix: dict[str, dict]) -> str:
    if secret.revealed:
        state = "REVEALED"
    else:
        known = sorted(c.name for c in characters if knowledge_cell_state(matrix, secret.id, c.id) == "known")
        state = ", ".join(known) if known else "known to no one"
    return f"· {secret.title} (id:{secret.id})  [{state}]"


class WhoKnowsWhat(Static):
    async def refresh_from(self, read) -> None:
        secrets = await read.list_secrets()
        characters = await read.list_characters()
        matrix = await read.knowledge_matrix()
        lines = [who_knows_what_line(s, characters, matrix) for s in secrets]
        self.update("\n".join(lines) or "no secrets yet")
