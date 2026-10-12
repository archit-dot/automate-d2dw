import asyncio
import json
import logging

from database import repo
from config import settings
from client.entity import input_peer_from_row

logger = logging.getLogger(__name__)


class PublisherWorker:
    def __init__(self, telegram):
        self.telegram = telegram
        self.locks = {}

    async def _destination_peer(self, row):
        destination = repo.destination(row["destination_id"])
        if not destination:
            raise RuntimeError("Destination no longer exists.")
        peer = input_peer_from_row(destination)
        if peer is not None:
            return peer
        resolved = await self.telegram.resolve_stored(destination)
        repo.update_destination_peer(destination["id"], resolved)
        return resolved.input_peer

    async def publish_route(self, route_id):
        lock = self.locks.setdefault(route_id, asyncio.Lock())
        async with lock:
            row = repo.pending_route(route_id)
            if not row:
                return False

            post_id = row["post_id"]
            repo.mark_publishing(post_id)
            try:
                ids = json.loads(row["diskwala_message_ids"] or "[]")
                if not ids:
                    raise RuntimeError("Queued post has no DiskWala response IDs.")

                bot = await self.telegram.client.get_input_entity(settings.diskwala_bot)
                responses = await self.telegram.client.get_messages(bot, ids=ids)
                if not isinstance(responses, list):
                    responses = [responses]
                responses = sorted([m for m in responses if m], key=lambda m: m.id)
                if not responses:
                    raise RuntimeError("DiskWala response messages no longer exist.")

                destination = await self._destination_peer(row)
                final_text = row["final_text"] or ""
                media = [m for m in responses if m.media]

                if media:
                    if len(media) == 1:
                        await self.telegram.client.send_file(
                            destination,
                            media[0].media,
                            caption=final_text or (media[0].text or None),
                        )
                    else:
                        captions = []
                        for index, message in enumerate(media):
                            if index == 0:
                                captions.append(final_text or (message.text or ""))
                            else:
                                captions.append(message.text or "")
                        await self.telegram.client.send_file(
                            destination,
                            [m.media for m in media],
                            caption=captions,
                        )

                    # If DiskWala returned useful text-only messages alongside
                    # media, preserve them after the media instead of dropping it.
                    extra_text = [m.text.strip() for m in responses if not m.media and (m.text or "").strip()]
                    if extra_text and not final_text:
                        await self.telegram.client.send_message(destination, "\n\n".join(extra_text))
                else:
                    text = final_text or "\n\n".join((m.text or "") for m in responses if (m.text or "").strip())
                    if not text:
                        raise RuntimeError("DiskWala returned messages without publishable media or text.")
                    await self.telegram.client.send_message(destination, text)

                repo.mark_published(post_id)
                logger.info("Published post=%s route=%s destination=%s", post_id, route_id, row["destination_chat_id"])
                return True
            except Exception as exc:
                repo.mark_failed(post_id, exc)
                logger.exception("Publishing failed post=%s route=%s", post_id, route_id)
                return False

    async def run(self):
        while True:
            await asyncio.sleep(15)
