from aiogram.types import (
    CopyTextButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot import texts
from app.bot.callbacks import (
    AdminStartAction,
    AdminStartCb,
    AnswerQuestionCb,
    ChangesCb,
    DocsCancelCb,
    DocsDoneCb,
    InviteTtlCb,
    MembersPageCb,
    MenuAction,
    MenuCb,
    PasswordRevealCb,
    RemoveMemberCb,
    RevokeInviteCb,
    RoleCb,
    SaveKnowledgeCb,
    SwitchProjectCb,
)
from app.core.enums import MemberRole
from app.services.access_service import CurrentProject
from app.services.invite_service import INVITE_TTL_DAYS
from app.services.membership_service import MembersPage
from app.storage.postgres.models import Invite, Membership


def role_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_ROLE_ADMIN, callback_data=RoleCb(role=MemberRole.ADMIN))
    builder.button(text=texts.BTN_ROLE_USER, callback_data=RoleCb(role=MemberRole.USER))
    return builder.adjust(2).as_markup()


def admin_start_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BTN_CREATE_PROJECT, callback_data=AdminStartCb(action=AdminStartAction.CREATE)
    )
    builder.button(
        text=texts.BTN_JOIN_AS_ADMIN, callback_data=AdminStartCb(action=AdminStartAction.JOIN)
    )
    return builder.adjust(1).as_markup()


def project_reply_kb(current: CurrentProject) -> ReplyKeyboardMarkup:
    button = KeyboardButton(text=texts.project_button(current.project.name, current.role))
    return ReplyKeyboardMarkup(keyboard=[[button]], resize_keyboard=True, is_persistent=True)


def main_menu_kb(current: CurrentProject) -> InlineKeyboardMarkup:
    project_id = current.project.id
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BTN_INSTRUCTIONS,
        callback_data=MenuCb(action=MenuAction.INSTRUCTIONS, project_id=project_id),
    )
    builder.button(
        text=texts.BTN_JOIN_OTHER,
        callback_data=MenuCb(action=MenuAction.JOIN_OTHER, project_id=project_id),
    )
    sizes = [1, 1]
    if current.role == MemberRole.ADMIN:
        for text, action in (
            (texts.BTN_DOCS_APPEND, MenuAction.DOCS_APPEND),
            (texts.BTN_DOCS_REPLACE, MenuAction.DOCS_REPLACE),
            (texts.BTN_INVITE, MenuAction.INVITE),
            (texts.BTN_INVITES, MenuAction.INVITES),
            (texts.BTN_MEMBERS, MenuAction.MEMBERS),
        ):
            builder.button(text=text, callback_data=MenuCb(action=action, project_id=project_id))
        sizes += [2, 2, 1]
    return builder.adjust(*sizes).as_markup()


def add_to_group_kb(bot_username: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_ADD_TO_GROUP, url=texts.add_to_group_url(bot_username))
    return builder.as_markup()


def password_reveal_kb(project_id: int, password: str) -> InlineKeyboardMarkup:
    """Показать пароль во всплывающем окне или скопировать в буфер обмена (CopyTextButton).
    Ни то ни другое не оставляет пароль текстом в переписке."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BTN_SHOW_PASSWORD, callback_data=PasswordRevealCb(project_id=project_id)
    )
    builder.button(text=texts.BTN_COPY_PASSWORD, copy_text=CopyTextButton(text=password))
    return builder.adjust(1).as_markup()


def projects_kb(
    memberships: list[Membership], current_project_id: int | None
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for membership in memberships:
        builder.button(
            text=texts.project_list_button(
                membership.project.name,
                membership.role,
                membership.project_id == current_project_id,
            ),
            callback_data=SwitchProjectCb(project_id=membership.project_id),
        )
    return builder.adjust(1).as_markup()


def invite_ttl_kb(project_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for days in INVITE_TTL_DAYS:
        builder.button(
            text=texts.ttl_button(days), callback_data=InviteTtlCb(project_id=project_id, days=days)
        )
    return builder.adjust(len(INVITE_TTL_DAYS)).as_markup()


def active_invites_kb(project_id: int, invites: list[Invite]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for invite in invites:
        builder.button(
            text=f"{texts.BTN_REVOKE} {invite.code[:8]}…",
            callback_data=RevokeInviteCb(project_id=project_id, invite_id=invite.id),
        )
    return builder.adjust(1).as_markup()


def members_kb(project_id: int, page: MembersPage) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for membership in page.items:
        builder.button(
            text=texts.member_button(
                membership.user.full_name, membership.user.username, membership.role
            ),
            callback_data=RemoveMemberCb(project_id=project_id, user_id=membership.user_id),
        )
    nav = 0
    if page.page > 0:
        builder.button(
            text=texts.BTN_PREV,
            callback_data=MembersPageCb(project_id=project_id, page=page.page - 1),
        )
        nav += 1
    if page.page < page.pages - 1:
        builder.button(
            text=texts.BTN_NEXT,
            callback_data=MembersPageCb(project_id=project_id, page=page.page + 1),
        )
        nav += 1
    sizes = [1] * len(page.items) + ([nav] if nav else [])
    return builder.adjust(*sizes).as_markup()


def confirm_remove_kb(project_id: int, user_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BTN_CONFIRM_REMOVE,
        callback_data=RemoveMemberCb(project_id=project_id, user_id=user_id, confirm=True),
    )
    builder.button(
        text=texts.BTN_CANCEL, callback_data=MembersPageCb(project_id=project_id, page=0)
    )
    return builder.adjust(2).as_markup()


def docs_upload_kb(project_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_DONE, callback_data=DocsDoneCb(project_id=project_id))
    builder.button(text=texts.BTN_CANCEL, callback_data=DocsCancelCb(project_id=project_id))
    return builder.adjust(2).as_markup()


def changes_kb(update_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_CHANGES, callback_data=ChangesCb(update_id=update_id))
    return builder.as_markup()


def unanswered_question_kb(question_id: int, project_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_ANSWER, callback_data=AnswerQuestionCb(question_id=question_id))
    builder.button(
        text=texts.BTN_DOCS_APPEND,
        callback_data=MenuCb(action=MenuAction.DOCS_APPEND, project_id=project_id),
    )
    return builder.adjust(1).as_markup()


def save_knowledge_kb(question_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BTN_SAVE_KNOWLEDGE, callback_data=SaveKnowledgeCb(question_id=question_id)
    )
    return builder.as_markup()
