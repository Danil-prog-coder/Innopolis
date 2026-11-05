"""Граница между ботом и ИИ-частью проекта.

Бот не знает, как устроен RAG: он вызывает ``LLMGateway``. Контракт PERS-42
фиксирует snapshot проекта, источники, типизированный no-answer и потоковые
события. Реальная индексация и публикация generation — PERS-43/48; здесь только
контракт и безопасная заглушка/фейк.
"""

import json
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from app.core.enums import DocsMode

logger = logging.getLogger(__name__)

_DISPLAY_URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://|www\.|t\.me/", re.IGNORECASE)
_DISPLAY_ABSOLUTE_PATH_RE = re.compile(
    r"(^|\s)(?:/[^\s]|~[\\/]|[A-Za-z]:[\\/]|\\\\)|file://", re.IGNORECASE
)
_CITATION_LABEL_RE = re.compile(r"^\[\d{1,3}\]$")


def _is_display_title(value: str) -> bool:
    """Заголовок источника — display text, а не путь/URL/секрет."""
    return not (_DISPLAY_URL_RE.search(value) or _DISPLAY_ABSOLUTE_PATH_RE.search(value))


class NoAnswerReason(StrEnum):
    """Почему RAG не дал ответ. Это не ошибка сервиса."""

    NO_EVIDENCE = "no_evidence"
    NOT_ANSWERABLE = "not_answerable"


class AskErrorCode(StrEnum):
    UNAVAILABLE = "unavailable"
    CANCELLED = "cancelled"
    CONTRACT_VIOLATION = "contract_violation"


class AskEventType(StrEnum):
    STATUS = "status"
    DELTA = "delta"
    FINAL = "final"
    ERROR = "error"
    CANCELLED = "cancelled"


class UsageStatus(StrEnum):
    REPORTED = "reported"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


class CostStatus(StrEnum):
    """Provenance стоимости не зависит от полноты usage-токенов."""

    REPORTED = "reported"
    RECONCILED = "reconciled"
    ESTIMATED = "estimated"


def _int(value: object, *, field: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} должен быть int")
    if value < minimum:
        raise ValueError(f"{field} должен быть >= {minimum}")
    return value


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} должен быть непустой строкой")
    return value


def _chunk_text(value: object, *, field: str) -> str:
    """Текст provider-чанка: пробелы/переводы строк/разметка допустимы."""
    if not isinstance(value, str) or len(value) == 0:
        raise ValueError(f"{field} должен быть непустой строкой")
    return value


