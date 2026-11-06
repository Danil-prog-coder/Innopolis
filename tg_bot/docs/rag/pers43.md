# PERS-43: индексация и retrieval (candidate-only)

> INN-8 addendum: минимальная PG-публикация добавлена отдельно в
> `app/storage/postgres/knowledge_work.py` и `app/rag/ingestion.py`. Этот
> документ описывает candidate-слой `app/rag/index.py`; Qdrant по-прежнему
> остаётся candidate-only.

Статус: реализован baseline в `tg_bot/app/rag`, **без публикации generation**.
Публикация, PG CAS, `docs_version`, `DocUpdate`/outbox и retention — PERS-48.
APPEND реализован в candidate-only границе только по явному base snapshot и
полному corpus; базовая generation не модифицируется.

## Что реально проверено

- Модель: `intfloat/multilingual-e5-small`,
  revision `614241f622f53c4eeff9890bdc4f31cfecc418b3`, 384 dim, 512 tokens,
  `query: `/`passage: `, L2-normalize, cosine.
- Runtime, реально разрешённый lockfile: `sentence-transformers 5.7.0` (5.x),
  `qdrant-client 1.19.1`, CPU. ADR упоминал 5.1.x; 5.7.0 выбран текущим
  resolver-ом и зафиксирован в `uv.lock` — это единственное отклонение от
  текста ADR, требует подтверждения ревью.
- Qdrant: local path, collection `kb_chunks`; manifest — `kb_generations`.
- Integration-тесты запускаются только с `RAG_INTEGRATION=1` и используют
  настоящую E5 и настоящий Qdrant local; fake-эмбеддингов в baseline нет.

## Форматы и ошибки

Поддерживаются `.md` и `.txt` UTF-8. PDF и прочие расширения отклоняются
`UnsupportedDocumentError`; пустой файл — `EmptyDocumentError`; битый UTF-8 —
`CorruptDocumentError`; превышение `MAX_DOCUMENT_BYTES=20 MiB` или
`MAX_DECODED_CHARS=2_000_000` — `DocumentTooLargeError`. Ошибка любого файла
в batch означает отсутствие успешного ingestion.

## Chunking

