# PERS-42: контракт RAG v1

Статус: draft PERS-42. Контракт описывает bot-facing границу и тесты; реальная
индексация, публикация и retrieval — PERS-43/48. ADR: `docs/rag/architecture.md`.

## Границы

- PERS-42: типы `KnowledgeSnapshot`, `Source`, `AskResult`, `AskError`,
  `AskEvent`; project/generation scope; типизированные no-answer/unavailable/
  cancelled; последовательность событий; сохранение sources в
  `QuestionService`, Redis и Telegram.
- PERS-48 (не реализовано здесь): candidate generation, PostgreSQL CAS,
  `docs_version`/`knowledge_version`, operation, `DocUpdate`/provenance, outbox,
  point ID и retention. Пример транзакции ниже — **design**, не реализация.
- PERS-43: ingestion/retrieval. PERS-44: реальный provider streaming/UI.
  PERS-45: eval/no-answer gate. PERS-42 не доказывает качество RAG.

## Snapshot

```python
@dataclass(frozen=True, slots=True)
class KnowledgeSnapshot:
    project_id: int
    generation_id: str
    knowledge_version: int
```

Правила:

- snapshot читается **один раз** в начале запроса;
- retrieval, prompt, источники и финальный ответ используют только его;
- повторное чтение latest после каждого шага запрещено;
- `Source` обязан совпадать с snapshot по `project_id`, `generation_id`,
  `knowledge_version`;
- `KnowledgeSnapshot.compat(project_id, docs_version)` — временный маркер
  `compat-docs-N` для stub/fake. Это **не** подтверждённый published generation
  и не должно попадать в production-публикацию PERS-48.

## Source

```python
@dataclass(frozen=True, slots=True)
class Source:
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
```

- `citation_label` уникален в пределах результата.
- `document_id`/`chunk_id`/`generation_id` — внутренние идентификаторы; в UI они
  не показываются.
- Источники собирает сервер из отфильтрованных hits. Модель не может добавить
  source.
- Структурная проверка source/snapshot — проверка scope, **не** доказательство
  смысловой опоры. Groundedness/eval — PERS-45.

## AskResult

```python
@dataclass(frozen=True, slots=True)
class AskResult:
    found: bool
    answer: str | None
    sources: tuple[Source, ...] = ()
    snapshot: KnowledgeSnapshot | None = None
    request_id: str | None = None
    no_answer_reason: NoAnswerReason | None = None
    legacy_no_sources: bool = False
```

Правила:

- `found=True`: непустой `answer` и **проверенные sources** в том же snapshot.
  `legacy_no_sources=True` существует только для тестов/переходного legacy
  адаптера; `QuestionService` отклоняет такой ответ от real gateway типизированной
  ошибкой `LLMContractError`, а не превращает в no-answer.
- `found=False`: `answer is None`, `sources == ()`, `no_answer_reason` заполнен
  (`NO_EVIDENCE` или `NOT_ANSWERABLE`). Ошибка сервиса сюда не относится.
- `LLMUnavailableError` — сервис/сеть/таймаут. `LLMContractError` — нарушение
  контракта (нет snapshot/sources, чужой snapshot). `LLMCancelledError` — отмена.
  Ни одно из этих исключений не является no-answer и не кэшируется.
- `AskResult.to_json()`/`from_json()` — версионированный формат (`v=1`) для
  Redis. Битый/старый JSON — cache miss, не ответ.

Пример успешного ответа:

```python
AskResult(
    found=True,
    answer="Откройте «Профиль → Доступ».",
    sources=(
        Source(
            project_id=7,
            generation_id="gen-2026-11-05",
            knowledge_version=4,
            document_id="doc-access",
            document_version="3",
            chunk_id="chunk-12",
            title="Настройка доступа",
            excerpt="Профиль → Доступ",
            citation_label="[1]",
            section_path="Профиль → Доступ",
            page=12,
        ),
    ),
    snapshot=KnowledgeSnapshot(7, "gen-2026-11-05", 4),
    request_id="req-1",
)
```

