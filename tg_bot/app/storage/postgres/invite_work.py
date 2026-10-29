from datetime import datetime

from app.storage.postgres.client import PostgresClient
from app.storage.postgres.models import Invite


class PostgresInviteWork:
    def __init__(self, client: PostgresClient) -> None:
        self._client = client

    async def create(
        self, code: str, project_id: int, created_by_id: int, expires_at: datetime
    ) -> Invite:
        return await self._client.create(
            Invite,
            code=code,
            project_id=project_id,
            created_by_id=created_by_id,
            expires_at=expires_at,
        )

    async def get_by_code(self, code: str) -> Invite | None:
        return await self._client.get_one(Invite, code=code)

    async def get_active_for_project(self, project_id: int, now: datetime) -> list[Invite]:
        return await self._client.get_many(
            Invite,
            project_id=project_id,
            revoked_at__isnull=True,
            expires_at__gt=now,
            order_by=("created_at", "id"),
        )

    async def get_last_id(self, project_id: int) -> int:
        """Наибольший id приглашения проекта (0, если приглашений не было)."""
        latest = await self._client.get_many(
            Invite, project_id=project_id, order_by=("-id",), limit=1
        )
        return latest[0].id if latest else 0

    async def revoke(self, invite_id: int, project_id: int, now: datetime) -> bool:
        updated = await self._client.update(
            Invite,
            {"id": invite_id, "project_id": project_id, "revoked_at__isnull": True},
            revoked_at=now,
        )
        return updated == 1
