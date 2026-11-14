"""Void Play — всё, что ходит наружу и работает в фоне:

  • цены и картинки подарков из Telegram (обновляются сами)
  • курс TON и проверка входящих переводов TON
  • таймер PvP-раундов
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import aiohttp

import config
from game import Game
from pvp import PVP_GAMES

log = logging.getLogger("voidplay.services")

# Названия стандартных подарков Telegram (в Bot API у подарка нет имени — только эмодзи стикера)
EMOJI_NAMES = {
    "🧸": "Мишка", "💝": "Сердце", "🎁": "Подарок", "🌹": "Роза", "🎂": "Торт", "💐": "Букет", "🚀": "Ракета",
    "🏆": "Кубок", "💍": "Кольцо", "💎": "Алмаз", "🍾": "Шампанское", "🎄": "Ёлка", "🎃": "Тыква", "🕯": "Свеча",
    "🍭": "Леденец", "🎅": "Санта", "⭐": "Звезда", "🌟": "Звезда", "🧁": "Кекс", "🎈": "Шарик", "🍀": "Клевер",
    "🐸": "Лягушка", "🎩": "Шляпа", "👑": "Корона", "🔮": "Шар", "🧪": "Зелье", "🪄": "Палочка", "🎀": "Бант",
}


class Services:
    def __init__(self, game: Game, bot=None):
        self.game = game
        self.bot = bot
        self.http: aiohttp.ClientSession | None = None
        self.tasks: list[asyncio.Task] = []
        self._ton_usd: tuple[float, float] = (0.0, 0.0)   # (курс, когда получен)
        self._ton_checked = 0.0
        self._ton_lock = asyncio.Lock()

    async def start(self):
        self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12))
        self.tasks = [asyncio.create_task(self._loop(self._pvp_step, 1, "pvp"))]
        if config.GIFTS_SOURCE == "telegram" and self.bot:
            self.tasks.append(asyncio.create_task(self._loop(self.sync_gifts, config.GIFTS_SYNC_MINUTES * 60, "gifts")))
        if config.TON_WALLET:
            self.tasks.append(asyncio.create_task(self._loop(self.refresh_ton_rate, 300, "ton rate")))
            self.tasks.append(asyncio.create_task(self._loop(self._ton_step, 15, "ton")))

    async def stop(self):
        for t in self.tasks:
            t.cancel()
        if self.http:
            await self.http.close()

    async def _loop(self, fn, every: float, name: str):
        while True:
            try:
                await fn()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — фоновая задача не должна умирать
                log.warning("%s: %s", name, e)
            await asyncio.sleep(every)

    # =====================================================================
    #  PvP: разыгрываем раунды, даже если никто не смотрит на экран
    # =====================================================================
    async def _pvp_step(self):
        for g in PVP_GAMES:
            self.game.pvp_tick(g)

    # =====================================================================
    #  Подарки: каталог и цены из Telegram
    # =====================================================================
    async def sync_gifts(self) -> int:
        """Официальный список подарков Telegram и их цены в звёздах → каталог игры."""
        if not self.bot:
            return 0
        res = await self.bot.get_available_gifts()
        seen: dict[str, int] = {}
        gifts = []
        for g in res.gifts:
            if not g.star_count:
                continue
            st = g.sticker
            emoji = (st.emoji or "🎁") if st else "🎁"
            name = EMOJI_NAMES.get(emoji) or EMOJI_NAMES.get(emoji.replace("️", "")) or f"Подарок {emoji}"
            seen[name] = seen.get(name, 0) + 1
            thumb = st.thumbnail.file_id if st and st.thumbnail else (st.file_id if st and not st.is_animated and not st.is_video else None)
            gifts.append({"id": f"tg-{g.id}", "name": name, "price": g.star_count, "emoji": emoji, "file_id": thumb,
                          "limited": g.total_count is not None})
        # одинаковые имена (несколько подарков с одним эмодзи) различаем ценой
        for g in gifts:
            if seen[g["name"]] > 1:
                g["name"] = f"{g['name']} {g['price']}"
        n = self.game.set_gifts(gifts)
        log.info("подарки из Telegram: %s шт.", n)
        return n

    async def gift_image(self, gift_id: str) -> Path | None:
        """Картинка подарка (превью стикера): скачиваем один раз и держим на диске."""
        if not gift_id.replace("-", "").isalnum():
            return None
        path = config.GIFT_DIR / f"{gift_id}.webp"
        if path.exists():
            return path
        row = self.game.db.one("SELECT file_id FROM gift_prices WHERE id=?", (gift_id,))
        if not row or not row["file_id"] or not self.bot:
            return None
        config.GIFT_DIR.mkdir(parents=True, exist_ok=True)
        f = await self.bot.get_file(row["file_id"])
        await self.bot.download_file(f.file_path, destination=path)
        return path

    # =====================================================================
    #  TON: курс и входящие переводы
    # =====================================================================
    async def _get_json(self, url: str, **params) -> dict:
        async with self.http.get(url, params=params) as r:
            r.raise_for_status()
            return await r.json(content_type=None)

    async def refresh_ton_rate(self):
        """Обновляет курс TON в фоне (раз в 5 минут), чтобы запросы игроков никогда его не ждали."""
        for fetch in (self._rate_tonapi, self._rate_coingecko):
            try:
                v = float(await fetch())
                if v > 0:
                    self._ton_usd = (v, time.time())
                    return
            except Exception as e:  # noqa: BLE001
                log.debug("курс TON: %s", e)
        log.warning("курс TON не получен — беру последний известный или TON_USD из настроек")

    async def ton_usd(self) -> float:
        """Курс TON в $: свежий с биржи (не старше часа), иначе запасной config.TON_USD (0 — оплата TON недоступна)."""
        rate, at = self._ton_usd
        return rate if rate and time.time() - at < 3600 else config.TON_USD

    async def _rate_tonapi(self):
        d = await self._get_json("https://tonapi.io/v2/rates", tokens="ton", currencies="usd")
        return d["rates"]["TON"]["prices"]["USD"]

    async def _rate_coingecko(self):
        d = await self._get_json("https://api.coingecko.com/api/v3/simple/price", ids="the-open-network", vs_currencies="usd")
        return d["the-open-network"]["usd"]

    async def stars_per_ton(self) -> float:
        """Сколько звёзд даём за 1 TON (с учётом комиссии TON_FEE). 0 — курс неизвестен, оплата TON недоступна."""
        if not config.TON_WALLET:
            return 0.0
        usd = await self.ton_usd()
        return round(usd / config.STAR_USD * (1 - config.TON_FEE), 2) if usd > 0 else 0.0

    async def ton_incoming(self) -> list[dict]:
        """Последние входящие переводы на кошелёк: [{hash, nano, comment}]."""
        params = {"address": config.TON_WALLET, "limit": 50}
        if config.TONCENTER_KEY:
            params["api_key"] = config.TONCENTER_KEY
        d = await self._get_json("https://toncenter.com/api/v2/getTransactions", **params)
        out = []
        for tx in d.get("result", []):
            m = tx.get("in_msg") or {}
            nano = int(m.get("value") or 0)
            comment = (m.get("message") or "").strip()
            if nano > 0 and m.get("source") and comment:
                out.append({"hash": tx["transaction_id"]["hash"], "nano": nano, "comment": comment})
        return out

    async def check_ton(self, force: bool = False) -> int:
        """Сверить ожидающие счета с входящими переводами. Возвращает, сколько пополнений зачислено."""
        if not config.TON_WALLET:
            return 0
        async with self._ton_lock:
            if not force and time.time() - self._ton_checked < 5:   # не чаще раза в 5 секунд
                return 0
            self._ton_checked = time.time()
            waiting = {d["comment"] for d in self.game.ton_pending()}
            if not waiting:
                return 0
            n = 0
            for tx in await self.ton_incoming():
                comment = tx["comment"].upper()
                if comment not in waiting:
                    continue
                res = self.game.ton_credit(comment, tx["nano"], tx["hash"])
                if res:
                    n += 1
                    uid, stars = res
                    log.info("TON: +%s ⭐ игроку %s (%s)", stars, uid, comment)
                    await self._notify(uid, f"✅ Зачислено <b>{stars} ⭐</b> — оплата TON получена")
            return n

    async def _ton_step(self):
        await self.check_ton()

    async def _notify(self, uid: int, text: str):
        if not self.bot:
            return
        try:
            await self.bot.send_message(uid, text)
        except Exception:  # noqa: BLE001 — игрок мог не запускать бота
            pass
