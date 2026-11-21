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
APP_NAME = os.getenv("APP_NAME", "AstraRoll")

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
# Где открывается Web App (GitHub Pages или этот же домен)
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://yaroslav-commits.github.io/voidplayfiles/")

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8080"))
# Порт именно для API бота. Нужен, если хостинг сам занимает PORT своим сервером (bothost так делает в новых проектах):
# тогда задай API_PORT=8081 (или другой) и в панели хостинга направь домен на этот порт
API_PORT = int(os.getenv("API_PORT") or PORT)
# Папка с сайтом (необязательно). Если указать — бот сам раздаёт сайт на своём домене
# и CORS вообще не нужен: WEBAPP_URL = https://<домен-бота>/
WEBAPP_DIR = os.getenv("WEBAPP_DIR", "")
# С каких сайтов можно ходить в API (GitHub Pages и т.п.). * — с любых
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "https://yaroslav-commits.github.io").split(",") if o.strip()]

# Всё, что нельзя терять, лежит в папке data/ (база, кэш аватарок). При обновлении бота эту папку НЕ трогай
DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data"))
DB_PATH = Path(os.getenv("DB_PATH", DATA_DIR / "voidplay.db"))
AVATAR_DIR = Path(os.getenv("AVATAR_DIR", DATA_DIR / "avatars"))
GIFT_DIR = Path(os.getenv("GIFT_DIR", DATA_DIR / "gift_images"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
# Переезд со старой версии: база лежала рядом с кодом — один раз переносим её в data/
if not DB_PATH.exists() and (ROOT / "voidplay.db").exists() and "DB_PATH" not in os.environ:
    import shutil
    for _ext in ("", "-wal", "-shm"):
        if (ROOT / f"voidplay.db{_ext}").exists():
            shutil.move(str(ROOT / f"voidplay.db{_ext}"), str(DATA_DIR / f"voidplay.db{_ext}"))   # кэш картинок подарков из Telegram

# Канал для задания «подпишись». Бот должен быть админом канала
CHANNEL = os.getenv("CHANNEL", "@astraroll")
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/astraroll")
SUPPORT_URL = os.getenv("SUPPORT_URL", "https://t.me/nersew")
GIFTS_ACCOUNT = os.getenv("GIFTS_ACCOUNT", "@nersew")

# Админы бота (Telegram id), записаны прямо в коде
ADMIN_IDS = [6378471773]

# Экономика (1 звезда в игре = 1 Telegram Star)
# «Первый заработок без вложений»: новичок сразу получает стартовые звёзды
WELCOME_BONUS = int(os.getenv("WELCOME_BONUS", "5"))
SUBSCRIBE_REWARD = int(os.getenv("SUBSCRIBE_REWARD", "10"))
DAILY_REWARD = int(os.getenv("DAILY_REWARD", "3"))
# Ежедневный бонус только тем, кто пополнял хотя бы на эту сумму (0 — всем). Иначе его собирают с фейков каждый день
DAILY_MIN_DEPOSIT = int(os.getenv("DAILY_MIN_DEPOSIT", "50"))
REF_PERCENT = int(os.getenv("REF_PERCENT", "10"))         # % с твоей комиссии, которую приносят приглашённые (см. LEVELS)
REF_PER_FRIEND = int(os.getenv("REF_PER_FRIEND", "0"))     # бонус за первое пополнение друга (звёзды «из воздуха» — лучше 0)
MIN_DEPOSIT = int(os.getenv("MIN_DEPOSIT", "1"))
MIN_WITHDRAW = int(os.getenv("MIN_WITHDRAW", "100"))
HOUSE_EDGE = float(os.getenv("HOUSE_EDGE", "0.10"))       # комиссия апгрейдера
MAX_CHANCE = float(os.getenv("MAX_CHANCE", "0.75"))       # максимальный шанс апгрейда
MIN_BET = int(os.getenv("MIN_BET", "1"))
MAX_BET = int(os.getenv("MAX_BET", "5000"))             # максимальная ставка в Минах (чтобы один игрок не снял всю кассу)
MINES_EDGE = float(os.getenv("MINES_EDGE", "0.03"))       # комиссия в Минах
# Апгрейдер: ставка не больше этой доли от цены цели (0.8 → за подарок в 1000 ⭐ максимум 800 ⭐),
# чтобы не было апгрейдов «x1 и ниже»
UPGRADE_MAX_RATIO = float(os.getenv("UPGRADE_MAX_RATIO", "0.8"))

# ---------- PvP (Рулетка, Огонь и лёд) ----------
# Комиссия берётся только с выигрыша (со ставок проигравших) — победитель никогда не уходит в минус
PVP_FEE = float(os.getenv("PVP_FEE", "0"))   # 0 — в PvP комиссии нет, проект зарабатывает на выводе
PVP_ROUND_SECONDS = int(os.getenv("PVP_ROUND_SECONDS", "20"))   # таймер раунда после входа второго игрока
PVP_SHOW_SECONDS = int(os.getenv("PVP_SHOW_SECONDS", "13"))     # пауза между раундами: ~7 с крутится колесо + показ победителя
PVP_MIN_BET = int(os.getenv("PVP_MIN_BET", "1"))
PVP_MAX_BET = int(os.getenv("PVP_MAX_BET", "50000"))
PVP_MAX_PLAYERS = int(os.getenv("PVP_MAX_PLAYERS", "30"))

# ---------- Цены подарков ----------
# telegram — каталог и цены сами подтягиваются из Telegram (официальный список подарков и их цены в звёздах),
#            список GIFTS ниже нужен только как запасной, пока Telegram не ответил
# manual   — только список GIFTS ниже
# nft      — только настоящие NFT со склада бота (проигранные игроками и лежащие на твоём аккаунте). Рекомендуется
GIFTS_SOURCE = os.getenv("GIFTS_SOURCE", "nft")
SHOP_MARKUP = float(os.getenv("SHOP_MARKUP", "0.05"))            # наценка магазина подарков к цене коллекции
GIFTS_SYNC_MINUTES = int(os.getenv("GIFTS_SYNC_MINUTES", "30"))  # как часто обновлять цены
GIFT_MIN_PRICE = int(os.getenv("GIFT_MIN_PRICE", "0"))           # не показывать подарки дешевле / дороже
GIFT_MAX_PRICE = int(os.getenv("GIFT_MAX_PRICE", "0"))           # 0 — без ограничения

# ---------- Пополнение в TON ----------
# Адрес твоего TON-кошелька, куда приходят пополнения. Пусто — оплата TON выключена
TON_WALLET = os.getenv("TON_WALLET", "UQDSyfkCHWx7UZ40fJ9L2ZCL1tWFH0Kjf0wCau-mXVNTILhz")
TONCENTER_KEY = os.getenv("TONCENTER_KEY", "")           # необязательно: ключ toncenter.com (без него тоже работает)
STAR_USD = float(os.getenv("STAR_USD", "0.01537"))          # сколько $ стоит 1 звезда при пополнении в TON
TON_FEE = float(os.getenv("TON_FEE", "0"))               # твоя комиссия с пополнений в TON (0.05 = 5%)
TON_USD = float(os.getenv("TON_USD", "0"))               # запасной курс TON в $, если биржа не отвечает (0 — нет)
TON_MIN_STARS = int(os.getenv("TON_MIN_STARS", "50"))    # минимальное пополнение в TON, в звёздах

# ---------- Вывод и NFT-подарки ----------
WITHDRAW_FEE = float(os.getenv("WITHDRAW_FEE", "0.03"))            # твоя комиссия с любого вывода (0.03 = 3%)
# Вывод звёзд — только в TON, заявку оплачивает админ со своего кошелька.
# Сколько $ платим за 1 звезду при выводе. ВАЖНО: за звёзды, купленные через Telegram Stars, Telegram отдаёт тебе ~$0.013,
# поэтому ставить больше 0.013 можно, только если пополнение принимается в основном в TON
WITHDRAW_STAR_USD = float(os.getenv("WITHDRAW_STAR_USD", "0.013"))
WITHDRAW_MIN_DEPOSIT = int(os.getenv("WITHDRAW_MIN_DEPOSIT", "50"))   # вывод доступен после пополнений на эту сумму (защита от ферм бонусов)
# Во сколько $ считаем звезду, когда переводим рыночные цены NFT (в TON) в звёзды. Должно совпадать с курсом вывода:
# если поставить больше, NFT в магазине станут дешевле рынка и их будут скупать за Telegram Stars в убыток тебе
NFT_STAR_USD = float(os.getenv("NFT_STAR_USD", str(WITHDRAW_STAR_USD)))
# Пост в канал о крупных выигрышах в PvP: от какого банка (0 — выключено). Бот должен быть админом канала CHANNEL
PVP_ANNOUNCE_MIN = int(os.getenv("PVP_ANNOUNCE_MIN", "500"))
NFT_TRANSFER_STARS = int(os.getenv("NFT_TRANSFER_STARS", "25"))    # сколько звёзд Telegram берёт за передачу NFT (платит игрок)
# Необязательно: адрес JSON с флорами коллекций ({"Plush Pepe": 1234.5, ...} или [{"name":..,"floor":..}]) — цены обновятся сами.
# Без него цены задаются командой /floor
# Авто-цены: ключ Gift Asset (выдают в @giftassetapi). С ключом цены коллекций берутся с маркетов сами (минимальный флор)
GIFTASSET_KEY = os.getenv("GIFTASSET_KEY", "")
NFT_PRICE_FACTOR = float(os.getenv("NFT_PRICE_FACTOR", "0.95"))    # цена в боте = флор × этот множитель (0.95 → на 5% ниже рынка)
# Бесплатный вариант: цены с маркета Portals. Нужна строка авторизации «tma ...» (как получить — команда /portals в боте).
# Её можно задать и прямо из бота: /portals tma ... — тогда перезапуск не нужен
PORTALS_AUTH = os.getenv("PORTALS_AUTH", "")
PORTALS_URL = os.getenv("PORTALS_URL", "https://portal-market.com/api/collections/floors")
NFT_FLOORS_URL = os.getenv("NFT_FLOORS_URL", "https://giftasset.gifts/api/v1/gifts/get_gifts_price_list" if GIFTASSET_KEY else "")
NFT_FLOORS_UNIT = os.getenv("NFT_FLOORS_UNIT", "ton")              # в чём цены в этом JSON: ton или stars
NFT_FLOORS_MINUTES = int(os.getenv("NFT_FLOORS_MINUTES", "30"))

# ---------- Самолётик (краш): игрок против проекта ----------
# Преимущество проекта: в среднем с каждых 1000 ⭐ ставок остаётся CRASH_EDGE × 1000 (0.04 → 40 ⭐). Открыто и честно
CRASH_EDGE = float(os.getenv("CRASH_EDGE", "0.04"))
CRASH_BET_SECONDS = int(os.getenv("CRASH_BET_SECONDS", "8"))      # приём ставок перед взлётом
CRASH_SHOW_SECONDS = int(os.getenv("CRASH_SHOW_SECONDS", "4"))    # пауза после краша
CRASH_RATE = float(os.getenv("CRASH_RATE", "0.0001"))             # скорость роста: x2 через ~7 с, x10 через ~23 с
CRASH_MAX_MULT = float(os.getenv("CRASH_MAX_MULT", "100"))        # дальше самолётик не летит
CRASH_MAX_BET = int(os.getenv("CRASH_MAX_BET", "1000"))           # максимальная ставка
CRASH_MAX_WIN = int(os.getenv("CRASH_MAX_WIN", "20000"))          # потолок выплаты одной ставки

# Автокопии базы: раз в сутки в data/backups (хранится BACKUP_KEEP штук) + файл приходит админу в Telegram
BACKUP_KEEP = int(os.getenv("BACKUP_KEEP", "14"))
BACKUP_TO_ADMIN = os.getenv("BACKUP_TO_ADMIN", "1") == "1"

# Игры, которые можно открывать/закрывать из бота: /games
GAMES = {"upgrade": "Апгрейдер", "mines": "Мины", "crash": "Самолётик", "roulette": "PvP Рулетка", "fireice": "PvP Огонь и лёд"}

# Разработка: принимать запросы без подписи Telegram (только для локальных тестов!)
DEV_MODE = os.getenv("DEV_MODE", "0") == "1"

# Запасной ручной каталог (только для GIFTS_SOURCE=manual). В обычном режиме подарки — настоящие NFT со склада бота
GIFTS: list[tuple[str, str, int]] = []

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
    {"id": "bets5", "title": "Сделай 5 ставок", "text": "Сыграй 5 раз в любой игре", "kind": "bets", "goal": 5, "reward": 5},
    {"id": "dep1", "title": "Пополни баланс", "text": "Пополни баланс на 50 ⭐ или больше", "kind": "deposit_sum", "goal": 50, "reward": 10},
    {"id": "ref3", "title": "Пригласи друзей", "text": "Пригласи 3 друзей, которые пополнят баланс", "kind": "referrals", "goal": 3, "reward": 30},
]

# Уровни рефералки: с какого оборота депозитов рефералов и какой % ОТ КОМИССИИ ПРОЕКТА получает пригласивший.
# Платится только из уже заработанного (с комиссии на вывод), поэтому в минус увести не может
LEVELS = [
    {"n": 1, "pct": 10, "from": 0},
    {"n": 2, "pct": 15, "from": 1000},
    {"n": 3, "pct": 20, "from": 5000},
    {"n": 4, "pct": 25, "from": 15000},
    {"n": 5, "pct": 30, "from": 50000},
    {"n": 6, "pct": 40, "from": 150000},
]


def level_for(ref_deposits: int) -> dict:
    lvl = LEVELS[0]
    for L in LEVELS:
        if ref_deposits >= L["from"]:
            lvl = L
    return lvl
