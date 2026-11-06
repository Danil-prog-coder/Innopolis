"""OpenAI-compatible SSE adapter with explicit usage capability.

This is a configurable transport, not a claim that a commercial provider or key
exists. Real provider/Telegram live tests are blocked until configuration is
supplied. Whitespace-only deltas are preserved; hidden reasoning is ignored.
"""

import asyncio
import json
import math
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass

import aiohttp

from app.llm.gateway import LLMUnavailableError, TokenUsage, UsageStatus

MAX_OUTPUT_TOKENS_LIMIT = 131072


@dataclass(frozen=True, slots=True)
class CompletionChunk:
    text: str = ""
    usage: TokenUsage | None = None


class ProviderUsageError(LLMUnavailableError):
    pass


def _count(payload: Mapping[str, object], *keys: str) -> int | None:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ProviderUsageError(f"invalid usage count {key}")
        return value
    return None


def _nested_count(
    payload: Mapping[str, object], *, containers: tuple[str, ...], field: str
) -> int | None:
    for container in containers:
        details = payload.get(container)
        if isinstance(details, Mapping):
            value = details.get(field)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ProviderUsageError(f"invalid usage count {container}.{field}")
            return value
    return None


def normalize_usage(payload: object) -> TokenUsage:
    """Map provider usage without inventing missing tokens or cost."""
    if not isinstance(payload, Mapping):
        return TokenUsage(status=UsageStatus.UNKNOWN)
    try:
        input_tokens = _count(payload, "prompt_tokens", "input_tokens")
        output_tokens = _count(payload, "completion_tokens", "output_tokens")
        cached = _nested_count(
            payload,
            containers=("prompt_tokens_details", "input_tokens_details"),
            field="cached_tokens",
        )
        reasoning = _nested_count(
            payload,
            containers=("completion_tokens_details", "output_tokens_details"),
            field="reasoning_tokens",
        )
        counts = (input_tokens, output_tokens, cached, reasoning)
        if all(count is None for count in counts):
            return TokenUsage(status=UsageStatus.UNKNOWN)
        return TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached,
            reasoning_output_tokens=reasoning,
            status=UsageStatus.REPORTED,
        )
    except ValueError as error:
        raise ProviderUsageError("invalid provider usage") from error


class OpenAIStreamClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        max_output_tokens: int,
        timeout_seconds: float = 60.0,
        max_retries: int = 0,
        include_usage: bool = False,
        chat_completions_path: str = "/chat/completions",
    ) -> None:
        if max_retries != 0:
            raise ValueError(
                "retries are disabled until explicit attempt ledger (INN-12); max_retries must be 0"
            )
        if not base_url.strip() or not model.strip() or not api_key.strip():
            raise ValueError("base_url, model and api_key are required")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds должен быть конечным и положительным")
        if not chat_completions_path.strip():
            raise ValueError("chat_completions_path is required")
        if (
            isinstance(max_output_tokens, bool)
            or not isinstance(max_output_tokens, int)
            or not 1 <= max_output_tokens <= MAX_OUTPUT_TOKENS_LIMIT
        ):
            raise ValueError(
                f"max_output_tokens must be an integer in 1..{MAX_OUTPUT_TOKENS_LIMIT}"
            )
        self._url = f"{base_url.rstrip('/')}/{chat_completions_path.lstrip('/')}"
        self._api_key = api_key
        self._model = model
        self._max_output_tokens = max_output_tokens
        self._timeout = timeout_seconds
        # Explicit capability: compatible servers may reject stream_options.
        self._include_usage = include_usage

    def _payload(self, messages: list[dict[str, str]]) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": self._model,
            "messages": messages,
            "stream": True,
            # max_tokens is the common OpenAI-compatible field; provider-specific
            # alternatives can be added as an explicit capability later.
            "max_tokens": self._max_output_tokens,
        }
        if self._include_usage:
            payload["stream_options"] = {"include_usage": True}
        return payload

    async def _stream_once(self, messages: list[dict[str, str]]) -> AsyncIterator[CompletionChunk]:
        timeout = aiohttp.ClientTimeout(total=self._timeout)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    self._url,
                    json=self._payload(messages),
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Accept": "text/event-stream",
                    },
                ) as response:
                    if response.status == 429:
                        raise LLMUnavailableError("provider rate limited")
                    if response.status >= 500:
                        raise LLMUnavailableError("provider unavailable")
                    if response.status >= 400:
                        raise LLMUnavailableError(f"provider HTTP {response.status}")
                    done = False
                    async for raw_line in response.content:
                        line = raw_line.decode("utf-8").strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].lstrip(" ")
                        if data == "[DONE]":
                            done = True
                            break
                        event = json.loads(data)
                        if not isinstance(event, dict):
                            raise LLMUnavailableError("invalid provider event")
                        if event.get("error") is not None:
                            raise LLMUnavailableError("provider stream error")
                        usage = event.get("usage")
                        if usage is not None:
                            yield CompletionChunk(usage=normalize_usage(usage))
                        choices = event.get("choices") or []
                        if not choices:
                            continue
                        choice = choices[0] if isinstance(choices[0], dict) else {}
                        finish_reason = choice.get("finish_reason")
                        # Only an explicit "stop" is a successful completion.
                        # Unknown values (aborted, insufficient_system_resource,
                        # future provider values) must fail closed instead of
                        # being cached as a complete answer.
                        if finish_reason not in (None, "", "stop"):
                            raise LLMUnavailableError("provider stream incomplete")
                        delta = choice.get("delta", {})
                        content = delta.get("content") if isinstance(delta, dict) else None
                        # reasoning_content intentionally ignored: never expose hidden reasoning.
                        if content is None:
                            continue
                        if not isinstance(content, str):
                            raise LLMUnavailableError("provider delta content is not text")
                        if content:
                            yield CompletionChunk(text=content)
                    if not done:
                        raise LLMUnavailableError("provider stream ended without [DONE]")
        except asyncio.CancelledError:
            raise
        except LLMUnavailableError:
            raise
        except (
            aiohttp.ClientError,
            TimeoutError,
            UnicodeError,
            ValueError,
            KeyError,
            TypeError,
        ) as error:
            raise LLMUnavailableError("LLM provider unavailable or invalid response") from error

    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[CompletionChunk]:
        # No implicit retries: a retry is a potentially billable attempt. Explicit
        # attempt events/ledger belong to INN-12.
        async for chunk in self._stream_once(messages):
            yield chunk

    async def close(self) -> None:
        return None
