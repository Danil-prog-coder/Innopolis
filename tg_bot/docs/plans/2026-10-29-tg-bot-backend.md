# tg_bot: бэкенд Telegram-бота — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Реализовать Telegram-бота из раздела «User Flows» в README. В объём входит всё, кроме ИИ/LLM/ML: в местах, где нужен ИИ, бот обращается к абстрактному `LLMGateway`, а в этом объёме работает заглушка `StubLLMGateway`.

**Architecture:** aiogram 3 (long polling) поверх трёх слоёв: роутеры → сервисы (бизнес-логика, без типов Telegram) → Work-классы хранилищ (PostgreSQL через Tortoise ORM, Redis). Любое обращение к данным идёт через метод класса `<Хранилище><Сущность>Work`. Каждый такой класс использует базовый клиент своего хранилища. Экземпляры отдают фабрики `get_obj_*` из `app/dependencies.py`, а в хендлеры они попадают через собственный маркер `Depends(...)`, который разрешает inner-middleware.

**Tech Stack:** Python 3.12, aiogram 3, PostgreSQL 17, Tortoise ORM 1.x + asyncpg, Aerich, Redis 7 (redis-py asyncio), pydantic-settings, argon2-cffi, pytest + pytest-asyncio + fakeredis, ruff, uv, Docker Compose.

**Spec:** `README.md` (корень репозитория, разделы «Features» и «User Flows») + `tg_bot/STACK.md`.

## Global Constraints

- Весь код, конфиги и тесты лежат только в `tg_bot/`. Корень репозитория и сервис `llm` не трогаем.
- ИИ/LLM/ML не реализуем и с LLM-сервисом не интегрируемся: нет ни HTTP-клиента, ни контракта, ни httpx. Бот зависит только от абстрактного `LLMGateway` (Task 7), в DI зарегистрирован `StubLLMGateway`. Реальную реализацию позже напишет человек, отвечающий за ИИ.
- Правила хранилищ (от пользователя, обязательны):
  1. Работа с хранилищами только через методы классов. Свободных функций и запросов в роутерах и утилитах нет.
  2. На каждое хранилище один базовый клиент (`PostgresClient`, `RedisClient`): `connect`/`close` плюс универсальный CRUD. О сущностях он ничего не знает.
  3. На каждую сущность (таблицу или набор ключей) свой класс `<Хранилище><Сущность>Work`. Он использует базовый клиент внутри и не делает запросов к чужим сущностям.
  4. Роутеры и сервисы только вызывают методы Work-классов, сами запросов не собирают.
  5. Имена таблиц и ключей Redis хранятся в enum (`TableName`, `RedisKey`), строковых литералов в коде нет.
  6. Экземпляры отдаёт фабрика `get_obj_<имя>` из `app/dependencies.py`, в хендлеры они приходят через `Depends(...)`. Руками в коде объекты не создаются, исключение — сами фабрики и тесты.
- Секретный пароль показывается только во всплывающем окне (`callback.answer(..., show_alert=True)`), в историю переписки не попадает. В Postgres лежит только хеш argon2.
- Сообщение пользователя с введённым паролем бот удаляет сразу после чтения.
- Бот скачивает файлы не больше 20 МБ (лимит Bot API) — проверяем `file_size` до скачивания.
- Весь текст для пользователя живёт в `app/bot/texts.py`. Формулировки из README копируются дословно: «Создать проект», «Присоединиться к проекту в роли админа», «Дополнить документацию», «Заменить документацию», «Инструкция, как добавить бота в ваш чат», «Присоединиться к другому проекту», «Документация проекта обновлена», «Что изменилось», кнопка проекта `📁 {project} · {role}` с ролями «админ» / «пользователь».
- `parse_mode=HTML`, весь пользовательский ввод экранируется `html.escape`.
- Каждый вызов `LLMGateway` получает `project_id` (изоляция проектов).
- Линтер: `uv run ruff check . && uv run ruff format --check .` проходит без ошибок.

## Review Focus

1. **HTML в пользовательском тексте** (`<script>`, `&`, `<b>` в названии проекта, вопросе или ответе админа): текст показывается буквально, отправка не падает с `can't parse entities`. Тест в Task 9 (`test_texts_escape_user_input`).
2. **Альбом из нескольких документов** (media group) приходит параллельными апдейтами: ни один файл не теряется при сборе черновика. Тест в Task 6 (`test_docs_draft_concurrent_add_keeps_all`).
3. **Два админа одновременно отвечают на один вопрос**: пользователю уходит ровно один ответ, второй админ видит «уже ответили». Тест в Task 9 (`test_answer_question_race_only_one_wins`).
4. **Устаревшая кнопка** (админ удалён из проекта, проект переключён, callback с чужим `project_id`): действие отклоняется, а не выполняется над чужим проектом. Тесты в Task 8 (`test_removed_admin_cannot_create_invite`, `test_switch_to_foreign_project_rejected`).
5. **Ответ LLM длиннее 4096 символов** (лимит Telegram): ответ доходит, разбитый на несколько сообщений. Тест в Task 9 (`test_split_message_respects_limit`).

---

## Структура файлов

```
tg_bot/
  pyproject.toml  uv.lock  .env.example  .gitignore  .dockerignore  Dockerfile  docker-compose.yml
  migrations/models/0_*_init.py          # генерирует aerich
  app/
    __main__.py                          # точка входа: python -m app
    config.py                            # Settings
    dependencies.py                      # все фабрики get_obj_*
    core/depends.py                      # Depends, DependsResolver, DependsMiddleware
    core/enums.py                        # MemberRole, QuestionStatus, DocsMode, RateLimitScope
    core/security.py                     # ProjectPasswordManager
    core/errors.py                       # доменные исключения
    storage/enums.py                     # TableName, RedisKey
    storage/postgres/client.py  models.py  config.py
    storage/postgres/{user,project,membership,invite,question,doc_update,group_chat}_work.py
    storage/redis/client.py
    storage/redis/{answer_cache,rate_limit,password_reveal,docs_draft}_work.py
    llm/gateway.py                       # LLMGateway (ABC) + StubLLMGateway
    services/{access,project,invite,membership,question,docs,group_chat}_service.py
    bot/texts.py  callbacks.py  states.py  keyboards.py  middlewares.py  notifier.py
    bot/routers/__init__.py              # setup_routers() -> Router
    bot/routers/{start,admin_project,admin_invites,admin_members,admin_docs,projects,answers,questions,group}.py
  tests/  (зеркалит app/; conftest.py с фикстурами)
```

Все команды ниже выполняются из `tg_bot/`.

---

### Task 1: Каркас проекта и инфраструктура

**Files:**
- Create: `pyproject.toml`, `.env.example`, `.gitignore`, `.dockerignore`, `Dockerfile`, `docker-compose.yml`, `app/__init__.py`, `app/config.py`, `tests/__init__.py`, `tests/conftest.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Settings` (pydantic-settings, `env_file=".env"`) с полями `bot_token: SecretStr`, `postgres_host/port/db/user`, `postgres_password: SecretStr`, `redis_host/port/db`, `redis_password: SecretStr`, `answer_cache_ttl_seconds: int = 86400`, `question_rate_limit: int = 10`, `question_rate_window_seconds: int = 60`, `password_attempts_limit: int = 5`, `password_attempts_window_seconds: int = 900`, `password_reveal_ttl_seconds: int = 600`, `docs_max_files: int = 10`, `log_level: str = "INFO"`; свойства `postgres_dsn -> str` (`postgres://user:pass@host:port/db`) и `redis_url -> str` (`redis://:pass@host:port/db`).

- [ ] **Step 1: `uv init --app --python 3.12`. Зависимости: `uv add "aiogram>=3.15,<4" "tortoise-orm[asyncpg]>=0.25" "aerich>=0.9" "redis>=5.2" "pydantic-settings>=2.6" "argon2-cffi>=23.1"` и `uv add --dev pytest pytest-asyncio fakeredis ruff`.** В `pyproject.toml` задать `[tool.pytest.ini_options] asyncio_mode = "auto"`, `[tool.ruff] line-length = 100, target-version = "py312"`, `[tool.ruff.lint] select = ["E","F","I","B","UP","ASYNC"]`, `[tool.aerich] tortoise_orm = "app.storage.postgres.config.TORTOISE_ORM", location = "./migrations"`.

- [ ] **Step 2: Написать падающий тест**

```python
def test_settings_build_dsns(monkeypatch):
    for k, v in {
        "BOT_TOKEN": "1:x",
        "POSTGRES_HOST": "pg",
        "POSTGRES_PORT": "5432",
        "POSTGRES_DB": "bot",
        "POSTGRES_USER": "u",
        "POSTGRES_PASSWORD": "p",
        "REDIS_HOST": "r",
        "REDIS_PORT": "6379",
        "REDIS_DB": "0",
        "REDIS_PASSWORD": "rp",
    }.items():
        monkeypatch.setenv(k, v)
    s = Settings(_env_file=None)
    assert s.postgres_dsn == "postgres://u:p@pg:5432/bot"
    assert s.redis_url == "redis://:rp@r:6379/0"
    assert s.bot_token.get_secret_value() == "1:x"
    assert "1:x" not in repr(s)


def test_settings_missing_token_fails(monkeypatch):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
```

