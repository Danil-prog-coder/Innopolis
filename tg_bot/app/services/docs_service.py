from app.core.enums import DocsMode
from app.core.errors import DocUpdateNotFound, FileTooLarge, NoFilesToUpload
from app.llm.gateway import DocFile, LLMGateway
from app.rag.ingestion import RagIngestionService
from app.services.access_service import AccessGuard
from app.storage.postgres.doc_update_work import PostgresDocUpdateWork
from app.storage.postgres.membership_work import PostgresMembershipWork
from app.storage.postgres.models import DocUpdate, User
from app.storage.postgres.project_work import PostgresProjectWork


class DocsService:
    # Bot API не даёт боту скачать файл больше 20 МБ
    TELEGRAM_DOWNLOAD_LIMIT = 20 * 1024 * 1024

    def __init__(
        self,
        llm_gateway: LLMGateway,
        project_work: PostgresProjectWork,
        doc_update_work: PostgresDocUpdateWork,
        membership_work: PostgresMembershipWork,
        access_guard: AccessGuard,
        knowledge_publisher: RagIngestionService | None = None,
    ) -> None:
        self._llm = llm_gateway
        self._project_work = project_work
        self._doc_update_work = doc_update_work
        self._membership_work = membership_work
        self._access = access_guard
        # Real mode uses verified candidate -> PG CAS publication. The legacy
        # fake/stub path stays explicit and never claims a published generation.
        self._knowledge_publisher = knowledge_publisher

    @classmethod
    def check_file_size(cls, size: int | None) -> None:
        if size is None or size > cls.TELEGRAM_DOWNLOAD_LIMIT:
            raise FileTooLarge

    async def require_admin(self, admin: User, project_id: int) -> None:
        await self._access.require_admin(admin.id, project_id)

    async def apply_update(
        self, admin: User, project_id: int, mode: DocsMode, files: list[DocFile]
    ) -> DocUpdate:
        """Передать файлы ИИ-части и зафиксировать обновление.

        Если ИИ-часть упала, версия документации не меняется: кеш ответов остаётся верным.
        """
        await self._access.require_admin(admin.id, project_id)
        if not files:
            raise NoFilesToUpload
        if self._knowledge_publisher is not None:
            publication = await self._knowledge_publisher.ingest_and_publish(
                project_id,
                mode,
                files,
                created_by_id=admin.id,
            )
            return publication.doc_update
        summary = await self._llm.upload_documents(project_id, mode, files)
        await self._project_work.bump_docs_version(project_id)
        return await self._doc_update_work.create(project_id, admin.id, mode, summary)

    async def get_summary(self, user: User, update_id: int) -> str:
        update = await self._doc_update_work.get_by_id(update_id)
        if update is None:
            raise DocUpdateNotFound
        await self._access.require_member(user.id, update.project_id)
        return update.summary

    async def get_member_chat_ids(self, project_id: int) -> list[int]:
        return await self._membership_work.get_member_telegram_ids(project_id)
