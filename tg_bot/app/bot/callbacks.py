"""Callback-данные кнопок. Префиксы короткие: Telegram ограничивает callback_data 64 байтами.

Каждая кнопка, действующая на проект, несёт project_id, а сервис заново проверяет права:
устаревшая кнопка после переключения проекта или удаления из него ничего не сломает.
"""

from enum import StrEnum

from aiogram.filters.callback_data import CallbackData

from app.core.enums import MemberRole


class MenuAction(StrEnum):
    INSTRUCTIONS = "instr"
    JOIN_OTHER = "other"
    DOCS_APPEND = "dapp"
    DOCS_REPLACE = "drepl"
    INVITE = "inv"
    INVITES = "invs"
    MEMBERS = "mem"


class AdminStartAction(StrEnum):
    CREATE = "create"
    JOIN = "join"


class RoleCb(CallbackData, prefix="role"):
    role: MemberRole


class AdminStartCb(CallbackData, prefix="adm"):
    action: AdminStartAction


class MenuCb(CallbackData, prefix="menu"):
    action: MenuAction
    project_id: int = 0


class PasswordRevealCb(CallbackData, prefix="pwreveal"):
    project_id: int


class SwitchProjectCb(CallbackData, prefix="sw"):
    project_id: int


class InviteTtlCb(CallbackData, prefix="ittl"):
    project_id: int
    days: int


class RevokeInviteCb(CallbackData, prefix="irev"):
    project_id: int
    invite_id: int


class MembersPageCb(CallbackData, prefix="mpg"):
    project_id: int
    page: int


class RemoveMemberCb(CallbackData, prefix="mrm"):
    project_id: int
    user_id: int
    confirm: bool = False


class AnswerQuestionCb(CallbackData, prefix="ans"):
    question_id: int


class SaveKnowledgeCb(CallbackData, prefix="savekn"):
    question_id: int


class ChangesCb(CallbackData, prefix="chg"):
    update_id: int


class DocsDoneCb(CallbackData, prefix="ddone"):
    project_id: int


class DocsCancelCb(CallbackData, prefix="dcancel"):
    project_id: int
