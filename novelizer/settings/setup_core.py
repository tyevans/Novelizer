from __future__ import annotations

from dataclasses import dataclass, field

import httpx


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    models: list[str] = field(default_factory=list)
    error: str | None = None


async def probe_endpoint(
    base_url: str,
    api_key: str = "not-needed",
    timeout: float = 5.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ProbeResult:
    """Live connectivity test: GET {base_url}/models (OpenAI-compatible).
    Never raises — failures come back as ProbeResult(ok=False, error=...)."""
    url = f"{base_url.rstrip('/')}/models"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            response = await client.get(url, headers=headers)
        if response.status_code != 200:
            return ProbeResult(ok=False, error=f"HTTP {response.status_code} from {url}")
        payload = response.json()
        models = [m["id"] for m in payload.get("data", []) if isinstance(m, dict) and "id" in m]
        return ProbeResult(ok=True, models=models)
    except Exception as e:  # network, timeout, JSON decode — all become a message
        return ProbeResult(ok=False, error=str(e) or type(e).__name__)


def build_global_config_data(
    base_url: str,
    api_key: str = "",
    stories_dir: str = "",
    author_model: str = "",
    agent_model: str = "",
    embed_model: str = "",
    embed_base_url: str = "",
    embed_api_key: str = "",
) -> dict:
    """Pure assembly of a global-config dict from wizard fields. Empty fields
    are omitted so built-in defaults keep applying — including embed_base_url,
    whose absence means 'embed against the chat endpoint'."""
    base = base_url.strip().rstrip("/")
    if not base:
        raise ValueError("LLM base URL is required")
    data: dict = {"llm_base_url": base}
    for key, value in (
        ("llm_api_key", api_key),
        ("default_stories_dir", stories_dir),
        ("author_model", author_model),
        ("agent_model", agent_model),
        ("embed_model", embed_model),
        ("embed_base_url", embed_base_url.strip().rstrip("/")),
        ("embed_api_key", embed_api_key),
    ):
        value = value.strip()
        if value:
            data[key] = value
    return data
