from datetime import datetime

from app.core.enums import QuestionStatus
from app.storage.postgres.client import PostgresClient
from app.storage.postgres.models import Question


class PostgresQuestionWork:
    def __init__(self, client: PostgresClient) -> None:
        self._client = client

    async def create(
        self,
        project_id: int,
        asker_telegram_id: int,
        chat_id: int,
        message_id: int,
        text: str,
        image_file_id: str | None,
    ) -> Question:
        return await self._client.create(
            Question,
            project_id=project_id,
            asker_telegram_id=asker_telegram_id,
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            image_file_id=image_file_id,
        )

    async def get_by_id(self, question_id: int) -> Question | None:
        return await self._client.get_one(Question, id=question_id)

    async def get_created_between(self, since: datetime, until: datetime) -> list[Question]:
        """Вопросы, заданные в [since, until), новые первыми."""
        return await self._client.get_many(
            Question,
            created_at__gte=since,
            created_at__lt=until,
            order_by=("-created_at", "-id"),
        )

    async def mark_answered(
        self, question_id: int, admin_user_id: int, answer: str, now: datetime
    ) -> bool:
        """Отметить вопрос отвеченным. True только у того, кто успел первым."""
        updated = await self._client.update(
            Question,
            {"id": question_id, "status": QuestionStatus.PENDING},
            status=QuestionStatus.ANSWERED,
            answered_by_id=admin_user_id,
            answer=answer,
            answered_at=now,
        )
        return updated == 1
