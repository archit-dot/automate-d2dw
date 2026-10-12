import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from config import settings
from database import repo

logger = logging.getLogger(__name__)


def in_window(current, start, end):
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end


class ScheduleWorker:
    def __init__(self, publisher):
        self.publisher = publisher
        self.fired = set()
        self.tasks = set()

    async def run(self):
        while True:
            now = datetime.now(ZoneInfo(settings.timezone))
            minute_key = now.strftime("%Y-%m-%d %H:%M")
            current = now.strftime("%H:%M")

            for row in repo.schedules():
                route = repo.route(row["route_id"])
                if not route or not route["autopilot"] or not row["enabled"]:
                    continue
                if not in_window(current, route["start_time"], route["end_time"]):
                    continue
                if row["daily_time"] != current:
                    continue

                key = f"{row['id']}:{minute_key}"
                if key in self.fired:
                    continue
                self.fired.add(key)

                logger.info("Schedule fired route=%s time=%s", route["id"], current)
                task = asyncio.create_task(self.publisher.publish_route(route["id"]))
                self.tasks.add(task)
                task.add_done_callback(self.tasks.discard)

            if len(self.fired) > 10000:
                self.fired = set(list(self.fired)[-5000:])
            await asyncio.sleep(5)
