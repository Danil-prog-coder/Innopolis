from datetime import UTC, datetime, timedelta

import pytest

from app.core.enums import MemberRole
from app.core.errors import (
    CannotRemoveCreator,
    CannotRemoveSelf,
    InviteExpired,
    InviteNotFound,
    InviteRevoked,
    NotProjectAdmin,
    NotProjectMember,
)
from app.services.invite_service import InviteService

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


async def test_create_and_redeem(invite_service, project, admin, outsider):
    invite = await invite_service.create_invite(admin, project.id, 7)
    current = await invite_service.redeem(outsider, invite.code)
    assert current.project.id == project.id
    assert current.role == MemberRole.USER
    assert outsider.current_project_id == project.id


async def test_redeem_keeps_admin_role(invite_service, project, admin):
    invite = await invite_service.create_invite(admin, project.id, 7)
    assert (await invite_service.redeem(admin, invite.code)).role == MemberRole.ADMIN


async def test_user_cannot_create_invite(invite_service, project, user):
    with pytest.raises(NotProjectAdmin):
        await invite_service.create_invite(user, project.id, 7)


async def test_invalid_ttl_rejected(invite_service, project, admin):
    with pytest.raises(ValueError):
        await invite_service.create_invite(admin, project.id, 365)


async def test_redeem_expired_at_boundary(make_invite_service, project, admin, outsider):
    invite = await make_invite_service(lambda: T0).create_invite(admin, project.id, 1)
    just_before = make_invite_service(lambda: T0 + timedelta(days=1) - timedelta(seconds=1))
    await just_before.get_valid_invite(invite.code)
    at_expiry = make_invite_service(lambda: T0 + timedelta(days=1))
    with pytest.raises(InviteExpired):
        await at_expiry.redeem(outsider, invite.code)


async def test_redeem_revoked_and_unknown(invite_service, project, admin, outsider):
    invite = await invite_service.create_invite(admin, project.id, 7)
    assert await invite_service.revoke(admin, project.id, invite.id) is True
    with pytest.raises(InviteRevoked):
        await invite_service.redeem(outsider, invite.code)
    with pytest.raises(InviteNotFound):
        await invite_service.redeem(outsider, "unknown-code-123")


async def test_list_active(invite_service, project, admin):
    first = await invite_service.create_invite(admin, project.id, 1)
    second = await invite_service.create_invite(admin, project.id, 30)
    await invite_service.revoke(admin, project.id, first.id)
    assert [i.id for i in await invite_service.list_active(admin, project.id)] == [second.id]


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("https://t.me/my_bot?start=AbC_def-12345", "AbC_def-12345"),
        ("t.me/my_bot?start=AbC_def-12345", "AbC_def-12345"),
        ("Вот ссылка: https://t.me/my_bot?start=AbC_def-12345 заходи", "AbC_def-12345"),
        ("  AbC_def-12345 ", "AbC_def-12345"),
        ("привет", None),
        ("short", None),
        ("https://evil.com/?start=AbC_def-12345", None),
        ("", None),
    ],
)
def test_extract_code(text, code):
    assert InviteService.extract_code(text) == code


async def test_removed_admin_cannot_create_invite(
    invite_service, membership_service, project, admin, second_admin
):
    await membership_service.remove_member(admin, project.id, second_admin.id)
    with pytest.raises(NotProjectAdmin):
        await invite_service.create_invite(second_admin, project.id, 7)


async def test_cannot_remove_creator_or_self(membership_service, project, admin, second_admin):
    with pytest.raises(CannotRemoveCreator):
        await membership_service.remove_member(second_admin, project.id, admin.id)
    with pytest.raises(CannotRemoveSelf):
        await membership_service.remove_member(second_admin, project.id, second_admin.id)


async def test_remove_non_member(membership_service, project, admin, outsider):
    with pytest.raises(NotProjectMember):
        await membership_service.remove_member(admin, project.id, outsider.id)


async def test_user_cannot_remove(membership_service, project, user, second_admin):
    with pytest.raises(NotProjectAdmin):
        await membership_service.remove_member(user, project.id, second_admin.id)


async def test_removed_user_loses_current_project(
    membership_service, project_service, user_work, project, admin, user
):
    await project_service.switch_project(user, project.id)
    removed = await membership_service.remove_member(admin, project.id, user.id)
    assert removed.id == user.id
    fresh = await user_work.get_by_id(user.id)
    assert await project_service.get_current(fresh) is None


async def test_get_current_detects_stale_membership(
    project_service, membership_work, project, user
):
    await project_service.switch_project(user, project.id)
    await membership_work.remove(user.id, project.id)  # удалили в обход сервиса
    assert await project_service.get_current(user) is None
    assert user.current_project_id is None


async def test_list_members_paginates_and_clamps(
    membership_service, membership_work, user_work, project, admin
):
    for i in range(12):
        extra = await user_work.upsert_from_telegram(5000 + i, None, f"Extra {i}")
        await membership_work.add_or_upgrade(extra.id, project.id, MemberRole.USER)
    first = await membership_service.list_members(admin, project.id, 0)
    assert (first.total, first.pages, len(first.items)) == (15, 2, 10)
    last = await membership_service.list_members(admin, project.id, 99)
    assert (last.page, len(last.items)) == (1, 5)
    assert (await membership_service.list_members(admin, project.id, -3)).page == 0
