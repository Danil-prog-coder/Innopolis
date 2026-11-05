# PERS-41: RAG для Innopolis — ADR

Статус: draft ADR. Ниже — baseline-решения; продуктовые неизвестные перечислены
в разделе 8 и не считаются закрытыми. Дата: 2026-11-05.
База: `aa31c0580e6a0728ecd2133f9c5e56fa5fe1ab1f` (main).
Работа только в worktree `innopolis-pers41`; незакоммиченные изменения других
worktree не учитываются. Источник требований — постановка PERS-41.

## 1. Контекст: что реально есть в main

- `tg_bot/app/llm/gateway.py` в main содержит только `LLMGateway.ask`,
  `ask_image`, `upload_documents`, `save_knowledge`; в DI стоит
  `StubLLMGateway`.
- Реального поиска, индексации и генерации нет. `upload_documents` заглушки
  возвращает текстовую сводку, после чего `DocsService.apply_update` увеличивает
  `docs_version`; это не RAG.
- В `pyproject.toml` нет `qdrant-client`, `sentence-transformers`, `torch`
  и embedding-зависимостей.
- Есть рабочая обвязка бота: проекты, членство/роли, приглашения, загрузка
  файлов до 20 МБ, вопрос → ответ/передача админу, кэш ответов по
  `project_id + docs_version + question`, rate limit и обработка
  `LLMUnavailableError`.
- Контракта источников и streaming в main нет. `AskResult` содержит только
  `found` и `answer`. Всё, что делает вид, что streaming уже есть, к main не
  относится.

## 2. Карта исходных проектов

Это не зависимости и не код, который можно просто подключить. Каждый пункт —
источник паттернов, который требует переноса и проверки.

| Проект | Точный источник | Что полезно | Ограничения |
|---|---|---|---|
| **TN AI-Life** | локальный снимок `/Users/nikolaybubnov/Downloads/ai-life-deploy-stage 2`; git-ревизии нет; README фиксирует release 1.4.0 | гибридный поиск Weaviate (`BE/app/modules/weavaite/weavaite_client.py`), OCR (`pytesseract`, `easyocr`), gRPC-контур, кнопка остановки генерации | нет точной git-ревизии; `save_answer_to_cache` — заглушка (`weavaite_client.py:277-278`, `pass`), то есть записи в QA-кэш из этого снимка нет; тяжёлый стек (Weaviate/Neo4j/gRPC), нет изоляции проектов Innopolis |
| **MireaFive** | `https://github.com/tkavelli/MireaFive_team_rag-competition-reproduct`, commit `959ff5c4576c1ac736d46485eba6fc8f44ea4d00` | heading/chunking, dense retrieval, идея fusion, FAISS | README/код: Qwen3-Embedding-8B (1024d), «BM25» фактически `TfidfVectorizer` + cosine (`src/retriever.py`), fusion — convex combination 0.7/0.3; имя/флаг RRF не переключает фактический fusion; нужен NVIDIA GPU 24 ГБ; retrieval-only, без генерации/ACL |
| **GridModel support-rag** | локальный архив `/Users/nikolaybubnov/Downloads/AI Engineer/tmp/sg-systems-audit/a88eb9d/support-rag`; git HEAD `b8fc89727e6520404d366224f30f5231721d1f01` | offline extractive RAG, heading-aware chunking с anchor, цитаты «page#anchor», BM25 + dense, label-based eval (`ranx`), опциональный FlashRank | один корпус GridModel 07_UserGuide; LanceDB + numpy, нет multi-project/ACL; HTTP принимает `filters`, но в retrieval/pipeline они не применяются (grep по `filters` — только объявление в `integration/http_api.py`); в отчёте `docs/evidence/retrieval-report.json` refusal_rate = 0.0 при 8 adversarial-кейсах (0/8), то есть в этом наборе корректных отказов не наблюдалось |
| **ЦЕХ / maratsite** | `https://github.com/tkavelli/maratsite`, commit `6d0ebcdfc73ffc7cda028a349e9faf92bf743bac`; репозиторий **private**, доступ через GitHub API/права | ролевые Telegram-сценарии (`docs/specs/2026-10-15-roles-telegram-design.md`), outbox/доставка уведомлений (`drizzle/0012_telegram_outbound_messages.sql`) | это Next.js-приложение с доменной логикой заказов/сотрудников, не RAG; публичный URL без авторизации отдаёт 404; переносить можно только паттерны доставки/ролей, не модель данных |

