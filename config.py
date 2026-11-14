"""Void Play — настройки и каталог.

Всё, что обычно меняют: токен, ссылки, админы, экономика, цены NFT, задания, уровни рефералки.
Значения можно переопределить переменными окружения (.env или панель хостинга).
"""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # python-dotenv не обязателен, если переменные заданы в панели хостинга
    pass

ROOT = Path(__file__).resolve().parent


def _ints(v: str) -> list[int]:
    return [int(x) for x in v.replace(" ", "").split(",") if x.strip().lstrip("-").isdigit()]


# Название проекта — меняется в одном месте (бот, сайт берёт его с сервера)
APP_NAME = os.getenv("APP_NAME", "Void Play")

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
# Где открывается Web App (GitHub Pages или этот же домен)
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://yaroslav-commits.github.io/voidplayfiles/")

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8080"))
# Папка с сайтом (необязательно). Если указать — бот сам раздаёт сайт на своём домене
# и CORS вообще не нужен: WEBAPP_URL = https://<домен-бота>/
WEBAPP_DIR = os.getenv("WEBAPP_DIR", "")
# С каких сайтов можно ходить в API (GitHub Pages и т.п.). * — с любых
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]

DB_PATH = Path(os.getenv("DB_PATH", ROOT / "voidplay.db"))
AVATAR_DIR = Path(os.getenv("AVATAR_DIR", ROOT / "avatars"))
GIFT_DIR = Path(os.getenv("GIFT_DIR", ROOT / "gift_images"))   # кэш картинок подарков из Telegram

# Канал для задания «подпишись». Бот должен быть админом канала
CHANNEL = os.getenv("CHANNEL", "@testkanalbot")
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/testkanalbot")
SUPPORT_URL = os.getenv("SUPPORT_URL", "https://t.me/nersew")
GIFTS_ACCOUNT = os.getenv("GIFTS_ACCOUNT", "@nersew")

# Админы бота (Telegram id), записаны прямо в коде
ADMIN_IDS = [6378471773]

# Экономика (1 звезда в игре = 1 Telegram Star)
# «Первый заработок без вложений»: новичок сразу получает стартовые звёзды
WELCOME_BONUS = int(os.getenv("WELCOME_BONUS", "10"))
SUBSCRIBE_REWARD = int(os.getenv("SUBSCRIBE_REWARD", "15"))
DAILY_REWARD = int(os.getenv("DAILY_REWARD", "2"))
REF_PERCENT = int(os.getenv("REF_PERCENT", "5"))          # % с пополнений приглашённых
REF_PER_FRIEND = int(os.getenv("REF_PER_FRIEND", "10"))    # бонус за первое пополнение друга
MIN_DEPOSIT = int(os.getenv("MIN_DEPOSIT", "1"))
MIN_WITHDRAW = int(os.getenv("MIN_WITHDRAW", "100"))
HOUSE_EDGE = float(os.getenv("HOUSE_EDGE", "0.10"))       # комиссия апгрейдера
MAX_CHANCE = float(os.getenv("MAX_CHANCE", "0.75"))       # максимальный шанс апгрейда
MIN_BET = int(os.getenv("MIN_BET", "1"))
MINES_EDGE = float(os.getenv("MINES_EDGE", "0.03"))       # комиссия в Минах
# Апгрейдер: ставка не больше этой доли от цены цели (0.8 → за подарок в 1000 ⭐ максимум 800 ⭐),
# чтобы не было апгрейдов «x1 и ниже»
UPGRADE_MAX_RATIO = float(os.getenv("UPGRADE_MAX_RATIO", "0.8"))

# ---------- PvP (Рулетка, Огонь и лёд) ----------
# Комиссия берётся только с выигрыша (со ставок проигравших) — победитель никогда не уходит в минус
PVP_FEE = float(os.getenv("PVP_FEE", "0.10"))
PVP_ROUND_SECONDS = int(os.getenv("PVP_ROUND_SECONDS", "20"))   # таймер раунда после входа второго игрока
PVP_SHOW_SECONDS = int(os.getenv("PVP_SHOW_SECONDS", "9"))      # сколько показываем результат до нового раунда
PVP_MIN_BET = int(os.getenv("PVP_MIN_BET", "1"))
PVP_MAX_BET = int(os.getenv("PVP_MAX_BET", "50000"))
PVP_MAX_PLAYERS = int(os.getenv("PVP_MAX_PLAYERS", "30"))

# ---------- Цены подарков ----------
# telegram — каталог и цены сами подтягиваются из Telegram (официальный список подарков и их цены в звёздах),
#            список GIFTS ниже нужен только как запасной, пока Telegram не ответил
# manual   — только список GIFTS ниже
GIFTS_SOURCE = os.getenv("GIFTS_SOURCE", "telegram")
GIFTS_SYNC_MINUTES = int(os.getenv("GIFTS_SYNC_MINUTES", "30"))  # как часто обновлять цены
GIFT_MIN_PRICE = int(os.getenv("GIFT_MIN_PRICE", "0"))           # не показывать подарки дешевле / дороже
GIFT_MAX_PRICE = int(os.getenv("GIFT_MAX_PRICE", "0"))           # 0 — без ограничения

