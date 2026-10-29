"""Все тексты бота.

Сообщения уходят с parse_mode=HTML, поэтому пользовательские данные в функциях ниже
экранируются. Текст кнопок и всплывающих окон Telegram не разбирает как HTML —
там экранировать нельзя. Ответы ИИ и админа отправляются без parse_mode (см. split_message).
"""

from html import escape

from app.core.enums import MemberRole

TELEGRAM_MESSAGE_LIMIT = 4096
TELEGRAM_CAPTION_LIMIT = 1024
CAPTION_QUESTION_LEN = 800
_PROJECT_BUTTON_NAME_LEN = 40

# --- Роли ---

ROLE_LABELS = {MemberRole.ADMIN: "админ", MemberRole.USER: "пользователь"}

# --- Кнопки ---

BTN_ROLE_ADMIN = "Админ"
BTN_ROLE_USER = "Пользователь"
BTN_CREATE_PROJECT = "Создать проект"
BTN_JOIN_AS_ADMIN = "Присоединиться к проекту в роли админа"
BTN_INSTRUCTIONS = "Инструкция, как добавить бота в ваш чат"
BTN_JOIN_OTHER = "Присоединиться к другому проекту"
BTN_DOCS_APPEND = "Дополнить документацию"
BTN_DOCS_REPLACE = "Заменить документацию"
BTN_INVITE = "Пригласить участников"
BTN_INVITES = "Активные приглашения"
BTN_MEMBERS = "Участники"
BTN_SHOW_PASSWORD = "🔑 Показать пароль"
BTN_COPY_PASSWORD = "📋 Скопировать пароль"
BTN_CHANGES = "Что изменилось"
BTN_ANSWER = "Ответить"
BTN_SAVE_KNOWLEDGE = "Сохранить в базу знаний"
BTN_DONE = "Готово"
BTN_CANCEL = "Отмена"
BTN_REVOKE = "Отозвать"
BTN_CONFIRM_REMOVE = "Да, удалить"
BTN_PREV = "← Назад"
BTN_NEXT = "Вперёд →"
BTN_ADD_TO_GROUP = "Добавить бота в группу"
PROJECT_BUTTON_PREFIX = "📁 "

# --- Старт и роли ---

START = "Привет! Я отвечаю на вопросы по документации проекта.\n\nКак вы хотите начать?"
ADMIN_START = "Создайте новый проект или присоединитесь к существующему в роли админа."
ASK_INVITE_CODE = "Пришлите ссылку-приглашение от админа проекта или код из неё."
BAD_INVITE_FORMAT = (
    "Не похоже на ссылку-приглашение. Пришлите ссылку вида t.me/…?start=… или сам код."
)
INVITE_NOT_FOUND = "Приглашение не найдено. Проверьте ссылку."
INVITE_EXPIRED = "Срок действия приглашения истёк. Попросите админа прислать новое."
INVITE_REVOKED = "Приглашение отозвано. Попросите админа прислать новое."
NO_PROJECT = "Вы пока не состоите ни в одном проекте. Выберите, как подключиться."
CANCELLED = "Отменено."
NOTHING_TO_CANCEL = "Нечего отменять."

# --- Проект и пароль ---

ASK_PROJECT_NAME = "Введите название проекта (до 64 символов)."
INVALID_PROJECT_NAME = "Название должно быть от 1 до 64 символов. Попробуйте ещё раз."
ASK_ADMIN_PASSWORD = (
    "Введите секретный пароль проекта. Сообщение с паролем я сразу удалю из переписки."
)
INVALID_PASSWORD = "Неверный пароль. Попробуйте ещё раз или отправьте /cancel."
TOO_MANY_ATTEMPTS = "Слишком много попыток ввода пароля. Попробуйте позже."
PASSWORD_EXPIRED = "Время показа пароля истекло."  # всплывающее окно, не HTML
NO_RIGHTS = "Недостаточно прав."  # всплывающее окно
NOT_MEMBER = "Вы не участник этого проекта."  # всплывающее окно
PROJECTS_LIST = "Ваши проекты. Выберите, на какой переключиться:"

# --- Приглашения и участники ---

CHOOSE_INVITE_TTL = "Сколько дней будет действовать приглашение?"
NO_ACTIVE_INVITES = "Активных приглашений нет."
INVITE_REVOKED_OK = "Приглашение отозвано."
INVITE_ALREADY_INACTIVE = "Приглашение уже неактивно."
CANNOT_REMOVE_CREATOR = "Создателя проекта удалить нельзя."
CANNOT_REMOVE_SELF = "Нельзя удалить самого себя."
MEMBER_ALREADY_REMOVED = "Участник уже удалён."

# --- Документация ---