- [ ] **Step 3: Запустить и убедиться, что тест падает** — `uv run pytest tests/test_config.py -v` → FAIL (ImportError).

- [ ] **Step 4: Реализовать `Settings` в `app/config.py`.**

- [ ] **Step 5: Написать инфраструктурные файлы.**
  - `.env.example` — все переменные `Settings` с комментарием к каждой. `POSTGRES_HOST=localhost` и `REDIS_HOST=localhost` для запуска вне Docker.
  - `docker-compose.yml` — сервисы `postgres` (`postgres:17-alpine`, volume `pg_data`, healthcheck `pg_isready`), `redis` (`redis:7.4-alpine`, `redis-server --appendonly yes --requirepass ${REDIS_PASSWORD}`, volume `redis_data`, healthcheck `redis-cli -a $REDIS_PASSWORD ping`), `bot` (`build: .`, `env_file: .env`, `environment: POSTGRES_HOST: postgres, REDIS_HOST: redis`, `depends_on` обоих с `condition: service_healthy`, `restart: unless-stopped`). Порты БД публикуются только на `127.0.0.1`.
  - `Dockerfile` — `python:3.12-slim`, `COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/uv`, `uv sync --frozen --no-dev`, непривилегированный пользователь, `CMD ["sh", "-c", "uv run --no-sync aerich upgrade && uv run --no-sync python -m app"]`.
  - `.gitignore` — `.env`, `.venv/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`. `.dockerignore` — то же плюс `tests/`, `docs/`.

- [ ] **Step 6: Проверить** — `uv run pytest tests/test_config.py -v` → PASS. `cp .env.example .env && docker compose config -q` → без ошибок. `docker compose up -d postgres redis && docker compose ps` → оба `healthy`.

- [ ] **Step 7: Коммит** — `git add tg_bot && git commit -m "feat(tg_bot): project scaffold, settings, docker compose"`

---

### Task 2: DI — `Depends` и `DependsMiddleware`

**Files:**
- Create: `app/core/__init__.py`, `app/core/depends.py`
- Test: `tests/core/test_depends.py`

**Interfaces:**
- Produces:
  - `class Depends: def __init__(self, factory: Callable[..., Any]) -> None` — маркер, ставится значением по умолчанию у параметра хендлера или фабрики.
  - `class DependsResolver: def __init__(self) -> None; overrides: dict[Callable, Callable]; async def resolve(self, factory: Callable, cache: dict[Callable, Any] | None = None) -> Any` — рекурсивно разрешает `Depends` в параметрах самой фабрики, поддерживает sync и async фабрики. В рамках одного `cache` каждая фабрика вызывается один раз, `overrides` подменяют фабрику (для тестов, как `dependency_overrides` в FastAPI).
  - `class DependsMiddleware(BaseMiddleware): def __init__(self, resolver: DependsResolver)` — inner-middleware. Берёт `data["handler"].callback`, для каждого параметра со значением по умолчанию `Depends` кладёт `data[name] = await resolver.resolve(...)`. Сигнатура разбирается один раз на хендлер и кешируется.

- [ ] **Step 1: Написать падающие тесты**

```python
async def test_resolves_chain_and_caches_per_call():
    calls = []

    def get_a():
        calls.append("a")
        return "A"

    async def get_b(a: str = Depends(get_a)):
        return a + "B"

    def get_c(a: str = Depends(get_a), b: str = Depends(get_b)):
        return a + b

    assert await DependsResolver().resolve(get_c) == "AAB"
    assert calls == ["a"]


async def test_override_replaces_factory():
    def get_a():
        return "real"

    r = DependsResolver()
    r.overrides[get_a] = lambda: "fake"
    assert await r.resolve(get_a) == "fake"


async def test_middleware_injects_only_depends_params():
    def get_x():
        return 42

    async def handler(event, x: int = Depends(get_x), other: str = "keep"): ...

    data = {"handler": HandlerObject(callback=handler)}
    seen = {}

    async def next_(event, d):
        seen.update(d)

    await DependsMiddleware(DependsResolver())(next_, object(), data)
    assert seen["x"] == 42 and "other" not in seen
```

- [ ] **Step 2: Запустить** — `uv run pytest tests/core/test_depends.py -v` → FAIL (ImportError).
- [ ] **Step 3: Реализовать `app/core/depends.py`** через `inspect.signature`; для async-фабрик проверка `inspect.isawaitable(result)`.
- [ ] **Step 4: Запустить** → PASS.
- [ ] **Step 5: Коммит** — `git commit -m "feat(tg_bot): Depends-based DI for aiogram handlers"`

---

### Task 3: PostgresClient, модели, enum-ы, миграции

**Files:**
- Create: `app/storage/__init__.py`, `app/storage/enums.py`, `app/core/enums.py`, `app/storage/postgres/__init__.py`, `client.py`, `models.py`, `config.py`, `migrations/` (через aerich)
- Test: `tests/storage/postgres/test_client.py`; фикстура `pg_client` в `tests/conftest.py`

**Interfaces:**
- Produces:
  - `TableName(StrEnum)`: `USERS="users"`, `PROJECTS="projects"`, `MEMBERSHIPS="memberships"`, `INVITES="invites"`, `QUESTIONS="questions"`, `DOC_UPDATES="doc_updates"`, `GROUP_CHATS="group_chats"`.
  - `RedisKey(StrEnum)` с методом `build(**parts) -> str` (`self.value.format(**parts)`): `ANSWER_CACHE="cache:answer:{project_id}:{docs_version}:{question_hash}"`, `RATE_LIMIT="ratelimit:{scope}:{subject_id}"`, `PASSWORD_REVEAL="password_reveal:{project_id}"`, `DOCS_DRAFT="docs_draft:{telegram_id}:{project_id}"`, `FSM_PREFIX="fsm"`.
  - `app/core/enums.py`: `MemberRole` (`ADMIN="admin"`, `USER="user"`), `QuestionStatus` (`PENDING`, `ANSWERED`), `DocsMode` (`APPEND="append"`, `REPLACE="replace"`), `RateLimitScope` (`QUESTION`, `GROUP_QUESTION`, `PASSWORD`).
  - Модели Tortoise (`Meta.table = TableName.X`):
    - `User`: `id`, `telegram_id: BigInt unique`, `username: str|None`, `full_name: str(256)`, `current_project: FK Project null on_delete SET_NULL`, `created_at`.
    - `Project`: `id`, `public_id: str(8) unique`, `name: str(64)`, `password_hash: str(256)`, `creator: FK User`, `docs_version: int = 0`, `created_at`.
    - `Membership`: `user FK`, `project FK` (CASCADE), `role: CharEnum(MemberRole)`, `created_at`; `unique_together=("user","project")`.
    - `Invite`: `code: str(64) unique`, `project FK`, `created_by FK User`, `expires_at`, `revoked_at: null`, `created_at`.
    - `Question`: `project FK`, `asker_telegram_id: BigInt`, `chat_id: BigInt`, `message_id: int`, `text: Text`, `image_file_id: str|None`, `status: CharEnum(QuestionStatus) = PENDING`, `answered_by: FK User null`, `answer: Text null`, `created_at`, `answered_at: null`; индекс `("project","status")`.
    - `DocUpdate`: `project FK`, `created_by FK User`, `mode: CharEnum(DocsMode)`, `summary: Text`, `created_at`.
    - `GroupChat`: `chat_id: BigInt unique`, `project FK`, `bound_by_telegram_id: BigInt`, `created_at`.
  - `class PostgresClient: __init__(self, db_url: str, modules: dict[str, list[str]])`; `async connect() -> None` (`Tortoise.init`); `async close() -> None`; `async create(model: type[M], **fields) -> M`; `async get_one(model: type[M], **filters) -> M | None`; `async get_many(model: type[M], *, order_by: tuple[str, ...] = (), offset: int = 0, limit: int | None = None, **filters) -> list[M]`; `async count(model, **filters) -> int`; `async update(model, filters: dict[str, Any], **fields) -> int`; `async delete(model, **filters) -> int`.
  - `app/storage/postgres/config.py`: `TORTOISE_ORM` (dict для aerich, собирается из `Settings`) и `MODELS_MODULES = {"models": ["app.storage.postgres.models", "aerich.models"]}`.
  - Фикстура `pg_client` — `PostgresClient("sqlite://:memory:", {"models": ["app.storage.postgres.models"]})`, `connect()` плюс `Tortoise.generate_schemas()`, после теста `close()`.

- [ ] **Step 1: Написать падающие тесты**

```python
async def test_crud_roundtrip(pg_client):
    u = await pg_client.create(User, telegram_id=1, full_name="A")
    assert (await pg_client.get_one(User, telegram_id=1)).id == u.id
    assert await pg_client.update(User, {"id": u.id}, full_name="B") == 1
    assert (await pg_client.get_one(User, id=u.id)).full_name == "B"
    assert await pg_client.delete(User, id=u.id) == 1
    assert await pg_client.get_one(User, id=u.id) is None


async def test_unique_telegram_id(pg_client):
    await pg_client.create(User, telegram_id=1, full_name="A")
    with pytest.raises(IntegrityError):
        await pg_client.create(User, telegram_id=1, full_name="B")


def test_table_names_come_from_enum():
    assert User._meta.db_table == TableName.USERS
    assert {
        m._meta.db_table
        for m in (User, Project, Membership, Invite, Question, DocUpdate, GroupChat)
    } == set(TableName)


def test_redis_key_build():
    assert RedisKey.RATE_LIMIT.build(scope="question", subject_id=5) == "ratelimit:question:5"
```

