"""Хендлеры бота: /start, оплата Stars, админ-команды."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    PreCheckoutQuery,
    WebAppInfo,
)

from . import config
from .catalog import BY_ID
from .game import Game, GameError

log = logging.getLogger("voidplay.bot")


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
            f"Привет, <b>{u.first_name}</b>! <tg-emoji emoji-id='5199885118214255386'>👋</tg-emoji>\n\n"
            "Добро пожаловать в <b>Void Play</b> — апгрейдер, кейсы, мины и другие игры за звёзды и подарки.\n\n"
            "Жми «Играть» <tg-emoji emoji-id='5231102735817918643'>👇</tg-emoji>",
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
