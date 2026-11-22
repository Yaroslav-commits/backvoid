"""
Void Play — бот (aiogram 3) + API для Web App в одном процессе.

  • /start, кнопка «Играть», оплата Telegram Stars, админ-команды
  • HTTP API на PORT (8080) для сайта: /api/*
  • (необязательно) раздаёт сам сайт, если задан WEBAPP_DIR

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
    BotCommand,
    BotCommandScopeChat,
    CallbackQuery,
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
from config import PACKS
from game import DB, AuthError, Game, GameError, verify_init_data
from services import Services

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("voidplay")


# =====================================================================
#  Бот: /start, оплата Stars, админ-команды
# =====================================================================


def play_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Играть", web_app=WebAppInfo(url=config.WEBAPP_URL))],
        [InlineKeyboardButton(text="📢 Канал", url=config.CHANNEL_URL)],
    ])


def games_kb(game: Game) -> InlineKeyboardMarkup:
    """Кнопки «открыть/закрыть игру» для админа."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{'🟢' if game.game_open(g) else '🔴'} {title}", callback_data=f"game:{g}")]
        for g, title in config.GAMES.items()])


# Все команды админа в одном месте: из этого списка собираются /help и меню команд (кнопка «/» в чате с ботом)
ADMIN_COMMANDS = [
    ("Обзор", [
        ("help", "", "этот список команд"),
        ("stats", "", "статистика: игроки, пополнения, ставки, заработок"),
        ("nft", "", "проверка: аккаунт для подарков, склад NFT, комиссия, TON"),
        ("backup", "", "прислать копию базы прямо сейчас (автоматически — раз в сутки)"),
    ]),
    ("Игры", [
        ("games", "", "открыть или закрыть игры кнопками"),
        ("open", "игра", "открыть игру: upgrade, mines, crash, cases, roulette, fireice или all"),
        ("close", "игра", "закрыть игру (те же названия)"),
    ]),
    ("Игроки", [
        ("addstars", "id кол-во", "начислить игроку звёзды (с минусом — списать)"),
        ("givegift", "id nft:Название-Номер", "выдать игроку подарок со склада, например приз за ТОП"),
        ("promo", "КОД награда активаций", "создать промокод на звёзды"),
    ]),
    ("Цены NFT", [
        ("floor", "Коллекция цена", "задать цену коллекции вручную; без параметров — все цены"),
        ("syncprices", "", "обновить цены с маркета прямо сейчас"),
        ("portals", "tma …", "подключить авто-цены с Portals; без параметров — инструкция"),
        ("syncgifts", "", "обновить обычные подарки Telegram (только при GIFTS_SOURCE=telegram)"),
    ]),
]
ADMIN_HELP = "<b>Команды админа</b>\n" + "\n".join(
    f"\n<b>{group}</b>\n" + "\n".join(f"/{c}{f' <code>{a}</code>' if a else ''} — {d}" for c, a, d in cmds)
    for group, cmds in ADMIN_COMMANDS
) + "\n\nЗаявки на вывод, склад подарков, призы ТОПа и поиск игроков — в приложении: Настройки → Админ-панель."


async def setup_commands(bot: Bot):
    """Меню команд: игрокам — только /start и /help, админам — полный список с описаниями."""
    await bot.set_my_commands([BotCommand(command="start", description="Открыть игру"),
                               BotCommand(command="help", description="Помощь")])
    full = [BotCommand(command="start", description="Открыть игру")] + [
        BotCommand(command=c, description=(d[0].upper() + d[1:])[:250]) for _, cmds in ADMIN_COMMANDS for c, _a, d in cmds]
    for admin in config.ADMIN_IDS:
        try:
            await bot.set_my_commands(full, scope=BotCommandScopeChat(chat_id=admin))
        except Exception as e:  # noqa: BLE001 — админ ещё не запускал бота
            log.info("меню команд для %s не поставлено: %s", admin, e)