Проверенные пути/факты:

- `ai-life-deploy-stage 2` не является git-репозиторием; README и релизные
  заметки лежат в корне. OCR-модули и Weaviate-клиент подтверждены поиском по
  `BE/app`; `save_answer_to_cache` действительно `pass`.
- `support-rag` — git-репозиторий с HEAD `b8fc897...`; README описывает
  LanceDB, BM25, heading-aware chunking, eval и локальную генерацию. Отчёт
  `docs/evidence/retrieval-report.json` показывает `refusal_rate: 0.0` на
  8 adversarial-кейсах; `filters` в HTTP API объявлены, но не применяются.
- MireaFive и maratsite проверены по GitHub API: коммиты существуют, деревья
  файлов доступны. MireaFive публичный; maratsite private (`visibility=PRIVATE`).
  README MireaFive указывает Hit@5 36.40% на соревновании — это не метрика
  Innopolis. `HybridRetriever.search_hybrid` использует `TfidfVectorizer` и
  `cosine_similarity`, а не BM25; RRF в найденном коде не реализован.

## 3. Решение

### 3.1. Один backend: Qdrant

Выбран **Qdrant** через Python-клиент `qdrant-client` 1.19.x.

- Пилот: **local path** (`QdrantClient(path=...)`), один процесс бота/индексатора.
  Клиент использует файловую блокировку (portalocker); второй процесс на тот же
  путь не откроется. Это осознанное ограничение пилота, а не production-режим.
- Несколько процессов/реплик: **server mode** Qdrant, без local path.
- Одна коллекция `kb_chunks`, в payload обязательные поля:
  `project_id`, `document_id`, `document_version`, `chunk_id`, `text`,
  `source`, `section_path`, `page`, `generation_id`.
- **Point ID глобален в коллекции.** ID не является payload и не защищён
  payload-фильтром. Поэтому ID детерминированно включает
  `project_id + generation_id + chunk_id`, например UUIDv5 от строки
  `"<project_id>:<generation_id>:<chunk_id>"`. Upsert candidate generation с
  другим `generation_id` не может перезаписать точки активной generation;
  payload-фильтр `generation_id == published` этого не гарантирует.
- Изоляция: каждый поиск обязан содержать фильтр
  `project_id == <id> AND generation_id == <published_generation_id>`; поля
  `project_id` и `generation_id` индексируются payload-индексами. Функция
  поиска не принимает запрос без обоих значений.
- `REPLACE` не удаляет активные points. Индексация пишет **candidate
  generation**: новый `generation_id`, точки с `project_id` + `generation_id`
  в payload и в ID; активная generation не мутируется и остаётся читаемой.
- Публикация — только после полной успешной индексации и проверки количества
  points: одна транзакция PostgreSQL с CAS
  `UPDATE knowledge_generations SET published_generation_id = :new
   WHERE project_id = :id AND published_generation_id = :expected`.
  Успешный CAS — единственный момент переключения читаемой версии; при
  конфликте/сбое старая generation остаётся активной, новая — candidate.
- **Та же PG-транзакция атомарно фиксирует** generation, `docs_version` /
  `knowledge_version`, состояние operation, `DocUpdate`/provenance и событие
  outbox (если outbox уже реализован). Отдельный bump `docs_version` в
  `DocsService.apply_update` после ответа LLM не допускается: иначе версия,
  generation и provenance могут разойтись. Публикация либо фиксирует всё
  вместе, либо не фиксирует ничего.
- **Reader фиксирует snapshot один раз.** В начале запроса читается пара
  `(published_generation_id, knowledge_version)` в одной транзакции/чтении и
  сохраняется до конца запроса. Поиск, сборка prompt, источники и финальный
  ответ используют этот snapshot; повторное чтение «latest» после каждого шага
  запрещено, иначе ответ может смешать две generation.
