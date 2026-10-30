from dataclasses import dataclass
from datetime import datetime, timedelta

from app.core.enums import MemberRole, QuestionStatus
from app.storage.postgres.membership_work import PostgresMembershipWork
from app.storage.postgres.models import Project, Question
from app.storage.postgres.project_work import PostgresProjectWork
from app.storage.postgres.question_work import PostgresQuestionWork


@dataclass(frozen=True, slots=True)
class ProjectDigest:
    project: Project
    admin_chat_ids: list[int]
    total: int
    pending: int
    latest: list[Question]


class DigestService:
    """Еженедельная сводка админам: вопросы, на которые в документации не нашлось ответа.

    В таблице questions только такие вопросы: ответы ИИ туда не попадают.
    """

    PERIOD = timedelta(days=7)
    LATEST_LIMIT = 5

    def __init__(
        self,
        question_work: PostgresQuestionWork,
        project_work: PostgresProjectWork,
        membership_work: PostgresMembershipWork,
    ) -> None:
        self._question_work = question_work
        self._project_work = project_work
        self._membership_work = membership_work

    async def build_weekly(self, until: datetime) -> list[ProjectDigest]:
        """Сводки за неделю до ``until`` по проектам, где были такие вопросы.

        Вопросы грузятся целиком: на нынешних объёмах (сотни в неделю) этого хватает.
        При тысячах — считать total/pending через GROUP BY в базе.
        """
        questions = await self._question_work.get_created_between(until - self.PERIOD, until)
        by_project: dict[int, list[Question]] = {}
        for question in questions:
            by_project.setdefault(question.project_id, []).append(question)

        digests: list[ProjectDigest] = []
        for project in await self._project_work.get_many_by_ids(list(by_project)):
            admin_ids = await self._membership_work.get_member_telegram_ids(
                project.id, MemberRole.ADMIN
            )
            if not admin_ids:
                continue
            items = by_project[project.id]
            digests.append(
                ProjectDigest(
                    project=project,
                    admin_chat_ids=admin_ids,
                    total=len(items),
                    pending=sum(q.status == QuestionStatus.PENDING for q in items),
                    latest=items[: self.LATEST_LIMIT],
                )
            )
        return digests