async def start_api(runner: web.AppRunner) -> int | None:
    """Запускает API. Если порт занят (часто его держит сам хостинг) — ждём и пробуем соседние порты.
    Бот в Telegram работает в любом случае, даже если API не поднялся. → порт или None."""
    for attempt in range(3):   # прошлый процесс бота мог ещё не освободить порт
        try:
            await web.TCPSite(runner, config.HOST, config.API_PORT).start()
            log.info("API слушает %s:%s", config.HOST, config.API_PORT)
            return config.API_PORT
        except OSError as e:
            log.warning("порт %s занят (%s), попытка %s/3", config.API_PORT, e.strerror, attempt + 1)
            await asyncio.sleep(2)
    for port in range(config.API_PORT + 1, config.API_PORT + 11):
        try:
            await web.TCPSite(runner, config.HOST, port).start()
            log.warning("API слушает %s:%s — порт %s занят другим процессом", config.HOST, port, config.API_PORT)
            return port
        except OSError:
            continue
    log.error("API не запущен: порты %s–%s заняты", config.API_PORT, config.API_PORT + 10)
    return None


def build_router(game: Game, services: Services | None = None) -> Router:
    r = Router()

    @r.message(CommandStart())
    async def start(message: Message, command: CommandObject):
        u = message.from_user
        _, is_new = game.upsert_user(u.model_dump(), command.args)
        if is_new and command.args:
            log.info("new user %s by %s", u.id, command.args)
        await message.answer(
            f'Привет, <b>{u.first_name}</b>! <tg-emoji emoji-id="5199885118214255386">👋</tg-emoji>\n\n'
            f"Добро пожаловать в <b>{config.APP_NAME}</b> — апгрейдер, мины и другие игры за звёзды и подарки.\n\n"
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
            await message.answer(f"✅ Зачислено <b>{amount + bonus} ⭐</b> на баланс {config.APP_NAME}", reply_markup=play_kb())

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
            await message.answer(f"Готово: {game.gift_view(gift)['name']} → {uid}")
        except (ValueError, GameError, KeyError) as e:
            ids = ", ".join(f"{g['id']} ({g['name']})" for g in game.catalog[:40])
            await message.answer(f"Ошибка: {e}\nФормат: /givegift user_id gift_id\nПодарки: {ids}")

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
        wins = db.one("SELECT COALESCE(SUM(amount),0) s FROM transactions WHERE type='win' AND gift_id IS NOT NULL")["s"]
        swins = db.one("SELECT COALESCE(SUM(amount),0) s FROM transactions WHERE type='win' AND gift_id IS NULL")["s"]
        wd = db.one("SELECT COUNT(*) c, COALESCE(SUM(amount),0) s FROM transactions WHERE type='wd' AND status='pending'")
        ton = db.one("SELECT COALESCE(SUM(amount),0) s FROM payments WHERE charge_id LIKE 'ton:%'")["s"]
        house = game.house_total()
        pvp_bets = db.one("SELECT COUNT(*) c FROM pvp_rounds WHERE status='done'")["c"]
        closed = ", ".join(config.GAMES[g] for g in game.closed) or "нет"
        await message.answer(
            f"👥 Игроков: {users}\n💰 Пополнено: {dep} ⭐ (из них через TON: {ton} ⭐)\n🎲 Ставок: {bets['c']} на {bets['s']} ⭐\n"
            f"🏆 Выиграно NFT на {wins} ⭐, звёздами {swins} ⭐\n"
            f"⚔️ PvP: раундов {pvp_bets}, комиссия — рулетка {house.get('roulette', 0)} ⭐, огонь и лёд {house.get('fireice', 0)} ⭐\n"
            f"💸 Заявок на вывод: {wd['c']} на {wd['s']} ⭐\n🎁 Подарков в каталоге: {len(game.catalog)}\n🔴 Закрытые игры: {closed}")

    @r.message(Command("backup"))
    async def backup_cmd(message: Message):
        if not is_admin(message) or not services:
            return
        try:
            await services.backup(force=True)
        except Exception as e:  # noqa: BLE001
            await message.answer(f"Не получилось: {e}")

    @r.message(Command("help", "admin"))
    async def help_cmd(message: Message):
        if is_admin(message):
            return await message.answer(ADMIN_HELP)
        await message.answer(
            f"<b>{config.APP_NAME}</b> — PvP-игры на звёзды и NFT-подарки.\n\n"
            "• Жми «Играть», чтобы открыть приложение\n"
            f"• Чтобы играть подарком, передай свой NFT аккаунту {config.GIFTS_ACCOUNT} — он появится в инвентаре\n"
            "• Вывод — в TON или подарком, прямо в приложении\n\n"
            f"Вопросы: {config.SUPPORT_URL}", reply_markup=play_kb())

    # ---------- открыть / закрыть игры ----------
    @r.message(Command("games"))
    async def games_cmd(message: Message):
        if is_admin(message):
            await message.answer("Игры. Нажми, чтобы открыть или закрыть:", reply_markup=games_kb(game))

    @r.callback_query(F.data.startswith("game:"))
    async def games_toggle(q: CallbackQuery):
        if q.from_user.id not in config.ADMIN_IDS:
            return await q.answer("Только для админа", show_alert=True)
        g = q.data.split(":", 1)[1]
        try:
            game.set_game(g, not game.game_open(g))
        except GameError:
            return await q.answer("Нет такой игры")
        await q.answer(f"{config.GAMES[g]}: {'открыта' if game.game_open(g) else 'закрыта'}")
        await q.message.edit_reply_markup(reply_markup=games_kb(game))

    async def _switch(message: Message, command: CommandObject, is_open: bool):
        if not is_admin(message):
            return
        g = (command.args or "").strip().lower()
        if g == "all":
            for x in config.GAMES:
                game.set_game(x, is_open)
        elif g in config.GAMES:
            game.set_game(g, is_open)
        else:
            return await message.answer(f"Формат: /{'open' if is_open else 'close'} игра\nИгры: {', '.join(config.GAMES)}, all")
        await message.answer("Готово:", reply_markup=games_kb(game))

    @r.message(Command("open"))
    async def open_cmd(message: Message, command: CommandObject):
        await _switch(message, command, True)

    @r.message(Command("close"))
    async def close_cmd(message: Message, command: CommandObject):
        await _switch(message, command, False)

    @r.message(Command("syncgifts"))
    async def syncgifts(message: Message):
        if not is_admin(message):
            return
        if not services or config.GIFTS_SOURCE != "telegram":
            return await message.answer("Авто-цены выключены (GIFTS_SOURCE=manual)")
        try:
            n = await services.sync_gifts()
            top = "\n".join(f"{g['name']} — {g['price']} ⭐" for g in game.catalog[:15])
            await message.answer(f"Обновлено подарков: {n}\n\n{top}")
        except Exception as e:  # noqa: BLE001
            await message.answer(f"Не получилось: {e}")

    # ---------- NFT-подарки: аккаунт проекта подключён к боту через Telegram Business ----------
    async def tell_admins(bot, text: str):
        for a in config.ADMIN_IDS:
            try:
                await bot.send_message(a, text)
            except Exception:  # noqa: BLE001
                pass

    @r.business_connection()
    async def biz_connect(bc):
        if bc.user.id not in config.ADMIN_IDS:   # чужой аккаунт подключил бота — игнорируем
            return
        rights = getattr(bc, "rights", None)
        can = bool(rights and getattr(rights, "can_transfer_and_upgrade_gifts", False))
        if bc.is_enabled:
            game.set_setting("biz:conn", bc.id)
            game.set_setting("biz:user", bc.user.id)
        elif game.setting("biz:conn") == bc.id:
            game.set_setting("biz:conn", "")
        added = 0
        if bc.is_enabled and can and services:
            try:
                added = await services.sync_stock()
            except Exception as e:  # noqa: BLE001
                log.warning("sync stock: %s", e)
        await tell_admins(bc.bot, (f"✅ Аккаунт подключён (на склад добавлено подарков: {added}) — подарки, отправленные ему в личку, попадут игрокам в инвентарь.\n"
                                   + ("" if can else "⚠️ Включи боту право «Подарки и звёзды → передача подарков», иначе вывод NFT не сработает."))
                          if bc.is_enabled else "❌ Аккаунт отключён от бота — приём NFT остановлен.")

    @r.business_message(F.unique_gift)
    async def biz_gift(message: Message):
        conn, owner = game.setting("biz:conn"), game.setting("biz:user")
        u, info = message.from_user, message.unique_gift
        if not conn or message.business_connection_id != conn or not u or str(u.id) == owner or u.is_bot:
            return
        g = info.gift
        if not info.owned_gift_id:
            return
        game.upsert_user(u.model_dump())
        view = game.nft_deposit(u.id, g.name, g.base_name, g.number, g.model.name if g.model else "",
                                g.backdrop.name if g.backdrop else "", info.owned_gift_id, conn, info.transfer_star_count)
        if not view:
            return
        price = f"{view['price']} ⭐" if view["price"] else "цена уточняется"
        try:
            await message.bot.send_message(u.id, f"🎁 <b>{view['name']}</b> зачислен в инвентарь ({price}).\nИграй им в PvP или выводи обратно в любой момент.", reply_markup=play_kb())
        except Exception:  # noqa: BLE001 — игрок ещё не запускал бота
            pass
        await tell_admins(message.bot, f"🎁 NFT от @{u.username or u.id}: <b>{view['name']}</b> — {price}"
                          + ("" if view["price"] else f"\nЗадай цену: <code>/floor {game.floor_key(g.name)} ЦЕНА</code>"))

    @r.message(Command("floor"))
    async def floor_cmd(message: Message, command: CommandObject):
        if not is_admin(message):
            return
        a = (command.args or "").split()
        if len(a) != 2 or not a[1].isdigit():
            lst = "\n".join(f"{k} — {v} ⭐" for k, v in sorted(game.floors.items())) or "пока пусто"
            return await message.answer(f"Формат: /floor Коллекция цена_в_звёздах\n\nСейчас:\n{lst}")
        game.set_floor(a[0], int(a[1]))
        await message.answer(f"Готово: {game.floor_key(a[0])} = {a[1]} ⭐")

    @r.message(Command("syncprices"))
    async def syncprices(message: Message):
        if not is_admin(message):
            return
        if not (config.NFT_FLOORS_URL or config.PORTALS_AUTH or game.setting("portals:auth")):
            return await message.answer("Авто-цены выключены. Бесплатно: /portals · платно: GIFTASSET_KEY в настройках")
        try:
            await message.answer(f"Цены обновлены: {await services.sync_floors()} коллекций")
        except Exception as e:  # noqa: BLE001
            await message.answer(f"Не получилось: {e}")

    @r.message(Command("portals"))
    async def portals_cmd(message: Message, command: CommandObject):
        if not is_admin(message):
            return
        a = (command.args or "").strip()
        if not a:
            return await message.answer(
                "<b>Бесплатные цены NFT с маркета Portals</b>\n\n"
                "1. Открой web.telegram.org в Chrome на компьютере и войди в аккаунт\n"
                "2. Нажми F12 → вкладка Network\n"
                "3. Открой в Telegram бота @portals и запусти его приложение\n"
                "4. В Network найди любой запрос к portal-market.com (в фильтре набери portal-market) → Headers → Authorization\n"
                "5. Скопируй значение целиком (начинается с <code>tma </code>) и отправь мне:\n"
                "<code>/portals tma ...</code>\n\n"
                "Строка живёт несколько дней. Когда устареет — я напишу, и её надо будет обновить так же.")
        game.set_setting("portals:auth", a)
        try:
            await message.delete()   # в строке авторизации — доступ к твоему аккаунту Portals, не оставляем её в чате
        except Exception:  # noqa: BLE001
            pass
        try:
            await message.answer(f"✅ Portals подключён, цены обновлены: {await services.sync_floors()} коллекций")
        except Exception as e:  # noqa: BLE001
            await message.answer(f"Строку сохранил, но цены получить не вышло: {e}")

    @r.message(Command("nft"))
    async def nft_cmd(message: Message):
        if not is_admin(message):
            return
        rows = game.db.q("SELECT status, COUNT(*) c FROM nfts GROUP BY status")
        per_ton = await services.stars_per_ton() if services else 0
        await message.answer(
            f"Аккаунт для подарков: {'✅ подключён' if game.setting('biz:conn') else '❌ не подключён (Настройки → Telegram для бизнеса → Чат-боты)'}\n"
            f"NFT: {', '.join(f'{x[0]}: {x[1]}' for x in ((r['status'], r['c']) for r in rows)) or 'нет'}\n"
            f"Комиссия на вывод: {config.WITHDRAW_FEE * 100:g}%\n"
            f"TON: кошелёк {'задан' if config.TON_WALLET else '❌ НЕ задан (переменная TON_WALLET)'}, "
            f"курс {'1 TON = ' + str(per_ton) + ' ⭐' if per_ton else '❌ не получен'}")

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
        "Access-Control-Allow-Headers": "Content-Type, X-Init-Data, X-Dev-User, Authorization",
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
    except (ValueError, TypeError):  # кривые числа в запросе
        resp = fail("bad_amount")
    except Exception:  # noqa: BLE001
        log.exception("api error")
        resp = fail("server", 500)
        await alert_admins(request.app.get("bot"), f"⚠️ Ошибка на сервере: {request.method} {request.path}\nПодробности — в логах хостинга")
    if request.path.startswith("/api/"):
        for k, v in _cors_headers(request).items():
            if v:
                resp.headers[k] = v
    return resp


_alert_at = 0.0


async def alert_admins(bot, text: str):
    """Сообщить админу о проблеме, но не чаще раза в 10 минут (чтобы не заспамить)."""
    global _alert_at
    if not bot or time.time() - _alert_at < 600:
        return
    _alert_at = time.time()
    for a in config.ADMIN_IDS:
        try:
            await bot.send_message(a, text)
        except Exception:  # noqa: BLE001
            pass


# Простая защита от спама запросами: не больше 12 действий в секунду с одного аккаунта
_hits: dict[int, list[float]] = {}


def rate_limit(uid: int, per_sec: int = 12):
    t = time.time()
    h = [x for x in _hits.get(uid, []) if t - x < 1]
    if len(h) >= per_sec:
        raise GameError("slow")
    h.append(t)
    _hits[uid] = h
    if len(_hits) > 50000:
        _hits.clear()


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
    if request.method == "POST":
        rate_limit(int(data["user"]["id"]))
    user, _ = game.upsert_user(data["user"], data.get("start_param"))
    return user["id"]


async def body(request: web.Request) -> dict:
    try:
        return await request.json()
    except Exception:  # noqa: BLE001
        return {}


async def full_state(request: web.Request, uid: int) -> dict:
    st = request.app["game"].state(uid)
    st["config"]["botUsername"] = request.app.get("bot_username", "")
    sv: Services = request.app["services"]
    per_ton = await sv.stars_per_ton()
    usd = await sv.ton_usd()
    st["rates"] = {"usdToStars": round(1 / config.STAR_USD, 2), "tonToStars": per_ton,
                   "tonOut": round(usd / config.WITHDRAW_STAR_USD, 2) if usd > 0 else 0}   # звёзд за 1 TON при выводе
    st["config"]["admin"] = uid in config.ADMIN_IDS
    st["config"]["ton"] = {"enabled": per_ton > 0, "minStars": config.TON_MIN_STARS}
    return st


# ---------------- эндпоинты ----------------
VERSION = "3.0"


async def h_health(request):
    """Проверка связи: открой https://<домен>/api/health в браузере — должно быть ok: true."""
    return ok(time=int(time.time()), name=config.APP_NAME, version=VERSION,
              bot=request.app.get("bot_username", ""), devMode=config.DEV_MODE)


async def h_root(_):
    return web.Response(text=f"{config.APP_NAME} API v{VERSION} работает. Проверка: /api/health")


async def h_auth(request):
    uid = current_user(request)
    return ok(await full_state(request, uid))


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


async def h_mines_start(request):
    uid = current_user(request)
    b = await body(request)
    g = request.app["game"]
    game = g.mines_start(uid, int(b.get("stake") or 0), int(b.get("mines") or 3))
    return ok(game=game, balance=g.balance(uid))


async def h_mines_open(request):
    uid = current_user(request)
    b = await body(request)
    g = request.app["game"]
    res = g.mines_open(uid, int(b.get("cell", -1)))
    return ok(res, balance=g.balance(uid))


async def h_mines_cashout(request):
    uid = current_user(request)
    g = request.app["game"]
    res = g.mines_cashout(uid)
    return ok(res, balance=g.balance(uid))


# ---------------- PvP ----------------
async def h_pvp(request):
    uid = current_user(request)
    return ok(request.app["game"].pvp_state(request.query.get("game", "roulette"), uid))


async def h_pvp_bet(request):
    uid = current_user(request)
    b = await body(request)
    g = request.app["game"]
    st = g.pvp_bet(uid, str(b.get("game", "")), int(b.get("amount") or 0), b.get("side"), str(b.get("gift") or "") or None)
    return ok(st, balance=g.balance(uid))


# ---------------- Кейсы ----------------
async def h_cases(request):
    current_user(request)
    return ok(cases=request.app["game"].cases_list())


async def h_case_open(request):
    uid = current_user(request)
    return ok(request.app["game"].case_open(uid, str((await body(request)).get("id") or "")))


# ---------------- Самолётик ----------------
async def h_crash(request):
    uid = current_user(request)
    return ok(request.app["game"].crash_state(uid))


async def h_crash_bet(request):
    uid = current_user(request)
    b = await body(request)
    g = request.app["game"]
    st = g.crash_bet(uid, int(b.get("amount") or 0), b.get("auto") or None)
    return ok(st, balance=g.balance(uid))


async def h_crash_cashout(request):
    uid = current_user(request)
    g = request.app["game"]
    st = g.crash_cashout(uid)
    return ok(st, balance=g.balance(uid))


async def h_crash_cancel(request):
    uid = current_user(request)
    g = request.app["game"]
    st = g.crash_cancel(uid)
    return ok(st, balance=g.balance(uid))


async def h_pvp_cancel(request):
    uid = current_user(request)
    g = request.app["game"]
    st = g.pvp_cancel(uid, str((await body(request)).get("game", "")))
    return ok(st, balance=g.balance(uid))


# ---------------- TON ----------------
async def h_ton_invoice(request):
    """Счёт на пополнение в TON: адрес, сумма и комментарий, по которому найдём перевод."""
    uid = current_user(request)
    sv: Services = request.app["services"]
    per_ton = await sv.stars_per_ton()
    if per_ton <= 0:
        return fail("ton_off")
    dep = request.app["game"].ton_create(uid, int((await body(request)).get("stars") or 0), per_ton)
    ton = dep["nano"] / 1e9
    return ok(dep, address=config.TON_WALLET, ton=f"{ton:.3f}", rate=per_ton,
              link=f"https://app.tonkeeper.com/transfer/{config.TON_WALLET}?amount={dep['nano']}&text={dep['comment']}",
              tonLink=f"ton://transfer/{config.TON_WALLET}?amount={dep['nano']}&text={dep['comment']}")


async def h_ton_check(request):
    uid = current_user(request)
    g = request.app["game"]
    dep_id = int((await body(request)).get("id") or 0)
    st = g.ton_status(uid, dep_id)
    if st["status"] == "pending":
        try:
            await request.app["services"].check_ton()
        except Exception as e:  # noqa: BLE001 — toncenter моргнул, попробуем на следующем запросе
            log.warning("ton check: %s", e)
        st = g.ton_status(uid, dep_id)
    return ok(st, balance=g.balance(uid))


async def h_gift_img(request):
    """Картинка подарка из Telegram (кэшируется на диске и в браузере)."""
    try:
        path = await request.app["services"].gift_image(request.match_info["gid"])
    except Exception as e:  # noqa: BLE001 — Telegram не отдал файл: сайт покажет заглушку
        log.debug("gift image: %s", e)
        path = None
    if not path:
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Cache-Control": "public, max-age=604800", "Access-Control-Allow-Origin": "*"})


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
    """Вывод звёзд — только в TON: игрок указывает кошелёк, админ переводит вручную."""
    uid = current_user(request)
    b = await body(request)
    game: Game = request.app["game"]
    sv: Services = request.app["services"]
    if not await sv.ton_usd():
        try:
            await sv.refresh_ton_rate()
        except Exception:  # noqa: BLE001
            pass
    w = game.withdraw(uid, int(b.get("stars") or 0), b.get("wallet"), await sv.ton_usd())
    u = game.user(uid)
    text = (f"💸 Заявка на вывод #{w['id']}\n"
            f"Игрок: {u['first_name']} @{u['username'] or '—'} (id {uid})\n"
            f"Списано: {int(b.get('stars') or 0)} ⭐ (твоя комиссия {w['fee'] - w['ref']} ⭐)\n"
            f"К выплате: <b>{w['ton']} TON</b>\n<code>{w['wallet']}</code>\n"
            f"Отметь «выплачено» в админ-панели приложения")
    for admin in config.ADMIN_IDS:
        try:
            await request.app["bot"].send_message(admin, text)
        except Exception:  # noqa: BLE001
            pass
    return ok(balance=game.balance(uid), id=w["id"], ton=w["ton"])


async def h_wallet(request):
    uid = current_user(request)
    request.app["game"].set_wallet(uid, (await body(request)).get("address"))
    return ok()


async def h_nft_withdraw(request):
    """Вывод NFT: бот передаёт подарок игроку с аккаунта проекта."""
    uid = current_user(request)
    game: Game = request.app["game"]
    w = game.nft_withdraw(uid, str((await body(request)).get("uid") or ""))
    try:
        await request.app["bot"].transfer_gift(business_connection_id=game.setting("biz:conn") or w["conn_id"], owned_gift_id=w["owned_id"],
                                               new_owner_chat_id=uid, star_count=w["cost"] or None)
    except Exception as e:  # noqa: BLE001 — Telegram отказал: возвращаем подарок и комиссию
        log.warning("nft withdraw %s: %s", w["slug"], e)
        game.nft_withdraw_undo(uid, w)
        return fail("transfer_failed")
    game.nft_withdraw_done(w)
    return ok(balance=game.balance(uid), inventory=game.inventory(uid))


async def h_shop_buy(request):
    uid = current_user(request)
    return ok(request.app["game"].shop_buy(uid, str((await body(request)).get("id") or "")))


# ---------------- админка в приложении ----------------
def admin_user(request) -> int:
    uid = current_user(request)
    if uid not in config.ADMIN_IDS:
        raise AuthError("not admin")
    return uid


async def h_admin(request):
    admin_user(request)
    return ok(request.app["game"].admin_overview())


async def h_admin_act(request):
    uid = admin_user(request)
    game: Game = request.app["game"]
    b = await body(request)
    act, extra = str(b.get("action") or ""), {}
    if act in ("wd_done", "wd_reject"):
        r = game.admin_wd(int(b.get("id") or 0), act == "wd_done")
        try:
            await request.app["bot"].send_message(r["user_id"], (f"✅ Вывод {r['ton']} TON отправлен на твой кошелёк" if r.get("ton") else f"✅ Вывод {r['pay']} ⭐ выплачен") if act == "wd_done"
                                                  else f"↩️ Заявка на вывод отклонена, {r['amount']} ⭐ вернулись на баланс")
        except Exception:  # noqa: BLE001
            pass
    elif act == "floor":
        game.set_floor(str(b.get("name") or ""), int(b.get("price") or 0))
    elif act == "game":
        game.set_game(str(b.get("game") or ""), bool(b.get("open")))
    elif act == "lock":
        game.admin_lock(str(b.get("id") or ""), bool(b.get("locked")))
    elif act == "sync":
        if not game.setting("biz:conn"):
            extra["error"] = "Аккаунт не подключён к боту"
        else:
            try:
                extra["added"] = await request.app["services"].sync_stock()
            except Exception as e:  # noqa: BLE001 — показываем админу настоящую причину
                log.warning("sync stock: %s", e)
                extra["error"] = f"Telegram не отдал подарки: {e}"[:300]
    elif act == "prize":
        game.admin_prize(str(b.get("id") or ""))
    elif act == "take":
        game.admin_take(uid, str(b.get("id") or ""), int(b.get("to") or 0) or None)
    elif act == "user":
        extra["found"] = game.admin_user(b.get("q") or "")
    elif act == "stars":
        game.admin_add_balance(int(b.get("id") or 0), int(b.get("amount") or 0))
        extra["found"] = game.admin_user(b.get("id"))
    elif act == "ban":
        game.admin_ban(int(b.get("id") or 0), bool(b.get("banned")))
        extra["found"] = game.admin_user(b.get("id"))
    else:
        return fail("not_found")
    return ok(game.admin_overview(), **extra)


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


def build_app(game: Game, bot: Bot, services: Services | None = None) -> web.Application:
    app = web.Application(middlewares=[cors_mw])
    app["game"] = game
    app["bot"] = bot
    app["services"] = services or Services(game, bot)
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
    r.add_post("/api/mines/start", h_mines_start)
    r.add_post("/api/mines/open", h_mines_open)
    r.add_post("/api/mines/cashout", h_mines_cashout)
    r.add_get("/api/pvp", h_pvp)
    r.add_get("/api/cases", h_cases)
    r.add_post("/api/cases/open", h_case_open)
    r.add_get("/api/crash", h_crash)
    r.add_post("/api/crash/bet", h_crash_bet)
    r.add_post("/api/crash/cashout", h_crash_cashout)
    r.add_post("/api/crash/cancel", h_crash_cancel)
    r.add_post("/api/pvp/bet", h_pvp_bet)
    r.add_post("/api/pvp/cancel", h_pvp_cancel)
    r.add_post("/api/ton/invoice", h_ton_invoice)
    r.add_post("/api/ton/check", h_ton_check)
    r.add_get("/api/gift/{gid}", h_gift_img)
    r.add_post("/api/tasks/claim", h_tasks_claim)
    r.add_post("/api/invoice", h_invoice)
    r.add_post("/api/withdraw", h_withdraw)
    r.add_post("/api/nft/withdraw", h_nft_withdraw)
    r.add_post("/api/wallet", h_wallet)
    r.add_post("/api/shop/buy", h_shop_buy)
    r.add_get("/api/admin", h_admin)
    r.add_post("/api/admin/act", h_admin_act)
    r.add_get("/api/avatar/{uid}", h_avatar)
    site = Path(config.WEBAPP_DIR) if config.WEBAPP_DIR else None
    if site and (site / "index.html").exists():
        # сайт и API на одном домене — самый надёжный вариант, CORS не нужен
        r.add_get("/", lambda _: web.FileResponse(site / "index.html"))
        r.add_static("/", site, show_index=False)
        log.info("Раздаю сайт из %s", site)
    else:
        r.add_get("/", h_root)
    return app


# =====================================================================
#  Запуск
# =====================================================================


async def main() -> None:
    if not config.BOT_TOKEN:
        raise SystemExit("Укажи BOT_TOKEN (в .env или в панели хостинга)")
    if not config.WEBAPP_URL.startswith("https://"):
        raise SystemExit("Укажи WEBAPP_URL — адрес сайта (https://...), например https://yaroslav-commits.github.io/voidplayfiles/")

    game = Game(DB(config.DB_PATH))
    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    services = Services(game, bot)
    dp = Dispatcher()
    dp.include_router(build_router(game, services))

    try:
        me = await bot.get_me()
    except Exception:
        await bot.session.close()
        raise
    app = build_app(game, bot, services)
    app["bot_username"] = me.username

    runner = web.AppRunner(app)
    await runner.setup()
    api_port = await start_api(runner)

    try:
        await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Играть", web_app=WebAppInfo(url=config.WEBAPP_URL)))
    except Exception as e:  # noqa: BLE001
        log.warning("не удалось поставить кнопку меню: %s", e)

    try:
        await setup_commands(bot)
    except Exception as e:  # noqa: BLE001
        log.warning("не удалось поставить меню команд: %s", e)

    await services.start()
    issues = [x for x in (
        (f"Порт {config.API_PORT} занят сервером-заглушкой хостинга, поэтому приложение пишет «Нет связи». "
         f"В панели bothost: «Дополнительные настройки» → включи «Использовать домен» и укажи внутренний порт 3000 (не 8080), "
         f"удали переменную API_PORT, если задавал, и перезапусти бота") if api_port and api_port != config.API_PORT else "",
        f"API не запустился: порты {config.API_PORT}–{config.API_PORT + 10} заняты. Бот в Telegram работает, а приложение — нет. Задай свободный порт переменной PORT"
        if not api_port else "",
        "DEV_MODE включён — любой может играть за чужой аккаунт! Выключи (DEV_MODE=0)" if config.DEV_MODE else "",
        "ALLOWED_ORIGINS = * — разреши только свой сайт (например https://yaroslav-commits.github.io)" if "*" in config.ALLOWED_ORIGINS else "",
        "TON_WALLET не задан — пополнение и вывод в TON выключены" if not config.TON_WALLET else "",
        "CHANNEL указывает на тестовый канал" if "test" in config.CHANNEL.lower() else "",
        "Аккаунт для подарков не подключён (Telegram для бизнеса → Чат-боты)" if not game.setting("biz:conn") else "",
        "Нет авто-цен NFT: подключи /portals" if not (config.NFT_FLOORS_URL or config.PORTALS_AUTH or game.setting("portals:auth")) else "",
    ) if x]
    for a in config.ADMIN_IDS:
        try:
            await bot.send_message(a, f"🟢 <b>{config.APP_NAME}</b> запущен (v{VERSION})\n"
                                      + ("\n".join(f"⚠️ {x}" for x in issues) if issues else "✅ Настройки в порядке"))
        except Exception:  # noqa: BLE001
            pass
    log.info("Бот @%s запущен. Web App: %s", me.username, config.WEBAPP_URL)
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await services.stop()
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