- Все чтения (поиск, prompt, сборка источников, финальный ответ) используют
  зафиксированный `published_generation_id` из PG, а не «последнюю» generation
  из Qdrant. Старые generation сохраняются до завершения активных readers
  (in-flight запросы, кэши, фоновые задачи) и истечения retention-политики;
  фиксированный grace — только минимальная страховка, а не условие удаления.
  Удаление — отдельная операция после проверки, что на generation больше нет
  ссылок. Детали — PERS-43/48.
- Кэш ответов бота должен включать `knowledge_version`/published generation,
  иначе после публикации возможен ответ по старой версии.

Альтернативы: pgvector (меньше сервисов, но нужен extension и свои миграции),
LanceDB (уже есть в support-rag, но другой профиль эксплуатации), Weaviate
(тяжелее; есть в AI-Life), FAISS (нет payload/ACL). Qdrant выбран за local/server
режимы, фильтры и переносимый Python-клиент; окончательная проверка — на eval.

### 3.2. Embedding baseline

**Модель:** `intfloat/multilingual-e5-small`.

Проверено по model card и HF API на revision:

| Параметр | Значение |
|---|---|
| Revision | `614241f622f53c4eeff9890bdc4f31cfecc418b3` |
| License | MIT |
| Размерность | 384 (`hidden_size`) |
| Слои / heads | 12 / 12 |
| Параметры | 117 654 272 (F32 safetensors) |
| Вес в fp32 | ~449 MiB (расчёт по числу параметров) |
| Токенизатор | `XLMRobertaTokenizer`; `model_max_length=512` |
| Максимум | 512 токенов (`max_position_embeddings`) |
| Префиксы | `query: ` для вопроса, `passage: ` для фрагмента |
| Нормализация | L2 (`F.normalize(..., p=2, dim=1)` / `normalize_embeddings=True`) |
| Метрика | cosine; Qdrant `Distance.COSINE` |

Runtime baseline: `sentence-transformers` 5.1.x, CPU, `revision=<sha>`,
`max_seq_length=512`, `normalize_embeddings=True`, batch 16–32. Модель
загружается один раз на процесс. CPU выбран как предсказуемый baseline для
Mac arm64; MPS/ONNX можно проверить отдельно, но не считать ускорением без
измерения.

**Бюджет 512 токенов — это весь input, а не только текст чанка.** В него входят
специальные токены `<s>`, `</s>` и префикс `passage: ` (или `query: `). Поэтому:

- размер чанка проверяется тем же токенизатором и той же revision:
  `len(tokenizer("passage: " + chunk, add_special_tokens=True)) <= 512`;
- целевой размер контента — ~400 токенов, но жёсткий предел вычисляется с
  учётом префикса и special tokens, а не задаётся как «512 контента»;
- при превышении чанк режется/переносится до эмбеддинга; молчаливая
  `truncation=True` не допускается, иначе теряется конец фрагмента;
- для вопроса проверяется полный `query: <question>`; если он не влезает в
  бюджет, вопрос отклоняется с понятной ошибкой, а не обрезается молча.

Это **baseline, а не измеренное качество**. MTEB-метрики model card относятся к
другим данным; на русской документации Innopolis качество не проверялось.

**Проверено:** revision, размерность, токенизатор, префиксы, нормализация,
`max_position_embeddings=512` — по model card/HF API на указанной revision.

**Не проверено и является нашим дизайном:** структура `Source`/`citation_label`,
`generation_id`, `section_path`, `page`, правила цитирования. Модель не выбирает
источники: они собираются из отфильтрованных результатов поиска, а не из текста
ответа.

### 3.3. Индексация и chunking

- Форматы первого среза: `.md` и `.txt` в UTF-8. Остальные форматы отклоняются
  явной ошибкой; PDF с текстовым слоем, DOCX, HTML и OCR — следующие кандидаты,
  но не входят в MVP.
- Chunking: по заголовкам/абзацам, целевой размер ~400 токенов модели,
  overlap ~50, с проверкой полного входа `passage: ` + special tokens ≤ 512
  (см. 3.2). Хранить `section_path` и позицию.
- В Qdrant хранится текст **без** префикса; префикс `passage: ` добавляется
  только при вычислении эмбеддинга. Вопрос эмбеддится с `query: `.