# ---------- Пополнение в TON ----------
# Адрес твоего TON-кошелька, куда приходят пополнения. Пусто — оплата TON выключена
TON_WALLET = os.getenv("TON_WALLET", "UQDSyfkCHWx7UZ40fJ9L2ZCL1tWFH0Kjf0wCau-mXVNTILhz")
TONCENTER_KEY = os.getenv("TONCENTER_KEY", "")           # необязательно: ключ toncenter.com (без него тоже работает)
STAR_USD = float(os.getenv("STAR_USD", "0.02"))          # сколько $ стоит 1 звезда при пополнении в TON
TON_FEE = float(os.getenv("TON_FEE", "0"))               # твоя комиссия с пополнений в TON (0.05 = 5%)
TON_USD = float(os.getenv("TON_USD", "0"))               # запасной курс TON в $, если биржа не отвечает (0 — нет)
TON_MIN_STARS = int(os.getenv("TON_MIN_STARS", "50"))    # минимальное пополнение в TON, в звёздах

# ---------- Вывод и NFT-подарки ----------
WITHDRAW_FEE = float(os.getenv("WITHDRAW_FEE", "0.03"))            # твоя комиссия с любого вывода (0.03 = 3%)
NFT_TRANSFER_STARS = int(os.getenv("NFT_TRANSFER_STARS", "25"))    # сколько звёзд Telegram берёт за передачу NFT (платит игрок)
# Необязательно: адрес JSON с флорами коллекций ({"Plush Pepe": 1234.5, ...} или [{"name":..,"floor":..}]) — цены обновятся сами.
# Без него цены задаются командой /floor
NFT_FLOORS_URL = os.getenv("NFT_FLOORS_URL", "")
NFT_FLOORS_UNIT = os.getenv("NFT_FLOORS_UNIT", "ton")              # в чём цены в этом JSON: ton или stars
NFT_FLOORS_MINUTES = int(os.getenv("NFT_FLOORS_MINUTES", "30"))

# Игры, которые можно открывать/закрывать из бота: /games
GAMES = {"upgrade": "Апгрейдер", "mines": "Мины", "roulette": "PvP Рулетка", "fireice": "PvP Огонь и лёд"}

# Разработка: принимать запросы без подписи Telegram (только для локальных тестов!)
DEV_MODE = os.getenv("DEV_MODE", "0") == "1"

GIFTS = [
    ("pepe", "Plush Pepe", 194999),
    ("gift-126", "NFT #126", 6500),
    ("gift-128", "NFT #128", 4400),
    ("gift-127", "NFT #127", 2800),
    ("gift-129", "NFT #129", 2500),
    ("gift-004", "NFT #004", 2200),
    ("gift-074", "NFT #074", 1999),
    ("gift-090", "NFT #090", 1350),
    ("gift-020", "NFT #020", 1200),
    ("gift-065", "NFT #065", 1060),
    ("gift-023", "NFT #023", 950),
    ("gift-047", "NFT #047", 750),
    ("gift-132", "NFT #132", 680),
    ("gift-103", "NFT #103", 500),
    ("gift-120", "NFT #120", 450),
]

CATALOG = [{"id": i, "name": n, "price": p, "img": f"img/gifts/{i}.webp"} for i, n, p in GIFTS]
BY_ID = {g["id"]: g for g in CATALOG}

# Выгодные пакеты пополнения: сколько звёзд платишь → бонус сверху (0 — без бонуса)
PACKS = [
    {"stars": 100, "bonus": 0},
    {"stars": 500, "bonus": 0},
    {"stars": 1000, "bonus": 0},
]

# Задания. kind — чем меряется прогресс (см. game.task_progress)
TASKS = [
    {"id": "bets5", "title": "Сделай 5 ставок", "text": "Сыграй 5 раз в любой игре — хоть по 1 ⭐", "kind": "bets", "goal": 5, "reward": 10},
    {"id": "sub", "title": "Подписка", "text": "Подпишись на канал Void Play", "kind": "subscribed", "goal": 1, "reward": 5},
    {"id": "dep1", "title": "Первое пополнение", "text": "Пополни баланс на любую сумму", "kind": "deposits", "goal": 1, "reward": 20},
    {"id": "ref3", "title": "Пригласи друзей", "text": "Пригласи 3 друзей по своей ссылке", "kind": "referrals", "goal": 3, "reward": 50},
]

# Уровни рефералки: с какого оборота депозитов рефералов и какой %
LEVELS = [
    {"n": 1, "pct": 5, "from": 0},
    {"n": 2, "pct": 7, "from": 1000},
    {"n": 3, "pct": 10, "from": 5000},
    {"n": 4, "pct": 12, "from": 15000},
    {"n": 5, "pct": 15, "from": 50000},
    {"n": 6, "pct": 20, "from": 150000},
]


def level_for(ref_deposits: int) -> dict:
    lvl = LEVELS[0]
    for L in LEVELS:
        if ref_deposits >= L["from"]:
            lvl = L
    return lvl
