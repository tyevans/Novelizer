from __future__ import annotations
from typing import Callable

from novelizer.telemetry.recorder import run_with_identity


class ResearchAnswerError(RuntimeError):
    """The research runner returned no structured answer."""


def _transcript_block(history: list[tuple[str, str]]) -> str:
    if not history:
        return "(no research conversation yet)"
    lines = [f"{role}: {text}" for role, text in history[-20:]]
    return "\n".join(lines)


class ResearchService:
    """Stateless entry point for the research bounded context. The caller
    (ResearchScreen) owns conversation history; this service never persists
    anything and never writes to canon."""

    def __init__(self, runner_for: Callable, telemetry=None) -> None:
        self._runner_for = runner_for
        self._telemetry = telemetry

    async def ask(self, question: str, history: list[tuple[str, str]]) -> str:
        prompt = (
            f"Research conversation so far:\n{_transcript_block(history)}\n\n"
            f"New question: {question}"
        )
        runner = self._runner_for()
        async with run_with_identity(self._telemetry, "research"):
            result = await runner.ainvoke({"messages": [{"role": "user", "content": prompt}]})
            answer = result.get("structured_response")
            if answer is None:
                raise ResearchAnswerError("research runner returned no structured answer")
        return answer.answer_text
