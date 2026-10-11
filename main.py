<<<<<<< HEAD
"""
Void Play — бот + API для Web App в одном процессе.

  • aiogram 3: /start, кнопка «Играть», оплата Telegram Stars, админ-команды
  • aiohttp на PORT (8080): /api/* для Web App (сам сайт лежит отдельно, на GitHub Pages)

Запуск:  pip install -r requirements.txt  →  python main.py
Настройки — переменные окружения (см. .env.example).
"""
from __future__ import annotations

=======
>>>>>>> e3a9820361e8aee10e621e3f9e7616973a666676
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import MenuButtonWebApp, WebAppInfo
from aiohttp import web

from backend import config
from backend.bot import build_router
from backend.db import DB
from backend.game import Game
from backend.web import build_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("voidplay")


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
    log.info("API слушает %s:%s, статика: %s", config.HOST, config.PORT, "да" if config.SERVE_STATIC else "нет")

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
