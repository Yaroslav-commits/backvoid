"""Настройки из переменных окружения (.env или панель хостинга)."""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # python-dotenv не обязателен, если переменные заданы в панели хостинга
    pass

ROOT = Path(__file__).resolve().parent.parent


def _ints(v: str) -> list[int]:
    return [int(x) for x in v.replace(" ", "").split(",") if x.strip().lstrip("-").isdigit()]


BOT_TOKEN = os.getenv("BOT_TOKEN", "")
# Где открывается Web App (GitHub Pages или этот же домен)
WEBAPP_URL = os.getenv("WEBAPP_URL", "")

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8080"))
# Раздавать фронтенд из docs/ с этого же сервера (https://voidplay.bothost.tech/)
SERVE_STATIC = os.getenv("SERVE_STATIC", "0") == "1"
WEBAPP_DIR = Path(os.getenv("WEBAPP_DIR", ROOT / "docs")).resolve()
# С каких сайтов можно ходить в API (GitHub Pages и т.п.). * — с любых
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]

DB_PATH = Path(os.getenv("DB_PATH", ROOT / "voidplay.db"))
AVATAR_DIR = Path(os.getenv("AVATAR_DIR", ROOT / "avatars"))

# Канал для задания «подпишись». Бот должен быть админом канала
CHANNEL = os.getenv("CHANNEL", "@testkanalbot")
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/testkanalbot")
SUPPORT_URL = os.getenv("SUPPORT_URL", "https://t.me/Hanoru")
GIFTS_ACCOUNT = os.getenv("GIFTS_ACCOUNT", "@voidplay")

ADMIN_IDS = _ints(os.getenv("ADMIN_IDS", "6378471773", "6883630937"))

# Экономика (1 звезда в игре = 1 Telegram Star)
SUBSCRIBE_REWARD = int(os.getenv("SUBSCRIBE_REWARD", "15"))
DAILY_REWARD = int(os.getenv("DAILY_REWARD", "5"))
REF_PERCENT = int(os.getenv("REF_PERCENT", "5"))          # % с пополнений приглашённых
REF_PER_FRIEND = int(os.getenv("REF_PER_FRIEND", "10"))    # бонус за первое пополнение друга
MIN_DEPOSIT = int(os.getenv("MIN_DEPOSIT", "1"))
MIN_WITHDRAW = int(os.getenv("MIN_WITHDRAW", "100"))
HOUSE_EDGE = float(os.getenv("HOUSE_EDGE", "0.10"))       # комиссия апгрейдера
MAX_CHANCE = float(os.getenv("MAX_CHANCE", "0.75"))       # максимальный шанс апгрейда
MIN_BET = int(os.getenv("MIN_BET", "1"))

# Разработка: принимать запросы без подписи Telegram (только для локальных тестов!)
DEV_MODE = os.getenv("DEV_MODE", "0") == "1"