- [ ] **Step 2: Запустить** — `uv run pytest tests/storage/postgres/test_client.py -v` → FAIL.
- [ ] **Step 3: Реализовать enum-ы, модели и `PostgresClient`.**
- [ ] **Step 4: Запустить** → PASS.
- [ ] **Step 5: Сгенерировать миграцию на реальном Postgres** — `docker compose up -d postgres && uv run aerich init-db` → появляется `migrations/models/0_*_init.py`, в базе созданы 7 таблиц (`docker compose exec postgres psql -U $POSTGRES_USER -d $POSTGRES_DB -c '\dt'`).
- [ ] **Step 6: Коммит** — `git commit -m "feat(tg_bot): postgres client, models, initial migration"`

---

### Task 4: Postgres Work-классы — пользователи, проекты, членство

**Files:**
- Create: `app/storage/postgres/user_work.py`, `project_work.py`, `membership_work.py`
- Test: `tests/storage/postgres/test_user_work.py`, `test_project_work.py`, `test_membership_work.py`

**Interfaces:**
- Consumes: `PostgresClient`, модели (Task 3).
- Produces (у каждого класса `__init__(self, client: PostgresClient)`):
  - `PostgresUserWork`: `upsert_from_telegram(telegram_id: int, username: str | None, full_name: str) -> User`; `get_by_telegram_id(telegram_id: int) -> User | None`; `get_by_id(user_id: int) -> User | None`; `set_current_project(user_id: int, project_id: int | None) -> None`; `reset_current_project_if(user_id: int, project_id: int) -> None`.
  - `PostgresProjectWork`: `create(name: str, public_id: str, password_hash: str, creator_id: int) -> Project`; `get_by_id(project_id: int) -> Project | None`; `get_by_public_id(public_id: str) -> Project | None`; `bump_docs_version(project_id: int) -> int` (атомарно через `F("docs_version") + 1`, возвращает новое значение).
  - `PostgresMembershipWork`: `add_or_upgrade(user_id: int, project_id: int, role: MemberRole) -> Membership` (админа никогда не понижает до пользователя); `get(user_id: int, project_id: int) -> Membership | None`; `get_user_memberships(user_id: int) -> list[Membership]` (с `prefetch_related("project")`, сортировка по имени проекта); `get_project_members(project_id: int, offset: int, limit: int) -> list[Membership]` (с `prefetch_related("user")`); `count_project_members(project_id: int) -> int`; `get_member_telegram_ids(project_id: int, role: MemberRole | None = None) -> list[int]`; `remove(user_id: int, project_id: int) -> bool`.

- [ ] **Step 1: Написать падающие тесты**

```python
async def test_upsert_updates_existing(user_work):
    a = await user_work.upsert_from_telegram(10, "old", "Old")
    b = await user_work.upsert_from_telegram(10, "new", "New")
    assert a.id == b.id and b.username == "new"


async def test_bump_docs_version_is_incremental(project_work, project):
    assert await project_work.bump_docs_version(project.id) == 1
    assert await project_work.bump_docs_version(project.id) == 2


async def test_add_or_upgrade_never_downgrades_admin(membership_work, user, project):
    await membership_work.add_or_upgrade(user.id, project.id, MemberRole.ADMIN)
    m = await membership_work.add_or_upgrade(user.id, project.id, MemberRole.USER)
    assert m.role == MemberRole.ADMIN


async def test_add_or_upgrade_upgrades_user(membership_work, user, project):
    await membership_work.add_or_upgrade(user.id, project.id, MemberRole.USER)
    assert (
        await membership_work.add_or_upgrade(user.id, project.id, MemberRole.ADMIN)
    ).role == MemberRole.ADMIN


async def test_member_telegram_ids_filtered_by_role(membership_work, project, admin, user):
    assert await membership_work.get_member_telegram_ids(project.id, MemberRole.ADMIN) == [
        admin.telegram_id
    ]
    assert sorted(await membership_work.get_member_telegram_ids(project.id)) == sorted(
        [admin.telegram_id, user.telegram_id]
    )


async def test_remove_returns_false_for_non_member(membership_work, project):
    assert await membership_work.remove(9999, project.id) is False
```
Фикстуры `user_work`, `project_work`, `membership_work`, `user`, `admin`, `project` (создатель `admin`, членство ADMIN, `user` с ролью USER) добавить в `tests/conftest.py`.

- [ ] **Step 2: Запустить** — `uv run pytest tests/storage/postgres -v` → FAIL.
- [ ] **Step 3: Реализовать три класса.**
- [ ] **Step 4: Запустить** → PASS.
- [ ] **Step 5: Коммит** — `git commit -m "feat(tg_bot): user/project/membership storage classes"`

---

### Task 5: Postgres Work-классы — приглашения, вопросы, обновления документации, групповые чаты

**Files:**
- Create: `app/storage/postgres/invite_work.py`, `question_work.py`, `doc_update_work.py`, `group_chat_work.py`
- Test: `tests/storage/postgres/test_invite_work.py`, `test_question_work.py`, `test_doc_update_work.py`, `test_group_chat_work.py`

**Interfaces:**
- Produces (все с `__init__(self, client: PostgresClient)`):
  - `PostgresInviteWork`: `create(code: str, project_id: int, created_by_id: int, expires_at: datetime) -> Invite`; `get_by_code(code: str) -> Invite | None`; `get_active_for_project(project_id: int, now: datetime) -> list[Invite]` (не отозваны, `expires_at > now`); `revoke(invite_id: int, project_id: int, now: datetime) -> bool` (только приглашение этого проекта и только если оно ещё не отозвано).
  - `PostgresQuestionWork`: `create(project_id: int, asker_telegram_id: int, chat_id: int, message_id: int, text: str, image_file_id: str | None) -> Question`; `get_by_id(question_id: int) -> Question | None`; `mark_answered(question_id: int, admin_user_id: int, answer: str, now: datetime) -> bool` — один `UPDATE ... WHERE id=? AND status='pending'`, `True` только если обновлена ровно одна строка.
  - `PostgresDocUpdateWork`: `create(project_id: int, created_by_id: int, mode: DocsMode, summary: str) -> DocUpdate`; `get_by_id(update_id: int) -> DocUpdate | None`.
  - `PostgresGroupChatWork`: `bind(chat_id: int, project_id: int, bound_by_telegram_id: int) -> GroupChat` (upsert по `chat_id`, перепривязка разрешена); `get_by_chat_id(chat_id: int) -> GroupChat | None`.

- [ ] **Step 1: Написать падающие тесты**

```python
async def test_active_invites_exclude_expired_and_revoked(invite_work, project, admin):
    now = datetime.now(UTC)
    ok = await invite_work.create("ok", project.id, admin.id, now + timedelta(days=1))
    await invite_work.create("old", project.id, admin.id, now - timedelta(seconds=1))
    rev = await invite_work.create("rev", project.id, admin.id, now + timedelta(days=1))
    await invite_work.revoke(rev.id, project.id, now)
    assert [i.code for i in await invite_work.get_active_for_project(project.id, now)] == ["ok"]


async def test_revoke_foreign_project_invite_refused(invite_work, project, other_project, admin):
    inv = await invite_work.create("c", project.id, admin.id, datetime.now(UTC) + timedelta(days=1))
    assert await invite_work.revoke(inv.id, other_project.id, datetime.now(UTC)) is False


async def test_mark_answered_only_once(question_work, project, admin):
    q = await question_work.create(project.id, 1, 1, 1, "why?", None)
    now = datetime.now(UTC)
    assert await question_work.mark_answered(q.id, admin.id, "because", now) is True
    assert await question_work.mark_answered(q.id, admin.id, "again", now) is False
    assert (await question_work.get_by_id(q.id)).answer == "because"


async def test_group_bind_rebinds(group_chat_work, project, other_project):
    await group_chat_work.bind(-100, project.id, 1)
    await group_chat_work.bind(-100, other_project.id, 2)
    assert (await group_chat_work.get_by_chat_id(-100)).project_id == other_project.id
```

- [ ] **Step 2: Запустить** → FAIL. **Step 3: Реализовать.** **Step 4: Запустить** `uv run pytest tests/storage/postgres -v` → PASS.
- [ ] **Step 5: Коммит** — `git commit -m "feat(tg_bot): invite/question/doc-update/group-chat storage classes"`

---

### Task 6: Redis — клиент, Work-классы, FSM storage

**Files:**
- Create: `app/storage/redis/__init__.py`, `client.py`, `answer_cache_work.py`, `rate_limit_work.py`, `password_reveal_work.py`, `docs_draft_work.py`
- Test: `tests/storage/redis/test_*.py`; фикстура `redis_client` (`RedisClient(url="redis://fake", redis=fakeredis.aioredis.FakeRedis(decode_responses=True))`)

