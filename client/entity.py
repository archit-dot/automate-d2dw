import re
from dataclasses import dataclass
from typing import Optional

from telethon import utils
from telethon.tl.types import InputPeerChannel, InputPeerChat, InputPeerUser

POST_LINK_RE = re.compile(
    r"https?://(?:t\.me|telegram\.me)/(?:s/)?(?:c/)?([^/?#]+)/([0-9]+)",
    re.I,
)


@dataclass(frozen=True)
class ResolvedPeer:
    marked_id: int
    entity_id: int
    peer_type: str
    access_hash: Optional[int]
    title: str
    username: Optional[str]
    entity: object
    input_peer: object


def clean_ref(value: str) -> str:
    value = (value or "").strip()
    value = value.strip("<>[]()")
    return value


def extract_chat_ref(value: str) -> str:
    value = clean_ref(value)
    match = POST_LINK_RE.search(value)
    if match:
        ref = match.group(1)
        if ref.isdigit():
            return f"-100{ref}"
        return ref
    if value.startswith("https://t.me/") or value.startswith("http://t.me/"):
        value = value.rstrip("/").split("/")[-1]
    if value.startswith("@"):
        return value
    return value


def entity_info(entity) -> ResolvedPeer:
    peer_type = "unknown"
    access_hash = getattr(entity, "access_hash", None)
    entity_id = int(getattr(entity, "id"))

    name = getattr(entity, "title", None) or getattr(entity, "first_name", None) or "Telegram chat"
    username = getattr(entity, "username", None)

    if entity.__class__.__name__ in {"Channel", "ChannelForbidden"}:
        peer_type = "channel"
    elif entity.__class__.__name__ in {"Chat", "ChatForbidden"}:
        peer_type = "chat"
    elif entity.__class__.__name__ in {"User", "UserEmpty"}:
        peer_type = "user"

    marked_id = utils.get_peer_id(entity)
    input_peer = utils.get_input_peer(entity)
    return ResolvedPeer(marked_id, entity_id, peer_type, access_hash, name, username, entity, input_peer)


def input_peer_from_row(row):
    peer_type = row["peer_type"]
    entity_id = row["entity_id"]
    access_hash = row["access_hash"]
    if entity_id is None:
        return None
    if peer_type == "channel":
        if access_hash is None:
            return None
        return InputPeerChannel(int(entity_id), int(access_hash))
    if peer_type == "chat":
        return InputPeerChat(int(entity_id))
    if peer_type == "user":
        if access_hash is None:
            return None
        return InputPeerUser(int(entity_id), int(access_hash))
    return None


async def resolve_peer(client, ref: str, *, allow_dialog_scan: bool = True) -> ResolvedPeer:
    """Resolve a Telegram chat and return a reusable account-specific InputPeer.

    Numeric IDs can only be resolved when the current Telegram session has
    encountered the peer and therefore knows its access hash. A full dialog
    scan is performed before reporting failure. Usernames and Telegram links
    are resolved directly by Telethon.
    """
    raw = clean_ref(ref)
    value = extract_chat_ref(raw)
    numeric = value.lstrip("-").isdigit()

    candidates = []
    if numeric:
        wanted = int(value)
        candidates.append(wanted)
        # Accept positive channel IDs entered without the Bot-API -100 prefix.
        if not value.startswith("-") and len(value) >= 9:
            candidates.append(int(f"-100{value}"))
    else:
        candidates.append(value)

    # Fast path: use Telethon's cache/input entity machinery first.
    for candidate in candidates:
        try:
            input_peer = await client.get_input_entity(candidate)
            entity = await client.get_entity(input_peer)
            return entity_info(entity)
        except (ValueError, TypeError):
            pass

    if allow_dialog_scan:
        # get_dialogs is intentionally used here because it populates Telethon's
        # entity cache with account-specific access hashes.
        dialogs = await client.get_dialogs(limit=None)
        wanted_ids = set(candidates) if numeric else set()
        for dialog in dialogs:
            try:
                marked = int(dialog.id)
                peer_marked = int(utils.get_peer_id(dialog.entity))
                if numeric and (marked in wanted_ids or peer_marked in wanted_ids):
                    return entity_info(dialog.entity)
            except Exception:
                continue

    hint = (
        "The Telegram user session cannot resolve this numeric ID. The chat "
        "must be accessible to that account and appear in its Telegram dialogs. "
        "Use @username or a t.me link when the chat has one."
        if numeric
        else "The Telegram user session could not resolve this username/link."
    )
    raise ValueError(f"Cannot resolve {raw}. {hint}")
