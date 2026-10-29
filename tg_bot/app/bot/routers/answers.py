import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyParameters

from app.bot import keyboards, texts, views
from app.bot.callbacks import AnswerQuestionCb, SaveKnowledgeCb
from app.bot.states import AnswerQuestion
from app.core.depends import Depends
from app.core.enums import QuestionStatus
from app.core.errors import DomainError, EmptyQuestion, QuestionTooLong
from app.dependencies import get_obj_question_service
from app.llm.gateway import LLMUnavailableError
from app.services.question_service import QuestionService
from app.storage.postgres.models import Question, User

logger = logging.getLogger(__name__)


async def start_answer(
    callback: CallbackQuery,
    callback_data: AnswerQuestionCb,
    state: FSMContext,
    db_user: User,
    question_service: QuestionService = Depends(get_obj_question_service),
) -> None:
    try:
        question = await question_service.get_question_for_admin(db_user, callback_data.question_id)
    except DomainError as error:
        await views.alert(callback, error)
        return
    if question.status == QuestionStatus.ANSWERED:
        await callback.answer(texts.ALREADY_ANSWERED, show_alert=True)
        return
    await state.set_state(AnswerQuestion.text)
    await state.set_data({"question_id": question.id})
    await callback.message.answer(texts.ASK_ANSWER)
    await callback.answer()


async def deliver_answer(bot: Bot, question: Question) -> bool:
    """Отправить ответ админа туда, где задан вопрос (личка или групповой чат)."""
    parts = texts.split_message(texts.answer_to_asker(question.text, question.answer))
    try:
        for index, part in enumerate(parts):
            reply = (
                ReplyParameters(message_id=question.message_id, allow_sending_without_reply=True)
                if index == 0
                else None
            )
            await bot.send_message(question.chat_id, part, parse_mode=None, reply_parameters=reply)
    except TelegramAPIError as error:
        logger.info("Cannot deliver answer for question %s: %s", question.id, error)
        return False
    return True


async def receive_answer(
    message: Message,
    state: FSMContext,
    bot: Bot,
    db_user: User,
    question_service: QuestionService = Depends(get_obj_question_service),
) -> None:
    question_id = (await state.get_data())["question_id"]
    try:
        question = await question_service.answer_question(db_user, question_id, message.text)
    except (EmptyQuestion, QuestionTooLong) as error:
        await message.answer(views.error_text(error))
        return
    except DomainError as error:
        await state.clear()
        await message.answer(views.error_text(error))
        return
    await state.clear()
    if not await deliver_answer(bot, question):
        await message.answer(texts.ANSWER_NOT_DELIVERED)
    await message.answer(texts.ANSWER_SENT, reply_markup=keyboards.save_knowledge_kb(question.id))


async def save_knowledge(
    callback: CallbackQuery,
    callback_data: SaveKnowledgeCb,
    db_user: User,
    question_service: QuestionService = Depends(get_obj_question_service),
) -> None:
    try:
        await question_service.save_to_knowledge(db_user, callback_data.question_id)
    except DomainError as error:
        await views.alert(callback, error)
        return
    except LLMUnavailableError:
        await callback.answer(texts.LLM_UNAVAILABLE, show_alert=True)
        return
    await callback.answer(texts.KNOWLEDGE_SAVED, show_alert=True)
    await callback.message.edit_reply_markup(reply_markup=None)


def create_router() -> Router:
    router = Router(name="answers")
    router.message.filter(F.chat.type == ChatType.PRIVATE)
    router.callback_query.register(start_answer, AnswerQuestionCb.filter())
    router.message.register(receive_answer, AnswerQuestion.text, F.text)
    router.callback_query.register(save_knowledge, SaveKnowledgeCb.filter())
    return router
