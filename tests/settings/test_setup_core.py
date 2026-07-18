import httpx
import pytest

from novelizer.settings.setup_core import ProbeResult, build_global_config_data, probe_endpoint


def _transport(handler) -> httpx.AsyncBaseTransport:
    return httpx.MockTransport(handler)


async def test_probe_ok_lists_models():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/v1/models")
        assert request.headers["authorization"] == "Bearer sk-test"
        return httpx.Response(200, json={"data": [{"id": "m-big"}, {"id": "m-fast"}]})

    result = await probe_endpoint("http://h:1/v1", api_key="sk-test", transport=_transport(handler))
    assert result == ProbeResult(ok=True, models=["m-big", "m-fast"], error=None)


async def test_probe_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "no"})

    result = await probe_endpoint("http://h:1/v1", transport=_transport(handler))
    assert result.ok is False
    assert result.models == []
    assert "401" in result.error


async def test_probe_connection_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    result = await probe_endpoint("http://h:1/v1", transport=_transport(handler))
    assert result.ok is False
    assert "refused" in result.error


async def test_probe_bad_payload():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    result = await probe_endpoint("http://h:1/v1", transport=_transport(handler))
    assert result.ok is False
    assert result.error


def test_build_config_data_full():
    data = build_global_config_data(
        base_url=" http://h:1/v1/ ",
        api_key="sk-x",
        stories_dir="~/novels",
        author_model="m1",
        agent_model="m2",
        embed_model="m3",
    )
    assert data == {
        "llm_base_url": "http://h:1/v1",
        "llm_api_key": "sk-x",
        "default_stories_dir": "~/novels",
        "author_model": "m1",
        "agent_model": "m2",
        "embed_model": "m3",
    }


def test_build_config_data_omits_empties():
    data = build_global_config_data(base_url="http://h:1/v1", api_key="  ", author_model="")
    assert data == {"llm_base_url": "http://h:1/v1"}


def test_build_config_data_requires_base_url():
    with pytest.raises(ValueError):
        build_global_config_data(base_url="   ")
