from app.core.enums import MemberRole


async def test_upsert_updates_existing(user_work):
    first = await user_work.upsert_from_telegram(10, "old", "Old")
    second = await user_work.upsert_from_telegram(10, "new", "New")
    assert first.id == second.id
    assert second.username == "new"
    assert (await user_work.get_by_telegram_id(10)).full_name == "New"


async def test_upsert_truncates_long_name(user_work):
    user = await user_work.upsert_from_telegram(11, None, "x" * 500)
    assert len(user.full_name) == 256


async def test_reset_current_project_only_if_matches(user_work, project, other_project, user):
    await user_work.set_current_project(user.id, project.id)
    await user_work.reset_current_project_if(user.id, other_project.id)
    assert (await user_work.get_by_id(user.id)).current_project_id == project.id
    await user_work.reset_current_project_if(user.id, project.id)
    assert (await user_work.get_by_id(user.id)).current_project_id is None


async def test_bump_docs_version_is_incremental(project_work, project):
    assert await project_work.bump_docs_version(project.id) == 1
    assert await project_work.bump_docs_version(project.id) == 2


async def test_get_by_public_id(project_work, project):
    assert (await project_work.get_by_public_id("a1b2c3d4")).id == project.id
    assert await project_work.get_by_public_id("00000000") is None


async def test_add_or_upgrade_never_downgrades_admin(membership_work, project, admin):
    membership = await membership_work.add_or_upgrade(admin.id, project.id, MemberRole.USER)
    assert membership.role == MemberRole.ADMIN
    assert (await membership_work.get(admin.id, project.id)).role == MemberRole.ADMIN


async def test_add_or_upgrade_upgrades_user(membership_work, project, user):
    upgraded = await membership_work.add_or_upgrade(user.id, project.id, MemberRole.ADMIN)
    assert upgraded.role == MemberRole.ADMIN
    assert (await membership_work.get(user.id, project.id)).role == MemberRole.ADMIN


async def test_member_telegram_ids_filtered_by_role(
    membership_work, project, admin, second_admin, user
):
    admins = await membership_work.get_member_telegram_ids(project.id, MemberRole.ADMIN)
    everyone = await membership_work.get_member_telegram_ids(project.id)
    assert sorted(admins) == sorted([admin.telegram_id, second_admin.telegram_id])
    assert sorted(everyone) == sorted(
        [admin.telegram_id, second_admin.telegram_id, user.telegram_id]
    )


async def test_user_memberships_sorted_with_project(membership_work, project_work, project, user):
    zeta = await project_work.create("Яблоко", "11111111", "h", user.id)
    await membership_work.add_or_upgrade(user.id, zeta.id, MemberRole.ADMIN)
    names = [m.project.name for m in await membership_work.get_user_memberships(user.id)]
    assert names == ["Альфа", "Яблоко"]


async def test_project_members_pagination(membership_work, project):
    assert await membership_work.count_project_members(project.id) == 3
    first = await membership_work.get_project_members(project.id, offset=0, limit=2)
    rest = await membership_work.get_project_members(project.id, offset=2, limit=2)
    assert len(first) == 2 and len(rest) == 1
    assert first[0].user.full_name == "Admin"


async def test_remove(membership_work, project, user):
    assert await membership_work.remove(user.id, project.id) is True
    assert await membership_work.get(user.id, project.id) is None
    assert await membership_work.remove(user.id, project.id) is False