**Interfaces:**
- Produces:
  - `RedisClient(url: str, redis: Redis | None = None)`: `async connect()` (`Redis.from_url(url, decode_responses=True)` плюс `ping`; если передан готовый `redis`, используется он); `async close()`; свойство `redis -> Redis`; `async get(key) -> str | None`; `async set(key, value: str, ttl_seconds: int | None = None)`; `async delete(key) -> int`; `async incr(key, ttl_seconds: int) -> int` (INCR и `EXPIRE NX` в одном pipeline); `async list_push(key, value: str, ttl_seconds: int) -> int` (RPUSH и EXPIRE, возвращает длину); `async list_pop_last(key) -> str | None`; `async list_all(key) -> list[str]`.
  - `RedisAnswerCacheWork(client, ttl_seconds: int)`: `get(project_id: int, docs_version: int, question: str) -> str | None`; `set(project_id, docs_version, question, answer: str) -> None`. Ключ строится от sha256 нормализованного вопроса (strip, casefold, схлопнутые пробелы).
  - `RedisRateLimitWork(client)`: `hit(scope: RateLimitScope, subject_id: int, limit: int, window_seconds: int) -> bool` (`True` — запрос разрешён); `reset(scope, subject_id) -> None`.
  - `RedisPasswordRevealWork(client, ttl_seconds: int)`: `put(project_id: int, password: str) -> None`; `get(project_id: int) -> str | None`. Пароль можно показать повторно, пока не истёк TTL.
  - `DraftFile` (pydantic): `file_id: str`, `filename: str`, `size: int`.
  - `RedisDocsDraftWork(client, ttl_seconds: int = 3600)`: `add(telegram_id: int, project_id: int, file: DraftFile, max_files: int) -> int` (возвращает новое число файлов, сверх `max_files` сразу откатывает свой push и возвращает `-1`); `list(telegram_id, project_id) -> list[DraftFile]`; `clear(telegram_id, project_id) -> None`.

- [ ] **Step 1: Написать падающие тесты**

```python
async def test_cache_normalizes_question(answer_cache_work):
    await answer_cache_work.set(1, 0, "  Как  ДЕЛА? ", "ok")
    assert await answer_cache_work.get(1, 0, "как дела?") == "ok"
    assert await answer_cache_work.get(1, 1, "как дела?") is None  # новая версия документации
    assert await answer_cache_work.get(2, 0, "как дела?") is None  # другой проект


async def test_rate_limit_blocks_after_limit(rate_limit_work):
    results = [await rate_limit_work.hit(RateLimitScope.PASSWORD, 7, 3, 60) for _ in range(4)]
    assert results == [True, True, True, False]
    await rate_limit_work.reset(RateLimitScope.PASSWORD, 7)
    assert await rate_limit_work.hit(RateLimitScope.PASSWORD, 7, 3, 60) is True


async def test_rate_limit_key_has_ttl(rate_limit_work, redis_client):
    await rate_limit_work.hit(RateLimitScope.QUESTION, 7, 3, 60)
    assert (
        0
        < await redis_client.redis.ttl(RedisKey.RATE_LIMIT.build(scope="question", subject_id=7))
        <= 60
    )


async def test_docs_draft_concurrent_add_keeps_all(docs_draft_work):
    files = [DraftFile(file_id=f"f{i}", filename=f"{i}.md", size=1) for i in range(5)]
    await asyncio.gather(*(docs_draft_work.add(1, 1, f, max_files=10) for f in files))
    assert {f.file_id for f in await docs_draft_work.list(1, 1)} == {f.file_id for f in files}


async def test_docs_draft_rejects_over_limit(docs_draft_work):
    f = DraftFile(file_id="x", filename="x", size=1)
    assert [await docs_draft_work.add(1, 1, f, max_files=2) for _ in range(3)] == [1, 2, -1]
    assert len(await docs_draft_work.list(1, 1)) == 2
```

- [ ] **Step 2: Запустить** → FAIL. **Step 3: Реализовать.** **Step 4: Запустить** `uv run pytest tests/storage/redis -v` → PASS.
- [ ] **Step 5: Коммит** — `git commit -m "feat(tg_bot): redis client and storage classes"`

---

### Task 7: Граница с ИИ — `LLMGateway` и заглушка

**Files:**
- Create: `app/llm/__init__.py`, `app/llm/gateway.py`
- Test: `tests/llm/test_gateway.py`

**Interfaces:**
- Produces:
  - `AskResult` (frozen dataclass): `found: bool`, `answer: str | None`. `DocFile` (frozen dataclass): `filename: str`, `content: bytes`.
  - `class LLMUnavailableError(Exception)` — реализация бросает его, если ИИ-часть недоступна. Бот отвечает `texts.LLM_UNAVAILABLE`.
  - `class LLMGateway(ABC)` — абстрактные методы: `async ask(project_id: int, question: str) -> AskResult`; `async ask_image(project_id: int, image: bytes, caption: str | None) -> AskResult`; `async upload_documents(project_id: int, mode: DocsMode, files: list[DocFile]) -> str` (возвращает сводку изменений для «Что изменилось»); `async save_knowledge(project_id: int, question: str, answer: str) -> None`; `async close() -> None` (по умолчанию ничего не делает). Docstring класса описывает для человека, отвечающего за ИИ, что должен делать каждый метод.
  - `class StubLLMGateway(LLMGateway)` — без ИИ: `ask` и `ask_image` всегда возвращают `AskResult(found=False, answer=None)`, поэтому вопрос уходит админу. `upload_documents` возвращает фактическую сводку от бота: `"Документация дополнена. Файлы: a.md, b.pdf"`, а для `REPLACE` — `"Документация заменена. Файлы: ..."`. `save_knowledge` только пишет в лог.

- [ ] **Step 1: Написать падающие тесты**

```python
async def test_stub_never_answers():
    gw = StubLLMGateway()
    assert await gw.ask(1, "q") == AskResult(found=False, answer=None)
    assert await gw.ask_image(1, b"img", None) == AskResult(found=False, answer=None)


@pytest.mark.parametrize(
    "mode,prefix",
    [(DocsMode.APPEND, "Документация дополнена"), (DocsMode.REPLACE, "Документация заменена")],
)
async def test_stub_summary_lists_files(mode, prefix):
    summary = await StubLLMGateway().upload_documents(
        1, mode, [DocFile("a.md", b"x"), DocFile("b.pdf", b"y")]
    )
    assert summary == f"{prefix}. Файлы: a.md, b.pdf"


def test_gateway_is_abstract():
    with pytest.raises(TypeError):
        LLMGateway()
```

- [ ] **Step 2: Запустить** → FAIL. **Step 3: Реализовать.** **Step 4: Запустить** `uv run pytest tests/llm -v` → PASS.
- [ ] **Step 5: Коммит** — `git commit -m "feat(tg_bot): LLM gateway interface with stub implementation"`

---

### Task 8: Сервисы доступа, проектов, приглашений и участников

**Files:**
- Create: `app/core/errors.py`, `app/core/security.py`, `app/services/__init__.py`, `access_service.py`, `project_service.py`, `invite_service.py`, `membership_service.py`
- Test: `tests/core/test_security.py`, `tests/services/test_project_service.py`, `test_invite_service.py`, `test_membership_service.py`

**Interfaces:**
- Consumes: Work-классы из Tasks 4–6, `Settings`.
- Produces:
  - `app/core/errors.py`: `DomainError(Exception)` и наследники `NotProjectMember`, `NotProjectAdmin`, `InvalidProjectName`, `InvalidPassword`, `TooManyAttempts`, `InviteNotFound`, `InviteExpired`, `InviteRevoked`, `CannotRemoveCreator`, `CannotRemoveSelf`, `RateLimited`, `QuestionTooLong`, `QuestionAlreadyAnswered`, `QuestionNotFound`, `FileTooLarge`, `NoFilesToUpload`.
  - `ProjectPasswordManager`: `new_public_id() -> str` (`secrets.token_hex(4)`); `generate(public_id: str) -> str` (`f"{public_id}-{secrets.token_urlsafe(18)}"`, длина ≤ 200 — помещается в alert); `hash(password: str) -> str`; `verify(password_hash: str, password: str) -> bool` (ловит `VerifyMismatchError`, `InvalidHashError`); `@staticmethod extract_public_id(password: str) -> str | None` (после `strip()` — часть до первого `-`, только если она совпадает с `^[0-9a-f]{8}$`).
  - `CurrentProject` (dataclass): `project: Project`, `role: MemberRole`.
  - `AccessGuard(membership_work)`: `require_member(user_id, project_id) -> Membership` (иначе `NotProjectMember`); `require_admin(user_id, project_id) -> Membership` (иначе `NotProjectAdmin`).
  - `ProjectService(project_work, membership_work, user_work, password_manager, password_reveal_work, rate_limit_work, access_guard, settings)`:
    `create_project(creator: User, name: str) -> Project` — после `strip()` имя длиной 1..64, иначе `InvalidProjectName`; при коллизии `public_id` до 3 попыток; создатель становится ADMIN, проект — текущим, пароль кладётся в `password_reveal_work`.
    `reveal_password(user: User, project_id: int) -> str | None` — только создателю, иначе `NotProjectAdmin`.
    `join_as_admin(user: User, password: str) -> CurrentProject` — сначала лимит `PASSWORD` (`TooManyAttempts`), потом поиск по `public_id` и проверка хеша (`InvalidPassword` одинаковый для «нет проекта» и «не тот пароль»), `add_or_upgrade(ADMIN)`, проект становится текущим, счётчик сбрасывается.
    `check_password(user: User, project_id: int, password: str) -> None` — `require_admin`, тот же лимит; если пароль от другого проекта — `InvalidPassword`.
    `get_current(user: User) -> CurrentProject | None` — если членства уже нет, сбрасывает `current_project` и возвращает `None`.
    `list_user_projects(user: User) -> list[Membership]`.
    `switch_project(user: User, project_id: int) -> CurrentProject` — `require_member`.
  - `InviteService(invite_work, membership_work, user_work, access_guard)`: константа `INVITE_TTL_DAYS = (1, 7, 30)`; `create_invite(admin: User, project_id: int, ttl_days: int) -> Invite` (`require_admin`, `ttl_days` только из `INVITE_TTL_DAYS`, иначе `ValueError`; `code = secrets.token_urlsafe(16)`); `list_active(admin, project_id) -> list[Invite]`; `revoke(admin, project_id, invite_id) -> bool`; `@staticmethod extract_code(text: str) -> str | None` (принимает `t.me/<bot>?start=<code>`, `https://t.me/...` и голый код `^[A-Za-z0-9_-]{8,64}$`); `get_valid_invite(code: str) -> Invite` (`InviteNotFound`/`InviteExpired`/`InviteRevoked`); `redeem(user: User, code: str) -> CurrentProject` (`add_or_upgrade(USER)`, проект становится текущим).
  - `MembersPage` (dataclass): `items: list[Membership]`, `total: int`, `page: int`, `pages: int`.
  - `MembershipService(membership_work, user_work, project_work, access_guard)`: `PAGE_SIZE = 10`; `list_members(admin: User, project_id: int, page: int) -> MembersPage`; `remove_member(admin: User, project_id: int, target_user_id: int) -> User` — `require_admin`, затем `CannotRemoveCreator`, `CannotRemoveSelf`, `NotProjectMember`; у удалённого сбрасывается `current_project`, если это был этот проект.