- Повторная загрузка того же файла в `APPEND` не дедуплицируется в MVP;
  это открытое решение.

### 3.4. Retrieval, no-answer и безопасный streaming

- Dense-only top-k=5 по cosine; без hybrid/BM25 и без reranker в MVP.
- В MVP **нет откалиброванного score threshold**. Пока его нет, при непустой
  generation все top-k фрагменты попадают в контекст; нельзя обещать, что
  нерелевантный вопрос получит отказ. Это известное ограничение, а не
  «no-answer уже работает».
- Определённый `found=False` возможен только до генерации:
  - для проекта/generation нет ни одного чанка;
  - либо сработал откалиброванный evidence-gate из PERS-45.
- Если чанки есть, а gate не откалиброван, non-streaming путь передаёт контекст
  генератору и позволяет ему вернуть `NO_ANSWER`; это решение принимается по
  полному ответу, без показа частичного текста. Качество такого отказа не
  измерено.

**Почему нельзя стримить до решения о NO_ANSWER.** Если модель может вернуть
`NO_ANSWER`, отправка дельт до финальной классификации создаёт противоречие:
пользователь уже видит часть ответа, а финал обязан быть `found=False`.
Безопасная схема — одно из двух:

1. **Предварительное решение по evidence (выбрано для PERS-44).** До первого
   draft выполняется evidence-gate: нет чанков или score ниже откалиброванного
   порога → `found=False`, draft не начинается, вопрос уходит админу. Стриминг
   разрешён только после «answerable» решения. Если провайдер всё равно вернул
   `NO_ANSWER` после начала потока, это **нарушение контракта/ошибка сервиса**,
   а не отсутствие данных: `LLMUnavailableError`, без `found=False`, без кэша.
2. **Буферизация до классификации.** Провайдер стримит в серверный буфер, ответ
   классифицируется целиком, и только потом либо отдаётся финальный
   `found=True`, либо `found=False`. Это не real-time draft и не удовлетворяет
   PERS-44 по задержке; допустимо как запасной вариант, если gate ненадёжен.

**Запрещено:** отправить draft-дельты, а затем завершить `found=False` или
показать `NO_ANSWER`. После первой дельты допустим только `found=True` с
полным ответом либо ошибка сервиса.

Разделение исходов:

| Ситуация | Результат | Draft | Кэш | Вопрос админу |
|---|---|---|---|---|
| Нет чанков для project/generation | `found=False` | нет | нет | да |
| Evidence-gate: не хватает доказательств | `found=False` | нет | нет | да |
| Ошибка Qdrant/эмбеддинга/провайдера, таймаут | `LLMUnavailableError` | нет или обрывается | нет | нет |
| `NO_ANSWER` после начала потока | `LLMUnavailableError` (contract violation) | обрывается | нет | нет |
| Пользователь/провайдер отменил | `cancelled`/`LLMUnavailableError` | обрывается | нет | нет |

- В ответе источниками считаются **только фактически переданные в контекст и
  прошедшие фильтр фрагменты**, не текст, придуманный моделью.
- До появления PERS-45 gate и PERS-42 контракта streaming-путь не включается
  в production. Non-streaming `ask` может работать с top-k и `NO_ANSWER`, но
  тоже без обещаний качества до eval.

### 3.5. Generator и streaming

- Контракт генератора: **OpenAI-совместимый OpenAI Chat Completions streaming**
  (`POST /v1/chat/completions`, `stream: true`, SSE `data:` с `choices[].delta.content`,
  завершение `[DONE]`; `stream_options.include_usage` — если провайдер поддерживает).
- Реальный провайдер, модель, endpoint и ключ **не выбраны и не подтверждены**.
  В репозитории нет ключа, и ADR не предполагает, что он есть. Без явно
  заданных `LLM_BASE_URL`, `LLM_MODEL` и секрета `LLM_API_KEY` компонент обязан
  падать с `LLMUnavailableError`, а не изображать поток.
- Ollama CLI на машине не найден. Локальный llama.cpp/vLLM и hosted-провайдер
  остаются альтернативами; выбор — открытое решение владельца/infra.