- Разделы Markdown по заголовкам; `section_path` вида `Альфа → Доступ`.
  Fence-aware: строки `# ...` внутри fenced code blocks (``` или ~~~) не
  считаются заголовками; код не разрывается ложными секциями.
- Token-aware структурное разбиение по абзацам/предложениям; целевой размер
  400 полных input-токенов, overlap 50, жёсткий предел 512.
- Полный вход проверяется токенизатором E5 с `passage: ` и special tokens;
  молчаливая `truncation=True` не используется.
- `chunk.text` — точная подстрока документа; `start_char`/`end_char` указывают
  на неё. Excerpt в `Source` не переписывается. Крупный code block может быть
  осознанно разбит по token-бюджету; каждый chunk остаётся точной подстрокой.
- `document_id` детерминирован от имени и content hash; `document_version` —
  SHA-256 текста; `chunk_id` — SHA-256 от document/chunk/offset/text.
- Point ID — UUIDv5 от `project_id:generation_id:chunk_id`. Payload-фильтр не
  защищает от overwrite; поэтому generation входит и в ID.

## API

```python
from app.llm.gateway import DocFile, KnowledgeSnapshot
from app.rag import E5Embedder, QdrantChunkStore, RagIndex

embedder = E5Embedder()
store = QdrantChunkStore("/var/lib/innopolis/qdrant")
rag = RagIndex(store, embedder)

manifest = rag.index_generation(
    [DocFile("alpha.md", open("alpha.md", "rb").read())],
    project_id=1,
    generation_id="candidate-2026-11-05",
    knowledge_version=2,
)
print(manifest.to_json())

sources = rag.search(
    KnowledgeSnapshot(1, "candidate-2026-11-05", 2),
    "Какой код доступа у Альфы?",
    candidate_k=20,
    context_top_k=5,
)
rag.close()
```

`candidate_k` — сколько кандидатов запросить у Qdrant; `context_top_k` — сколько
отдать в контекст. Оба значения независимы; `candidate_k >= context_top_k`.

## Manifest

`IndexManifest` содержит: версию manifest, `status="candidate"`, mode,
`project_id`/`generation_id`/`knowledge_version`, коллекцию, модель и revision,
dimension, metric, normalization, prompt-префиксы, parser/chunker versions,
token-лимиты, per-document `filename/document_id/document_version/bytes/chars/
chunks`, общий `chunk_count`, `corpus_hash`, `config_hash`, `manifest_hash`.
`manifest_hash` считается по неизменяемой идентичности candidate (без
retrieval top-k и mode); `config_hash` — по indexing-конфигу модели/parser/
chunker. Manifest сохраняется в `kb_generations`.

## Дубликаты, APPEND/REPLACE и публикация

- Candidate generation immutable: повтор с тем же corpus/config/manifest
  идемпотентен; повтор с изменённым содержимым или indexing-конфигом
  отклоняется `GenerationAlreadyExistsError`, старые points не меняются.
- Дубликаты filenames/document IDs в одном batch отклоняются.
- `APPEND`: обязательны новый `generation_id` и `base_snapshot`. Base manifest
  должен быть завершён и совместим по model/revision/config/chunk counts.
  Переданный corpus обязан содержать каждый base document с неизменным
  `document_id`/`document_version`; отсутствие — `AppendMissingDocumentError`,
  изменение — `AppendDocumentChangedError`, дубликат — `DocumentAlreadyExistsError`.
  Новые документы разрешены. Base не меняется; новый candidate получает
  `base_generation_id`/`base_manifest_hash`. Неоднозначные обновления документов
  отклоняются.
- `REPLACE` строит новый candidate и не трогает активную generation.
- При ошибке batch/manifest ничего не публикуется. Если Qdrant успел записать
  часть candidate, manifest не создаётся; повтор с тем же generation
  отклоняется, частичный candidate остаётся инертным.
- Публикации нет: `docs_version` не bump-ится, `DocUpdate`/outbox не пишутся.
- До PERS-48 **нет** защиты опубликованной generation от прямого изменения
  Qdrant и нет безопасного конкурентного ingest. Manifest-проверка защищает
  только последовательный повтор; это явное ограничение, не заявление о
  production-защите.

Handoff в PERS-48: publisher получает `project_id`, `generation_id`,
`knowledge_version` и `manifest_hash`, проверяет полный candidate по manifest и
атомарно публикует generation/version/operation/DocUpdate/outbox через PG CAS.
До этого retrieval должен использовать только явно переданный snapshot; latest
из Qdrant не читается.

## Изоляция и source lookup

`search`, `count` и `get_sources` фильтруют `project_id` и `generation_id`.
Source lookup использует Qdrant filter + `HasIdCondition`; после ответа
проверяет payload. Тесты покрывают два проекта и две generation одного проекта
при одном вопросе.

## Проверки

```sh
make -C tg_bot test-local ARGS='tests/rag'                 # validation, integration skipped
RAG_INTEGRATION=1 make -C tg_bot test-local ARGS='tests/rag/test_rag_integration.py -q'
make -C tg_bot test
```

Фактический локальный прогон: 5 unit validation + 11 integration (real E5/Qdrant)
прошли, включая успешный APPEND, отказы missing/changed/no-base и fence-aware
разбор кода. Полный suite запускается через общий lock; integration по умолчанию
skipped, чтобы CI без модели не давал ложный success.

## Ограничения

- Fence-aware разбор поддерживает ``` и ~~~; indented code blocks, HTML-блоки
  и прочие конструкции Markdown не моделируются. Заголовки с 4+ пробелами не
  считаются заголовками.
- Qdrant local не применяет payload indexes (предупреждение клиента); фильтры
  корректны, но индексы появятся только в server mode.
- Local path — один процесс; конкурентный ingest не безопасен.
- Нет eval/threshold/no-answer; качество retrieval не измерено.
- Нет генератора ответа, streaming, PDF/OCR и публикации.
- Реальный baseline проверен на синтетическом открытом корпусе; это не
  доказательство качества на документации Innopolis.
