from enum import StrEnum


class MemberRole(StrEnum):
    ADMIN = "admin"
    USER = "user"


class QuestionStatus(StrEnum):
    PENDING = "pending"
    ANSWERED = "answered"


class DocsMode(StrEnum):
    APPEND = "append"
    REPLACE = "replace"


class RateLimitScope(StrEnum):
    QUESTION = "question"
    GROUP_QUESTION = "group_question"
    PASSWORD = "password"
