import asyncio

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.methods import SendMessage

from app.bot import texts
from app.bot.notifier import Notifier, SendReport
from app.core.enums import MemberRole


def test_texts_escape_user_input():
    message = texts.admin_unanswered("<i>P</i>", "<b>x</b> & y")
    assert "&lt;b&gt;x&lt;/b&gt; &amp; y" in message
    assert "&lt;i&gt;P&lt;/i&gt;" in message
    assert "<b>x</b>" not in message
    assert "&lt;script&gt;" in texts.joined_project("<script>", MemberRole.USER)


def test_project_button_is_plain_text_and_truncated():
    assert texts.project_button("<i>", MemberRole.ADMIN) == "📁 <i> · админ"
    long_button = texts.project_button("x" * 100, MemberRole.USER)
    assert long_button == "📁 " + "x" * 39 + "… · пользователь"


def test_admin_unanswered_matches_spec_wording():
    assert (
        "Мне задали вопрос «X», однако в документации ответа нет. "
        "Ответьте сами или дополните документацию." in texts.admin_unanswered("P", "X")
    )


def test_split_message_respects_limit():
    text = ("строка\n" * 1000) + "x" * 5000
    parts = texts.split_message(text)
    assert all(len(p) <= 4096 for p in parts)
    assert "".join(parts).replace("\n", "") == text.replace("\n", "")


def test_split_message_hard_cut_and_short():
    assert [len(p) for p in texts.split_message("a" * 9000)] == [4096, 4096, 808]
    assert texts.split_message("short") == ["short"]
    assert texts.split_message("") == [""]


def test_split_message_prefers_spaces():
    parts = texts.split_message("word " * 2000, limit=100)
    assert all(len(p) <= 100 and not p.startswith(" ") for p in parts)
    assert all(p.endswith("word") for p in parts[:-1])


class FakeBot:
    def __init__(self, failures):
        self.failures = failures
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        failure = self.failures.get(chat_id)
        method = SendMessage(chat_id=chat_id, text=text)
        if failure == "forbidden":
            raise TelegramForbiddenError(method=method, message="bot was blocked")
        if failure == "bad_request":
            raise TelegramBadRequest(method=method, message="chat not found")
        if failure in ("retry_once", "retry_always"):
            if failure == "retry_once":
                self.failures[chat_id] = None
            raise TelegramRetryAfter(method=method, message="flood", retry_after=0)
        self.sent.append(chat_id)


async def test_notifier_counts_failures_and_retries():
    bot = FakeBot({2: "forbidden", 3: "retry_once", 4: "bad_request", 5: "retry_always"})
    report = await Notifier(bot, rate_per_second=1000).send_many([1, 2, 3, 4, 5, 1], "hi")
    assert report == SendReport(sent=2, failed=3)
    assert bot.sent == [1, 3]


async def test_notifier_background_task_completes():
    bot = FakeBot({})
    notifier = Notifier(bot, rate_per_second=1000)
    task = notifier.send_many_in_background([1, 2], "hi")
    await notifier.wait_background()
    assert task.result() == SendReport(sent=2, failed=0)
    assert not notifier._tasks


class MarkupBot:
    def __init__(self):
        self.edited = []

    async def edit_message_reply_markup(self, chat_id, message_id, reply_markup):
        self.edited.append((chat_id, message_id, reply_markup))


async def test_remove_markup_later_removes_buttons():
    bot = MarkupBot()
    notifier = Notifier(bot)
    notifier.remove_markup_later(5, 77, delay_seconds=0)
    await asyncio.sleep(0.05)
    assert bot.edited == [(5, 77, None)]
    assert not notifier._scheduled
