import asyncio
import logging

from telethon import events

from config import settings

logger = logging.getLogger(__name__)


class DiskWalaConverter:
    def __init__(self, telegram):
        self.telegram = telegram
        self.bot_username = settings.diskwala_bot
        # DiskWala is a single conversational bot. Serialize requests so a
        # response can never be attributed to the wrong source post.
        self.lock = asyncio.Lock()

    async def convert_messages(self, messages, timeout=120, quiet_seconds=2.5):
        if not messages:
            return []

        async with self.lock:
            client = self.telegram.client
            bot_input = await client.get_input_entity(self.bot_username)
            bot = await client.get_entity(bot_input)
            baseline_messages = await client.get_messages(bot_input, limit=20)
            baseline = max((m.id for m in baseline_messages), default=0)
            response_queue = asyncio.Queue()

            async def on_response(event):
                response = event.message
                if response.id <= baseline:
                    return
                # The converter bot's own chat is the correlation boundary.
                # Ignore the forwarded source message(s), which are sent by us.
                if getattr(response, "sender_id", None) != getattr(bot, "id", None):
                    return
                await response_queue.put(response)

            client.add_event_handler(on_response, events.NewMessage(chats=bot_input))
            try:
                logger.info(
                    "DiskWala: forwarding %s source message(s) %s",
                    len(messages),
                    [m.id for m in messages],
                )
                forwarded = await client.forward_messages(
                    bot_input,
                    messages,
                    from_peer=messages[0].peer_id,
                )
                forwarded_list = forwarded if isinstance(forwarded, list) else [forwarded]
                forwarded_ids = {int(getattr(x, "id", x)) for x in forwarded_list}

                first = await self._first_response(
                    response_queue,
                    client,
                    bot_input,
                    bot.id,
                    baseline,
                    forwarded_ids,
                    timeout,
                )
                responses = [first]

                deadline = asyncio.get_running_loop().time() + quiet_seconds
                while True:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        break
                    try:
                        msg = await asyncio.wait_for(response_queue.get(), timeout=remaining)
                    except asyncio.TimeoutError:
                        break
                    if all(msg.id != old.id for old in responses):
                        responses.append(msg)
                        deadline = asyncio.get_running_loop().time() + quiet_seconds

                responses.sort(key=lambda m: m.id)
                logger.info(
                    "DiskWala: received %d response message(s): %s",
                    len(responses),
                    [m.id for m in responses],
                )
                return responses
            finally:
                client.remove_event_handler(on_response, events.NewMessage(chats=bot_input))

    async def _first_response(
        self,
        response_queue,
        client,
        bot_input,
        bot_id,
        baseline,
        forwarded_ids,
        timeout,
    ):
        try:
            return await asyncio.wait_for(response_queue.get(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            recent = await client.get_messages(bot_input, limit=30)
            candidates = [
                m
                for m in recent
                if int(m.id) > baseline
                and int(m.id) not in forwarded_ids
                and getattr(m, "sender_id", None) == int(bot_id)
            ]
            if not candidates:
                raise TimeoutError(
                    f"DiskWala response not received within {timeout}s"
                ) from exc
            return max(candidates, key=lambda m: m.id)

    async def convert_message(self, message, timeout=120, quiet_seconds=2.5):
        return await self.convert_messages([message], timeout=timeout, quiet_seconds=quiet_seconds)