- [ ] **Step 1: Написать падающие тесты** (`now` фиксируется через параметр `clock: Callable[[], datetime] = lambda: datetime.now(UTC)` в конструкторах `InviteService` и `QuestionService`)

```python
def test_password_roundtrip_and_public_id():
    pm = ProjectPasswordManager()
    pid = pm.new_public_id()
    pw = pm.generate(pid)
    assert pm.extract_public_id(f"  {pw}\n") == pid and len(pw) <= 200
    h = pm.hash(pw)
    assert pm.verify(h, pw) and not pm.verify(h, pw + "x") and not pm.verify("garbage", pw)
    assert pm.extract_public_id("nohyphen") is None and pm.extract_public_id("ZZZZZZZZ-abc") is None


async def test_create_project_makes_creator_admin_and_current(project_service, user):
    p = await project_service.create_project(user, "  Альфа ")
    cur = await project_service.get_current(user)
    assert p.name == "Альфа" and cur.role == MemberRole.ADMIN and cur.project.id == p.id
    pw = await project_service.reveal_password(user, p.id)
    assert ProjectPasswordManager().verify(p.password_hash, pw)


@pytest.mark.parametrize("name", ["", "   ", "x" * 65])
async def test_create_project_invalid_name(project_service, user, name):
    with pytest.raises(InvalidProjectName):
        await project_service.create_project(user, name)


async def test_reveal_password_only_creator(project_service, project, user):
    with pytest.raises(NotProjectAdmin):
        await project_service.reveal_password(user, project.id)


async def test_join_as_admin_bruteforce_limited(project_service, user):  # лимит 5 из settings
    for _ in range(5):
        with pytest.raises(InvalidPassword):
            await project_service.join_as_admin(user, "deadbeef-wrong")
    with pytest.raises(TooManyAttempts):
        await project_service.join_as_admin(user, "deadbeef-wrong")


async def test_redeem_keeps_admin_role(invite_service, project, admin):
    inv = await invite_service.create_invite(admin, project.id, 7)
    assert (await invite_service.redeem(admin, inv.code)).role == MemberRole.ADMIN


async def test_redeem_expired_at_boundary(invite_service_at, project, admin, user):
    inv = await invite_service_at(T0).create_invite(admin, project.id, 1)
    with pytest.raises(InviteExpired):
        await invite_service_at(T0 + timedelta(days=1)).redeem(user, inv.code)


@pytest.mark.parametrize(
    "text,code",
    [
        ("https://t.me/my_bot?start=AbC_def-12345", "AbC_def-12345"),
        ("t.me/my_bot?start=AbC_def-12345", "AbC_def-12345"),
        ("  AbC_def-12345 ", "AbC_def-12345"),
        ("привет", None),
        ("short", None),
        ("https://evil.com/?x=1", None),
    ],
)
def test_extract_code(text, code):
    assert InviteService.extract_code(text) == code


async def test_removed_admin_cannot_create_invite(
    invite_service, membership_service, project, admin, second_admin
):
    await membership_service.remove_member(admin, project.id, second_admin.id)
    with pytest.raises(NotProjectAdmin):
        await invite_service.create_invite(second_admin, project.id, 7)


async def test_cannot_remove_creator_or_self(membership_service, project, admin, second_admin):
    with pytest.raises(CannotRemoveCreator):
        await membership_service.remove_member(second_admin, project.id, admin.id)
    with pytest.raises(CannotRemoveSelf):
        await membership_service.remove_member(second_admin, project.id, second_admin.id)


async def test_removed_user_loses_current_project(
    membership_service, project_service, project, admin, user
):
    await project_service.switch_project(user, project.id)
    await membership_service.remove_member(admin, project.id, user.id)
    assert await project_service.get_current(user) is None


async def test_switch_to_foreign_project_rejected(project_service, user, other_project):
    with pytest.raises(NotProjectMember):
        await project_service.switch_project(user, other_project.id)
```

- [ ] **Step 2: Запустить** → FAIL. **Step 3: Реализовать.** **Step 4: Запустить** `uv run pytest tests/core tests/services -v` → PASS.
- [ ] **Step 5: Коммит** — `git commit -m "feat(tg_bot): project, invite and membership services"`

---

### Task 9: Сервисы вопросов, документации и групповых чатов; Notifier; тексты

**Files:**
- Create: `app/services/question_service.py`, `docs_service.py`, `group_chat_service.py`, `app/bot/__init__.py`, `app/bot/texts.py`, `app/bot/notifier.py`
- Test: `tests/services/test_question_service.py`, `test_docs_service.py`, `test_group_chat_service.py`, `tests/bot/test_texts.py`, `tests/bot/test_notifier.py`

**Interfaces:**
- Consumes: Tasks 4–8, `LLMGateway`.
- Produces:
  - `Asker` (dataclass): `telegram_id: int`, `chat_id: int`, `message_id: int`. `AskOutcome` (dataclass): `answer: str | None`, `question: Question | None` (ровно одно из полей не `None`).
  - `QuestionService(llm_gateway, answer_cache_work, rate_limit_work, question_work, project_work, membership_work, access_guard, settings, clock)`: `MAX_QUESTION_LEN = 4000`. `ask_text(project_id: int, text: str, asker: Asker, scope: RateLimitScope) -> AskOutcome` — лимит частоты по `asker.telegram_id` для `QUESTION` и по `asker.chat_id` для `GROUP_QUESTION` (`RateLimited`); пустой или слишком длинный текст — `QuestionTooLong`; затем кеш по `docs_version`, затем LLM; найденный ответ кешируется, иначе создаётся `Question`. `ask_image(project_id: int, image: bytes, image_file_id: str, caption: str | None, asker: Asker) -> AskOutcome` — без кеша, в вопросе сохраняется `caption or "[скриншот]"`. `get_admin_chat_ids(project_id: int) -> list[int]`. `answer_question(admin: User, question_id: int, answer: str) -> Question` (`QuestionNotFound`, `require_admin`, `QuestionAlreadyAnswered`). `save_to_knowledge(admin: User, question_id: int) -> None` (`require_admin`, вопрос должен быть в статусе ANSWERED, вызывает `llm.save_knowledge`).
  - `DocsService(llm_gateway, project_work, doc_update_work, membership_work, access_guard, settings)`: `TELEGRAM_DOWNLOAD_LIMIT = 20 * 1024 * 1024`; `@classmethod check_file_size(size: int | None) -> None` (`None` или больше лимита — `FileTooLarge`); `apply_update(admin: User, project_id: int, mode: DocsMode, files: list[DocFile]) -> DocUpdate` (`require_admin`, пустой список — `NoFilesToUpload`, `llm_gateway.upload_documents` (вернёт сводку), затем `bump_docs_version` и `DocUpdate` со сводкой; при `LLMUnavailableError` версия не меняется); `get_summary(user: User, update_id: int) -> str` (`require_member` для проекта обновления); `get_member_chat_ids(project_id: int) -> list[int]`.
  - `GroupChatService(group_chat_work, invite_service, project_work)`: `bind(chat_id: int, code: str, by_telegram_id: int) -> Project` (через `invite_service.get_valid_invite`); `get_bound_project(chat_id: int) -> Project | None`.
  - `app/bot/texts.py`: все строки интерфейса как константы плюс функции-форматтеры. Каждая функция экранирует аргументы через `html.escape`. Обязательные функции: `project_button(name: str, role: MemberRole) -> str` (`f"📁 {name} · {label}"`, имя обрезается до 40 символов с `…`); `admin_unanswered(question: str) -> str` (дословно «Мне задали вопрос «{q}», однако в документации ответа нет. Ответьте сами или дополните документацию.»); `invite_link(bot_username: str, code: str) -> str`; `split_message(text: str, limit: int = 4096) -> list[str]` (режет по `\n`, затем по пробелу, затем жёстко).
  - `Notifier(bot: Bot, rate_per_second: float = 25)`: `async send_many(chat_ids: list[int], text: str, reply_markup=None) -> SendReport(sent: int, failed: int)` — отправка последовательная с паузой `1/rate`; при `TelegramRetryAfter` ждёт `retry_after` и повторяет один раз; `TelegramForbiddenError` и `TelegramBadRequest` идут в `failed` и пишутся в лог. `send_many_in_background(...) -> asyncio.Task` — держит ссылку на задачу в `set` и убирает её по `add_done_callback`.