No-answer:

```python
AskResult(
    found=False,
    answer=None,
    snapshot=snapshot,
    no_answer_reason=NoAnswerReason.NOT_ANSWERABLE,
)
```

## Usage / attempt

```python
class UsageStatus(StrEnum):
    REPORTED = "reported"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


class CostStatus(StrEnum):
    REPORTED = "reported"
    RECONCILED = "reconciled"
    ESTIMATED = "estimated"


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_output_tokens: int | None = None
    status: UsageStatus = UsageStatus.UNKNOWN


@dataclass(frozen=True, slots=True)
class AttemptUsage:
    request_id: str
    attempt_id: str
    usage: TokenUsage
    cost: Decimal | None = None
    cost_status: CostStatus | None = None
    currency: str | None = None
    rate_version: str | None = None
    priced_at: datetime | None = None
```

Правила:

- `None` — unknown, `0` — известно ноль. `status=UNKNOWN` требует все counts
  `None`; cost при этом может быть известен отдельно (см. ниже).
- `REPORTED`/`ESTIMATED` требует хотя бы один известный count.
- `cached_input_tokens ⊆ input_tokens`, `reasoning_output_tokens ⊆ output_tokens`.
  Свойства `uncached_input_tokens` и `non_reasoning_output_tokens` — остатки,
  **не** «billable»: cached/reasoning тоже могут тарифицироваться. Никакого
  повторного сложения cache/reasoning в `total_tokens`.
- `request_id` общий для retry-попыток; `attempt_id` уникален для попытки.
  `AskResult.request_id` и `AskEvent.request_id` обязаны совпадать с
  `usage.request_id`. `attempt_id` выделяется до первого чтения provider;
  `error`/`cancelled`/timeout/contract violation несут usage, даже если
  успешного final нет. Если provider не вызывался, attempt не создаётся.
- `cost` — только reported/reconciled/estimated provider cost (Decimal), не
  расчёт по тарифам. Provenance стоимости (`cost_status`) отделён от статуса
  токенов: известная сумма допустима при unknown counts (например, счёт
  провайдера после оборванного потока), если есть `cost_status`, ISO 4217
  `currency`, `rate_version` и timezone-aware `priced_at`. Без `cost` все эти
  поля должны быть `None`. Из неизвестных токенов cost не вычисляется.
- `AskResult.usage` nullable; JSON (`v=1`) сохраняет status/counts/cost/metadata.
  Постоянный ledger и агрегация — PERS-48, не PERS-42.

Пример reported usage:

```python
AttemptUsage(
    request_id="req-1",
    attempt_id="attempt-1",
    usage=TokenUsage(
        input_tokens=1000,
        output_tokens=500,
        cached_input_tokens=400,
        reasoning_output_tokens=100,
        status=UsageStatus.REPORTED,
    ),
    cost=Decimal("0.0042"),
    cost_status=CostStatus.REPORTED,
    currency="USD",
    rate_version="tariff-2026-11",
    priced_at=datetime(2026, 11, 5, tzinfo=UTC),
)
# uncached_input_tokens == 600, non_reasoning_output_tokens == 400;
# total_tokens == 1500, cached/reasoning не прибавляются повторно.
```

Unknown usage — не ноль:

```python
AttemptUsage(
    request_id="req-2",
    attempt_id="attempt-2",
    usage=TokenUsage(status=UsageStatus.UNKNOWN),
)
```

Известная сумма при неизвестных токенах (сверка счёта):

```python
AttemptUsage(
    request_id="req-2",
    attempt_id="attempt-2",
    usage=TokenUsage(status=UsageStatus.UNKNOWN),
    cost=Decimal("0.10"),
    cost_status=CostStatus.RECONCILED,
    currency="USD",
    rate_version="provider-invoice-2026-11",
    priced_at=datetime(2026, 11, 5, tzinfo=UTC),
)
```

