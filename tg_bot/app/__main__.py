import asyncio
import logging

from app.bot.dispatcher import build_dispatcher
from app.bot.jobs import build_scheduler
from app.dependencies import (
    get_obj_bot,
    get_obj_fsm_storage,
    get_obj_llm_gateway,
    get_obj_notifier,
    get_obj_postgres_client,
    get_obj_redis_client,
    get_obj_resolver,
    get_obj_scheduler_work,
    get_obj_settings,
)

logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_obj_settings()
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    postgres = get_obj_postgres_client()
    redis = get_obj_redis_client()
    await postgres.connect()
    await redis.connect()

    bot = get_obj_bot()
    resolver = get_obj_resolver()
    dp = build_dispatcher(get_obj_fsm_storage(), resolver)
    scheduler = build_scheduler(resolver, settings, get_obj_scheduler_work(redis))
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        scheduler.start()
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await scheduler.stop()
        await get_obj_notifier().shutdown()
        await get_obj_llm_gateway().close()
        await redis.close()
        await postgres.close()
        await bot.session.close()
        logger.info("Bot stopped")


if __name__ == "__main__":
    asyncio.run(main())
