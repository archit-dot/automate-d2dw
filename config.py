import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

@dataclass(frozen=True)
class Settings:
    bot_token: str = os.getenv("BOT_TOKEN", "").strip()
    owner_id: int = int(os.getenv("OWNER_ID", "0") or 0)
    api_id: int = int(os.getenv("API_ID", "0") or 0)
    api_hash: str = os.getenv("API_HASH", "").strip()
    phone_number: str = os.getenv("PHONE_NUMBER", "").strip()
    session_name: str = os.getenv("SESSION_NAME", "sessions/telepost_user").strip()
    diskwala_bot: str = os.getenv("DISKWALA_BOT", "@DW2DW_LinkConverterBot").strip()
    timezone: str = os.getenv("TIMEZONE", "Asia/Kolkata").strip()
    db_path: str = os.getenv("DB_PATH", "data/telepost.db").strip()

settings = Settings()

def validate():
    missing = []
    if not settings.bot_token: missing.append("BOT_TOKEN")
    if not settings.owner_id: missing.append("OWNER_ID")
    if not settings.api_id: missing.append("API_ID")
    if not settings.api_hash: missing.append("API_HASH")
    if missing:
        raise RuntimeError("Missing .env values: " + ", ".join(missing))
