import asyncio

import pytest

from app.services.active_draft_registry import ActiveDraftRegistry, DraftAlreadyActive


def test_two_chats_get_distinct_draft_ids_and_cannot_cross_stop():
    registry = ActiveDraftRegistry(start_draft_id=10)
    first = registry.register(chat_id=1, message_thread_id=None, actor_user_id=1)
    second = registry.register(chat_id=2, message_thread_id=None, actor_user_id=2)

    assert first.draft_id != second.draft_id
    assert (
        registry.stop(
            chat_id=2,
            message_thread_id=None,
            draft_id=first.draft_id,
            actor_user_id=2,
        )
        is False
    )
    assert not first.event.is_set()
    assert (
        registry.stop(
            chat_id=1,
            message_thread_id=None,
            draft_id=first.draft_id,
            actor_user_id=1,
        )
        is True
    )
    assert first.event.is_set()


def test_thread_and_actor_are_part_of_stop_authorization():
    registry = ActiveDraftRegistry()
    registration = registry.register(chat_id=10, message_thread_id=20, actor_user_id=30)

    assert (
        registry.stop(
            chat_id=10,
            message_thread_id=21,
            draft_id=registration.draft_id,
            actor_user_id=30,
        )
        is False
    )
    assert (
        registry.stop(
            chat_id=10,
            message_thread_id=20,
            draft_id=registration.draft_id,
            actor_user_id=31,
        )
        is False
    )
    assert not registration.event.is_set()
    assert (
        registry.stop(
            chat_id=10,
            message_thread_id=20,
            draft_id=registration.draft_id,
            actor_user_id=30,
        )
        is True
    )


def test_allocator_does_not_overwrite_an_active_draft_id():
    registry = ActiveDraftRegistry(start_draft_id=1)
    first = registry.register(chat_id=1, message_thread_id=None, actor_user_id=1)
    assert first.draft_id == 1

    # Simulate a wrap/collision candidate; allocation must skip active ids.
    registry._next_draft_id = 1
    second = registry.register(chat_id=2, message_thread_id=None, actor_user_id=2)
    assert second.draft_id == 2
    assert not first.event.is_set()


def test_stop_context_is_scoped_to_actor_and_thread():
    registry = ActiveDraftRegistry()
    first = registry.register(chat_id=1, message_thread_id=None, actor_user_id=1)
    second = registry.register(chat_id=2, message_thread_id=None, actor_user_id=2)

    assert (
        registry.stop_context(
            chat_id=1,
            message_thread_id=None,
            actor_user_id=2,
        )
        is False
    )
    assert not first.event.is_set()
    assert not second.event.is_set()
    assert (
        registry.stop_context(
            chat_id=1,
            message_thread_id=None,
            actor_user_id=1,
        )
        is True
    )
    assert first.event.is_set()
    assert not second.event.is_set()


def test_threads_are_independent_and_second_request_same_thread_is_rejected():
    registry = ActiveDraftRegistry(start_draft_id=10)
    first = registry.register(chat_id=1, message_thread_id=10, actor_user_id=1)
    second = registry.register(chat_id=1, message_thread_id=11, actor_user_id=1)

    assert first.draft_id != second.draft_id
    with pytest.raises(DraftAlreadyActive):
        registry.register(chat_id=1, message_thread_id=10, actor_user_id=1)
    assert not first.event.is_set()
    assert not second.event.is_set()

    assert (
        registry.stop_context(
            chat_id=1,
            message_thread_id=10,
            actor_user_id=1,
        )
        is True
    )
    assert first.event.is_set()
    assert not second.event.is_set()
    registry.unregister(first)

    replacement = registry.register(chat_id=1, message_thread_id=10, actor_user_id=1)
    assert replacement.draft_id != first.draft_id


async def test_concurrent_register_same_context_has_one_winner():
    registry = ActiveDraftRegistry()

    async def attempt():
        try:
            return registry.register(
                chat_id=1,
                message_thread_id=None,
                actor_user_id=1,
            )
        except DraftAlreadyActive:
            return None

    results = await asyncio.gather(attempt(), attempt())
    assert sum(result is not None for result in results) == 1


def test_unregister_does_not_remove_another_registration():
    registry = ActiveDraftRegistry()
    first = registry.register(chat_id=1, message_thread_id=None, actor_user_id=1)
    second = registry.register(chat_id=2, message_thread_id=None, actor_user_id=2)

    registry.unregister(first)

    assert (
        registry.stop(
            chat_id=2,
            message_thread_id=None,
            draft_id=second.draft_id,
            actor_user_id=2,
        )
        is True
    )
    assert second.event.is_set()


def test_unregister_makes_stale_stop_a_noop_and_id_is_not_reused_immediately():
    registry = ActiveDraftRegistry(start_draft_id=5)
    first = registry.register(chat_id=1, message_thread_id=None, actor_user_id=1)
    registry.unregister(first)

    assert (
        registry.stop(
            chat_id=1,
            message_thread_id=None,
            draft_id=first.draft_id,
            actor_user_id=1,
        )
        is False
    )
    second = registry.register(chat_id=1, message_thread_id=None, actor_user_id=1)
    assert second.draft_id != first.draft_id
    assert second.draft_id > first.draft_id
