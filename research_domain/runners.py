"""Per-role deepagents runner builders: thin compositions of
agent_kit.build_chat_model + build_agent_runner with each role's system
prompt and response format."""
from __future__ import annotations

from dataclasses import dataclass

from agent_kit import ExcludeToolsMiddleware, build_agent_runner, build_chat_model

from research_domain.schemas import ExtractorOutput, RetractorOutput, VerifierOutput

EXTRACTOR_SYSTEM_PROMPT = """## Role
You extract factual claims from research corpus documents. A claim is a
single checkable assertion, phrased as one standalone sentence. You never
invent claims the document does not make, and you never modify anything:
your only output is the structured claim list."""

VERIFIER_SYSTEM_PROMPT = """## Role
You verify claims against the rest of the research corpus. Use the tools
to read other documents before answering. Corroborate only when another
document independently supports the claim; refute only when a document
directly contradicts it, citing that document as the counter-claim's
source. When the corpus is silent, say so with an empty verdict."""

RETRACTOR_SYSTEM_PROMPT = """## Role
You resolve recorded contradictions between claims. Given a target claim
and the claims refuting it, decide which should stand. Supersede the
target only when a refuting claim is better supported; otherwise return
no corrections and let the original stand."""

_ROLES = {
    "extractor": (EXTRACTOR_SYSTEM_PROMPT, ExtractorOutput),
    "verifier": (VERIFIER_SYSTEM_PROMPT, VerifierOutput),
    "retractor": (RETRACTOR_SYSTEM_PROMPT, RetractorOutput),
}


@dataclass(frozen=True)
class ModelSettings:
    model: str
    base_url: str
    api_key: str
    temperature: float = 0.2
    max_tokens: int | None = 4096


def build_role_runner(role: str, settings: ModelSettings, tools: list):
    if role not in _ROLES:
        raise ValueError(f"unknown role: {role!r} (expected one of {sorted(_ROLES)})")
    system_prompt, response_format = _ROLES[role]
    model = build_chat_model(
        settings.model, settings.base_url, settings.api_key,
        temperature=settings.temperature, max_tokens=settings.max_tokens,
    )
    return build_agent_runner(
        model=model,
        system_prompt=system_prompt,
        response_format=response_format,
        tools=tools,
        middleware=[ExcludeToolsMiddleware(excluded=frozenset({"write_todos"}))],
    )
