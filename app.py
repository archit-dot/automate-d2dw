import asyncio
import logging

from config import validate
from database import init_db
from database.db import log
from bot.handlers import build_application
from client.telegram_client import TelegramService
from workers.source_worker import SourceWorker
from workers.publisher_worker import PublisherWorker
from scheduler.scheduler import ScheduleWorker
from utils.logger import setup_logging

logger = logging.getLogger(__name__)


async def main():
    validate()
    setup_logging()
    init_db()

    telegram = TelegramService()
    await telegram.start()

    # Resolve and persist account-specific Telegram entities before any worker
    # starts. This eliminates the old "numeric ID only" failure mode.
    failures = await telegram.refresh_configured_entities()
    for kind, item_id, title, error in failures:
        logger.error("Configured %s #%s unavailable (%s): %s", kind, item_id, title, error)
        log("entity_refresh", "failed", f"{kind}={item_id} {title}: {error}")

    source_worker = SourceWorker(telegram)
    publisher_worker = PublisherWorker(telegram)
    schedule_worker = ScheduleWorker(publisher_worker)

    source_task = asyncio.create_task(source_worker.run(), name="source-monitor")
    publisher_task = asyncio.create_task(publisher_worker.run(), name="publisher-worker")
    scheduler_task = asyncio.create_task(schedule_worker.run(), name="scheduler")

    app = build_application(telegram, source_worker.monitor)
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    logger.info("Telepost started")
    logger.info("Admin bot polling")
    logger.info("MTProto user session connected")
    logger.info("Source monitor, scheduler and publisher started")

    try:
        await asyncio.Event().wait()
    finally:
        source_task.cancel()
        publisher_task.cancel()
        scheduler_task.cancel()
        await asyncio.gather(source_task, publisher_task, scheduler_task, return_exceptions=True)
        await source_worker.monitor.stop()
        await telegram.stop()
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
