from dataclasses import dataclass
from dotenv import load_dotenv
import os

load_dotenv()


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    anthropic_api_key: str
    db_path: str
    allowed_user_id: int | None


settings = Settings(
    telegram_token=os.environ["TELEGRAM_BOT_TOKEN"],
    anthropic_api_key=os.environ["ANTHROPIC_API_KEY"],
    db_path=os.getenv("DB_PATH", "data/bot.db"),
    allowed_user_id=int(v) if (v := os.getenv("ALLOWED_USER_ID")) else None,
)
