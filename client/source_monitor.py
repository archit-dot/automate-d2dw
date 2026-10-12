import asyncio
import logging
import re

from telethon import events

from database import repo
from database.db import log
from .diskwala_client import DiskWalaConverter
from .entity import input_peer_from_row

logger = logging.getLogger(__name__)
DISKWALA_RE = re.compile(r"https?://[^\s<>]*diskwala[^\s<>]*", re.I)


class SourceMonitor:
    def __init__(self, telegram):
        self.telegram = telegram
        self.converter = DiskWalaConverter(telegram)
        self.handlers = []
        self.album_tasks = {}
        self.route_locks = {}
        self.autopilot_tasks = {}
        self.running = False

    @staticmethod
    def has_diskwala_link(message):
        text = getattr(message, "text", "") or ""
        return bool(DISKWALA_RE.search(text))

    async def _source_peer(self, source):
        peer = input_peer_from_row(source)
        if peer is not None:
            return peer
        resolved = await self.telegram.resolve(source["username"] or str(source["chat_id"]))
        repo.update_source_peer(source["id"], resolved)
        return resolved.input_peer

    async def start(self):
        self.running = True
        client = self.telegram.client
        existing = {getattr(h, "_telepost_source_id", None) for h in self.handlers}

        for source in repo.sources():
            if source["id"] in existing:
                continue
            if not source["enabled"]:
                continue
            try:
                peer = await self._source_peer(source)
            except Exception as exc:
                logger.error("Source #%s unavailable: %s", source["id"], exc)
                log("source_bind", "failed", f"source={source['id']}: {exc}")
                continue

            async def handler(event, source_id=source["id"]):
                try:
                    await self._incoming(source_id, event.message)
                except Exception as exc:
                    logger.exception(
                        "Source processing failed: source=%s message=%s",
                        source_id,
                        event.message.id,
                    )
                    log(
                        "source_process",
                        "failed",
                        f"source={source_id} message={event.message.id}: {exc}",
                    )

            handler._telepost_source_id = source["id"]
            client.add_event_handler(handler, events.NewMessage(chats=peer))
            self.handlers.append(handler)
            logger.info("Monitoring source #%s %s (%s)", source["id"], source["title"], source["chat_id"])

    async def _collect_post(self, source, message):
        """Return one logical post: a single message or its complete media album."""
        grouped_id = getattr(message, "grouped_id", None)
        if not grouped_id:
            return [message]

        key = (source["id"], int(grouped_id))
        existing = self.album_tasks.get(key)
        if existing:
            try:
                await existing
            except Exception:
                pass
            return []

        async def gather():
            await asyncio.sleep(1.0)
            peer = await self._source_peer(source)
            nearby = await self.telegram.client.get_messages(
                peer,
                min_id=max(0, int(message.id) - 20),
                max_id=int(message.id) + 20,
                limit=50,
            )
            album = [m for m in nearby if getattr(m, "grouped_id", None) == grouped_id]
            album.append(message)
            unique = {m.id: m for m in album}
            return sorted(unique.values(), key=lambda m: m.id)

        task = asyncio.create_task(gather())
        self.album_tasks[key] = task
        try:
            return await task
        finally:
            self.album_tasks.pop(key, None)

    async def _next_eligible(self, source, after_id, scan_limit=100):
        peer = await self._source_peer(source)
        messages = await self.telegram.client.get_messages(peer, limit=scan_limit)
        messages = [m for m in messages if int(m.id) > int(after_id)]
        messages.sort(key=lambda m: m.id)
        for message in messages:
            if self.has_diskwala_link(message):
                return message
        return None

    async def fill_route(self, route_id):
        lock = self.route_locks.setdefault(route_id, asyncio.Lock())
        async with lock:
            return await self._fill_route_locked(route_id)

    def start_autopilot(self, route_id):
        """Start this route's independent, persistent queue-refill task."""
        route_id = int(route_id)
        task = self.autopilot_tasks.get(route_id)
        if task and not task.done():
            return task
        task = asyncio.create_task(
            self._autopilot_loop(route_id),
            name=f"telepost-autopilot-route-{route_id}",
        )
        self.autopilot_tasks[route_id] = task
        task.add_done_callback(
            lambda finished, rid=route_id: self.autopilot_tasks.pop(rid, None)
            if self.autopilot_tasks.get(rid) is finished else None
        )
        return task

    async def stop_autopilot(self, route_id):
        """Stop only the selected route's queue-refill task."""
        task = self.autopilot_tasks.pop(int(route_id), None)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _autopilot_loop(self, route_id):
        """Keep an enabled route's queue topped up without blocking the UI."""
        while True:
            try:
                route = repo.route(route_id)
                if (
                    not route
                    or not route["enabled"]
                    or not route["autopilot"]
                    or not route["destination_enabled"]
                    or route["destination_paused"]
                ):
                    return

                capacity = min(40, int(route["max_posts"] or 40))
                queued = repo.route_queue_count(route_id)
                if queued < capacity:
                    added = await self.fill_route(route_id)
                    # Back off briefly after successful work; retry more slowly
                    # when no eligible source posts are currently available.
                    await asyncio.sleep(2 if added else 10)
                else:
                    await asyncio.sleep(5)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Auto-Pilot refill failed for route=%s", route_id)
                log("autopilot_refill", "failed", f"route={route_id}: {exc}")
                await asyncio.sleep(10)

    async def _fill_route_locked(self, route_id):
        """Fill a route up to 40 queued eligible logical posts using strict round-robin."""
        route = repo.route(route_id)
        if not route:
            raise ValueError("Route not found.")
        limit = min(40, int(route["max_posts"] or 40))
        added = 0
        sources_seen_without_result = set()

        while repo.route_queue_count(route_id) < limit:
            current_sources = repo.route_sources(route_id)
            if not current_sources:
                break

            expected = repo.expected_source(route_id)
            if not expected:
                break
            sid = int(expected["source_id"])

            eligible = await self._next_eligible(expected, expected["route_last_message_id"], scan_limit=100)
            if not eligible:
                repo.advance_rotation(route_id)
                sources_seen_without_result.add(sid)
                if sources_seen_without_result.issuperset(int(s["id"]) for s in current_sources):
                    break
                continue

            logical_messages = await self._collect_post(expected, eligible)
            if not logical_messages:
                continue
            max_message_id = max(int(m.id) for m in logical_messages)

            before = repo.route_queue_count(route_id)
            ok = await self.process(route_id, sid, logical_messages)
            after = repo.route_queue_count(route_id)
            if ok or after > before:
                added += max(0, after - before)
                repo.update_route_source_message(route_id, sid, max_message_id)
                repo.advance_rotation(route_id)
                sources_seen_without_result.clear()
            else:
                # Conversion failure stays on the same source turn so the
                # failed item can be retried instead of silently skipped.
                break

        return added

    async def _drain_pending(self, route_id):
        """Process observed messages waiting for the route's next source turn."""
        safety = max(1, len(repo.route_sources(route_id)))
        for _ in range(safety):
            route = repo.route(route_id)
            if not route or repo.route_queue_count(route_id) >= min(40, int(route["max_posts"] or 40)):
                return
            expected = repo.expected_source(route_id)
            if not expected:
                return
            pending_id = int(expected["route_pending_message_id"] or 0)
            last_id = int(expected["route_last_message_id"] or 0)
            if pending_id <= last_id:
                return

            source = repo.source(int(expected["source_id"]))
            if not source:
                return
            peer = await self._source_peer(source)
            message = await self.telegram.client.get_messages(peer, ids=pending_id)
            if not message:
                repo.update_route_source_message(route_id, source["id"], pending_id)
                repo.advance_rotation(route_id)
                continue

            logical_messages = await self._collect_post(source, message)
            if not logical_messages:
                return
            max_message_id = max(int(m.id) for m in logical_messages)
            if not any(self.has_diskwala_link(m) for m in logical_messages):
                repo.update_route_source_message(route_id, source["id"], max_message_id)
                repo.advance_rotation(route_id)
                continue

            before = repo.route_queue_count(route_id)
            ok = await self.process(route_id, source["id"], logical_messages)
            after = repo.route_queue_count(route_id)
            if not ok and after == before:
                return
            repo.update_route_source_message(route_id, source["id"], max_message_id)
            repo.advance_rotation(route_id)

    async def _incoming(self, source_id, message):
        source = repo.source(source_id)
        if not source or not source["enabled"]:
            return
        for route in repo.routes_for_source(source_id):
            lock = self.route_locks.setdefault(int(route["id"]), asyncio.Lock())
            async with lock:
                await self._incoming_route(route, source, message)

    async def _incoming_route(self, route, source, message):
        route_limit = min(40, int(route["max_posts"] or 40))
        if repo.route_queue_count(route["id"]) >= route_limit:
            return

        expected = repo.expected_source(route["id"])
        if not expected:
            return

        if int(expected["source_id"]) != int(source["id"]):
            if self.has_diskwala_link(message):
                repo.set_route_source_pending(route["id"], source["id"], message.id)
            return

        if int(message.id) <= int(expected["route_last_message_id"]):
            return

        logical_messages = await self._collect_post(source, message)
        if not logical_messages:
            return

        max_message_id = max(int(m.id) for m in logical_messages)
        if not any(self.has_diskwala_link(m) for m in logical_messages):
            repo.update_route_source_message(route["id"], source["id"], max_message_id)
            repo.advance_rotation(route["id"])
            await self._drain_pending(route["id"])
            return

        before = repo.route_queue_count(route["id"])
        ok = await self.process(route["id"], source["id"], logical_messages)
        after = repo.route_queue_count(route["id"])
        if ok or after > before:
            repo.update_route_source_message(route["id"], source["id"], max_message_id)
            repo.advance_rotation(route["id"])
            await self._drain_pending(route["id"])

    async def process(self, route_id, source_id, messages):
        if not messages:
            return False
        if not any(self.has_diskwala_link(m) for m in messages):
            return False

        anchor = messages[0]
        message_ids = [int(m.id) for m in messages]
        grouped_id = getattr(anchor, "grouped_id", None)
        original_text = "\n\n".join((m.text or "").strip() for m in messages if (m.text or "").strip()).strip()
        post = repo.create_post(
            route_id,
            source_id,
            anchor.id,
            grouped_id,
            original_text,
            source_message_ids=message_ids,
        )
        if not repo.post_can_process(post["id"]):
            return False

        try:
            logger.info("Route %s: converting source=%s messages=%s", route_id, source_id, message_ids)
            responses = await self.converter.convert_messages(messages)
            if not responses:
                raise RuntimeError("DiskWala returned no response messages.")

            converted_text = "\n\n".join(
                (r.text or "").strip() for r in responses if (r.text or "").strip()
            ).strip()
            response_ids = [int(r.id) for r in responses]
            bot = await self.telegram.client.get_input_entity(self.converter.bot_username)
            bot_entity = await self.telegram.client.get_entity(bot)
            final_text = repo.apply_header_footer(route_id, converted_text)
            repo.set_processed(post["id"], converted_text, final_text, int(bot_entity.id), response_ids)
            logger.info("Route %s: queued post=%s responses=%s", route_id, post["id"], response_ids)
            log(
                "diskwala_conversion",
                "success",
                f"route={route_id} source={source_id} messages={message_ids} responses={response_ids}",
            )
            return True
        except Exception as exc:
            repo.mark_failed(post["id"], exc)
            logger.exception("DiskWala conversion failed route=%s source=%s", route_id, source_id)
            log(
                "diskwala_conversion",
                "failed",
                f"route={route_id} source={source_id} messages={message_ids}: {exc}",
            )
            return False

    async def stop(self):
        client = self.telegram.client
        for handler in self.handlers:
            client.remove_event_handler(handler)
        self.handlers.clear()

        tasks = list(self.autopilot_tasks.values())
        self.autopilot_tasks.clear()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        for task in list(self.album_tasks.values()):
            task.cancel()
        if self.album_tasks:
            await asyncio.gather(*self.album_tasks.values(), return_exceptions=True)
        self.album_tasks.clear()
        self.route_locks.clear()
        self.running = False

    async def run(self):
        while True:
            try:
                await self.start()
                # Restore Auto-Pilot workers after startup/reconnection. Each
                # route gets its own task, independent from all other routes.
                for route in repo.routes():
                    if (
                        route["enabled"]
                        and route["autopilot"]
                        and route["destination_enabled"]
                        and not route["destination_paused"]
                    ):
                        self.start_autopilot(route["id"])
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                await self.stop()
                raise
            except Exception:
                logger.exception("Source monitor refresh failed; retrying in 10 seconds")
                await asyncio.sleep(10)
