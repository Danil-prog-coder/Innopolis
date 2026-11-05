from tortoise import fields
from tortoise.models import Model

from app.core.enums import DocsMode, MemberRole, QuestionStatus
from app.llm.gateway import CostStatus, UsageStatus
from app.storage.enums import TableName


class User(Model):
    id = fields.IntField(primary_key=True)
    telegram_id = fields.BigIntField(unique=True)
    username = fields.CharField(max_length=64, null=True)
    full_name = fields.CharField(max_length=256)
    # Без FK: внешний ключ на projects дал бы цикл users <-> projects (projects.creator).
    # Актуальность значения проверяет ProjectService.get_current по членству.
    current_project_id = fields.IntField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = TableName.USERS


class Project(Model):
    id = fields.IntField(primary_key=True)
    public_id = fields.CharField(max_length=8, unique=True)
    name = fields.CharField(max_length=64)
    password_hash = fields.CharField(max_length=256)
    creator: fields.ForeignKeyRelation[User] = fields.ForeignKeyField(
        "models.User", related_name="created_projects", on_delete=fields.RESTRICT
    )
    docs_version = fields.IntField(default=0)
    # PG — единственный authority опубликованного snapshot. NULL — публикации
    # ещё нет; real-mode отвечает fail-closed, а не compat-заглушкой.
    active_generation_id = fields.CharField(max_length=128, null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    creator_id: int

    class Meta:
        table = TableName.PROJECTS


class Membership(Model):
    id = fields.IntField(primary_key=True)
    user: fields.ForeignKeyRelation[User] = fields.ForeignKeyField(
        "models.User", related_name="memberships", on_delete=fields.CASCADE
    )
    project: fields.ForeignKeyRelation[Project] = fields.ForeignKeyField(
        "models.Project", related_name="memberships", on_delete=fields.CASCADE
    )
    role = fields.CharEnumField(MemberRole, max_length=16)
    created_at = fields.DatetimeField(auto_now_add=True)

    user_id: int
    project_id: int

    class Meta:
        table = TableName.MEMBERSHIPS
        unique_together = (("user", "project"),)
        indexes = (("project", "role"),)


class Invite(Model):
    id = fields.IntField(primary_key=True)
    code = fields.CharField(max_length=64, unique=True)
    project: fields.ForeignKeyRelation[Project] = fields.ForeignKeyField(
        "models.Project", related_name="invites", on_delete=fields.CASCADE
    )
    created_by: fields.ForeignKeyRelation[User] = fields.ForeignKeyField(
        "models.User", related_name=False, on_delete=fields.CASCADE
    )
    expires_at = fields.DatetimeField()
    revoked_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    project_id: int
    created_by_id: int

    class Meta:
        table = TableName.INVITES
        indexes = (("project", "expires_at"),)


class Question(Model):
    id = fields.IntField(primary_key=True)
    project: fields.ForeignKeyRelation[Project] = fields.ForeignKeyField(
        "models.Project", related_name="questions", on_delete=fields.CASCADE
    )
    asker_telegram_id = fields.BigIntField()
    chat_id = fields.BigIntField()
    message_id = fields.BigIntField()
    text = fields.TextField()
    image_file_id = fields.CharField(max_length=256, null=True)
    status = fields.CharEnumField(QuestionStatus, max_length=16, default=QuestionStatus.PENDING)
    answered_by: fields.ForeignKeyNullableRelation[User] = fields.ForeignKeyField(
        "models.User", related_name=False, null=True, on_delete=fields.SET_NULL
    )
    answer = fields.TextField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    answered_at = fields.DatetimeField(null=True)

    project_id: int
    answered_by_id: int | None

    class Meta:
        table = TableName.QUESTIONS
        indexes = (("project", "status"),)


class DocUpdate(Model):
    id = fields.IntField(primary_key=True)
    project: fields.ForeignKeyRelation[Project] = fields.ForeignKeyField(
        "models.Project", related_name="doc_updates", on_delete=fields.CASCADE
    )
    created_by: fields.ForeignKeyRelation[User] = fields.ForeignKeyField(
        "models.User", related_name=False, on_delete=fields.CASCADE
    )
    mode = fields.CharEnumField(DocsMode, max_length=16)
    summary = fields.TextField()
    created_at = fields.DatetimeField(auto_now_add=True)

    project_id: int
    created_by_id: int

    class Meta:
        table = TableName.DOC_UPDATES


class ProjectBan(Model):
    """Пользователь удалён из проекта: старые приглашения и пароль ему больше не помогут."""

    id = fields.IntField(primary_key=True)
    user: fields.ForeignKeyRelation[User] = fields.ForeignKeyField(
        "models.User", related_name=False, on_delete=fields.CASCADE
    )
    project: fields.ForeignKeyRelation[Project] = fields.ForeignKeyField(
        "models.Project", related_name=False, on_delete=fields.CASCADE
    )
    # Наибольший id приглашения на момент удаления: вернуть могут только приглашения
    # с бо́льшим id. id монотонен, в отличие от времени (у часов бывает шаг ~15 мс).
    last_invite_id = fields.IntField()
    created_at = fields.DatetimeField(auto_now_add=True)

    user_id: int
    project_id: int

    class Meta:
        table = TableName.PROJECT_BANS
        unique_together = (("user", "project"),)


class GroupChat(Model):
    id = fields.IntField(primary_key=True)
    chat_id = fields.BigIntField(unique=True)
    project: fields.ForeignKeyRelation[Project] = fields.ForeignKeyField(
        "models.Project", related_name="group_chats", on_delete=fields.CASCADE
    )
    bound_by_telegram_id = fields.BigIntField()
    created_at = fields.DatetimeField(auto_now_add=True)

    project_id: int

    class Meta:
        table = TableName.GROUP_CHATS


class KnowledgeGeneration(Model):
    """Опубликованная generation. Строки не удаляются при замене active."""

    id = fields.IntField(primary_key=True)
    project: fields.ForeignKeyRelation[Project] = fields.ForeignKeyField(
        "models.Project", related_name="knowledge_generations", on_delete=fields.CASCADE
    )
    generation_id = fields.CharField(max_length=128)
    knowledge_version = fields.IntField()
    manifest_hash = fields.CharField(max_length=64)
    mode = fields.CharEnumField(DocsMode, max_length=16)
    base_generation_id = fields.CharField(max_length=128, null=True)
    created_by: fields.ForeignKeyNullableRelation[User] = fields.ForeignKeyField(
        "models.User", related_name=False, null=True, on_delete=fields.SET_NULL
    )
    published_at = fields.DatetimeField()
    created_at = fields.DatetimeField(auto_now_add=True)

    project_id: int
    created_by_id: int | None

    class Meta:
        table = TableName.KNOWLEDGE_GENERATIONS
        unique_together = (("project", "generation_id"),)
        indexes = (("project", "knowledge_version"),)


class LlmAttempt(Model):
    """Постоянный учёт одной оплаченной попытки; не заменяет ledger INN-12."""

    id = fields.IntField(primary_key=True)
    project_id = fields.IntField()
    request_id = fields.CharField(max_length=128)
    attempt_id = fields.CharField(max_length=128, unique=True)
    outcome = fields.CharField(max_length=32)
    error_code = fields.CharField(max_length=32, null=True)
    usage_status = fields.CharEnumField(UsageStatus, max_length=16)
    input_tokens = fields.IntField(null=True)
    output_tokens = fields.IntField(null=True)
    cached_input_tokens = fields.IntField(null=True)
    reasoning_output_tokens = fields.IntField(null=True)
    cost = fields.DecimalField(max_digits=18, decimal_places=8, null=True)
    cost_status = fields.CharEnumField(CostStatus, max_length=16, null=True)
    currency = fields.CharField(max_length=3, null=True)
    rate_version = fields.CharField(max_length=128, null=True)
    priced_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = TableName.LLM_ATTEMPTS
        indexes = (("project_id", "created_at"), ("request_id",))
