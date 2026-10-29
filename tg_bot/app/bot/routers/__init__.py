from aiogram import Router

from app.bot.routers import (
    admin_docs,
    admin_invites,
    admin_members,
    admin_project,
    answers,
    fallback,
    group,
    projects,
    questions,
    start,
)


def setup_routers() -> Router:
    """Собрать роутеры заново (aiogram не даёт подключить один роутер к двум диспетчерам).

    Порядок важен:
    - projects сразу после start: кнопка «📁 Проект · роль» видна всегда и должна
      срабатывать, даже если человек посреди другого шага (иначе её текст примут
      за название проекта или пароль);
    - questions ловит любой текст в личке без состояния;
    - fallback — последний: отвечает на всё, что не обработали остальные.
    """
    root = Router(name="root")
    modules = (
        start,
        projects,
        admin_project,
        admin_invites,
        admin_members,
        admin_docs,
        answers,
        group,
        questions,
        fallback,
    )
    for module in modules:
        root.include_router(module.create_router())
    return root