- Промпт: только переданные фрагменты; при недостатке контекста — специальный
  `NO_ANSWER`; ссылки `[n]` на переданные источники; язык ответа — язык вопроса;
  без внешних знаний. `NO_ANSWER` допустим только **до** первой дельты
  (pre-gate) или при буферизации; после начала потока это contract violation
  (см. 3.4), а не `found=False`.
- **Разделение проверенного и проектного:** префиксы `query:`/`passage:`,
  размерность 384, токенизатор и лимит 512 проверены по model card. Промпт,
  формат `Source`, `citation_label`, правила `[n]` и поведение `NO_ANSWER` —
  наш дизайн, а не свойство модели. Генератор, провайдер и ключ не
  подтверждены; никакая модель для генерации в этом ADR не выбрана.
- Streaming-контракт (события `status/delta/final/error/cancelled`, финальный
  `AskResult` с источниками, `request_id`, `generation_id`, `knowledge_version`,
  `no_answer_reason`) в main отсутствует и должен быть добавлен в PERS-42.
  PR #2 его не добавляет.

## 4. Интеграция с ботом и ACL

- Новая реализация `LLMGateway` живёт за существующей границей
  `tg_bot/app/llm/gateway.py` и подменяет `StubLLMGateway` в DI; router и
  сервисы не должны зависеть от Qdrant/провайдера напрямую.
- **Область видимости вычисляется на сервере:** `project_id` и
  `published_generation_id` берутся из аутентифицированного текущего проекта и
  PG CAS. Они не берутся из текста вопроса, callback-данных, ответа модели или
  заголовков клиента.
- **Фильтр применяется до всех ветвей:** перед vector search, перед любым
  rerank/фильтрацией, перед сборкой prompt, перед сборкой источников и перед
  финальной отправкой. Функции поиска и сборки контекста не принимают запрос
  без обоих значений; ветки «без фильтра» не существует.
- **Источники не генерирует модель.** `Source`/`citation_label` собираются
  только из фактически отфильтрованных hits; финальный парсер проверяет каждый
  citation id по множеству переданных фрагментов и отбрасывает/отклоняет
  неизвестные. Модель не может добавить источник.
- **Проверка прав перед выдачей.** Членство/текущий проект перепроверяются в
  начале запроса и непосредственно перед финальным ответом/источниками. При
  отзыве доступа или смене проекта финальный ответ и источники не выдаются.
  Уже показанный draft отозвать нельзя — это ограничение streaming-контракта;
  поэтому до его подтверждения streaming не включается. Guard-интерфейсы сами
  по себе недостаточны: нужны тесты на двухпроектную изоляцию, неверную
  generation и revoke в середине запроса.
- Кэш ответов остаётся в боте и ключуется
  `project_id + knowledge_version/published generation + question`.
  RAG-сервис не кэширует незавершённые ответы. `save_knowledge` должен
  инвалидировать кэш или увеличивать версию — текущий main этого не делает;
  это открытое решение.
- Отсутствие данных и ошибка сервиса различаются по таблице 3.4:
  `found=False` — только отсутствие доказательств/данных; сбой Qdrant,
  эмбеддинга, провайдера, таймаут или `NO_ANSWER` после начала потока —
  `LLMUnavailableError`, без создания вопроса и без кэширования.
- Загрузка файлов: существующий лимит 20 МБ и `docs_max_files`; ошибки
  индексации не должны увеличивать `docs_version`/публиковать generation.

## 5. PR #2 `feature/answer-streaming`

- URL: `https://github.com/Danil-prog-coder/Innopolis/pull/2`
- Состояние на проверке: **OPEN**, не смержен, base `main`, head
  `feature/answer-streaming`, mergeable, обновлён 2026-11-04.
- Назначение: UI-анимация **готового** ответа кадрами Rich-черновиков и
  заглушек; в теле PR прямо сказано: «Сейчас кадры — проигрывание готового
  текста. `LLMGateway.ask()` пока отдаёт ответ целиком».
- PR также содержит кнопку «Стоп» и групповые заглушки, но реальная
  Telegram-приёмка черновиков и «Стоп» **не проверена** (нужен настоящий токен).
  Реестр генераций хранится в памяти процесса; для нескольких экземпляров нужен
  общий флаг.