## AskEvent и последовательность

```python
@dataclass(frozen=True, slots=True)
class AskError:
    code: AskErrorCode  # UNAVAILABLE | BUSY | CANCELLED | CONTRACT_VIOLATION
    retryable: bool
    detail: str | None = None  # только логи


@dataclass(frozen=True, slots=True)
class AskEvent:
    type: AskEventType  # STATUS | DELTA | FINAL | ERROR | CANCELLED
    request_id: str
    seq: int
    text: str | None = None
    result: AskResult | None = None
    error: AskError | None = None
    usage: AttemptUsage | None = None  # только ERROR/CANCELLED
```

- `delta.text` может быть пробелом, переводом строки или markdown-разметкой;
  проверяется тип и `len > 0`, без `strip()`.
- `status` не содержит result/error; `delta` не содержит result/error.
- `final` содержит result и не содержит error/text; `request_id` результата
  совпадает с событием.
- `error` содержит `AskError` (кроме CANCELLED); `cancelled` — только
  `AskErrorCode.CANCELLED`. Это terminal-события. `BUSY` означает, что
  process-local concurrency-лимит занят до provider-вызова; такая ошибка не
  несёт `attempt_id`/usage/ledger-запись.
- `usage` допустим только на `error`/`cancelled` и обязан иметь тот же
  `request_id`. Reported usage сохраняется, если provider успел его прислать;
  иначе `TokenUsage(status=UNKNOWN)` с counts `None`, не ноль. Для `final`
  usage лежит в `result.usage`. `consume_stream` переносит usage terminal
  события в `LLMUnavailableError`/`LLMCancelledError`; это не ledger.
- `consume_stream(stream, request_id)` проверяет:
  - `request_id` одинаковый, `seq` строго `1,2,3...`;
  - ровно один terminal и он последний; события после terminal запрещены;
  - завершение без terminal — `AskStreamContractError`;
  - `delta → error` допустим, но это `LLMUnavailableError`, не no-answer;
  - `found=False` после хотя бы одной delta запрещён;
  - `cancelled` закрывает upstream (`aclose`) и поднимает `LLMCancelledError`;
  - частичный текст не возвращается и не должен кэшироваться.
- `iter_validated_events(stream, request_id)` — AsyncIterator для PERS-44:
  отдаёт `status`/`delta` сразу, не буферизуя готовый ответ, проверяет
  `request_id`/`seq`, на terminal закрывает upstream **ровно один раз** (включая
  путь отмены/`aclose`) и завершается. Тест доказывает, что первая delta
  приходит до final (final ждёт внешний gate), и что повторное закрытие
  неидемпотентного upstream не происходит. Streaming router в PERS-42 не включён.
- `validate_event_sequence(...)` — синхронная проверка того же для тестов.

## Redis cache

- Новый namespace: `cache:answer:v1:{project_id}:{knowledge_version}:{question_hash}`.
  Старые `cache:answer:*` не читаются.
- Хешируется полный уже провалидированный текст: trim на границах делает
  `QuestionService._validate`, внутренние пробелы/переносы/индентация и регистр
  сохраняются. Числа, отрицания и регистрозависимые идентификаторы не
  схлопываются.
- В кэш попадает только `found=True` с непустыми валидными sources и текущим
  snapshot. `found=False`, ошибка, отмена и незавершённый поток не кэшируются.
- Cache hit с другим snapshot, без sources, с legacy-строкой или битым JSON —
  miss; после miss ответ заново идёт в gateway с тем же snapshot, latest не
  подставляется.
- Cache hit восстанавливает `sources`; `AskOutcome.sources` содержит те же
  источники, что были на первом ответе.
- В кэш пишется `AskResult.to_cache_json()`: `request_id` и `usage`/cost
  обнуляются. Cache hit — историческая запись, не новая попытка; повторной
  стоимости/токенов не появляется. Попытка учитывается только на реальном
  вызове gateway; постоянный ledger — PERS-48. Старый cache JSON с usage или
  request_id считается miss, а не исторической стоимостью.

