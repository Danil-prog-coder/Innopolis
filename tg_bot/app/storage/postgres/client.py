from collections.abc import Iterable
from typing import Any, TypeVar

from tortoise import Tortoise
from tortoise.context import TortoiseContext
from tortoise.models import Model

M = TypeVar("M", bound=Model)


class PostgresClient:
    """Подключение к PostgreSQL и универсальный CRUD. О конкретных сущностях не знает."""

    def __init__(self, db_url: str, modules: dict[str, Iterable[str]]) -> None:
        self._db_url = db_url
        self._modules = modules
        self._context: TortoiseContext | None = None

    async def connect(self) -> None:
        # Глобальный fallback нужен, чтобы подключение видели задачи, которые aiogram
        # и Notifier создают вне контекста, где вызван connect()
        self._context = await Tortoise.init(
            db_url=self._db_url, modules=self._modules, _enable_global_fallback=True
        )

    async def close(self) -> None:
        if self._context is not None:
            await self._context.close_connections()
            self._context = None

    async def generate_schemas(self) -> None:
        """Создать таблицы по моделям. Только для тестов: в работе схему ведёт aerich."""
        await Tortoise.generate_schemas()

    async def create(self, model: type[M], **fields: Any) -> M:
        return await model.create(**fields)

    async def get_one(self, model: type[M], **filters: Any) -> M | None:
        return await model.get_or_none(**filters)

    async def get_many(
        self,
        model: type[M],
        *,
        order_by: tuple[str, ...] = (),
        offset: int = 0,
        limit: int | None = None,
        prefetch: tuple[str, ...] = (),
        **filters: Any,
    ) -> list[M]:
        query = model.filter(**filters)
        if prefetch:
            query = query.prefetch_related(*prefetch)
        if order_by:
            query = query.order_by(*order_by)
        if offset:
            query = query.offset(offset)
        if limit is not None:
            query = query.limit(limit)
        return await query

    async def get_values(self, model: type[Model], field: str, **filters: Any) -> list[Any]:
        """Значения одного поля (можно через связь: ``user__telegram_id``)."""
        return list(await model.filter(**filters).values_list(field, flat=True))

    async def upsert(self, model: type[M], lookup: dict[str, Any], **fields: Any) -> M:
        """Обновить запись по ``lookup`` или создать её, если такой нет."""
        obj, _ = await model.update_or_create(defaults=fields, **lookup)
        return obj

    async def count(self, model: type[Model], **filters: Any) -> int:
        return await model.filter(**filters).count()

    async def update(self, model: type[Model], filters: dict[str, Any], **fields: Any) -> int:
        return await model.filter(**filters).update(**fields)

    async def delete(self, model: type[Model], **filters: Any) -> int:
        return await model.filter(**filters).delete()
