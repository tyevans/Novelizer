from __future__ import annotations
from langchain_core.messages import HumanMessage
from agent_kit import build_light_model

_PROMPT = (
    "Summarize this tool call in one short plain sentence (under 15 words), "
    "no markdown, no quotes.\n\nTool: {tool_name}\nInput: {input_summary}\n{result_line}"
)


async def summarize_tool_call(
    settings, tool_name: str, input_summary: str, output_summary: str, error: str,
) -> str:
    """Cheap one-line summary of a finished/failed tool call, for backfilling
    into the Engine Room's live stream. Runs with no telemetry callbacks (so
    it never appears in the machinery view itself) and a small max_tokens
    cap to keep it cheap; the caller is expected to treat failures here as
    non-fatal (see novelizer/tui/app.py)."""
    result_line = f"Error: {error}" if error else f"Result: {output_summary}"
    prompt = _PROMPT.format(tool_name=tool_name, input_summary=input_summary,
                            result_line=result_line)
    # Light path: fifteen words describing a tool call that already happened.
    model = build_light_model(
        settings.resolved_light_model, settings.llm_base_url, settings.llm_api_key,
        max_tokens=40, reasoning=settings.light_reasoning,
    )
    response = await model.ainvoke([HumanMessage(content=prompt)])
    text = str(response.content).strip().replace("\n", " ")
    return text[:200]
