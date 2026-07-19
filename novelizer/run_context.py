"""Ambient identity of the agent run currently executing.

Deliberately dependency-free: canon (Committer) and telemetry both read
these, and canon must not import the telemetry package.
"""
from __future__ import annotations
from contextvars import ContextVar

current_run_id: ContextVar[str | None] = ContextVar("current_run_id", default=None)
current_agent_name: ContextVar[str] = ContextVar("current_agent_name", default="")
