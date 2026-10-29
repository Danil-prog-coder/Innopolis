"""Внедрение зависимостей в хендлеры aiogram в стиле FastAPI.

Хендлер объявляет зависимость значением по умолчанию::

    async def handler(message: Message, chat_work: PostgresChatWork = Depends(get_obj_chat_work)):
        ...

``DependsMiddleware`` перед вызовом хендлера разрешает такие параметры и кладёт
результат в ``data``, а aiogram передаёт его в хендлер по имени параметра.
"""

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

Factory = Callable[..., Any]


class Depends:
    __slots__ = ("factory",)

    def __init__(self, factory: Factory) -> None:
        self.factory = factory

    def __repr__(self) -> str:
        return f"Depends({getattr(self.factory, '__name__', self.factory)!r})"


def _depends_params(func: Factory) -> dict[str, Depends]:
    """Параметры функции, которые надо разрешить через Depends."""
    result: dict[str, Depends] = {}
    for param in inspect.signature(func).parameters.values():
        if isinstance(param.default, Depends):
            result[param.name] = param.default
    return result


class DependsResolver:
    def __init__(self) -> None:
        # Подмена фабрик (для тестов): {оригинальная фабрика: подменная}
        self.overrides: dict[Factory, Factory] = {}

    async def resolve(self, factory: Factory, cache: dict[Factory, Any] | None = None) -> Any:
        """Вызвать фабрику, рекурсивно разрешив её собственные Depends.

        В пределах одного ``cache`` каждая фабрика вызывается не больше одного раза.
        """
        if cache is None:
            cache = {}
        if factory in cache:
            return cache[factory]

        target = self.overrides.get(factory, factory)
        kwargs: dict[str, Any] = {}
        for param in inspect.signature(target).parameters.values():
            if isinstance(param.default, Depends):
                kwargs[param.name] = await self.resolve(param.default.factory, cache)
            elif param.default is inspect.Parameter.empty and param.kind not in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            ):
                name = getattr(target, "__name__", repr(target))
                raise TypeError(
                    f"Фабрика {name} не может быть разрешена: параметр {param.name!r} "
                    "без значения по умолчанию и без Depends"
                )

        result = target(**kwargs)
        if inspect.isawaitable(result):
            result = await result
        cache[factory] = result
        return result


class DependsMiddleware(BaseMiddleware):
    """Inner-middleware: разрешает Depends-параметры хендлера."""

    def __init__(self, resolver: DependsResolver) -> None:
        self._resolver = resolver
        self._signatures: dict[Callable[..., Any], dict[str, Depends]] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        handler_object = data.get("handler")
        if handler_object is not None:
            callback = inspect.unwrap(handler_object.callback)
            params = self._signatures.get(callback)
            if params is None:
                params = _depends_params(callback)
                self._signatures[callback] = params
            cache: dict[Factory, Any] = {}
            for name, dependency in params.items():
                data[name] = await self._resolver.resolve(dependency.factory, cache)
        return await handler(event, data)