- Это параллельная незавершённая работа, а не реализованный RAG и не
  provider delta. Возможности PR нельзя приписывать main. Приёмка PERS-44
  требует подлинного потока от провайдера; UI-часть PR может быть
  переиспользована после появления реального `ask_stream`, но это не проверено.

## 6. Ресурсы и режим запуска

Целевая машина: Mac arm64, **16 GiB RAM, 10 CPU**, Ollama CLI отсутствует.

- Embedding E5-small: ~449 MiB веса fp32; ориентировочный RSS процесса
  эмбеддинга 1.5–2.5 GiB при batch 16–32 (оценка, не измерение). CPU-only;
  GPU не требуется. Загрузка модели ~0.5 GiB на диск.
- Qdrant local path: диск и RAM зависят от числа чанков. Для 384-мерного
  float32-вектора это ~1.5 KiB на чанк плюс payload; 10 000 чанков — порядка
  десятков MiB, но точный объём и latency нужно измерить.
- Local path — **только один процесс**. Для нескольких процессов нужен
  Qdrant server; это открытое решение по эксплуатации.
- Генератор: ресурсы зависят от выбранного провайдера/модели и не оцениваются
  до выбора. Локальная генерация на 16 GiB не подтверждена.

**Latency/throughput не измерены.** Любые числа качества или скорости в этом
документе являются гипотезами.

## 7. Альтернативы

| Решение | Плюсы | Причины не выбирать сейчас |
|---|---|---|
| pgvector | уже есть PostgreSQL; меньше сервисов | нет extension/миграций; меньше готовых payload-фильтров и local/server режимов |
| LanceDB | используется в support-rag; embedded | другой профиль эксплуатации, меньше проверен на multi-project фильтрацию |
| Weaviate | hybrid search в AI-Life | тяжёлый стек, избыточен для пилота |
| FAISS | быстрый dense | нет payload/ACL/серверного режима из коробки |
| E5-base/large, bge-m3, Qwen3-Embedding | потенциально выше качество | больше CPU/RAM; качество на русской документации не измерено |
| Hybrid dense+BM25, reranker | лучше на редких терминах | добавляет компоненты до появления eval; не первый срез |
| Hosted OpenAI-совместимый API | быстрый старт streaming | ключ/endpoint/стоимость не подтверждены; данные уходят наружу |
| Local llama.cpp/vLLM | данные не уходят | Ollama отсутствует; модель/квантование/ресурсы не проверены |

## 8. Открытые решения и владельцы

| Решение | Владелец | Блокирует |
|---|---|---|
| Provider, модель, endpoint, ключ, стоимость/DPA | AI + infra + product | PERS-44 |
| Candidate generation + PG CAS: реализация, retention, rollback | backend/AI | PERS-43/48 |
| Eval-набор, evidence-gate/no-answer, метрики Recall@5/MRR/groundedness | product/docs + AI | PERS-45 (не блокирует PERS-43) |
| PDF/DOCX/HTML/OCR и лимиты | product | расширение форматов |
| Qdrant local vs server и multi-process | infra | production-режим |
| ACL/snapshot: права и версия на момент генерации | backend/security | PERS-42/44 |
| Инвалидация кэша после `save_knowledge` | backend | корректность ответов |
| Судьба PR #2: переиспользовать UI или закрыть | reviewer/owner | интеграция streaming |

## 9. Следующий вертикальный срез

1. **PERS-42 — контракт.** `ask_stream`/события, `AskResult.sources`,
   `request_id`, `knowledge_version`/`generation_id`, коды ошибок/`no_answer_reason`,
   таймауты, snapshot/ACL, usage; тесты без реального провайдера. PR #2 не
   считается выполнением этого пункта.
2. **PERS-43 — ingestion + retrieval (без ожидания eval).** `.md`/`.txt`,
   E5-small с зафиксированной revision, Qdrant, chunking, candidate generation,
   PG CAS публикация и project/generation-фильтр. Разработку индекса можно
   вести без eval; тесты изоляции двух проектов и неверной generation входят
   сюда.
