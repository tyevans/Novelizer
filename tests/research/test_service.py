import pytest
from novelizer.research.schemas import ResearchAnswer
from novelizer.research.service import ResearchAnswerError, ResearchService


class _R:
    def __init__(self, out):
        self._out = out
        self.calls = []

    async def ainvoke(self, inputs):
        self.calls.append(inputs)
        return {"structured_response": self._out}


class _Empty:
    async def ainvoke(self, inputs):
        return {"structured_response": None}


@pytest.mark.asyncio
async def test_ask_returns_runner_text_verbatim():
    runner = _R(ResearchAnswer(answer_text="Three threads are currently stale."))
    service = ResearchService(lambda: runner)

    answer = await service.ask("Anything stale?", history=[])

    assert answer == "Three threads are currently stale."


@pytest.mark.asyncio
async def test_ask_includes_history_and_question_in_the_prompt():
    runner = _R(ResearchAnswer(answer_text="ok"))
    service = ResearchService(lambda: runner)

    await service.ask(
        "and the paradoxes?",
        history=[("you", "any leaks?"), ("project", "No leaks found.")],
    )

    prompt = runner.calls[0]["messages"][0]["content"]
    assert "any leaks?" in prompt
    assert "No leaks found." in prompt
    assert "and the paradoxes?" in prompt


@pytest.mark.asyncio
async def test_ask_raises_when_runner_returns_no_structured_response():
    service = ResearchService(lambda: _Empty())

    with pytest.raises(ResearchAnswerError):
        await service.ask("anything?", history=[])
