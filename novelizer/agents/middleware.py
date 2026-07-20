from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware


def _tool_name(tool: Any) -> str:
    name = getattr(tool, "name", None)
    if name is not None:
        return name
    return tool.get("name", "")


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
