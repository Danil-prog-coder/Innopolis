import pytest
from aiogram.dispatcher.event.handler import HandlerObject

from app.core.depends import Depends, DependsMiddleware, DependsResolver


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


async def test_separate_calls_do_not_share_cache():
    counter = iter(range(100))

    def get_n():
        return next(counter)

    resolver = DependsResolver()
    assert await resolver.resolve(get_n) == 0
    assert await resolver.resolve(get_n) == 1


async def test_override_replaces_factory():
    def get_a():
        return "real"

    def get_b(a: str = Depends(get_a)):
        return a

    resolver = DependsResolver()
    resolver.overrides[get_a] = lambda: "fake"
    assert await resolver.resolve(get_b) == "fake"


async def test_factory_with_plain_params_rejected():
    def bad(x):
        return x

    with pytest.raises(TypeError, match="bad"):
        await DependsResolver().resolve(bad)


async def test_factory_with_defaults_uses_them():
    def get_a(x: int = 5):
        return x

    assert await DependsResolver().resolve(get_a) == 5


async def test_middleware_injects_only_depends_params():
    def get_x():
        return 42

    async def handler(event, x: int = Depends(get_x), other: str = "keep"): ...

    data = {"handler": HandlerObject(callback=handler)}
    seen = {}

    async def next_(event, passed):
        seen.update(passed)

    await DependsMiddleware(DependsResolver())(next_, object(), data)
    assert seen["x"] == 42
    assert "other" not in seen


async def test_middleware_shares_instance_within_one_event():
    def get_obj():
        return object()

    def get_wrapper(obj: object = Depends(get_obj)):
        return obj

    async def handler(event, a: object = Depends(get_obj), b: object = Depends(get_wrapper)): ...

    seen = {}

    async def next_(event, passed):
        seen.update(passed)

    data = {"handler": HandlerObject(callback=handler)}
    await DependsMiddleware(DependsResolver())(next_, object(), data)
    assert seen["a"] is seen["b"]


async def test_middleware_without_handler_passes_through():
    async def next_(event, passed):
        return "ok"

    assert await DependsMiddleware(DependsResolver())(next_, object(), {}) == "ok"
