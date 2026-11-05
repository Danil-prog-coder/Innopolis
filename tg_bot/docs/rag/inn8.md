# INN-8: RAG gateway, streaming provider и Telegram draft/final

Статус: реализован модульный срез поверх INN-7. В текущий router **не
подключён**: реальный published snapshot publisher (INN-12) ещё отсутствует.
Реальные provider/Telegram live-тесты не выполнялись: ключа, endpoint и
рабочего bot token в этой задаче нет.

## Граница публикации

- `PublishedSnapshotProvider` (`app/llm/published.py`) — read-only контракт:
  вернуть атомарно опубликованный snapshot проекта или `None`.
- В production используется `UnavailablePublishedSnapshotProvider`: он всегда
  падает `PublishedSnapshotUnavailable`, пока INN-12 не реализует PG publisher.
- PG — единственный authority публикации. Qdrant manifest проверяется
  `RagIndex` как независимый integrity check, но **не** является вторым
  publish switch: статус `candidate` в Qdrant не превращается в published.
- `RagChatGateway` дополнительно отвергает `KnowledgeSnapshot.compat(...)`.
  Сейчас `QuestionService` ещё строит compat snapshot; это переходное
  состояние, и gateway намеренно не принимает его за published generation.
- Fake publisher существует только в тестах. Candidate generation нельзя
  использовать как published в production.

## Provider

`app/llm/openai_stream.py` — настраиваемый OpenAI-compatible SSE adapter
(`/v1/chat/completions`), не заявление о выбранном коммерческом провайдере.

- `stream: true`, истинные `choices[].delta.content`; пробельные delta не
  выбрасываются.
- Успех только после `data: [DONE]`. `finish_reason` `length`,
  `content_filter`, `tool_calls`, `error` считаются незавершённым потоком или
  ошибкой, а не успешным ответом.
- `reasoning_content` игнорируется и никогда не отдаётся пользователю.
- `stream_options.include_usage` — явная capability. Если сервер её не
  поддерживает, adapter не добавляет опцию; usage не выдумывается.
- `max_retries != 0` запрещён: ретрай — потенциально платная попытка, а
  attempt ledger появится в INN-12. Ошибки 429/5xx не повторяются молча.
- Секреты, тело запроса и ответа не попадают в сообщения ошибок/логи.

### Конфигурация

`base_url`, `model`, `api_key`, `timeout_seconds`, `chat_completions_path` и
`include_usage` передаются явно. Нет жёсткой привязки к OpenAI: transport
использует только OpenAI-compatible JSON/SSE-формат, модель передаётся как
есть, путь endpoint настраивается. Целевые модели — DeepSeek-V4.1-Flash-1017
или GLM-5.3-Flash; конкретный недорогой provider ещё не выбран. Реальные
provider-specific capabilities, включая `stream_options.include_usage`,
проверяются на выбранном endpoint позже; по умолчанию capability выключена.
Пустые `base_url`/`api_key`/`model` и невалидный timeout отклоняются
fail-closed. Ключ не хранится в репозитории и не выводится в лог.

```python
client = OpenAIStreamClient(
    base_url="https://<endpoint>/v1",
    api_key="<key>",
    model="DeepSeek-V4.1-Flash-1017",  # или GLM-5.3-Flash
    include_usage=False,  # включить после проверки endpoint
)
```

## Gateway

`app/llm/rag_gateway.py`: published snapshot → scoped retrieval → provider →
структурная проверка цитат → terminal event.

- Ответ строится только по `Source` из retrieval в проверенном snapshot.
- System prompt трактует источники и вопрос как недоверенные данные: встроенные
  инструкции документов игнорируются, инструменты не вызываются, скрытые
  рассуждения не показываются.
- Проверка цитат — **структурная**: метки `[n]` должны ссылаться на источники
  scoped retrieval. Это не доказательство groundedness; строгая проверка
  остаётся отдельной задачей.
- Нет источников → типизированный `found=False`/`NO_EVIDENCE` без вызова
  модели. Неверная цитата → `contract_violation`, не no-answer.
- `stop_event` и deadline гоняются с pending `anext`: при остановке/таймауте
  pending чтение отменяется, upstream закрывается, отдаётся `cancelled` или
  `unavailable`. Тест использует зависший provider и завершается меньше секунды.
- Partial текст не кэшируется и не отдаётся как final. Group получает только
  final.
- Usage берётся только из фактического usage event provider. Если usage нет,
  статус `unknown`, counts `None`; ноль не подставляется. Ledger и учёт
  стоимости — INN-12.
- `attempt_id` выделяется до первого чтения provider, только после успешного
  retrieval и проверки stop. `error`/`cancelled`/timeout/contract violation
  несут `AskEvent.usage` с тем же `request_id`: reported usage, если provider
  успел его прислать, иначе `unknown` без нулей. `ask()` пробрасывает usage в
  `LLMUnavailableError`/`LLMCancelledError`. Если provider не вызывался (нет
  источников или stop до вызова), attempt не создаётся.

## Telegram

`app/llm/telegram_stream.py` — модуль доставки, пока не подключён к router.

- Private chat: `send_rich_message_draft` со стабильным `draft_id` на request,
  throttle/coalesce по `DraftPolicy`; draft помечен «⏳ Предварительный ответ»,
  URLs вырезаны, `skip_entity_detection=True`. Draft — ephemeral preview.
- Group: drafts отключены, только финальный `send_rich_message`.
- Terminal state сериализован: stop проверяется после последнего события, перед
  отправкой и гоняется с самим Telegram-вызовом и retry-задержкой. Если stop
  победил до доставки, final не отправляется, `terminal=None`,
  `aborted_by_stop=True`. Уже завершившийся API call не отменяется задним числом.
- На `cancelled`/`error` финал не отправляется; частичный текст не становится
  сообщением.
- Retry только `429` (`TelegramRetryAfter`). Timeout/network не повторяется:
  результат доставки неоднозначен.
- Plain fallback только для известных форматных `TelegramBadRequest`
  (parse entities, unclosed/unsupported tag, слишком длинный rich text). Права,
  чат/thread и прочие 400 пробрасываются, fallback не маскирует их.
- Draft-ошибки не ломают финал.

## Что проверено локально

Только локальные HTTP SSE и fake Bot API:

```bash
make -C tg_bot test-local ARGS='tests/llm/test_openai_stream_inn8.py tests/llm/test_rag_gateway_inn8.py tests/llm/test_telegram_stream_inn8.py -q'
```

51 тест проходит: истинные delta/usage capability/`[DONE]`/finish_reason,
конфигурируемые model/base URL/path, fail-closed конфигурация, отсутствие
silent retry, отказ compat snapshot, typed `NO_ANSWER`, stop/deadline с
зависшим provider, retained usage на error/cancel/contract violation,
attempt только при вызове provider, unknown usage без нулей, draft/final,
group-only-final, preliminary/URL-safe draft, stop vs final (после события,
во время retry-задержки и in-flight send), отмена, 429-only retry и узкий
plain fallback.

## Блокеры до реального включения

1. INN-12: PG publisher, атомарная публикация generation/version, attempt
   ledger, отказ от candidate/compat в production.
2. Конфигурация provider (base URL, model, secret, timeout, usage capability) и
   отдельный real integration test.
3. Реальный Telegram bot token для live draft/final проверки; сейчас только
   fake Bot API.
4. Строгая groundedness-проверка цитат (структурная проверка уже есть).
