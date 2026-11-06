# INN-8: публикация snapshot, real gateway и usage ledger

Статус: реализован минимальный безопасный срез publication + real gateway +
persistent attempt ledger + private-chat streaming draft/final/stop. **INN-8 не
закрыт**: migration/CAS не проверены на реальном PostgreSQL, live
provider/Telegram тесты не выполнялись, групповая ACL (INN-10) не реализована —
real-mode группы fail-closed.

## Публикация snapshot

PG — единственный authority опубликованной generation.

- `projects.active_generation_id` + `projects.docs_version` задают активную
  пару generation/version.
- `knowledge_generations` хранит историю: `generation_id`, `knowledge_version`,
  `manifest_hash`, mode, base generation, автора и `published_at`.
- `PostgresKnowledgeWork.publish` в одной транзакции:
  - блокирует project row (`SELECT FOR UPDATE`);
  - проверяет `docs_version == expected_version`;
  - для APPEND проверяет, что base указан и совпадает с текущей active
    generation внутри той же блокировки;
  - для REPLACE запрещает base;
  - вставляет новую generation;
  - делает version CAS и переключает `active_generation_id`;
  - создаёт `DocUpdate`;
  - не удаляет старые generation.
- Конкурентный publish: один выигрывает CAS, второй получает
  `KnowledgePublishConflict`. Старая active generation остаётся в истории.
- `PostgresKnowledgeWork.get` читает PG pointer + matching generation row под
  одним lock. Несогласованный pointer — `PublishedSnapshotUnavailable`, не
  `None`.
- `get_exact` проверяет pinned historical generation. Если публикация случилась
  между чтением snapshot и gateway, запрос продолжает использовать pinned
  generation/version; если строка/retention удалена — fail-closed, без
  смешивания поколений. Qdrant points старых generation не удаляются.

Qdrant manifest остаётся `candidate` и проверяется как integrity check. Он не
является вторым publish switch: статус в Qdrant не меняется.

## Ингест

`RagIngestionService` — real-mode путь загрузки документов.

- `RagIndex.index_generation` строит candidate и manifest.
- Перед PG CAS вызывающая сторона проверяет candidate manifest:
  project, generation, ожидаемую новую version, manifest hash, chunk count,
  mode и base generation. Одного `generation_id` недостаточно.
- REPLACE поддержан. **APPEND отклоняется fail-closed**: бот пока не хранит
  полный предыдущий корпус, поэтому честный merge невозможен.
- При конфликте публикации candidate остаётся инертным; повтор создаёт новую
  generation.
- `DocsService` в real-mode использует publisher и не вызывает
  `llm.upload_documents`/`bump_docs_version` повторно. Stub/legacy путь явный и
  не заявляет published generation.

## Snapshot и gateway

- `QuestionService` принимает optional `PublishedSnapshotProvider`. В real-mode
  provider обязателен; `None`/missing generation — fail-closed, без compat
  fallback.
- Stub/test режим по-прежнему использует `KnowledgeSnapshot.compat`.
- `RagChatGateway` отвергает compat. Для явно переданного snapshot используется
  `get_exact` (pinned reader), а не повторное чтение latest.
- Retrieval идёт только по scoped `Source` проверенного snapshot; candidate
  manifest проверяется `RagIndex`.
- `RagError` при retrieval превращается в typed `LLMUnavailableError`, не в
  no-answer.

## Provider

`app/llm/openai_stream.py` — настраиваемый OpenAI-compatible SSE adapter
(`/v1/chat/completions`), не заявление о выбранном коммерческом провайдере.

- `base_url`, `model`, `api_key`, `timeout_seconds`, `max_output_tokens`,
  `chat_completions_path`, `include_usage` передаются явно; модель не
  переписывается.
- `max_output_tokens` обязателен, проверяется как integer `1..131072` и
  уходит в OpenAI-compatible payload как `max_tokens`. Это не заявление о
  provider-specific лимите конкретной модели.
- `stream: true`, истинные `choices[].delta.content`; успех только после
  `[DONE]`. Любой непустой `finish_reason`, кроме явного `stop`, — не успех:
  включая `length`, `content_filter`, `tool_calls`, `aborted`,
  `insufficient_system_resource` и неизвестные значения. Usage из того же
  события сохраняется перед fail-closed ошибкой.
- `reasoning_content` игнорируется.
- `include_usage` — explicit capability; по умолчанию выключена. Пустые
  `base_url`/`api_key`/`model`, невалидный timeout и невалидный
  `max_output_tokens` отклоняются fail-closed.
- `max_retries != 0` запрещён до explicit attempt ledger/INN-12.
- Секреты, тело запроса и ответа не логируются.

Одновременные provider generation в процессе ограничены
`max_concurrent_generations` (`LLM_MAX_CONCURRENT_GENERATIONS`, default 1,
1..64). В DI лимитер — cached singleton процесса и передаётся в
`RagChatGateway`; прямые тестовые экземпляры могут иметь свой limiter.
Занятость — fail-fast: gateway отдаёт terminal `busy` до создания
`attempt_id`, provider-вызова и ledger-записи. Слот освобождается в `finally`
для final/error/cancel/timeout/`aclose`/`GeneratorExit`. Это process-local
лимит; несколько worker требуют общего лимитера.

Целевые модели — DeepSeek-V4.1-Flash-1017 или GLM-5.3-Flash; конкретный
endpoint ещё не выбран. Реальные capabilities проверяются позже.

## Gateway и ledger