ASK_PASSWORD_FOR_REPLACE = (
    "Замена удалит всю текущую документацию проекта. Введите секретный пароль проекта — "
    "сообщение с паролем я сразу удалю."
)
FILE_TOO_LARGE = "Файл больше 20 МБ — Telegram не даёт боту скачать такой. Разбейте его на части."
NO_FILES = "Вы ещё не прислали ни одного файла."
SEND_DOCUMENT_HINT = "Пришлите файлы документом, затем нажмите «Готово». Отмена — /cancel."
DOCS_PROCESSING = "Загружаю документацию…"
DOCS_UPDATED_ADMIN = "Документация обновлена. Участники проекта получат уведомление."
DOCS_CANCELLED = "Загрузка документации отменена."
CHANGES_NOT_FOUND = "Сводка изменений не найдена."
DOWNLOAD_FAILED = "Не удалось скачать файл из Telegram. Пришлите его ещё раз."
BUTTON_EXPIRED = "Кнопка устарела."  # всплывающее окно
DOCS_ALREADY_PROCESSING = "Документация уже загружается."  # всплывающее окно
REPLACE_CONFIRMATION_EXPIRED = (
    "Подтверждение паролем устарело. Нажмите «Заменить документацию» и введите пароль снова."
)
PASSWORD_NOT_A_QUESTION = (
    "Похоже, это секретный пароль проекта. Я удалил сообщение и никуда его не передал. "
    "Пароль вводится только после кнопки «Присоединиться к проекту в роли админа» "
    "или «Заменить документацию»."
)
REMOVED_FROM_PROJECT_BEFORE = (
    "Вас удалили из этого проекта. Чтобы вернуться, попросите админа прислать новое приглашение."
)
GROUP_REBIND_FORBIDDEN = (
    "Этот чат уже привязан к другому проекту. Перепривязать его может только админ того проекта."
)
UNKNOWN_COMMAND = "Не знаю такой команды. Меню проекта — /menu, начать заново — /start."
WAITING_OTHER_INPUT = "Сейчас я жду другое. Чтобы выйти из этого шага — /cancel."
UNSUPPORTED_MESSAGE = "Я понимаю текст и скриншоты. Напишите вопрос или пришлите изображение."
INTERNAL_ERROR = "Что-то пошло не так. Попробуйте ещё раз чуть позже."

# --- Вопросы ---

NOT_FOUND_FORWARDED = (
    "В документации ответа нет. Я передал вопрос админу проекта — ответ придёт сюда."
)
ASK_ANSWER = "Напишите ответ — я перешлю его автору вопроса. Отмена — /cancel."
ANSWER_SENT = (
    "Ответ отправлен. Сохранить его в базу знаний проекта, чтобы в следующий раз я ответил сам?"
)
ANSWER_NOT_DELIVERED = (
    "Не удалось доставить ответ: пользователь заблокировал бота или чат недоступен."
)
ALREADY_ANSWERED = "На этот вопрос уже ответил другой админ."
QUESTION_NOT_FOUND = "Вопрос не найден."
QUESTION_NOT_ANSWERED = "Сначала ответьте на вопрос."
KNOWLEDGE_SAVED = "Сохранено в базу знаний."
RATE_LIMITED = "Слишком много вопросов подряд. Подождите немного."
LLM_UNAVAILABLE = "Сервис ответов временно недоступен. Попробуйте позже."
QUESTION_TOO_LONG = "Слишком длинный текст — сократите его до 4000 символов."
EMPTY_TEXT = "Пустое сообщение — напишите текст."

# --- Групповой чат ---

JOIN_USAGE = "Укажите секретный пароль проекта: /join <пароль>."
DELETE_PASSWORD_MANUALLY = (
    "⚠️ Удалите сообщение с паролем: у бота нет прав удалять сообщения в этой группе. "
    "Пароль даёт права админа проекта."
)
ASK_USAGE = "Напишите вопрос после команды: /ask <вопрос>."


def role_label(role: MemberRole) -> str:
    return ROLE_LABELS[MemberRole(role)]


def project_button(name: str, role: MemberRole) -> str:
    """Текст кнопки текущего проекта над полем ввода (кнопка — не HTML)."""
    if len(name) > _PROJECT_BUTTON_NAME_LEN:
        name = name[: _PROJECT_BUTTON_NAME_LEN - 1] + "…"
    return f"{PROJECT_BUTTON_PREFIX}{name} · {role_label(role)}"


def project_list_button(name: str, role: MemberRole, is_current: bool) -> str:
    mark = "✅ " if is_current else ""
    return f"{mark}{name} · {role_label(role)}"


def joined_project(name: str, role: MemberRole) -> str:
    return f"Вы в проекте «<b>{escape(name)}</b>» как {role_label(role)}."


def main_menu(name: str) -> str:
    return (
        f"Задайте вопрос по документации проекта «<b>{escape(name)}</b>» — "
        "просто напишите его в чат или пришлите скриншот."
    )


