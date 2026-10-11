"""Каталог NFT, задания, уровни рефералки. Цены — в звёздах (1 ⭐ = 1 Telegram Star).

Названия и цены придуманы — меняй как хочешь. Картинки лежат во фронтенде:
docs/assets/img/nft/<id>.png (их выгружает tools/figma_export.py).
"""
from __future__ import annotations


# id, название, цена в звёздах
GIFTS = [
    ("pepe", "Plush Pepe", 4999),
    ("gift-126", "NFT #126", 3500),
    ("gift-128", "NFT #128", 2400),
    ("gift-127", "NFT #127", 1800),
    ("gift-129", "NFT #129", 1500),
    ("gift-004", "NFT #004", 1200),
    ("gift-074", "NFT #074", 999),
    ("gift-090", "NFT #090", 850),
    ("gift-020", "NFT #020", 700),
    ("gift-065", "NFT #065", 560),
    ("gift-023", "NFT #023", 450),
    ("gift-047", "NFT #047", 350),
    ("gift-132", "NFT #132", 280),
    ("gift-103", "NFT #103", 200),
    ("gift-120", "NFT #120", 150),
]

CATALOG = [{"id": i, "name": n, "price": p, "img": f"assets/img/nft/{i}.png"} for i, n, p in GIFTS]
BY_ID = {g["id"]: g for g in CATALOG}

# Выгодные пакеты пополнения: сколько звёзд платишь → бонус сверху (0 — без бонуса)
PACKS = [
    {"stars": 100, "bonus": 0},
    {"stars": 500, "bonus": 0},
    {"stars": 1000, "bonus": 0},
]

# Задания. kind — чем меряется прогресс (см. game.task_progress)
TASKS = [
    {"id": "bets5", "title": "Сделай 5 ставок", "text": "Сыграй 5 раз в апгрейдере", "kind": "bets", "goal": 5, "reward": 10},
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
