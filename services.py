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
        if self.bot:
            self.tasks.append(asyncio.create_task(self._loop(self.sync_stock, 300, "nft stock")))
        if config.NFT_FLOORS_URL:
            self.tasks.append(asyncio.create_task(self._loop(self.sync_floors, config.NFT_FLOORS_MINUTES * 60, "nft floors")))
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
    async def sync_stock(self) -> int:
        """Все NFT с подключённого аккаунта, которых ещё нет в боте, → на склад."""
        conn = self.game.setting("biz:conn")
        if not conn or not self.bot:
            return 0
        added, offset = 0, None
        for _ in range(20):
            res = await self.bot.get_business_account_gifts(business_connection_id=conn, offset=offset, limit=100)
            for og in res.gifts:
                g = getattr(og, "gift", None)
                if getattr(og, "type", "") != "unique" or not g or not og.owned_gift_id:
                    continue
                added += self.game.nft_stock(g.name, g.base_name, g.number, g.model.name if g.model else "",
                                             g.backdrop.name if g.backdrop else "", og.owned_gift_id, conn)
            offset = res.next_offset
            if not offset:
                break
        if added:
            log.info("склад: добавлено NFT с аккаунта: %s", added)
        return added

    async def sync_floors(self) -> int:
        """Флоры NFT-коллекций из NFT_FLOORS_URL → цены в звёздах."""
        k_ = config.GIFTASSET_KEY
        hdr = {"X-API-Key": k_, "x-api-token": k_, "Authorization": f"Bearer {k_}"} if k_ else {}
        async with self.http.get(config.NFT_FLOORS_URL, headers=hdr) as r:
            if r.status in (401, 403):
                raise RuntimeError("источник цен не принял ключ (GIFTASSET_KEY)")
            r.raise_for_status()
            d = await r.json(content_type=None)
        if isinstance(d, dict) and len(d) == 1 and isinstance(next(iter(d.values())), (dict, list)):
            d = next(iter(d.values()))   # {"data": {...}}
        items = d.items() if isinstance(d, dict) else [(x.get("name") or x.get("collection") or x.get("title"), x) for x in d if isinstance(x, dict)]
        k = 1.0
        if config.NFT_FLOORS_UNIT == "ton":
            if not await self.ton_usd():
                await self.refresh_ton_rate()
            k = await self.ton_usd() / config.STAR_USD
        n = 0
        for name, v in items:
            if isinstance(v, dict):
                nums = [x for x in v.values() if isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0]
                # цены по маркетам ({"portals": 73.4, "tonnel": 79, ...}) → берём минимальную
                v = next((v[f] for f in ("floor", "floor_price", "floorPrice", "price", "min_price") if v.get(f)), min(nums) if nums else 0)
            try:
                price = int(float(v) * k * config.NFT_PRICE_FACTOR)
            except (TypeError, ValueError):
                continue
            if name and price > 0:
                self.game.set_floor(name, price)
                n += 1
        log.info("цены NFT обновлены: %s коллекций", n)
        return n

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
