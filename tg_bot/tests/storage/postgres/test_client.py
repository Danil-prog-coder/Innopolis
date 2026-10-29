import pytest
from tortoise.exceptions import IntegrityError

from app.storage.enums import RedisKey, TableName
from app.storage.postgres.models import (
    DocUpdate,
    GroupChat,
    Invite,
    Membership,
    Project,
    ProjectBan,
    Question,
    User,
)


async def test_crud_roundtrip(pg_client):
    user = await pg_client.create(User, telegram_id=1, full_name="A")
    assert (await pg_client.get_one(User, telegram_id=1)).id == user.id
    assert await pg_client.update(User, {"id": user.id}, full_name="B") == 1
    assert (await pg_client.get_one(User, id=user.id)).full_name == "B"
    assert await pg_client.count(User) == 1
    assert await pg_client.delete(User, id=user.id) == 1
    assert await pg_client.get_one(User, id=user.id) is None


async def test_get_many_order_offset_limit(pg_client):
    for i in range(5):
        await pg_client.create(User, telegram_id=i, full_name=f"u{i}")
    page = await pg_client.get_many(User, order_by=("-telegram_id",), offset=1, limit=2)
    assert [u.telegram_id for u in page] == [3, 2]


async def test_unique_telegram_id(pg_client):
    await pg_client.create(User, telegram_id=1, full_name="A")
    with pytest.raises(IntegrityError):
        await pg_client.create(User, telegram_id=1, full_name="B")


async def test_big_telegram_ids_fit(pg_client):
    user = await pg_client.create(User, telegram_id=-1002233445566, full_name="Group-like id")
    assert (await pg_client.get_one(User, id=user.id)).telegram_id == -1002233445566


def test_table_names_come_from_enum():
    models = (User, Project, Membership, Invite, Question, DocUpdate, GroupChat, ProjectBan)
    assert User._meta.db_table == TableName.USERS
    assert {m._meta.db_table for m in models} == set(TableName)


def test_redis_key_build():
    assert RedisKey.RATE_LIMIT.build(scope="question", subject_id=5) == "ratelimit:question:5"
