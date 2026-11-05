"""In-memory stop signals for active Telegram draft generations.

Stops are authorized by chat, thread and actor context, not by a bare
31-bit draft id. Draft ids are allocated uniquely among active generations and
are not immediately reused, so a stale stop cannot hit a newer draft.
"""

import asyncio
import secrets
from dataclasses import dataclass, field

_DRAFT_ID_MAX = 0x7FFFFFFF


@dataclass(frozen=True, slots=True)
class DraftRegistration:
    chat_id: int
    message_thread_id: int | None
    actor_user_id: int
    draft_id: int
    event: asyncio.Event = field(compare=False, repr=False)


class DraftAlreadyActive(RuntimeError):
    """One active generation per chat/thread; the caller must wait or stop it."""

    def __init__(self, registration: "DraftRegistration") -> None:
        super().__init__("a draft is already active in this chat/thread")
        self.registration = registration


class ActiveDraftRegistry:
    def __init__(self, *, start_draft_id: int | None = None) -> None:
        if start_draft_id is None:
            # Reduce stale-stop matches across process restarts.
            start_draft_id = secrets.randbelow(_DRAFT_ID_MAX) + 1
        if not 1 <= start_draft_id <= _DRAFT_ID_MAX:
            raise ValueError("start_draft_id out of range")
        self._next_draft_id = start_draft_id
        self._active: dict[int, DraftRegistration] = {}
        self._by_context: dict[tuple[int, int | None], DraftRegistration] = {}

    def register(
        self,
        *,
        chat_id: int,
        message_thread_id: int | None,
        actor_user_id: int,
    ) -> DraftRegistration:
        context = (chat_id, message_thread_id)
        existing = self._by_context.get(context)
        if existing is not None:
            raise DraftAlreadyActive(existing)
        draft_id = self._allocate_draft_id()
        event = asyncio.Event()
        registration = DraftRegistration(
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            actor_user_id=actor_user_id,
            draft_id=draft_id,
            event=event,
        )
        self._active[draft_id] = registration
        self._by_context[context] = registration
        return registration

    def stop(
        self,
        *,
        chat_id: int,
        message_thread_id: int | None,
        draft_id: int,
        actor_user_id: int,
    ) -> bool:
        registration = self._active.get(draft_id)
        if registration is None:
            return False
        if (
            registration.chat_id != chat_id
            or registration.message_thread_id != message_thread_id
            or registration.actor_user_id != actor_user_id
        ):
            return False
        registration.event.set()
        return True

    def stop_context(
        self,
        *,
        chat_id: int,
        message_thread_id: int | None,
        actor_user_id: int,
    ) -> bool:
        """Stop only the active draft in this chat/thread for this actor."""
        registration = self._by_context.get((chat_id, message_thread_id))
        if registration is None or registration.actor_user_id != actor_user_id:
            return False
        registration.event.set()
        return True

    def unregister(self, registration: DraftRegistration) -> None:
        if self._active.get(registration.draft_id) is not registration:
            return
        self._active.pop(registration.draft_id, None)
        context = (registration.chat_id, registration.message_thread_id)
        if self._by_context.get(context) is registration:
            self._by_context.pop(context, None)

    def _allocate_draft_id(self) -> int:
        candidate = self._next_draft_id
        while candidate in self._active:
            candidate = candidate % _DRAFT_ID_MAX + 1
        self._next_draft_id = candidate % _DRAFT_ID_MAX + 1
        return candidate
