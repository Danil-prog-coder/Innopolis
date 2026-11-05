"""Local HTTP SSE tests for the configurable OpenAI-compatible adapter."""

from contextlib import asynccontextmanager

import pytest
from aiohttp import web

from app.llm.gateway import LLMUnavailableError, UsageStatus
from app.llm.openai_stream import OpenAIStreamClient, normalize_usage


@asynccontextmanager
async def sse_server(handler, *, route="/v1/chat/completions", base="/v1"):
    app = web.Application()
    app.router.add_post(route, handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    assert site._server is not None
    port = site._server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}{base}"
    finally:
        await runner.cleanup()


def client(base_url: str, *, include_usage: bool = False) -> OpenAIStreamClient:
    return OpenAIStreamClient(
        base_url=base_url,
        api_key="test-key",
        model="test-model",
        timeout_seconds=5,
        include_usage=include_usage,
    )


async def collect(c: OpenAIStreamClient):
    return [chunk async for chunk in c.stream([{"role": "user", "content": "q"}])]


async def test_true_deltas_usage_and_explicit_capability():
    seen: list[dict] = []

    async def handler(request: web.Request) -> web.Response:
        seen.append(await request.json())
        body = (
            'data: {"choices":[{"delta":{"content":" "}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"A [1]"}}]}\n\n'
            'data: {"choices":[],"usage":{"prompt_tokens":4,"completion_tokens":2,'
            '"prompt_tokens_details":{"cached_tokens":1},'
            '"completion_tokens_details":{"reasoning_tokens":1}}}\n\n'
            "data: [DONE]\n\n"
        )
        return web.Response(text=body, content_type="text/event-stream")

    async with sse_server(handler) as base_url:
        chunks = await collect(client(base_url, include_usage=True))

    assert [chunk.text for chunk in chunks if chunk.text] == [" ", "A [1]"]
    usage = next(chunk.usage for chunk in chunks if chunk.usage is not None)
    assert usage.status is UsageStatus.REPORTED
    assert (usage.input_tokens, usage.output_tokens) == (4, 2)
    assert (usage.cached_input_tokens, usage.reasoning_output_tokens) == (1, 1)
    assert seen[0]["model"] == "test-model"
    assert seen[0]["messages"] == [{"role": "user", "content": "q"}]
    assert seen[0]["stream"] is True
    assert seen[0]["stream_options"] == {"include_usage": True}


async def test_capability_false_omits_stream_options():
    seen: list[dict] = []

    async def handler(request: web.Request) -> web.Response:
        seen.append(await request.json())
        return web.Response(
            text='data: {"choices":[{"delta":{"content":"A"}}]}\n\ndata: [DONE]\n\n',
            content_type="text/event-stream",
        )

    async with sse_server(handler) as base_url:
        await collect(client(base_url, include_usage=False))

    assert "stream_options" not in seen[0]


async def test_finish_reason_length_is_incomplete_not_success():
    async def handler(request: web.Request) -> web.Response:
        return web.Response(
            text='data: {"choices":[{"delta":{"content":"A"},"finish_reason":"length"}]}\n\n'
            "data: [DONE]\n\n",
            content_type="text/event-stream",
        )

    async with sse_server(handler) as base_url:
        with pytest.raises(LLMUnavailableError, match="incomplete"):
            await collect(client(base_url))


async def test_missing_done_is_error():
    async def handler(request: web.Request) -> web.Response:
        return web.Response(
            text='data: {"choices":[{"delta":{"content":"A"}}]}\n\n',
            content_type="text/event-stream",
        )

    async with sse_server(handler) as base_url:
        with pytest.raises(LLMUnavailableError, match="DONE"):
            await collect(client(base_url))


def test_retries_are_disabled_until_attempt_ledger():
    with pytest.raises(ValueError, match="INN-12"):
        OpenAIStreamClient(
            base_url="http://127.0.0.1:1/v1",
            api_key="x",
            model="m",
            max_retries=1,
        )


@pytest.mark.parametrize("field", ["base_url", "api_key", "model"])
def test_missing_provider_configuration_fails_closed(field):
    values = {
        "base_url": "http://127.0.0.1:1/v1",
        "api_key": "x",
        "model": "m",
    }
    values[field] = " "
    with pytest.raises(ValueError, match="required"):
        OpenAIStreamClient(**values)


@pytest.mark.parametrize("timeout", [0, -1, float("inf")])
def test_invalid_timeout_is_rejected(timeout):
    with pytest.raises(ValueError, match="timeout"):
        OpenAIStreamClient(
            base_url="http://127.0.0.1:1/v1",
            api_key="x",
            model="m",
            timeout_seconds=timeout,
        )


async def test_custom_chat_completions_path_is_used():
    seen: list[dict] = []

    async def handler(request: web.Request) -> web.Response:
        seen.append(await request.json())
        return web.Response(
            text='data: {"choices":[{"delta":{"content":"A [1]"}}]}\n\ndata: [DONE]\n\n',
            content_type="text/event-stream",
        )

    async with sse_server(handler, route="/custom/stream", base="") as base_url:
        client = OpenAIStreamClient(
            base_url=base_url,
            api_key="test-key",
            model="DeepSeek-V4.1-Flash-1017",
            chat_completions_path="/custom/stream",
        )
        chunks = await collect(client)

    assert [chunk.text for chunk in chunks if chunk.text] == ["A [1]"]
    assert seen[0]["model"] == "DeepSeek-V4.1-Flash-1017"


async def test_429_is_not_silently_retried():
    calls = 0

    async def handler(request: web.Request) -> web.Response:
        nonlocal calls
        calls += 1
        return web.Response(status=429, text="limited")

    async with sse_server(handler) as base_url:
        with pytest.raises(LLMUnavailableError, match="rate"):
            await collect(client(base_url))

    assert calls == 1


async def test_reasoning_content_is_not_exposed():
    async def handler(request: web.Request) -> web.Response:
        body = (
            'data: {"choices":[{"delta":{"reasoning_content":"secret thought"}}]}\n\n'
            "data: [DONE]\n\n"
        )
        return web.Response(text=body, content_type="text/event-stream")

    async with sse_server(handler) as base_url:
        chunks = await collect(client(base_url))

    assert all(not chunk.text for chunk in chunks)


def test_usage_unknown_is_not_zero():
    usage = normalize_usage({})
    assert usage.status is UsageStatus.UNKNOWN
    assert usage.input_tokens is None
    assert usage.output_tokens is None


def test_usage_invalid_is_not_invented():
    with pytest.raises(LLMUnavailableError):
        normalize_usage({"prompt_tokens": -1})
