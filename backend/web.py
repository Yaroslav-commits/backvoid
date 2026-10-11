"""HTTP API для Web App (aiohttp). Все запросы с фронта подписаны initData Telegram."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from aiohttp import web
from aiogram import Bot
from aiogram.types import LabeledPrice

from . import config
from .auth import AuthError, verify_init_data
from .catalog import PACKS
from .game import Game, GameError

log = logging.getLogger("voidplay.api")
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


# ---------------- фронтенд с этого же домена ----------------
async def h_index(_):
    return web.FileResponse(config.WEBAPP_DIR / "index.html", headers={"Cache-Control": "no-cache"})


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
    if config.SERVE_STATIC and (config.WEBAPP_DIR / "index.html").exists():
        r.add_get("/", h_index)
        for d in ("css", "js", "assets"):
            if (config.WEBAPP_DIR / d).is_dir():
                r.add_static(f"/{d}/", config.WEBAPP_DIR / d)
    return app
