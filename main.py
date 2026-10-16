"""
Void Play — бот (aiogram 3) + API для Web App в одном процессе.

  • /start, кнопка «Играть», оплата Telegram Stars, админ-команды
  • HTTP API на PORT (8080) для сайта: /api/*

Файлы:  main.py (бот + API + запуск) · game.py (база и логика) · config.py (настройки и каталог)
Запуск: pip install -r requirements.txt  →  python main.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    MenuButtonWebApp,
    Message,
    PreCheckoutQuery,
    WebAppInfo,
)
from aiohttp import web

import config
from config import BY_ID, PACKS
from game import DB, AuthError, Game, GameError, verify_init_data

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("voidplay")


# =====================================================================
#  Бот: /start, оплата Stars, админ-команды
# =====================================================================


def play_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎮 Играть", web_app=WebAppInfo(url=config.WEBAPP_URL))],
        [InlineKeyboardButton(text="📢 Канал", url=config.CHANNEL_URL)],
    ])


def build_router(game: Game) -> Router:
    r = Router()

    @r.message(CommandStart())
    async def start(message: Message, command: CommandObject):
        u = message.from_user
        _, is_new = game.upsert_user(u.model_dump(), command.args)
        if is_new and command.args:
            log.info("new user %s by %s", u.id, command.args)
        await message.answer(
            f'Привет, <b>{u.first_name}</b>! <tg-emoji emoji-id="5199885118214255386">👋</tg-emoji>\n\n'
            "Добро пожаловать в <b>Void Play</b> — апгрейдер, кейсы, мины и другие игры за звёзды и подарки.\n\n"
            'Жми «Играть» <tg-emoji emoji-id="5231102735817918643">👇</tg-emoji>',
            reply_markup=play_kb(),
        )

    # ---------- оплата Telegram Stars ----------
    @r.pre_checkout_query()
    async def pre_checkout(q: PreCheckoutQuery):
        ok = q.invoice_payload.startswith("dep:") and q.currency == "XTR"
        await q.answer(ok=ok, error_message=None if ok else "Платёж не распознан")

    @r.message(F.successful_payment)
    async def paid(message: Message):
        p = message.successful_payment
        try:
            _, uid, amount, bonus = p.invoice_payload.split(":")
            uid, amount, bonus = int(uid), int(amount), int(bonus)
        except ValueError:
            log.error("bad payload %s", p.invoice_payload)
            return
        if p.total_amount != amount:
            log.warning("amount mismatch %s != %s", p.total_amount, amount)
            amount, bonus = p.total_amount, 0
        if game.credit_deposit(uid, amount, p.telegram_payment_charge_id, bonus):
            await message.answer(f"✅ Зачислено <b>{amount + bonus} ⭐</b> на баланс Void Play", reply_markup=play_kb())

    # ---------- админка ----------
    def is_admin(m: Message) -> bool:
        return bool(m.from_user and m.from_user.id in config.ADMIN_IDS)

    @r.message(Command("addstars"))
    async def add_stars(message: Message, command: CommandObject):
        """/addstars <user_id> <кол-во>  (минус — списать)"""
        if not is_admin(message):
            return
        try:
            uid, n = (int(x) for x in (command.args or "").split())
            game.admin_add_balance(uid, n)
            await message.answer(f"Готово. Баланс {uid}: {game.balance(uid)} ⭐")
        except (ValueError, GameError) as e:
            await message.answer(f"Ошибка: {e}\nФормат: /addstars user_id кол-во")

    @r.message(Command("givegift"))
    async def give_gift(message: Message, command: CommandObject):
        """/givegift <user_id> <gift_id> — положить NFT в инвентарь (например, после получения подарка)"""
        if not is_admin(message):
            return
        try:
            uid, gift = (command.args or "").split()
            game.admin_give_gift(int(uid), gift)
            await message.answer(f"Готово: {BY_ID[gift]['name']} → {uid}")
        except (ValueError, GameError, KeyError) as e:
            await message.answer(f"Ошибка: {e}\nФормат: /givegift user_id gift_id\nПодарки: {', '.join(BY_ID)}")

    @r.message(Command("promo"))
    async def promo(message: Message, command: CommandObject):
        """/promo <КОД> <награда> <кол-во активаций>"""
        if not is_admin(message):
            return
        try:
            code, reward, uses = (command.args or "").split()
            game.admin_promo(code, int(reward), int(uses))
            await message.answer(f"Промокод {code.upper()}: +{reward} ⭐, активаций {uses}")
        except ValueError:
            await message.answer("Формат: /promo КОД награда активаций")

    @r.message(Command("stats"))
    async def stats(message: Message):
        if not is_admin(message):
            return
        db = game.db
        users = db.one("SELECT COUNT(*) c FROM users")["c"]
        dep = db.one("SELECT COALESCE(SUM(amount),0) s FROM payments")["s"]
        bets = db.one("SELECT COUNT(*) c, COALESCE(SUM(amount),0) s FROM transactions WHERE type='fee'")
        wins = db.one("SELECT COALESCE(SUM(amount),0) s FROM transactions WHERE type='win'")["s"]
        wd = db.one("SELECT COUNT(*) c, COALESCE(SUM(amount),0) s FROM transactions WHERE type='wd' AND status='pending'")
        await message.answer(
            f"👥 Игроков: {users}\n💰 Пополнено Stars: {dep}\n🎲 Ставок: {bets['c']} на {bets['s']} ⭐\n"
            f"🏆 Выиграно NFT на {wins} ⭐\n💸 Заявок на вывод: {wd['c']} на {wd['s']} ⭐")

    @r.message(F.text)
    async def fallback(message: Message):
        await message.answer("Открой приложение кнопкой ниже 👇", reply_markup=play_kb())

    return r


# =====================================================================
#  HTTP API для сайта (все запросы подписаны initData Telegram)
# =====================================================================
dumps = lambda o: json.dumps(o, ensure_ascii=False)  # noqa: E731


def ok(data=None, **kw):
    return web.json_response({"ok": True, **(data or {}), **kw}, dumps=dumps)


def fail(code: str, status: int = 400):
    return web.json_response({"ok": False, "error": code}, status=status, dumps=dumps)


# ---------------- middleware: CORS + ошибки ----------------
def _cors_headers(request: web.Request) -> dict:
    origin = request.headers.get("Origin", "")
    allow = "*" in config.ALLOWED_ORIGINS or origin in config.ALLOWED_ORIGINS
    return {
        "Access-Control-Allow-Origin": origin if (allow and origin) else ("*" if allow else ""),
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, X-Init-Data, X-Dev-User",
        "Access-Control-Max-Age": "86400",
        "Vary": "Origin",
    }


@web.middleware
async def cors_mw(request: web.Request, handler):
    if request.method == "OPTIONS":
        return web.Response(status=204, headers=_cors_headers(request))
    try:
        resp = await handler(request)
    except GameError as e:
        resp = fail(e.code)
    except AuthError as e:
        log.info("auth failed: %s", e)
        resp = fail("auth", 401)
    except web.HTTPException as e:
        resp = e
    except Exception:  # noqa: BLE001
        log.exception("api error")
        resp = fail("server", 500)
    if request.path.startswith("/api/"):
        for k, v in _cors_headers(request).items():
            if v:
                resp.headers[k] = v
    return resp


# ---------------- авторизация ----------------
def current_user(request: web.Request) -> int:
    """Проверяет initData и возвращает id пользователя (создаёт его при первом входе)."""
    game: Game = request.app["game"]
    init = request.headers.get("X-Init-Data", "")
    if not init and config.DEV_MODE and request.headers.get("X-Dev-User"):
        uid = int(request.headers["X-Dev-User"])
        game.upsert_user({"id": uid, "first_name": f"Dev{uid}", "username": f"dev{uid}"})
        return uid
    data = verify_init_data(init, config.BOT_TOKEN)
    user, _ = game.upsert_user(data["user"], data.get("start_param"))
    return user["id"]


async def body(request: web.Request) -> dict:
    try:
        return await request.json()
    except Exception:  # noqa: BLE001
        return {}


def full_state(request: web.Request, uid: int) -> dict:
    st = request.app["game"].state(uid)
    st["config"]["botUsername"] = request.app.get("bot_username", "")
    return st


# ---------------- эндпоинты ----------------
async def h_health(_):
    return ok(time=int(time.time()))


async def h_auth(request):
    uid = current_user(request)
    return ok(full_state(request, uid))


async def h_feed(request):
    return ok(items=request.app["game"].feed(20))


async def h_leaders(request):
    return ok(request.app["game"].leaders(50))


async def h_daily(request):
    uid = current_user(request)
    reward = request.app["game"].daily(uid)
    return ok(reward=reward, balance=request.app["game"].balance(uid))


async def h_subscribe(request):
    uid = current_user(request)
    bot: Bot = request.app["bot"]
    try:
        m = await bot.get_chat_member(config.CHANNEL, uid)
    except Exception as e:  # noqa: BLE001 — бот не админ канала / неверный CHANNEL
        log.warning("get_chat_member failed: %s", e)
        return fail("check_failed")
    status = getattr(m, "status", "")
    is_member = status in ("member", "administrator", "creator") or (status == "restricted" and getattr(m, "is_member", False))
    if not is_member:
        return fail("not_subscribed")
    reward = request.app["game"].reward_subscription(uid)
    return ok(reward=reward, balance=request.app["game"].balance(uid))


async def h_promo(request):
    uid = current_user(request)
    code = str((await body(request)).get("code", ""))[:32]
    reward = request.app["game"].promo(uid, code)
    return ok(reward=reward, balance=request.app["game"].balance(uid))


async def h_upgrade(request):
    uid = current_user(request)
    b = await body(request)
    res = request.app["game"].upgrade(uid, str(b.get("target", "")), stake=int(b.get("stake") or 0), gift_uid=b.get("gift") or None)
    return ok(res)


async def h_tasks_claim(request):
    uid = current_user(request)
    reward = request.app["game"].claim_task(uid, str((await body(request)).get("id", "")))
    return ok(reward=reward, balance=request.app["game"].balance(uid), tasks=request.app["game"].tasks(uid))


async def h_invoice(request):
    """Ссылка на оплату Telegram Stars: 1 ⭐ в игре = 1 Telegram Star."""
    uid = current_user(request)
    amount = int((await body(request)).get("stars") or 0)
    if amount < config.MIN_DEPOSIT or amount > 100000:
        return fail("bad_amount")
    bonus = next((p["bonus"] for p in PACKS if p["stars"] == amount), 0)
    bot: Bot = request.app["bot"]
    link = await bot.create_invoice_link(
        title="Пополнение баланса",
        description=f"{amount} ⭐ на баланс Void Play" + (f" (+{bonus} бонус)" if bonus else ""),
        payload=f"dep:{uid}:{amount}:{bonus}",
        currency="XTR",
        prices=[LabeledPrice(label=f"{amount} Stars", amount=amount)],
    )
    return ok(link=link)


async def h_withdraw(request):
    uid = current_user(request)
    b = await body(request)
    amount = int(b.get("stars") or 0)
    method = str(b.get("method") or "stars")[:10]
    game: Game = request.app["game"]
    tx_id = game.withdraw(uid, amount, method)
    u = game.user(uid)
    text = (f"💸 Заявка на вывод #{tx_id}\n"
            f"Игрок: {u['first_name']} @{u['username'] or '—'} (id {uid})\n"
            f"Сумма: {amount} ⭐, способ: {method}")
    for admin in config.ADMIN_IDS:
        try:
            await request.app["bot"].send_message(admin, text)
        except Exception:  # noqa: BLE001
            pass
    return ok(balance=game.balance(uid), id=tx_id)


def _letter_svg(name: str, uid: int) -> str:
    colors = ["#30a1f5", "#ff4d6d", "#a855f7", "#18c832", "#ff9f1a", "#0033ff"]
    c = colors[uid % len(colors)]
    ch = (name or "?").strip()[:1].upper() or "?"
    ch = ch.replace("&", "&amp;").replace("<", "&lt;")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" fill="{c}"/>'
            f'<text x="32" y="42" font-family="Arial,sans-serif" font-size="30" font-weight="700" fill="#fff" text-anchor="middle">{ch}</text></svg>')


async def h_avatar(request):
    """Аватарка игрока: фото из Telegram (кэшируется на диск) или буква на цветном фоне."""
    try:
        uid = int(request.match_info["uid"])
    except ValueError:
        raise web.HTTPNotFound()
    game: Game = request.app["game"]
    row = game.db.one("SELECT first_name, username, photo_url FROM users WHERE id=?", (uid,))
    if not row:
        raise web.HTTPNotFound()
    cache: Path = config.AVATAR_DIR / f"{uid}.jpg"
    headers = {"Cache-Control": "public, max-age=3600", "Access-Control-Allow-Origin": "*"}
    if cache.exists() and time.time() - cache.stat().st_mtime < 86400:
        return web.FileResponse(cache, headers=headers)
    neg = request.app["avatar_miss"]
    try:
        if time.time() - neg.get(uid, 0) < 3600:
            raise LookupError("no photo (cached)")
        bot: Bot = request.app["bot"]
        photos = await bot.get_user_profile_photos(uid, limit=1)
        if photos.total_count:
            f = await bot.get_file(photos.photos[0][-1].file_id)
            config.AVATAR_DIR.mkdir(parents=True, exist_ok=True)
            await bot.download_file(f.file_path, destination=cache)
            return web.FileResponse(cache, headers=headers)
        neg[uid] = time.time()
    except Exception as e:  # noqa: BLE001
        neg[uid] = time.time()
        log.debug("avatar %s: %s", uid, e)
    if row["photo_url"]:
        raise web.HTTPFound(row["photo_url"])
    return web.Response(text=_letter_svg(row["first_name"] or row["username"], uid), content_type="image/svg+xml", headers=headers)


def build_app(game: Game, bot: Bot) -> web.Application:
    app = web.Application(middlewares=[cors_mw])
    app["game"] = game
    app["bot"] = bot
    app["avatar_miss"] = {}
    r = app.router
    r.add_get("/api/health", h_health)
    r.add_post("/api/auth", h_auth)
    r.add_get("/api/feed", h_feed)
    r.add_get("/api/leaders", h_leaders)
    r.add_post("/api/daily", h_daily)
    r.add_post("/api/subscribe/check", h_subscribe)
    r.add_post("/api/promo", h_promo)
    r.add_post("/api/upgrade", h_upgrade)
    r.add_post("/api/tasks/claim", h_tasks_claim)
    r.add_post("/api/invoice", h_invoice)
    r.add_post("/api/withdraw", h_withdraw)
    r.add_get("/api/avatar/{uid}", h_avatar)
    return app


# =====================================================================
#  Запуск
# =====================================================================


async def main() -> None:
    if not config.BOT_TOKEN:
        raise SystemExit("Укажи BOT_TOKEN (в .env или в панели хостинга)")
    if not config.WEBAPP_URL.startswith("https://"):
        raise SystemExit("Укажи WEBAPP_URL — адрес сайта на GitHub Pages, например https://ник.github.io/voidplay-webapp/")

    game = Game(DB(config.DB_PATH))
    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(build_router(game))

    me = await bot.get_me()
    app = build_app(game, bot)
    app["bot_username"] = me.username

    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, config.HOST, config.PORT).start()
    log.info("API слушает %s:%s", config.HOST, config.PORT)

    try:
        await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Играть", web_app=WebAppInfo(url=config.WEBAPP_URL)))
    except Exception as e:  # noqa: BLE001
        log.warning("не удалось поставить кнопку меню: %s", e)

    log.info("Бот @%s запущен. Web App: %s", me.username, config.WEBAPP_URL)
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