- [ ] **Step 1: Написать падающие тесты** (LLM подменяется фейком `FakeLLMGateway` в `tests/conftest.py`: в нём настраиваемые ответы и счётчик вызовов)

```python
async def test_ask_uses_cache_second_time(question_service, fake_llm, project, asker):
    fake_llm.answer = AskResult(found=True, answer="A")
    for _ in range(2):
        assert (
            await question_service.ask_text(project.id, "Q", asker, RateLimitScope.QUESTION)
        ).answer == "A"
    assert fake_llm.ask_calls == 1


async def test_not_found_creates_question(question_service, fake_llm, project, asker):
    fake_llm.answer = AskResult(found=False, answer=None)
    out = await question_service.ask_text(project.id, "Q", asker, RateLimitScope.QUESTION)
    assert out.answer is None and out.question.status == QuestionStatus.PENDING


async def test_cache_invalidated_by_docs_update(
    question_service, docs_service, fake_llm, project, admin, asker
):
    ...
    # после apply_update тот же вопрос снова идёт в LLM: fake_llm.ask_calls == 2


async def test_question_too_long(question_service, project, asker):
    with pytest.raises(QuestionTooLong):
        await question_service.ask_text(project.id, "x" * 4001, asker, RateLimitScope.QUESTION)


async def test_answer_question_race_only_one_wins(
    question_service, pending_question, admin, second_admin
):
    results = await asyncio.gather(
        question_service.answer_question(admin, pending_question.id, "a1"),
        question_service.answer_question(second_admin, pending_question.id, "a2"),
        return_exceptions=True,
    )
    assert sum(isinstance(r, QuestionAlreadyAnswered) for r in results) == 1


async def test_non_admin_cannot_answer(question_service, pending_question, user):
    with pytest.raises(NotProjectAdmin):
        await question_service.answer_question(user, pending_question.id, "a")


async def test_llm_failure_keeps_docs_version(docs_service, fake_llm, project, admin):
    fake_llm.fail = True
    with pytest.raises(LLMUnavailableError):
        await docs_service.apply_update(admin, project.id, DocsMode.APPEND, [DocFile("a.md", b"x")])
    assert (await Project.get(id=project.id)).docs_version == 0


@pytest.mark.parametrize("size", [None, 20 * 1024 * 1024 + 1])
def test_file_too_large(size):
    with pytest.raises(FileTooLarge):
        DocsService.check_file_size(size)


async def test_summary_hidden_from_non_member(docs_service, doc_update, outsider):
    with pytest.raises(NotProjectMember):
        await docs_service.get_summary(outsider, doc_update.id)


def test_texts_escape_user_input():
    assert "&lt;b&gt;" in texts.admin_unanswered("<b>x</b>")
    assert texts.project_button("<i>", MemberRole.ADMIN) == "📁 &lt;i&gt; · админ"


def test_split_message_respects_limit():
    parts = texts.split_message(("строка\n" * 1000) + "x" * 5000)
    assert (
        all(len(p) <= 4096 for p in parts)
        and "".join(parts).replace("\n", "") == ("строка" * 1000) + "x" * 5000
    )


async def test_notifier_counts_blocked_and_retries(mocked_bot):
    mocked_bot.fail_for = {2: TelegramForbiddenError, 3: "retry_once"}
    report = await Notifier(mocked_bot, rate_per_second=1000).send_many([1, 2, 3], "hi")
    assert report == SendReport(sent=2, failed=1)
```
(`mocked_bot` — простой фейк с `send_message`, достаточный для Notifier. Полный `MockedSession` появляется в Task 10.)

- [ ] **Step 2: Запустить** → FAIL. **Step 3: Реализовать.** **Step 4: Запустить** `uv run pytest tests/services tests/bot -v` → PASS.
- [ ] **Step 5: Коммит** — `git commit -m "feat(tg_bot): question, docs, group services, notifier, texts"`

---

### Task 10: `dependencies.py`, middleware, точка входа, `/start` и выбор роли

**Files:**
- Create: `app/dependencies.py`, `app/bot/middlewares.py`, `app/bot/callbacks.py`, `app/bot/states.py`, `app/bot/keyboards.py`, `app/bot/routers/__init__.py`, `app/bot/routers/start.py`, `app/__main__.py`
- Test: `tests/test_dependencies.py`, `tests/bot/conftest.py` (`MockedSession`, `bot`, `dp`, `feed`), `tests/bot/routers/test_start.py`

**Interfaces:**
- Consumes: всё выше.
- Produces:
  - `app/dependencies.py` — фабрика на каждый класс. Синглтоны (`@functools.cache`, без параметров): `get_obj_settings`, `get_obj_bot` (`Bot(token, default=DefaultBotProperties(parse_mode="HTML"))`), `get_obj_postgres_client`, `get_obj_redis_client`, `get_obj_llm_gateway`, `get_obj_password_manager`, `get_obj_notifier`, `get_obj_fsm_storage` (`RedisStorage(redis=client.redis, key_builder=DefaultKeyBuilder(prefix=RedisKey.FSM_PREFIX))`), `get_obj_resolver`. Остальные фабрики получают зависимости через `Depends`: `get_obj_user_work(client: PostgresClient = Depends(get_obj_postgres_client))`, `get_obj_project_work`, `get_obj_membership_work`, `get_obj_invite_work`, `get_obj_question_work`, `get_obj_doc_update_work`, `get_obj_group_chat_work`, `get_obj_answer_cache_work`, `get_obj_rate_limit_work`, `get_obj_password_reveal_work`, `get_obj_docs_draft_work`, `get_obj_access_guard`, `get_obj_project_service`, `get_obj_invite_service`, `get_obj_membership_service`, `get_obj_question_service`, `get_obj_docs_service`, `get_obj_group_chat_service`.
  - `UserMiddleware(resolver: DependsResolver)` — outer-middleware на `message` и `callback_query`: делает upsert пользователя из `event.from_user` и кладёт результат в `data["db_user"]`.
  - `callbacks.py` (aiogram `CallbackData`, у каждого короткий `prefix` (`PasswordRevealCb` → `"pwreveal"`, `SaveKnowledgeCb` → `"savekn"`, у остальных — сокращение имени), чтобы уложиться в лимит Telegram 64 байта на callback_data; в каждом callback, который действует на проект, есть `project_id`): `RoleCb(role)`, `AdminStartCb(action: "create"|"join")`, `MenuCb(action, project_id)` с действиями `instructions|join_other|docs_append|docs_replace|invite|invites|members`, `PasswordRevealCb(project_id)`, `SwitchProjectCb(project_id)`, `InviteTtlCb(project_id, days)`, `RevokeInviteCb(project_id, invite_id)`, `MembersPageCb(project_id, page)`, `RemoveMemberCb(project_id, user_id, confirm: bool)`, `AnswerQuestionCb(question_id)`, `SaveKnowledgeCb(question_id)`, `ChangesCb(update_id)`, `DocsDoneCb(project_id)`, `DocsCancelCb(project_id)`.
  - `states.py`: `CreateProject.name`, `JoinAsAdmin.password`, `JoinAsUser.code`, `DocsUpload.password`, `DocsUpload.files`, `AnswerQuestion.text`.
  - `keyboards.py`: `role_kb()`, `admin_start_kb()`, `project_reply_kb(cur: CurrentProject)` (одна кнопка `texts.project_button`, `is_persistent=True`), `main_menu_kb(cur: CurrentProject, bot_username: str)` (две кнопки для всех плюс админские кнопки при `role == ADMIN`; у «Инструкции» ещё и URL `https://t.me/{bot}?startgroup=true` внутри текста инструкции).
  - `setup_routers() -> Router` — порядок подключения: `start, admin_project, admin_invites, admin_members, admin_docs, projects, answers, group, questions` (`questions` последний, это catch-all для лички).
  - Хендлеры `start.py`: `/start` без аргумента сбрасывает FSM и показывает `role_kb`; `/start <code>` вызывает `invite_service.redeem`, затем «вы в проекте» плюс `project_reply_kb` и `main_menu_kb`, а при ошибке приглашения — понятный текст по виду ошибки; `/menu` показывает меню текущего проекта; `/cancel` сбрасывает FSM. `RoleCb(admin)` показывает `admin_start_kb`, `RoleCb(user)` переводит в `JoinAsUser.code` и просит прислать ссылку или код.
  - `__main__.py`: `main()` — логирование, `connect()` клиентов, `Dispatcher(storage=get_obj_fsm_storage())`, `UserMiddleware` (outer) и `DependsMiddleware` (inner) на `message` и `callback_query`, `include_router(setup_routers())`, `start_polling(bot, allowed_updates=dp.resolve_used_update_types())`, в `finally` — `close()` клиентов и сессии бота.
  - `tests/bot/conftest.py`: `MockedSession(BaseSession)` записывает каждый `TelegramMethod` в `requests` и возвращает результат, собранный из полей метода (`SendMessage` → `Message` с `message_id` по счётчику, `AnswerCallbackQuery`/`DeleteMessage` → `True`). Фикстура `feed(update_or_message)` вызывает `dp.feed_update(bot, Update(...))`. Через `resolver.overrides` фабрики Work-классов подменяются на версии с `pg_client` (sqlite) и `redis_client` (fakeredis), LLM — на `FakeLLMGateway`.

