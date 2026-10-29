from enum import StrEnum


class TableName(StrEnum):
    USERS = "users"
    PROJECTS = "projects"
    MEMBERSHIPS = "memberships"
    INVITES = "invites"
    QUESTIONS = "questions"
    DOC_UPDATES = "doc_updates"
    GROUP_CHATS = "group_chats"
    PROJECT_BANS = "project_bans"


class RedisKey(StrEnum):
    ANSWER_CACHE = "cache:answer:{project_id}:{docs_version}:{question_hash}"
    RATE_LIMIT = "ratelimit:{scope}:{subject_id}"
    PASSWORD_REVEAL = "password_reveal:{project_id}"
    DOCS_DRAFT = "docs_draft:{telegram_id}:{project_id}"
    DOCS_UPLOAD_LOCK = "docs_upload_lock:{telegram_id}:{project_id}"
    FSM_PREFIX = "fsm"

    def build(self, **parts: object) -> str:
        return self.value.format(**parts)