def _optional_text(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    return _text(value, field=field)


def _optional_count(value: object, *, field: str) -> int | None:
    if value is None:
        return None
    return _int(value, field=field)


def _decimal_or_none(value: object, *, field: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError(f"{field} должен быть Decimal/str/int, не float")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, str):
        try:
            result = Decimal(value)
        except InvalidOperation as error:
            raise ValueError(f"{field}: некорректный Decimal") from error
    else:
        raise ValueError(f"{field} должен быть Decimal/str/int")
    if not result.is_finite() or result < 0:
        raise ValueError(f"{field} должен быть конечным и неотрицательным")
    return result


@dataclass(frozen=True, slots=True)
class KnowledgeSnapshot:
    """Зафиксированная версия знаний, прочитанная один раз на запрос.

    ``compat`` — временный идентификатор до PERS-48, а не опубликованная
    generation. Он нужен только для перехода stub/fake и не должен попадать в
    production-публикацию.
    """

    project_id: int
    generation_id: str
    knowledge_version: int

    def __post_init__(self) -> None:
        _int(self.project_id, field="project_id", minimum=1)
        _text(self.generation_id, field="generation_id")
        _int(self.knowledge_version, field="knowledge_version")

    @classmethod
    def compat(cls, project_id: int, docs_version: int) -> "KnowledgeSnapshot":
        """Snapshot для текущего main до появления published generation."""
        return cls(
            project_id=project_id,
            generation_id=f"compat-docs-{docs_version}",
            knowledge_version=docs_version,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "project_id": self.project_id,
            "generation_id": self.generation_id,
            "knowledge_version": self.knowledge_version,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "KnowledgeSnapshot":
        if not isinstance(payload, dict):
            raise ValueError("snapshot должен быть объектом")
        return cls(
            project_id=_int(payload.get("project_id"), field="project_id", minimum=1),
            generation_id=_text(payload.get("generation_id"), field="generation_id"),
            knowledge_version=_int(payload.get("knowledge_version"), field="knowledge_version"),
        )


@dataclass(frozen=True, slots=True)
class Source:
    """Источник ответа в той же generation, что и snapshot запроса."""

    project_id: int
    generation_id: str
    knowledge_version: int
    document_id: str
    document_version: str
    chunk_id: str
    title: str
    excerpt: str
    citation_label: str
    section_path: str | None = None
    page: int | None = None

    def __post_init__(self) -> None:
        _int(self.project_id, field="project_id", minimum=1)
        _text(self.generation_id, field="generation_id")
        _int(self.knowledge_version, field="knowledge_version")
        _text(self.document_id, field="document_id")
        _text(self.document_version, field="document_version")
        _text(self.chunk_id, field="chunk_id")
        _text(self.title, field="title")
        if not _is_display_title(self.title):
            raise ValueError("title должен быть display text, без пути/URL")
        _text(self.excerpt, field="excerpt")
        _text(self.citation_label, field="citation_label")
        if not _CITATION_LABEL_RE.fullmatch(self.citation_label):
            raise ValueError("citation_label должен быть строго вида [N]")
        _optional_text(self.section_path, field="section_path")
        if self.page is not None:
            _int(self.page, field="page", minimum=1)

    def to_dict(self) -> dict[str, object]:
        return {
            "project_id": self.project_id,
            "generation_id": self.generation_id,
            "knowledge_version": self.knowledge_version,
            "document_id": self.document_id,
            "document_version": self.document_version,
            "chunk_id": self.chunk_id,
            "title": self.title,
            "excerpt": self.excerpt,
            "citation_label": self.citation_label,
            "section_path": self.section_path,
            "page": self.page,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "Source":
        if not isinstance(payload, dict):
            raise ValueError("source должен быть объектом")
        page = payload.get("page")
        return cls(
            project_id=_int(payload.get("project_id"), field="project_id", minimum=1),
            generation_id=_text(payload.get("generation_id"), field="generation_id"),
            knowledge_version=_int(payload.get("knowledge_version"), field="knowledge_version"),
            document_id=_text(payload.get("document_id"), field="document_id"),
            document_version=_text(payload.get("document_version"), field="document_version"),
            chunk_id=_text(payload.get("chunk_id"), field="chunk_id"),
            title=_text(payload.get("title"), field="title"),
            excerpt=_text(payload.get("excerpt"), field="excerpt"),
            citation_label=_text(payload.get("citation_label"), field="citation_label"),
            section_path=_optional_text(payload.get("section_path"), field="section_path"),
            page=_int(page, field="page", minimum=1) if page is not None else None,
        )


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Токены одной попытки. ``None`` — unknown, ``0`` — известно ноль.

    ``cached_input_tokens`` — подмножество input, ``reasoning_output_tokens`` —
    подмножество output. Итоги не складывают их повторно.
    """

    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_output_tokens: int | None = None
    status: UsageStatus = UsageStatus.UNKNOWN

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", UsageStatus(self.status))
        input_tokens = _optional_count(self.input_tokens, field="input_tokens")
        output_tokens = _optional_count(self.output_tokens, field="output_tokens")
        cached = _optional_count(self.cached_input_tokens, field="cached_input_tokens")
        reasoning = _optional_count(self.reasoning_output_tokens, field="reasoning_output_tokens")
        if input_tokens is not None and cached is not None and cached > input_tokens:
            raise ValueError("cached_input_tokens не может превышать input_tokens")
        if output_tokens is not None and reasoning is not None and reasoning > output_tokens:
            raise ValueError("reasoning_output_tokens не может превышать output_tokens")
        counts = (input_tokens, output_tokens, cached, reasoning)
        if self.status is UsageStatus.UNKNOWN and any(count is not None for count in counts):
            raise ValueError("unknown usage не содержит известных counts")
        if self.status is not UsageStatus.UNKNOWN and all(count is None for count in counts):
            raise ValueError("reported/estimated usage требует хотя бы один count")

    @property
    def total_tokens(self) -> int | None:
        if self.input_tokens is None or self.output_tokens is None:
            return None
        return self.input_tokens + self.output_tokens

    @property
    def uncached_input_tokens(self) -> int | None:
        """Input без cached; cache не прибавляется к total повторно."""
        if self.input_tokens is None or self.cached_input_tokens is None:
            return None
        return self.input_tokens - self.cached_input_tokens

    @property
    def non_reasoning_output_tokens(self) -> int | None:
        """Output без reasoning; reasoning не прибавляется к total повторно."""
        if self.output_tokens is None or self.reasoning_output_tokens is None:
            return None
        return self.output_tokens - self.reasoning_output_tokens

    def to_dict(self) -> dict[str, object]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "reasoning_output_tokens": self.reasoning_output_tokens,
            "status": self.status.value,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "TokenUsage":
        if not isinstance(payload, dict):
            raise ValueError("usage должен быть объектом")
        return cls(
            input_tokens=payload.get("input_tokens"),  # type: ignore[arg-type]
            output_tokens=payload.get("output_tokens"),  # type: ignore[arg-type]
            cached_input_tokens=payload.get("cached_input_tokens"),  # type: ignore[arg-type]
            reasoning_output_tokens=payload.get(  # type: ignore[arg-type]
                "reasoning_output_tokens"
            ),
            status=UsageStatus(payload.get("status", UsageStatus.UNKNOWN)),
        )


@dataclass(frozen=True, slots=True)
class AttemptUsage:
    """Usage одной попытки: request_id общий для retry, attempt_id уникален.

    Cost принимается только вместе с currency/rate_version/priced_at; без них
    стоимость не агрегируется. Это reported/estimated cost, не расчёт по
    тарифам — ledger остаётся в PERS-48.
    """

    request_id: str
    attempt_id: str
    usage: TokenUsage
    cost: Decimal | None = None
    cost_status: CostStatus | None = None
    currency: str | None = None
    rate_version: str | None = None
    priced_at: datetime | None = None

    def __post_init__(self) -> None:
        _text(self.request_id, field="request_id")
        _text(self.attempt_id, field="attempt_id")
        if not isinstance(self.usage, TokenUsage):
            raise ValueError("usage должен быть TokenUsage")
        object.__setattr__(self, "cost", _decimal_or_none(self.cost, field="cost"))
        if self.cost_status is not None:
            object.__setattr__(self, "cost_status", CostStatus(self.cost_status))
        if self.cost is None:
            if (
                self.cost_status is not None
                or self.currency is not None
                or self.rate_version is not None
                or self.priced_at is not None
            ):
                raise ValueError("cost metadata без cost не допускается")
        else:
            if self.cost_status is None:
                raise ValueError("cost требует cost_status")
            if not isinstance(self.currency, str) or not re.fullmatch(r"[A-Z]{3}", self.currency):
                raise ValueError("cost требует currency ISO 4217")
            _text(self.rate_version, field="rate_version")
            if not isinstance(self.priced_at, datetime) or self.priced_at.tzinfo is None:
                raise ValueError("cost требует timezone-aware priced_at")

    def to_dict(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "attempt_id": self.attempt_id,
            "usage": self.usage.to_dict(),
            "cost": str(self.cost) if self.cost is not None else None,
            "cost_status": (self.cost_status.value if self.cost_status is not None else None),
            "currency": self.currency,
            "rate_version": self.rate_version,
            "priced_at": (self.priced_at.isoformat() if self.priced_at is not None else None),
        }

    @classmethod
    def from_dict(cls, payload: object) -> "AttemptUsage":
        if not isinstance(payload, dict):
            raise ValueError("attempt usage должен быть объектом")
        priced_at = payload.get("priced_at")
        if priced_at is not None:
            if not isinstance(priced_at, str):
                raise ValueError("priced_at должен быть ISO-строкой")
            try:
                priced_at = datetime.fromisoformat(priced_at)
            except ValueError as error:
                raise ValueError("некорректный priced_at") from error
        cost_status = payload.get("cost_status")
        return cls(
            request_id=_text(payload.get("request_id"), field="request_id"),
            attempt_id=_text(payload.get("attempt_id"), field="attempt_id"),
            usage=TokenUsage.from_dict(payload.get("usage")),
            cost=_decimal_or_none(payload.get("cost"), field="cost"),
            cost_status=CostStatus(cost_status) if cost_status is not None else None,
            currency=payload.get("currency"),  # type: ignore[arg-type]
            rate_version=payload.get("rate_version"),  # type: ignore[arg-type]
            priced_at=priced_at,  # type: ignore[arg-type]
        )


@dataclass(frozen=True, slots=True)
class AskResult:
    """Нормализованный результат одного ответа.

    ``found=False`` — отсутствие данных/ответа, а не ошибка. ``found=True``
    требует непустой ответ; sources необязательны для совместимости с
    не-RAG ответами, но RAG-провайдер должен их заполнять.
    """

    found: bool
    answer: str | None
    sources: tuple[Source, ...] = ()
    snapshot: KnowledgeSnapshot | None = None
    request_id: str | None = None
    no_answer_reason: NoAnswerReason | None = None
    usage: AttemptUsage | None = None
    # Только для тестов/переходного legacy-адаптера. Real RAG обязан дать sources.
    legacy_no_sources: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.found, bool):
            raise ValueError("found должен быть bool")
        if not isinstance(self.legacy_no_sources, bool):
            raise ValueError("legacy_no_sources должен быть bool")
        if not isinstance(self.sources, tuple):
            object.__setattr__(self, "sources", tuple(self.sources))
        for source in self.sources:
            if not isinstance(source, Source):
                raise ValueError("sources должен содержать Source")
        if self.snapshot is not None and not isinstance(self.snapshot, KnowledgeSnapshot):
            raise ValueError("snapshot должен быть KnowledgeSnapshot или None")
        if self.request_id is not None:
            _text(self.request_id, field="request_id")
        if self.usage is not None:
            if not isinstance(self.usage, AttemptUsage):
                raise ValueError("usage должен быть AttemptUsage или None")
            if self.request_id is not None and self.usage.request_id != self.request_id:
                raise ValueError("usage.request_id не совпадает с result.request_id")
        if self.no_answer_reason is not None:
            object.__setattr__(self, "no_answer_reason", NoAnswerReason(self.no_answer_reason))

        if self.found:
            _text(self.answer, field="answer")
            if self.no_answer_reason is not None:
                raise ValueError("found=True не может иметь no_answer_reason")
            if not self.sources and not self.legacy_no_sources:
                raise ValueError("found=True требует проверенных sources")
            if self.sources and self.legacy_no_sources:
                raise ValueError("legacy_no_sources допустим только без sources")
        else:
            if self.answer is not None:
                if self.answer.strip():
                    raise ValueError("found=False не может содержать answer")
                object.__setattr__(self, "answer", None)
            if self.sources:
                raise ValueError("found=False не может содержать sources")
            if self.legacy_no_sources:
                raise ValueError("legacy_no_sources не применяется к found=False")
            if self.no_answer_reason is None:
                # Совместимость с прежними фейками: отсутствие ответа — no evidence.
                object.__setattr__(self, "no_answer_reason", NoAnswerReason.NO_EVIDENCE)

        if self.sources:
            if self.snapshot is None:
                raise ValueError("sources требуют snapshot для проверки scope")
            _check_sources_snapshot(self.sources, self.snapshot)

    def to_json(self) -> str:
        payload = {
            "v": 1,
            "found": self.found,
            "answer": self.answer,
            "sources": [source.to_dict() for source in self.sources],
            "snapshot": self.snapshot.to_dict() if self.snapshot is not None else None,
            "request_id": self.request_id,
            "no_answer_reason": (
                self.no_answer_reason.value if self.no_answer_reason is not None else None
            ),
            "usage": self.usage.to_dict() if self.usage is not None else None,
            "legacy_no_sources": self.legacy_no_sources,
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    def to_cache_json(self) -> str:
        """Кэш не является попыткой: не хранит request-specific usage/request_id."""
        cached = replace(self, request_id=None, usage=None)
        return cached.to_json()

    @classmethod
    def from_json(cls, raw: str) -> "AskResult":
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError) as error:
            raise ValueError("некорректный JSON AskResult") from error
        if not isinstance(payload, dict) or payload.get("v") != 1:
            raise ValueError("неподдерживаемая версия AskResult")
        snapshot_payload = payload.get("snapshot")
        reason = payload.get("no_answer_reason")
        return cls(
            found=payload.get("found"),  # type: ignore[arg-type]
            answer=payload.get("answer"),  # type: ignore[arg-type]
            sources=tuple(Source.from_dict(item) for item in payload.get("sources", [])),
            snapshot=(
                KnowledgeSnapshot.from_dict(snapshot_payload)
                if snapshot_payload is not None
                else None
            ),
            request_id=payload.get("request_id"),  # type: ignore[arg-type]
            no_answer_reason=(
                NoAnswerReason(reason) if reason is not None else None  # type: ignore[arg-type]
            ),
            usage=(
                AttemptUsage.from_dict(payload.get("usage"))
                if payload.get("usage") is not None
                else None
            ),
            legacy_no_sources=bool(payload.get("legacy_no_sources", False)),
        )


def _check_sources_snapshot(sources: tuple[Source, ...], snapshot: KnowledgeSnapshot) -> None:
    labels: set[str] = set()
    for source in sources:
        if (
            source.project_id != snapshot.project_id
            or source.generation_id != snapshot.generation_id
            or source.knowledge_version != snapshot.knowledge_version
        ):
            raise ValueError("source не принадлежит snapshot запроса")
        if source.citation_label in labels:
            raise ValueError("citation_label должен быть уникальным")
        labels.add(source.citation_label)


@dataclass(frozen=True, slots=True)
class AskError:
    """Типизированная ошибка потока. ``detail`` — только для логов."""

    code: AskErrorCode
    retryable: bool
    detail: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", AskErrorCode(self.code))
        if not isinstance(self.retryable, bool):
            raise ValueError("retryable должен быть bool")
        if self.code is AskErrorCode.CANCELLED and self.retryable:
            raise ValueError("cancelled не может быть retryable")
        _optional_text(self.detail, field="detail")


@dataclass(frozen=True, slots=True)
class AskEvent:
    """Событие потока. Первый ``final`` терминален; ``error``/``cancelled`` — тоже."""

    type: AskEventType
    request_id: str
    seq: int
    text: str | None = None
    result: AskResult | None = None
    error: AskError | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "type", AskEventType(self.type))
        _text(self.request_id, field="request_id")
        _int(self.seq, field="seq", minimum=1)

        if self.type is AskEventType.STATUS:
            if self.result is not None or self.error is not None:
                raise ValueError("status не содержит result/error")
        elif self.type is AskEventType.DELTA:
            _chunk_text(self.text, field="text")
            if self.result is not None or self.error is not None:
                raise ValueError("delta не содержит result/error")
        elif self.type is AskEventType.FINAL:
            if self.result is None or self.error is not None:
                raise ValueError("final требует result и не содержит error")
            if self.text is not None:
                raise ValueError("final не содержит text")
            if self.result.request_id is not None and self.result.request_id != self.request_id:
                raise ValueError("request_id результата не совпадает с событием")
        elif self.type is AskEventType.ERROR:
            if self.error is None or self.result is not None or self.text is not None:
                raise ValueError("error требует error и не содержит result/text")
            if self.error.code is AskErrorCode.CANCELLED:
                raise ValueError("cancelled должен быть отдельным событием")
        else:  # CANCELLED
            if self.error is None or self.result is not None or self.text is not None:
                raise ValueError("cancelled требует error и не содержит result/text")
            if self.error.code is not AskErrorCode.CANCELLED:
                raise ValueError("cancelled требует AskErrorCode.CANCELLED")


class LLMUnavailableError(Exception):
    """ИИ-часть недоступна (сеть, таймаут, ошибка сервиса)."""

    retryable = True


class LLMContractError(LLMUnavailableError):
    """Gateway нарушил контракт (snapshot/sources). Это не no-answer."""

    retryable = False


class LLMCancelledError(LLMUnavailableError):
    """Генерация/поток отменены. Не является no-answer."""

    retryable = False


class AskStreamContractError(LLMContractError):
    """Нарушена последовательность AskEvent."""


class _StreamState:
    __slots__ = ("_request_id", "_next_seq", "_terminal", "_saw_delta", "_result")

    def __init__(self, request_id: str) -> None:
        self._request_id = request_id
        self._next_seq = 1
        self._terminal: AskEventType | None = None
        self._saw_delta = False
        self._result: AskResult | None = None

    def observe(self, event: AskEvent) -> None:
        if event.request_id != self._request_id:
            raise AskStreamContractError("event request_id does not match stream")
        if event.seq != self._next_seq:
            raise AskStreamContractError(f"event seq {event.seq} != expected {self._next_seq}")
        self._next_seq += 1
        if self._terminal is not None:
            raise AskStreamContractError("event after terminal event")
        if event.type is AskEventType.DELTA:
            self._saw_delta = True
            return
        if event.type is AskEventType.FINAL:
            # После delta нельзя завершить no-answer: пользователь уже видел часть.
            if self._saw_delta and not event.result.found:  # type: ignore[union-attr]
                raise AskStreamContractError("found=False after delta")
            self._terminal = AskEventType.FINAL
            self._result = event.result
        elif event.type is AskEventType.ERROR:
            self._terminal = AskEventType.ERROR
        elif event.type is AskEventType.CANCELLED:
            self._terminal = AskEventType.CANCELLED

    def finish(self) -> AskResult:
        if self._terminal is None:
            raise AskStreamContractError("stream ended without terminal event")
        if self._terminal is AskEventType.FINAL:
            if self._result is None:
                raise AskStreamContractError("final without result")
            return self._result
        if self._terminal is AskEventType.ERROR:
            raise LLMUnavailableError("stream ended with error")
        raise LLMCancelledError("stream cancelled")


async def iter_validated_events(
    stream: AsyncIterator[AskEvent], request_id: str
) -> AsyncIterator[AskEvent]:
    """Отдавать события по мере прихода, проверяя seq/request_id/terminal.

    Delta/status уходят сразу, без буферизации готового ответа. На terminal
    событии итератор сначала закрывает upstream, затем отдаёт terminal и
    завершается; error/cancelled остаются типизированными событиями. Повторный
    terminal после первого не читается: для конечных последовательностей его
    проверяет ``validate_event_sequence``.
    """
    state = _StreamState(request_id)
    closed = False

    async def close_once() -> None:
        nonlocal closed
        if closed:
            return
        closed = True
        await _close_stream(stream)

    try:
        async for event in stream:
            state.observe(event)
            if event.type in (
                AskEventType.FINAL,
                AskEventType.ERROR,
                AskEventType.CANCELLED,
            ):
                await close_once()
                yield event
                return
            yield event
        state.finish()
    finally:
        await close_once()


async def _close_stream(stream: AsyncIterator[AskEvent]) -> None:
    close = getattr(stream, "aclose", None)
    if close is not None:
        await close()


async def consume_stream(stream: AsyncIterator[AskEvent], request_id: str) -> AskResult:
    """Довести поток до terminal, закрыть upstream и вернуть final.

    ``error``/``cancelled`` закрывают поток и поднимают типизированную ошибку;
    частичный текст не возвращается и не должен кэшироваться. Повторный terminal,
    неверный seq/request_id и отсутствие terminal — ``AskStreamContractError``.
    """
    state = _StreamState(request_id)
    try:
        async for event in stream:
            state.observe(event)
    finally:
        close = getattr(stream, "aclose", None)
        if close is not None:
            await close()
    return state.finish()


def validate_event_sequence(events: Iterable[AskEvent], request_id: str) -> AskResult:
    """Синхронная проверка уже собранной последовательности для тестов/адаптеров."""
    state = _StreamState(request_id)
    for event in events:
        state.observe(event)
    return state.finish()


@dataclass(frozen=True, slots=True)
class DocFile:
    filename: str
    content: bytes


class LLMGateway(ABC):
    """Что бот ожидает от ИИ-части. ``snapshot`` фиксирует project/version scope."""

    @abstractmethod
    async def ask(
        self,
        project_id: int,
        question: str,
        *,
        snapshot: KnowledgeSnapshot | None = None,
    ) -> AskResult:
        """Ответить на вопрос по документации проекта.

        ``found=False`` — в документации ответа нет; ``no_answer_reason``
        объясняет причину. ``found=True`` содержит Markdown-ответ и, для RAG,
        ``sources`` в том же snapshot.
        """

    async def ask_stream(
        self,
        project_id: int,
        question: str,
        request_id: str,
        *,
        snapshot: KnowledgeSnapshot | None = None,
    ) -> AsyncIterator[AskEvent]:
        """Совместимый final-only путь для реализаций без потоковой генерации."""
        result = await self.ask(project_id, question, snapshot=snapshot)
        yield AskEvent(type=AskEventType.FINAL, request_id=request_id, seq=1, result=result)

    @abstractmethod
    async def ask_image(
        self,
        project_id: int,
        image: bytes,
        caption: str | None,
        *,
        snapshot: KnowledgeSnapshot | None = None,
    ) -> AskResult:
        """Ответить по скриншоту (с необязательной подписью пользователя)."""

    @abstractmethod
    async def upload_documents(self, project_id: int, mode: DocsMode, files: list[DocFile]) -> str:
        """Проиндексировать документацию проекта.

        Публикация generation/version/provenance — PERS-43/48, не этот контракт.
        """

    @abstractmethod
    async def save_knowledge(self, project_id: int, question: str, answer: str) -> None:
        """Сохранить проверенный админом ответ в базу знаний проекта."""

    async def close(self) -> None:  # noqa: B027 — необязательный хук, не абстрактный
        """Освободить ресурсы при остановке бота."""


class StubLLMGateway(LLMGateway):
    """Заглушка без ИИ: типизированный no-answer, документация не индексируется."""

    async def ask(
        self,
        project_id: int,
        question: str,
        *,
        snapshot: KnowledgeSnapshot | None = None,
    ) -> AskResult:
        return AskResult(
            found=False,
            answer=None,
            snapshot=snapshot,
            no_answer_reason=NoAnswerReason.NO_EVIDENCE,
        )

    async def ask_image(
        self,
        project_id: int,
        image: bytes,
        caption: str | None,
        *,
        snapshot: KnowledgeSnapshot | None = None,
    ) -> AskResult:
        return AskResult(
            found=False,
            answer=None,
            snapshot=snapshot,
            no_answer_reason=NoAnswerReason.NO_EVIDENCE,
        )

    async def upload_documents(self, project_id: int, mode: DocsMode, files: list[DocFile]) -> str:
        action = "заменена" if mode == DocsMode.REPLACE else "дополнена"
        names = ", ".join(f.filename for f in files)
        return f"Документация {action}. Файлы: {names}"

    async def save_knowledge(self, project_id: int, question: str, answer: str) -> None:
        logger.info("StubLLMGateway: save_knowledge skipped for project %s", project_id)