def project_created(name: str, reveal_ttl_seconds: int) -> str:
    minutes = max(1, reveal_ttl_seconds // 60)
    return (
        f"Проект «<b>{escape(name)}</b>» создан.\n\n"
        "У проекта есть секретный пароль: он нужен, чтобы добавить ещё одного админа, "
        "заменить документацию или подключить бота к групповому чату. Пароль не остаётся "
        "в переписке: кнопки ниже показывают его во всплывающем окне или копируют в буфер "
        f"обмена. Сохраните его — кнопки исчезнут через {minutes} мин."
    )


def group_instructions(bot_username: str) -> str:
    bot = escape(bot_username)
    return (
        "<b>Как добавить бота в рабочий чат</b>\n\n"
        f"1. Нажмите кнопку ниже или добавьте @{bot} в группу вручную. Лучше сразу сделать "
        "бота администратором с правом удалять сообщения — тогда он удалит пароль из чата.\n"
        "2. Возьмите секретный пароль проекта (его выдают создателю проекта).\n"
        f"3. Отправьте в группе: <code>/join@{bot} пароль</code>.\n"
        f"4. Задавайте вопросы: <code>/ask@{bot} вопрос</code> или <code>@{bot} вопрос</code>.\n\n"
        "Бот видит только обращённые к нему сообщения и не читает остальную переписку."
    )


def add_to_group_url(bot_username: str) -> str:
    return f"https://t.me/{bot_username}?startgroup=true"


def invite_link(bot_username: str, code: str) -> str:
    return f"https://t.me/{bot_username}?start={code}"


def ttl_button(days: int) -> str:
    return f"{days} дн."


def invite_created(link: str, days: int) -> str:
    return (
        f"Ссылка-приглашение, действует {days} дн.:\n{escape(link)}\n\n"
        "Отправьте её команде. Код из ссылки подходит и для привязки группового чата."
    )


def invite_line(code: str, expires_at: str) -> str:
    return f"<code>{escape(code)}</code> — до {escape(expires_at)}"


def more_invites(count: int) -> str:
    return f"…и ещё {count}. Отзовите ненужные, чтобы увидеть остальные."


def active_invites(lines: list[str]) -> str:
    return "<b>Активные приглашения</b>\n\n" + "\n".join(lines)


def members_header(total: int, page: int, pages: int) -> str:
    return (
        f"<b>Участники проекта</b>: {total}. Страница {page + 1} из {pages}.\n"
        "Нажмите, чтобы удалить."
    )


def member_button(full_name: str, username: str | None, role: MemberRole) -> str:
    handle = f" @{username}" if username else ""
    return f"❌ {full_name}{handle} · {role_label(role)}"


def confirm_remove(full_name: str) -> str:
    return f"Удалить <b>{escape(full_name)}</b> из проекта? Он потеряет доступ к проекту."


def member_removed(full_name: str) -> str:
    return f"<b>{escape(full_name)}</b> удалён из проекта."


def removed_from_project(project_name: str) -> str:
    return f"Вас удалили из проекта «<b>{escape(project_name)}</b>»."


def docs_send_files(max_files: int) -> str:
    return (
        f"Пришлите файлы документации документом (до {max_files} файлов, каждый до 20 МБ), "
        "затем нажмите «Готово»."
    )


def file_accepted(filename: str, count: int, max_files: int) -> str:
    return f"Принято: {escape(filename)} ({count}/{max_files})."


def too_many_files(max_files: int) -> str:
    return f"За один раз можно загрузить не больше {max_files} файлов. Нажмите «Готово»."


def docs_updated(project_name: str) -> str:
    return f"Документация проекта «<b>{escape(project_name)}</b>» обновлена"


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def admin_unanswered(project_name: str, question: str, max_question_len: int = 3500) -> str:
    """Уведомление админу. Вопрос обрезается до экранирования, чтобы влезть в лимит
    сообщения (4096) или подписи к фото (1024, тогда max_question_len меньше)."""
    question = _shorten(question, max_question_len)
    return (
        f"📁 <b>{escape(project_name)}</b>\n"
        f"Мне задали вопрос «{escape(question)}», однако в документации ответа нет. "
        "Ответьте сами или дополните документацию."
    )


def answer_to_asker(question: str, answer: str) -> str:
    """Текст без HTML: ответ админа отправляется без parse_mode."""
    return f"Ответ админа на ваш вопрос «{question}»:\n\n{answer}"


def group_not_bound(bot_username: str) -> str:
    return (
        "Этот чат ещё не привязан к проекту. Отправьте "
        f"<code>/join@{escape(bot_username)} пароль</code> с секретным паролем проекта."
    )


def group_bound(project_name: str) -> str:
    return f"Чат привязан к проекту «<b>{escape(project_name)}</b>». Задавайте вопросы!"


def split_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Разбить текст на части не длиннее limit: по переводу строки, иначе по пробелу,
    иначе жёстко. Работает с текстом без HTML."""
    parts: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit + 1)
        if cut <= 0:
            cut = text.rfind(" ", 0, limit + 1)
        if cut <= 0:
            cut = limit
            parts.append(text[:cut])
            text = text[cut:]
        else:
            parts.append(text[:cut])
            text = text[cut + 1 :]
    if text or not parts:
        parts.append(text)
    return parts
