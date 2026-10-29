import pytest

from app.core.enums import MemberRole
from app.core.errors import (
    InvalidPassword,
    InvalidProjectName,
    NotProjectAdmin,
    NotProjectMember,
    TooManyAttempts,
)


@pytest.fixture
async def created(project_service, outsider):
    """Проект, созданный через сервис (с настоящим паролем). Создатель — outsider."""
    project = await project_service.create_project(outsider, "Гамма")
    password = await project_service.reveal_password(outsider, project.id)
    return project, password


async def test_create_project_makes_creator_admin_and_current(
    project_service, password_manager, user
):
    project = await project_service.create_project(user, "  Альфа   проект ")
    current = await project_service.get_current(user)
    assert project.name == "Альфа проект"
    assert current.role == MemberRole.ADMIN
    assert current.project.id == project.id
    password = await project_service.reveal_password(user, project.id)
    assert await password_manager.verify(project.password_hash, password)


@pytest.mark.parametrize("name", ["", "   ", "x" * 65])
async def test_create_project_invalid_name(project_service, user, name):
    with pytest.raises(InvalidProjectName):
        await project_service.create_project(user, name)


async def test_reveal_password_only_creator(project_service, created, admin):
    project, _ = created
    with pytest.raises(NotProjectAdmin):
        await project_service.reveal_password(admin, project.id)


async def test_join_as_admin_with_password(project_service, created, user):
    project, password = created
    current = await project_service.join_as_admin(user, f"  {password} ")
    assert current.project.id == project.id
    assert current.role == MemberRole.ADMIN
    assert (await project_service.get_current(user)).project.id == project.id


async def test_join_as_admin_wrong_password(project_service, created, user):
    project, password = created
    with pytest.raises(InvalidPassword):
        await project_service.join_as_admin(
            user, password[:-1] + ("A" if password[-1] != "A" else "B")
        )
    with pytest.raises(InvalidPassword):
        await project_service.join_as_admin(user, "garbage")


async def test_join_as_admin_bruteforce_limited(project_service, created, user):
    _, password = created
    for _ in range(5):
        with pytest.raises(InvalidPassword):
            await project_service.join_as_admin(user, "deadbeef-wrong")
    with pytest.raises(TooManyAttempts):
        await project_service.join_as_admin(user, password)  # даже верный пароль после лимита


async def test_successful_join_resets_attempts(project_service, created, user):
    _, password = created
    for _ in range(4):
        with pytest.raises(InvalidPassword):
            await project_service.join_as_admin(user, "deadbeef-wrong")
    await project_service.join_as_admin(user, password)
    for _ in range(5):
        with pytest.raises(InvalidPassword):
            await project_service.join_as_admin(user, "deadbeef-wrong")


async def test_check_password_requires_admin_and_same_project(project_service, created, user):
    project, password = created
    with pytest.raises(NotProjectAdmin):
        await project_service.check_password(user, project.id, password)


async def test_check_password_rejects_password_of_other_project(
    project_service, created, project, admin
):
    _, other_password = created
    with pytest.raises(InvalidPassword):
        await project_service.check_password(admin, project.id, other_password)


async def test_check_password_ok(project_service, created, outsider):
    project, password = created
    assert await project_service.check_password(outsider, project.id, password) is None


async def test_switch_to_foreign_project_rejected(project_service, user, other_project):
    with pytest.raises(NotProjectMember):
        await project_service.switch_project(user, other_project.id)


async def test_switch_and_list(project_service, user, project):
    current = await project_service.switch_project(user, project.id)
    assert current.role == MemberRole.USER
    assert [m.project.id for m in await project_service.list_user_projects(user)] == [project.id]


async def test_get_current_without_project(project_service, user):
    assert await project_service.get_current(user) is None