## Telegram

`views.with_sources(answer, sources)` добавляет серверный блок:

```text
Ответ

**Источники:**
- [1] — Настройка доступа — Профиль → Доступ — стр. 12
```

Показываются только `citation_label`, `title`, `section_path`, `page`.
`document_id`, `chunk_id`, `generation_id` и внутренние пути не показываются.
`title` — строгий display title: `Source` отклоняет URL/URI любой схемы
(`https://`, `tg://`, `file://`, …) и абсолютные локальные пути; ingest обязан
передавать название раздела, а не путь/секрет. `title` и `section_path` проходят
серверный whitelist (без Markdown/HTML-символов, URL/URI и локальных путей), а
Rich Message с источниками отправляется с `skip_entity_detection=True`, чтобы
ссылки/кнопки из метаданных не исполнялись.
Форматирование применяется в `deliver_outcome`, поэтому одинаково работает в
личке и в группе; cache hit и cache miss дают одинаковый пользовательский текст.
Rich Message с источниками уходит через существующий `sendRichMessage` с
plain-text fallback.

## Stub / fake

- `StubLLMGateway.ask`/`ask_image` возвращают `found=False`,
  `no_answer_reason=NO_EVIDENCE`; `ask_stream` по умолчанию final-only.
- `FakeLLMGateway` — явный тестовый адаптер: если тестовый ответ `found=True` без
  sources, он синтезирует source из переданного snapshot. Это не пример для
  production.
- `legacy_no_sources=True` позволяет сконструировать переходный ответ в тестах,
  но `QuestionService` всё равно отклоняет его как `LLMContractError`.

## Обновления и единый publisher — design PERS-48, не реализовано

PERS-42 не публикует generation и не bump-ит `docs_version`. Требование:

- ingestion пишет candidate generation с `project_id` + `generation_id` в payload
  и в point ID (UUIDv5 от `project_id:generation_id:chunk_id`);
- активная generation не мутируется;
- одна PG-транзакция атомарно фиксирует `published_generation_id` через CAS,
  `docs_version`/`knowledge_version`, operation, `DocUpdate`/provenance и
  outbox-событие (если outbox реализован);
- отдельный bump `docs_version` в `DocsService.apply_update` после LLM не
  допускается;
- reader читает пару generation/version один раз и держит snapshot до конца;
- старые generation живут до завершения readers и retention, не только grace.

Схема (design, не выполнено):

```sql
BEGIN;
UPDATE knowledge_generations
   SET published_generation_id = :new_generation
 WHERE project_id = :project_id
   AND published_generation_id = :expected_generation;
UPDATE projects
   SET docs_version = :new_version
 WHERE id = :project_id;
UPDATE knowledge_operations
   SET state = 'published'
 WHERE id = :operation_id;
INSERT INTO doc_updates (...) VALUES (...);
INSERT INTO outbox (...) VALUES (...);
COMMIT;
```

Если CAS не обновил ни одной строки или любой шаг упал — COMMIT не публикует
ничего; candidate остаётся candidate. Это ещё не реализовано в PERS-42.

## Проверки PERS-42

- `tests/llm/test_contract_v1.py` — snapshot/source/result/event/sequence,
  whitespace deltas, no-answer/error/cancel, closing upstream.
- `tests/services/test_question_sources_cache.py` — sources через service и
  cache, legacy/malformed/old snapshot miss, found без sources = contract error,
  code indentation/case/numbers.
- `tests/bot/test_sources_rendering.py` — источники в личке и группе, cache
  miss/hit, отсутствие внутренних id.
- `tests/storage/redis/test_redis_works.py` — namespace v1, exact hash, TTL.

Не утверждается: качество retrieval/groundedness, реальный provider streaming,
PERS-48 publisher и PERS-44 Telegram streaming.