- [ ] **Step 1: Написать падающие тесты**

```python
async def test_all_factories_resolve(resolver_with_test_clients):
    for name in [n for n in dir(dependencies) if n.startswith("get_obj_")]:
        assert await resolver_with_test_clients.resolve(getattr(dependencies, name)) is not None


async def test_start_shows_roles(feed, session):
    await feed(text="/start")
    assert "Админ" in str(session.last(SendMessage).reply_markup)


async def test_start_with_valid_code_joins(feed, session, invite, user_tg):
    await feed(text=f"/start {invite.code}", from_user=user_tg)
    sent = session.all(SendMessage)
    assert any("📁 " in str(m.reply_markup) for m in sent)


async def test_start_with_revoked_code_explains(feed, session, revoked_invite):
    await feed(text=f"/start {revoked_invite.code}")
    assert session.last(SendMessage).text == texts.INVITE_REVOKED
```

- [ ] **Step 2: Запустить** → FAIL. **Step 3: Реализовать.** **Step 4: Запустить** `uv run pytest -v` → PASS (весь набор).
- [ ] **Step 5: Smoke-проверка запуска** — `docker compose up -d postgres redis && uv run aerich upgrade && uv run python -m app` с реальным `BOT_TOKEN` в `.env`. В логе есть `Start polling`, ошибок нет. `/start` в Telegram показывает выбор роли. Если токена нет — так и пишем в отчёте, проверка остаётся за пользователем.
- [ ] **Step 6: Коммит** — `git commit -m "feat(tg_bot): DI wiring, entrypoint, start and role selection"`

---

### Task 11: Роутеры админа — проект, пароль, приглашения, участники, документация

**Files:**
- Create: `app/bot/routers/admin_project.py`, `admin_invites.py`, `admin_members.py`, `admin_docs.py`
- Test: `tests/bot/routers/test_admin_project.py`, `test_admin_invites.py`, `test_admin_members.py`, `test_admin_docs.py`

**Interfaces:**
- Consumes: сервисы (Tasks 8–9), `callbacks`, `states`, `keyboards`, `texts`, `Notifier`.
- Produces (поведение хендлеров):
  - `AdminStartCb(create)` → `CreateProject.name` → `create_project` → сообщение «проект создан» с кнопкой «Показать пароль» (`PasswordRevealCb`), плюс `project_reply_kb` и `main_menu_kb`. `PasswordRevealCb` → `reveal_password` → `callback.answer(pw, show_alert=True)`; после истечения TTL — alert с `texts.PASSWORD_EXPIRED`.
  - `AdminStartCb(join)` → `JoinAsAdmin.password` → `message.delete()` (`TelegramBadRequest` пишется в лог и не прерывает сценарий) → `join_as_admin` → меню. `InvalidPassword` и `TooManyAttempts` — свой текст для каждого, состояние сохраняется.
  - `MenuCb(invite)` → выбор TTL (`InviteTtlCb` с днями из `INVITE_TTL_DAYS`) → `create_invite` → сообщение со ссылкой `texts.invite_link(...)`. `MenuCb(invites)` → список активных приглашений с кнопками `RevokeInviteCb`.
  - `MenuCb(members)` → страница `list_members` (кнопки `RemoveMemberCb(confirm=False)`, навигация `MembersPageCb`) → подтверждение (`confirm=True`) → `remove_member` → удалённому уходит `texts.removed_from_project(name)` (ошибки отправки игнорируются).
  - `MenuCb(docs_append)` сразу переводит в `DocsUpload.files`; `MenuCb(docs_replace)` сначала `DocsUpload.password` → `message.delete()` → `check_password` → `DocsUpload.files`. Режим и `project_id` хранятся в FSM data. В `DocsUpload.files`: каждый `F.document` → `check_file_size` → `docs_draft_work.add` (при `-1` — текст о лимите) → ответ «Принято: {имя} ({n}/{max})» с кнопками `DocsDoneCb`/`DocsCancelCb`. `DocsDoneCb` → скачивание файлов через `bot.download` в `DocFile` → `apply_update` → черновик очищается, FSM сбрасывается → «Документация обновлена» → `notifier.send_many_in_background(member_ids, texts.DOCS_UPDATED, ChangesCb)`. При `LLMUnavailableError` — `texts.LLM_UNAVAILABLE`, черновик сохраняется, чтобы можно было повторить.
  - На любом `MenuCb` или `*Cb` с `project_id` сервис сам проверяет права, а `NotProjectAdmin` превращается в `callback.answer(texts.NO_RIGHTS, show_alert=True)`.

- [ ] **Step 1: Написать падающие тесты**

```python
async def test_create_project_flow_password_only_in_alert(feed, session, admin_tg):
    await feed(callback=AdminStartCb(action="create").pack(), from_user=admin_tg)
    await feed(text="Альфа", from_user=admin_tg)
    reveal = next(b for b in session.buttons() if b.callback_data.startswith("pwreveal"))
    await feed(callback=reveal.callback_data, from_user=admin_tg)
    alert = session.last(AnswerCallbackQuery)
    assert alert.show_alert and "-" in alert.text
    assert all(alert.text not in (m.text or "") for m in session.all(SendMessage))


async def test_join_as_admin_deletes_password_message(
    feed, session, project_with_password, user_tg
):
    await feed(callback=AdminStartCb(action="join").pack(), from_user=user_tg)
    msg_id = await feed(text=project_with_password.password, from_user=user_tg)
    assert session.last(DeleteMessage).message_id == msg_id


async def test_replace_docs_requires_password(feed, session, admin_tg, project):
    await feed(
        callback=MenuCb(action="docs_replace", project_id=project.id).pack(), from_user=admin_tg
    )
    assert session.last(SendMessage).text == texts.ASK_PASSWORD_FOR_REPLACE


async def test_too_large_document_rejected(feed, session, admin_in_docs_upload):
    await feed(
        document={
            "file_id": "f",
            "file_unique_id": "u",
            "file_name": "big.pdf",
            "file_size": 20 * 1024 * 1024 + 1,
        }
    )
    assert session.last(SendMessage).text == texts.FILE_TOO_LARGE


async def test_docs_done_notifies_members(
    feed, session, admin_with_draft, fake_llm, project_members
):
    await feed(callback=DocsDoneCb(project_id=admin_with_draft.project_id).pack())
    await drain_background_tasks()
    notified = {m.chat_id for m in session.all(SendMessage) if m.text == texts.DOCS_UPDATED}
    assert notified == set(project_members)


async def test_user_cannot_use_admin_button(feed, session, user_tg, project):
    await feed(callback=MenuCb(action="members", project_id=project.id).pack(), from_user=user_tg)
    assert session.last(AnswerCallbackQuery).text == texts.NO_RIGHTS
```

- [ ] **Step 2: Запустить** → FAIL. **Step 3: Реализовать.** **Step 4: Запустить** `uv run pytest tests/bot -v` → PASS.
- [ ] **Step 5: Коммит** — `git commit -m "feat(tg_bot): admin flows — project, password, invites, members, docs"`

---

### Task 12: Работа в проекте — подключение, переключение, вопросы, ответы админа, «Что изменилось»

**Files:**
- Create: `app/bot/routers/projects.py`, `answers.py`, `questions.py`
- Test: `tests/bot/routers/test_projects.py`, `test_answers.py`, `test_questions.py`

**Interfaces:**
- Consumes: сервисы, `Notifier`, `texts.split_message`.
- Produces (поведение):
  - `projects.py`: `JoinAsUser.code` + текст → `extract_code` → `redeem` → меню (ошибка → текст по виду, состояние сохраняется). `F.text.startswith("📁 ")` → список проектов (`SwitchProjectCb`, у текущего ✅). `SwitchProjectCb` → `switch_project` → обновлённый `project_reply_kb` и меню. `MenuCb(join_other)` → `role_kb` (дальше общий сценарий). `MenuCb(instructions)` → `texts.group_instructions(bot_username)`. `ChangesCb` → `get_summary`.
  - `answers.py`: `AnswerQuestionCb` → `AnswerQuestion.text` (`question_id` в FSM data) → `answer_question` → ответ уходит в `question.chat_id` с `reply_parameters=ReplyParameters(message_id=..., allow_sending_without_reply=True)`, разбитый через `split_message` → админу «Ответ отправлен» и кнопка `SaveKnowledgeCb`; если доставка не удалась — `texts.ANSWER_NOT_DELIVERED`. `QuestionAlreadyAnswered` → `texts.ALREADY_ANSWERED`. `SaveKnowledgeCb` → `save_to_knowledge` → «Сохранено в базу знаний».
  - `questions.py` (только личка, без состояния FSM): `F.text` → без текущего проекта — `texts.NO_PROJECT` и `role_kb`; с проектом — `ask_text(..., RateLimitScope.QUESTION)`, во время ожидания `ChatAction.TYPING`. Ответ уходит частями `split_message`. Если ответа нет — `texts.NOT_FOUND_FORWARDED`, а админам уходит `admin_unanswered` с кнопками `AnswerQuestionCb` и `MenuCb(docs_append)`. `F.photo` (берётся самое крупное) и `F.document` с `mime_type` `image/*` вне `DocsUpload` → `bot.download` → `ask_image`, админам — `send_photo` с подписью. `RateLimited` → `texts.RATE_LIMITED`, `LLMUnavailableError` → `texts.LLM_UNAVAILABLE`.

