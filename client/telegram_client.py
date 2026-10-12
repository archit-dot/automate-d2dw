from pathlib import Path

from telethon import TelegramClient

from config import settings
from database import repo
from .entity import ResolvedPeer, input_peer_from_row, resolve_peer


class TelegramService:
    def __init__(self):
        Path(settings.session_name).parent.mkdir(parents=True, exist_ok=True)
        self.client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash)

    async def start(self):
        if not self.client.is_connected():
            await self.client.connect()
        if not await self.client.is_user_authorized():
            raise RuntimeError(
                "Telegram session is not authorized. Run setup_session.py once to log in, then start Telepost."
            )
        me = await self.client.get_me()
        if getattr(me, "bot", False):
            raise RuntimeError("API_ID/API_HASH session must be a normal Telegram user, not a bot account.")

    async def stop(self):
        if self.client.is_connected():
            await self.client.disconnect()

    async def resolve(self, ref: str) -> ResolvedPeer:
        return await resolve_peer(self.client, ref)

    async def resolve_stored(self, row) -> ResolvedPeer:
        """Use the account-specific stored access hash when available."""
        stored = input_peer_from_row(row)
        if stored is not None:
            try:
                entity = await self.client.get_entity(stored)
                from .entity import entity_info
                return entity_info(entity)
            except Exception:
                pass

        ref = row["username"] or str(row["chat_id"])
        return await self.resolve(ref)

    async def refresh_configured_entities(self):
        """Re-resolve all saved peers at startup and refresh their hashes.

        Failure of one configured chat does not prevent the rest of Telepost
        from starting. The individual row is disabled until it can be resolved.
        """
        failures = []
        for row in repo.sources():
            try:
                resolved = await self.resolve_stored(row)
                repo.update_source_peer(row["id"], resolved)
                repo.set_source_enabled(row["id"], True)
            except Exception as exc:
                repo.set_source_enabled(row["id"], False)
                failures.append(("source", row["id"], row["title"], str(exc)))
        for row in repo.destinations():
            try:
                resolved = await self.resolve_stored(row)
                repo.update_destination_peer(row["id"], resolved)
                repo.set_destination_enabled(row["id"], True)
            except Exception as exc:
                repo.set_destination_enabled(row["id"], False)
                failures.append(("destination", row["id"], row["title"], str(exc)))
        return failures

    async def dialogs(self, limit=50):
        dialogs = await self.client.get_dialogs(limit=limit)
        result = []
        for dialog in dialogs:
            entity = dialog.entity
            if not getattr(entity, "title", None):
                continue
            from .entity import entity_info
            try:
                result.append((dialog, entity_info(entity)))
            except Exception:
                continue
        return result