3. **PERS-45 — eval параллельно.** Размеченный набор, Recall@5/MRR,
   groundedness, no-answer/adversarial; калибровка evidence-gate. Eval не
   блокирует PERS-43, но без него нельзя включать streaming и нельзя обещать
   отказы.
4. **PERS-44 — реальный streaming + Telegram.** Подключить подтверждённый
   OpenAI-совместимый провайдер, `content.delta` → draft, финальный
   `sendRichMessage`, ACL/snapshot, один draft на чат; провести реальную
   Telegram-приёмку. Streaming включается только после PERS-42 + PERS-45 gate
   (или буферизации до классификации). Stop/cancel не считать реализованным
   до отдельной проверки.

## 10. Что не утверждается

- Работающего RAG в main нет; качество поиска и ответов не измерено.
- Candidate generation + PG CAS — только проектная схема; в main/PERS-41 она не
  реализована и не проверена.
- Нет откалиброванного evidence-gate и порога no-answer. Пока их нет, нельзя
  обещать отказы; streaming в production не включается.
- Нет подтверждённого провайдера, ключа, endpoint, модели генерации и
  streaming-контракта в main. Промпт и формат `Source` — дизайн, не
  подтверждённое поведение модели.
- PR #2 не является provider streaming и не подтверждён в реальном Telegram.
- Нельзя отправлять дельты и затем завершать `found=False`/`NO_ANSWER`; такая
  схема явно запрещена (см. 3.4).
- Нет поддержки PDF/DOCX/OCR, hybrid/rerank, multi-process Qdrant.
- Нет stop/cancel и end-to-end Telegram-приёмки.
- Локальная генерация на Mac arm64/16 GiB не проверена.

## 11. Доказательства

- База main: `aa31c0580e6a0728ecd2133f9c5e56fa5fe1ab1f`;
  `tg_bot/app/llm/gateway.py` (stub), `tg_bot/pyproject.toml` (нет RAG-зависимостей).
- E5 model card: `https://huggingface.co/intfloat/multilingual-e5-small/raw/614241f622f53c4eeff9890bdc4f31cfecc418b3/README.md`;
  HF API revision: `https://huggingface.co/api/models/intfloat/multilingual-e5-small/revision/614241f622f53c4eeff9890bdc4f31cfecc418b3`;
  `config.json`: hidden_size 384, max_position_embeddings 512;
  `tokenizer_config.json`: XLMRobertaTokenizer, model_max_length 512.
- Qdrant local mode и файловая блокировка: Context7 `/qdrant/qdrant-client`
  (README local mode; `QdrantLocal` использует portalocker; закрытие освобождает lock).
- Qdrant cosine/filter: Context7 `/qdrant/qdrant-client` (`VectorParams(size, distance=Distance.COSINE)`,
  `query_filter=Filter(must=[FieldCondition(...)])`).
- Sentence Transformers: Context7 `/huggingface/sentence-transformers`
  (`normalize_embeddings=True`, загрузка с `revision`).
- OpenAI streaming: Context7 `/openai/openai-python` (`chat.completions.stream`,
  `content.delta`, `include_usage`).
- PR #2: `gh pr view 2` (OPEN, mergeable, head `feature/answer-streaming`,
  base `main`, updated 2026-11-04, body с прямым указанием на проигрывание
  готового текста и непроверенный реальный Telegram).
- Исходные проекты: пути/URL/коммиты из таблицы выше.
  - AI-Life: локальный снимок без git-ревизии; `save_answer_to_cache` —
    `pass` в `BE/app/modules/weavaite/weavaite_client.py:277-278`.
  - MireaFive: commit `959ff5c...`; `src/retriever.py` использует
    `TfidfVectorizer` + `cosine_similarity` и convex combination 0.7/0.3,
    а не BM25/RRF.
  - GridModel: commit `b8fc897...`; HTTP `filters` объявлены, но не
    применяются; `docs/evidence/retrieval-report.json` — `refusal_rate: 0.0`
    на 8 adversarial-кейсах.
  - maratsite: commit `6d0ebcdf...`; репозиторий private, публичный URL без
    авторизации 404; дерево и коммит проверены через `gh api`.
- Проверка доступности: HF model card/revision и PR #2 отдают HTTP 200;
  MireaFive публичный; maratsite private.
