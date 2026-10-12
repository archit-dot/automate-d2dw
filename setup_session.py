import asyncio
from pathlib import Path
from config import settings
from telethon import TelegramClient

async def main():
    Path(settings.session_name).parent.mkdir(parents=True,exist_ok=True)
    client=TelegramClient(settings.session_name,settings.api_id,settings.api_hash)
    await client.start(phone=settings.phone_number)
    me=await client.get_me()
    print("MTProto session OK")
    print("User ID:",me.id)
    print("Username:",f"@{me.username}" if me.username else "none")
    print("Session:",settings.session_name + ".session")
    await client.disconnect()

if __name__=="__main__":
    asyncio.run(main())