- [ ] **Step 1: Написать падающие тесты**

```python
async def test_paste_link_joins_project(feed, session, invite, user_tg):
    await feed(callback=RoleCb(role="user").pack(), from_user=user_tg)
    await feed(text=f"https://t.me/test_bot?start={invite.code}", from_user=user_tg)
    assert any(
        texts.project_button(invite.project.name, MemberRole.USER) in str(m.reply_markup)
        for m in session.all(SendMessage)
    )


async def test_text_without_project_prompts_join(feed, session, fresh_user_tg):
    await feed(text="как настроить?", from_user=fresh_user_tg)
    assert session.last(SendMessage).text == texts.NO_PROJECT


async def test_not_found_goes_to_admins(feed, session, fake_llm, project, user_tg, admin_tg):
    fake_llm.answer = AskResult(found=False, answer=None)
    await feed(text="вопрос X", from_user=user_tg)
    to_admin = [m for m in session.all(SendMessage) if m.chat_id == admin_tg.id]
    assert to_admin and "вопрос X" in to_admin[-1].text


async def test_admin_answer_reaches_asker_and_offers_save(
    feed, session, pending_question, admin_tg
):
    await feed(
        callback=AnswerQuestionCb(question_id=pending_question.id).pack(), from_user=admin_tg
    )
    await feed(text="вот ответ", from_user=admin_tg)
    assert any(
        m.chat_id == pending_question.chat_id and "вот ответ" in m.text
        for m in session.all(SendMessage)
    )
    assert any(b.callback_data.startswith("savekn") for b in session.buttons())


async def test_long_answer_is_split(feed, session, fake_llm, user_in_project_tg):
    fake_llm.answer = AskResult(found=True, answer="a" * 9000)
    await feed(text="q", from_user=user_in_project_tg)
    assert [len(m.text) for m in session.all(SendMessage)][-3:] == [4096, 4096, 808]


async def test_llm_down_message(feed, session, fake_llm, user_in_project_tg):
    fake_llm.fail = True
    await feed(text="q", from_user=user_in_project_tg)
    assert session.last(SendMessage).text == texts.LLM_UNAVAILABLE
```

- [ ] **Step 2: Запустить** → FAIL. **Step 3: Реализовать.** **Step 4: Запустить** `uv run pytest tests/bot -v` → PASS.
- [ ] **Step 5: Коммит** — `git commit -m "feat(tg_bot): project switching, questions, admin answers, change summary"`

---

### Task 13: Групповой чат

**Files:**
- Create: `app/bot/routers/group.py`
- Test: `tests/bot/routers/test_group.py`

**Interfaces:**
- Consumes: `GroupChatService`, `QuestionService`, `Notifier`.
- Produces: роутер с фильтром `F.chat.type.in_({"group", "supergroup"})`. `Command("join")` (aiogram сам проверяет `@имя_бота`) → `bind` → «Чат привязан к проекту «X»»; без кода или с плохим кодом — текст ошибки. `Command("ask")` и сообщение с entity `mention`, равным `@{bot_username}` (сама упоминание из вопроса вырезается) → проект через `get_bound_project`; если чат не привязан — `texts.GROUP_NOT_BOUND`; иначе `ask_text(..., RateLimitScope.GROUP_QUESTION)` с `Asker(chat_id=группа, message_id=сообщение)`. Ответ уходит reply в группу, а при отсутствии ответа — тот же сценарий с админами, что и в Task 12. Пустой `/ask` → `texts.ASK_USAGE`.

- [ ] **Step 1: Написать падающие тесты**

```python
async def test_join_binds_group(feed, session, invite):
    await feed(text=f"/join@test_bot {invite.code}", chat_type="supergroup", chat_id=-100)
    assert (await GroupChat.get(chat_id=-100)).project_id == invite.project_id


async def test_ask_in_unbound_group(feed, session):
    await feed(text="/ask@test_bot что такое X?", chat_type="group", chat_id=-200)
    assert session.last(SendMessage).text == texts.GROUP_NOT_BOUND


async def test_mention_question_answered_in_group(feed, session, bound_group, fake_llm):
    fake_llm.answer = AskResult(found=True, answer="ответ")
    await feed(
        text="@test_bot что такое X?",
        entities=[{"type": "mention", "offset": 0, "length": 9}],
        chat_type="supergroup",
        chat_id=bound_group.chat_id,
    )
    assert fake_llm.last_question == "что такое X?"
    assert session.last(SendMessage).chat_id == bound_group.chat_id


async def test_group_answer_never_uses_other_project(feed, bound_group, other_project, fake_llm):
    ...
    # fake_llm.last_project_id == bound_group.project_id
```

- [ ] **Step 2: Запустить** → FAIL. **Step 3: Реализовать.** **Step 4: Запустить** → PASS.
- [ ] **Step 5: Коммит** — `git commit -m "feat(tg_bot): group chat binding and questions"`

---

### Task 14: Финальная проверка

- [ ] **Step 1: Весь набор тестов и линтер** — `uv run pytest -q` → все проходят. `uv run ruff check . && uv run ruff format --check .` → ошибок нет.
- [ ] **Step 2: Перечитать весь дифф** `git diff main -- tg_bot` как чужой код по чеклисту responsible-engineering: отладочные print, мёртвый код, нарушения правил хранилищ. Проверить отдельно: `grep -rn "Tortoise\|\.filter(\|\.redis\." app/bot app/services` → пусто, обращения к хранилищу есть только в `app/storage`; `grep -rnE "\"(users|projects|memberships|invites|questions|doc_updates|group_chats)\"" app --include=*.py` → совпадения только в `storage/enums.py`.
- [ ] **Step 3: Полный запуск в Docker** — `docker compose up -d --build` → `bot` в статусе running, в логе миграция применена и `Start polling`. Ручной прогон в Telegram (при наличии токена): создать проект → показать пароль → пригласить → войти по ссылке со второго аккаунта → задать вопрос (заглушка: «ответа нет, передано админу», админ отвечает, ответ доходит) → дополнить документацию (участникам приходит уведомление, «Что изменилось» показывает список файлов) → переключить проект → добавить бота в группу и сделать `/join`.
- [ ] **Step 4: Отчёт пользователю** — что прогнано, какие граничные случаи покрыты и что не проверено: реальные ответы ИИ (их не будет, пока нет реализации `LLMGateway`; сейчас работает `StubLLMGateway`); доставка @упоминаний в группе при включённом privacy mode (если Telegram их не присылает, рабочим остаётся `/ask@бот`).

---

## Отклонения от плана при реализации

- **Tortoise ORM 1.x** вместо 0.2x: контекст подключения хранится в contextvar, поэтому `PostgresClient.connect()` включает `_enable_global_fallback`, иначе хендлеры и фоновые задачи не видят подключение.
- **`users.current_project_id` без внешнего ключа:** FK давал цикл `users ↔ projects` (через `projects.creator`). Актуальность значения проверяет `ProjectService.get_current`.
- **Индексы** `memberships(project_id, role)` и `invites(project_id, expires_at)`: Postgres не индексирует внешние ключи сам.
- **`ProjectPasswordManager.hash/verify` асинхронные** и выполняются в `asyncio.to_thread`, потому что argon2 нагружает CPU.
- **`PostgresProjectWork.create`** при коллизии `public_id` возвращает `None`, поэтому сервис не знает об исключениях ORM.
- **Тексты:** у кнопок и всплывающих окон экранирования нет, Telegram не разбирает там HTML. Ответы ИИ и админа уходят без `parse_mode`, а `split_message` режет исходный текст.
- **Роутеры:** каждый модуль отдаёт `create_router()`, потому что aiogram не подключает один роутер к двум диспетчерам. Модуль `projects` идёт сразу после `start`, а последним добавлен роутер `fallback`.
- **По итогам независимого ревью:**
  - запрет возврата удалённого участника — `ProjectBan` и `PostgresProjectBanWork`, сравнение по `id` приглашения, вторая миграция;
  - запрет перепривязки группы посторонним;
  - блокировка двойного «Готово» (`SET NX`);
  - подтверждение замены документации действует 15 минут;
  - пароль, отправленный как вопрос, удаляется и никуда не пересылается;
  - глобальный обработчик ошибок.
- **Тесты на настоящих БД:** переменные `TEST_POSTGRES_URL` и `TEST_REDIS_URL` (отдельная база, после каждого теста очищается).
