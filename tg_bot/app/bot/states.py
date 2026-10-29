from aiogram.fsm.state import State, StatesGroup


class CreateProject(StatesGroup):
    name = State()


class JoinAsAdmin(StatesGroup):
    password = State()


class JoinAsUser(StatesGroup):
    code = State()


class DocsUpload(StatesGroup):
    password = State()
    files = State()


class AnswerQuestion(StatesGroup):
    text = State()
