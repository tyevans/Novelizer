"""Ambient identity of the agent run currently executing.

Deliberately dependency-free so any layer (storage, telemetry, tools) can
read these without importing the rest of the kit.
"""
from __future__ import annotations
from contextvars import ContextVar

current_run_id: ContextVar[str | None] = ContextVar("current_run_id", default=None)
current_agent_name: ContextVar[str] = ContextVar("current_agent_name", default="")
