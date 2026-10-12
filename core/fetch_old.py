import re

from database import repo
from client.entity import resolve_peer

LINK_RE = re.compile(
    r"https?://(?:t\.me|telegram\.me)/(?:s/)?(?:c/)?([^/?#]+)/([0-9]+)",
    re.I,
)


def parse_telegram_post_link(link):
    match = LINK_RE.search((link or "").strip())
    if not match:
        raise ValueError("Invalid Telegram post link. Use https://t.me/channel/123")
    return match.group(1), int(match.group(2))


def _matches_source(source, resolved):
    return int(source["chat_id"]) == int(resolved.marked_id)


async def fetch_old_posts(user_id, route_id, link, count, telegram_service, source_monitor):
    del user_id
    route = repo.route(route_id)
    if not route:
        raise ValueError("Route not found.")

    sources = repo.route_sources(route_id)
    if not sources:
        raise ValueError("This route has no sources configured.")

    channel_ref, start_message_id = parse_telegram_post_link(link)
    resolved = await resolve_peer(telegram_service.client, channel_ref)
    source = next((s for s in sources if _matches_source(s, resolved)), None)
    if not source:
        raise ValueError("The linked channel is not configured as a source for this route.")

    limit = min(40, int(route["max_posts"] or 40), int(count) if count and int(count) > 0 else 40)
    queued = 0
    seen_message_ids = set()
    peer = resolved.input_peer

    async for message in telegram_service.client.iter_messages(
        peer,
        min_id=start_message_id - 1,
        reverse=True,
    ):
        if int(message.id) < start_message_id:
            continue
        if queued >= limit or repo.route_queue_count(route_id) >= min(40, int(route["max_posts"] or 40)):
            break
        if int(message.id) in seen_message_ids:
            continue

        logical_messages = await source_monitor._collect_post(source, message)
        if not logical_messages:
            continue
        ids = {int(m.id) for m in logical_messages}
        seen_message_ids.update(ids)

        if not any(source_monitor.has_diskwala_link(m) for m in logical_messages):
            continue

        before = repo.route_queue_count(route_id)
        await source_monitor.process(route_id, source["id"], logical_messages)
        after = repo.route_queue_count(route_id)
        if after > before:
            queued += 1

        repo.update_route_source_message(route_id, source["id"], max(ids))

    return queued
