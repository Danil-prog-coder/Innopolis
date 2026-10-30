from tortoise.exceptions import IntegrityError
from tortoise.expressions import F

from app.storage.postgres.client import PostgresClient
from app.storage.postgres.models import Project


class PostgresProjectWork:
    def __init__(self, client: PostgresClient) -> None:
        self._client = client

    async def create(
        self, name: str, public_id: str, password_hash: str, creator_id: int
    ) -> Project | None:
        """Создать проект. None — если ``public_id`` уже занят."""
        try:
            return await self._client.create(
                Project,
                name=name,
                public_id=public_id,
                password_hash=password_hash,
                creator_id=creator_id,
            )
        except IntegrityError:
            if await self.get_by_public_id(public_id) is not None:
                return None
            raise

    async def get_by_id(self, project_id: int) -> Project | None:
        return await self._client.get_one(Project, id=project_id)

    async def get_many_by_ids(self, project_ids: list[int]) -> list[Project]:
        if not project_ids:
            return []
        return await self._client.get_many(Project, id__in=project_ids, order_by=("id",))

    async def get_by_public_id(self, public_id: str) -> Project | None:
        return await self._client.get_one(Project, public_id=public_id)

    async def bump_docs_version(self, project_id: int) -> int:
        """Атомарно увеличить версию документации, вернуть новое значение."""
        await self._client.update(Project, {"id": project_id}, docs_version=F("docs_version") + 1)
        project = await self._client.get_one(Project, id=project_id)
        if project is None:
            raise LookupError(f"Project {project_id} not found")
        return project.docs_version
