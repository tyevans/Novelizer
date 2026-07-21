from __future__ import annotations

from typing import Any

from langchain_core.messages import SystemMessage
from langchain.agents.middleware import AgentMiddleware


def _tool_name(tool: Any) -> str:
    name = getattr(tool, "name", None)
    if name is not None:
        return name
    return tool.get("name", "")


def _format_todos(todos: list[dict[str, Any]]) -> str:
    marks = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}
    lines = [f"- {marks.get(t.get('status'), '[ ]')} {t.get('content', '')}" for t in todos]
    return "## Current todo list\n" + "\n".join(lines)


class ExcludeToolsMiddleware(AgentMiddleware[Any, Any, Any]):
    """Strip named tools from the model request before the model sees them.

    Placed after deepagents' tool-injecting middleware so it can remove
    built-ins like `write_todos` (Author-only per the pull-tools spec)."""

    def __init__(self, *, excluded: frozenset[str]) -> None:
        self._excluded = excluded

    def wrap_model_call(self, request, handler):
        if self._excluded:
            filtered = [t for t in request.tools if _tool_name(t) not in self._excluded]
            request = request.override(tools=filtered)
        return handler(request)

    async def awrap_model_call(self, request, handler):
        if self._excluded:
            filtered = [t for t in request.tools if _tool_name(t) not in self._excluded]
            request = request.override(tools=filtered)
        return await handler(request)


class TodoContextMiddleware(AgentMiddleware[Any, Any, Any]):
    """Re-append the live todo list to the system prompt on every model call.

    `TodoListMiddleware` only injects static write_todos instructions -- the
    actual current list rides along solely as a `write_todos` ToolMessage in
    message history. `SummarizationMiddleware` (always attached by
    `create_deep_agent`, see novelizer/agents/llm.py) can compact that
    message away with no awareness that it held the todo state, leaving the
    model blind to its own plan mid-run even though `state["todos"]`
    survives untouched in graph state. This middleware closes that gap by
    reading state directly, independent of message history."""

    def _augment(self, request):
        todos = request.state.get("todos") or []
        if not todos:
            return request
        block = {"type": "text", "text": f"\n\n{_format_todos(todos)}"}
        if request.system_message is not None:
            content = [*request.system_message.content_blocks, block]
        else:
            content = [block]
        return request.override(system_message=SystemMessage(content=content))

    def wrap_model_call(self, request, handler):
        return handler(self._augment(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._augment(request))
