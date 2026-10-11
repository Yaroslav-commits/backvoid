"""
Void Play — запуск бота (aiogram 3) + раздача Web App.

Что делает:
  • /start → приветствие и кнопка «Играть», открывающая Web App по WEBAPP_URL
  • ставит кнопку меню (слева от поля ввода) тоже на Web App
  • ловит реферальные ссылки: t.me/<bot>?start=ref_123
  • (по желанию) сам раздаёт файлы фронтенда на SERVE_PORT, если SERVE_STATIC=1

Запуск:
  pip install -r requirements.txt
  cp .env.example .env   # вписать BOT_TOKEN и WEBAPP_URL
  python main.py
"""
import asyncio
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
    Message,
    WebAppInfo,
)
from aiohttp import web
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "")          # https://... — Telegram принимает только HTTPS
SERVE_STATIC = os.getenv("SERVE_STATIC", "0") == "1"
SERVE_HOST = os.getenv("SERVE_HOST", "0.0.0.0")
SERVE_PORT = int(os.getenv("SERVE_PORT", "8080"))

BASE_DIR = Path(__file__).resolve().parent
# Наружу отдаём только фронтенд — main.py и .env никто не скачает
STATIC_DIRS = ("css", "js", "assets")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("voidplay")

dp = Dispatcher()


def play_keyboard(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🎮 Играть", web_app=WebAppInfo(url=url)),
    ]])


@dp.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject) -> None:
    url = WEBAPP_URL
    # Реферальная ссылка: /start ref_123 → пробрасываем в URL Web App как ?ref=123
    args = command.args or ""
    if args.startswith("ref_"):
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}ref={args[4:]}"
        # TODO: сохранить в БД, кто кого пригласил (message.from_user.id ← args[4:])
        log.info("user %s came by ref %s", message.from_user.id, args[4:])

    name = message.from_user.first_name if message.from_user else "игрок"
    await message.answer(
        f"Привет, <b>{name}</b>! 👋\n\n"
        "Добро пожаловать в <b>Void Play</b> — апгрейдер, кейсы, мины и другие игры за звёзды и подарки.\n\n"
        "Жми кнопку ниже, чтобы начать 👇",
        reply_markup=play_keyboard(url),
    )


@dp.message(F.text)
async def any_text(message: Message) -> None:
    await message.answer("Открой приложение кнопкой ниже 👇", reply_markup=play_keyboard(WEBAPP_URL))


# ---------- Раздача фронтенда (если не хостишь его отдельно) ----------
async def index(_: web.Request) -> web.FileResponse:
    return web.FileResponse(BASE_DIR / "index.html", headers={"Cache-Control": "no-cache"})


def build_web_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", index)
    for d in STATIC_DIRS:
        if (BASE_DIR / d).is_dir():
            app.router.add_static(f"/{d}/", BASE_DIR / d)
    return app


async def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("Укажи BOT_TOKEN в .env")
    if not WEBAPP_URL.startswith("https://"):
        raise SystemExit("WEBAPP_URL должен начинаться с https:// (требование Telegram)")

    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    # Кнопка меню слева от поля ввода → тоже открывает Web App
    await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Играть", web_app=WebAppInfo(url=WEBAPP_URL)))

    runner = None
    if SERVE_STATIC:
        runner = web.AppRunner(build_web_app())
        await runner.setup()
        await web.TCPSite(runner, SERVE_HOST, SERVE_PORT).start()
        log.info("Web App отдаётся на http://%s:%s (наружу нужен HTTPS: nginx/cloudflared/ngrok)", SERVE_HOST, SERVE_PORT)

    me = await bot.get_me()
    log.info("Бот @%s запущен, Web App: %s", me.username, WEBAPP_URL)
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        if runner:
            await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