- `stop_event`/deadline гоняются с pending `anext`; при stop/таймауте pending
  чтение отменяется, upstream закрывается, terminal типизирован.
- При занятости concurrency-лимита terminal `busy` выдаётся до provider-вызова;
  `attempt_id`/usage/ledger не создаются.
- Partial текст не кэшируется и не отдаётся как final.
- `attempt_id` создаётся до первого чтения provider.
- `error`/`cancelled`/timeout/contract violation несут `AskEvent.usage` с тем
  же `request_id`: reported usage, если provider успел его прислать, иначе
  `unknown` с `None`, не ноль.
- `llm_attempts` — persistent direct ledger: project, request, attempt,
  outcome, error code, usage counts/status и cost metadata. Replay с тем же
  payload идемпотентен; другой project/request/outcome/counts для того же
  `attempt_id` — `UsageLedgerConflict`.
- Ledger пишется до final. Если запись не удалась, successful final не
  выдаётся: gateway отдаёт terminal `unavailable`, чтобы не объявлять учёт
  успешным. Ошибка/cancel не превращаются в success.
- При внешней `asyncio.CancelledError` или `aclose()`/`GeneratorExit` после
  старта provider gateway делает best-effort запись `cancelled` с unknown usage.
  Точный пробел: если отмена пришлась ровно на незавершённую запись ledger,
  durable retry/outbox отсутствует — это INN-12. Если provider ещё не
  вызывался, attempt не создаётся.
- Это не полный ledger INN-12: нет outbox, reconciliation, агрегации и
  повторной доставки записей.

## Telegram

Private-chat streaming подключён к question router. Group streaming не включён:
real-mode группы fail-closed до INN-10.

Совместимость с PR #2: в `stub`/final-only режиме сохранён replay-слой
`app/bot/streaming.py` (черновик с кадрами, групповая заглушка). В
`RAG_MODE=real` используется true event-stream (`QuestionService.ask_text_stream`
→ `TelegramStreamDelivery`). Это два взаимоисключающих пути, дублирующих
сообщений нет. Stop-handler обслуживает оба реестра: `GenerationRegistry` для
replay-задач и `ActiveDraftRegistry` для true-stream draft. `/stop` ограничен
chat+thread+actor в обоих случаях.

- Private chat: stable draft id, throttle/coalesce, preliminary marker, URL
  defang, `skip_entity_detection`.
- Final отправляется один раз; no-answer final не отправляется пустым
  сообщением.
- Stop/final сериализованы; stop побеждает до отправки, retry только 429,
  plain fallback только для известных форматных BadRequest.
- Stop приходит и из `stopped_message_generation`, и из `/stop`.
- `ActiveDraftRegistry` авторизует stop по `chat_id + message_thread_id +
  actor_user_id + draft_id`, а не по одному 31-bit id.
- Один активный streaming-запрос на `chat/thread`: второй вопрос отклоняется
  `ANSWER_IN_PROGRESS` («дождитесь или /stop»), без перезаписи и без утечки
  первого draft. `/stop` останавливает только текущий chat+thread+actor.
- Draft ids уникальны среди активных и не переиспользуются сразу; stale stop
  после unregister — no-op; `unregister` удаляет только свой registration.
  Регистрация атомарна в event loop. Это process-local: multi-worker требует
  общего сигнала.
- Групповой ACL INN-10 не закрыт. Пока в real-mode любые group answers
  отклоняются `GroupAnswersUnavailable`, включая чужого пользователя/группу.

## Режимы

- `RAG_MODE=stub` — explicit dev/test, compat snapshot, без публикации.
- `RAG_MODE=real` — требует `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` и
  Qdrant config. При нехватке конфигурации startup падает fail-closed и не
  подменяется stub. При отсутствии published generation вопрос получает
  unavailable, не compat-ответ.

## Проверки

Локально без Docker/рабочих БД:

```bash
make -C tg_bot test-local ARGS='tests/bot tests/services tests/storage tests/llm tests/rag/test_ingestion_publication.py tests/test_dependencies_rag.py -q'
```

Покрыто: PG pointer/CAS/retention на SQLite, pinned historical snapshot,
stale conflict, APPEND/REPLACE policy, manifest scope/hash mismatch до CAS,
ledger idempotency/conflict, unknown cancel, ledger failure не даёт success,
external cancellation, fail-closed DI, no-answer без пустого Telegram final,
private streaming stop через update/`/stop`, stop из чужого chat/actor,
независимые thread, отказ второго запроса в том же thread, уникальные draft
ids, stale/unregister, real-mode group ACL fail-closed.

Postgres-only concurrent publish test помечен skip без `TEST_POSTGRES_URL`.
Реальный Postgres/Redis и live provider **не проверены**: Docker не отвечает,
рабочие БД и Redis DB0 не используются.

## Остаётся

1. Проверить migration и CAS на выделенной одноразовой Postgres БД; Docker
   сейчас не отвечает, поэтому эта часть **не закрыта**.
2. Выбрать endpoint/ключ, проверить `include_usage` и выполнить real provider
   test; live Telegram streaming/stop test с отдельным token.
3. INN-10: авторизация/аудитория group answers. До неё real-mode группы
   fail-closed.
4. Полный INN-12: outbox/reconciliation ledger, corpus storage для честного
   APPEND, retention/агрегация. Повторная загрузка после неоднозначного
   ответа не идемпотентна и может опубликовать следующую version.
5. Process-local stop registry: для multi-worker нужен общий сигнал.
6. Строгая groundedness-проверка цитат.
