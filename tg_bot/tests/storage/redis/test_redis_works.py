import asyncio
import hashlib

import pytest

from app.core.enums import RateLimitScope
from app.storage.enums import RedisKey
from app.storage.redis.docs_draft_work import DraftFile


async def test_client_basic_ops(redis_client):
    await redis_client.set("k", "v", ttl_seconds=10)
    assert await redis_client.get("k") == "v"
    assert await redis_client.delete("k") == 1
    assert await redis_client.get("k") is None


async def test_client_not_connected_raises():
    from app.storage.redis.client import RedisClient

    with pytest.raises(RuntimeError):
        _ = RedisClient("redis://localhost").redis


async def test_cache_is_exact_and_version_scoped(answer_cache_work):
    await answer_cache_work.set(1, 0, "q", "ok")
    assert await answer_cache_work.get(1, 0, "q") == "ok"
    # Ключ exact: регистр и границы значимы (trim делает QuestionService).
    assert await answer_cache_work.get(1, 0, "Q") is None
    assert await answer_cache_work.get(1, 0, "q ") is None
    assert await answer_cache_work.get(1, 1, "q") is None  # другая версия
    assert await answer_cache_work.get(2, 0, "q") is None  # другой проект


async def test_cache_distinguishes_numbers_negations_and_case(answer_cache_work):
    cases = {"не 5": "a", "не 6": "b", "5": "c", "User": "d", "user": "e"}
    for question, value in cases.items():
        await answer_cache_work.set(1, 0, question, value)

    for question, value in cases.items():
        assert await answer_cache_work.get(1, 0, question) == value
    assert await answer_cache_work.get(1, 0, "не") is None


async def test_cache_distinguishes_code_indentation_and_literals(answer_cache_work):
    four_spaces = "```python\nif x:\n    print('a')\n```"
    two_spaces = "```python\nif x:\n  print('a')\n```"
    other_literal = "```python\nif x:\n    print('b')\n```"

    await answer_cache_work.set(1, 0, four_spaces, "a")

    assert await answer_cache_work.get(1, 0, four_spaces) == "a"
    assert await answer_cache_work.get(1, 0, two_spaces) is None
    assert await answer_cache_work.get(1, 0, other_literal) is None


async def test_legacy_cache_namespace_is_not_read(answer_cache_work, redis_client):
    question_hash = hashlib.sha256(b"q").hexdigest()
    legacy_key = f"cache:answer:1:0:{question_hash}"
    await redis_client.set(legacy_key, "legacy", ttl_seconds=60)

    assert await answer_cache_work.get(1, 0, "q") is None


async def test_cache_has_ttl(answer_cache_work, redis_client):
    await answer_cache_work.set(1, 0, "q", "a")
    keys = await redis_client.redis.keys("cache:answer:v1:1:0:*")
    assert len(keys) == 1
    assert 0 < await redis_client.redis.ttl(keys[0]) <= 60


async def test_rate_limit_blocks_after_limit(rate_limit_work):
    results = [await rate_limit_work.hit(RateLimitScope.PASSWORD, 7, 3, 60) for _ in range(4)]
    assert results == [True, True, True, False]
    await rate_limit_work.reset(RateLimitScope.PASSWORD, 7)
    assert await rate_limit_work.hit(RateLimitScope.PASSWORD, 7, 3, 60) is True


async def test_rate_limit_scopes_and_subjects_independent(rate_limit_work):
    assert await rate_limit_work.hit(RateLimitScope.PASSWORD, 7, 1, 60) is True
    assert await rate_limit_work.hit(RateLimitScope.QUESTION, 7, 1, 60) is True
    assert await rate_limit_work.hit(RateLimitScope.PASSWORD, 8, 1, 60) is True
    assert await rate_limit_work.hit(RateLimitScope.PASSWORD, 7, 1, 60) is False


async def test_rate_limit_key_has_ttl_that_does_not_slide(rate_limit_work, redis_client):
    key = RedisKey.RATE_LIMIT.build(scope="question", subject_id=7)
    await rate_limit_work.hit(RateLimitScope.QUESTION, 7, 3, 60)
    await redis_client.redis.expire(key, 5)
    await rate_limit_work.hit(RateLimitScope.QUESTION, 7, 3, 60)
    assert 0 < await redis_client.redis.ttl(key) <= 5


async def test_password_reveal(password_reveal_work, redis_client):
    await password_reveal_work.put(5, "secret")
    assert await password_reveal_work.get(5) == "secret"
    assert await password_reveal_work.get(5) == "secret"  # можно показать повторно в пределах TTL
    assert await password_reveal_work.get(6) is None
    ttl = await redis_client.redis.ttl(RedisKey.PASSWORD_REVEAL.build(project_id=5))
    assert 0 < ttl <= 600


async def test_docs_draft_concurrent_add_keeps_all(docs_draft_work):
    files = [DraftFile(file_id=f"f{i}", filename=f"{i}.md", size=1) for i in range(5)]
    await asyncio.gather(*(docs_draft_work.add(1, 1, f, max_files=10) for f in files))
    stored = await docs_draft_work.list(1, 1)
    assert {f.file_id for f in stored} == {f.file_id for f in files}


async def test_docs_draft_rejects_over_limit(docs_draft_work):
    files = [DraftFile(file_id=f"f{i}", filename="x", size=1) for i in range(3)]
    assert [await docs_draft_work.add(1, 1, f, max_files=2) for f in files] == [1, 2, -1]
    assert [f.file_id for f in await docs_draft_work.list(1, 1)] == ["f0", "f1"]


async def test_docs_draft_isolated_and_clearable(docs_draft_work):
    file = DraftFile(file_id="x", filename="x", size=1)
    await docs_draft_work.add(1, 1, file, max_files=5)
    assert await docs_draft_work.list(1, 2) == []
    assert await docs_draft_work.list(2, 1) == []
    await docs_draft_work.clear(1, 1)
    assert await docs_draft_work.list(1, 1) == []
