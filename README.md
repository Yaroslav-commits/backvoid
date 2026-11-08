# Void Play — бот и API

Telegram-бот (aiogram 3) и HTTP API для сайта в одном процессе.

| Файл | Что внутри |
|---|---|
| `main.py` | бот, API `/api/*`, запуск |
| `game.py` | база SQLite и вся игровая логика (апгрейдер, мины, бонусы, рефералка) |
| `config.py` | настройки, каталог NFT, задания, уровни |

## Запуск на bothost.ru

1. Залей репозиторий, команда запуска: `python main.py`.
2. В переменных окружения задай `BOT_TOKEN`, `WEBAPP_URL` и `PORT` (см. `.env.example`).
3. Включи в панели домен для бота — по нему сайт ходит в API.
4. Открой в браузере `https://<домен-бота>/api/health` — должно показать `"ok": true`.
5. Впиши этот домен в `config.js` сайта (`API_BASE`).

### Вариант без CORS
Если положить сайт рядом с ботом и задать `WEBAPP_DIR=путь/к/сайту`, бот сам раздаёт
сайт со своего домена. Тогда `WEBAPP_URL=https://<домен-бота>/`, а `API_BASE` в `config.js` — пустая строка.

## Первый заработок без вложений
Новый игрок сразу получает `WELCOME_BONUS` звёзд (по умолчанию 10, `0` — выключить),
дальше — подписка, ежедневный бонус, задание «5 ставок» и рефералы. Пополнять не нужно.

## API

| Метод | Путь | Что делает |
|---|---|---|
| GET | `/api/health` | проверка связи |
| POST | `/api/auth` | вход по initData, всё состояние игрока |
| GET | `/api/feed`, `/api/leaders` | лента выигрышей, лидеры сезона |
| POST | `/api/daily`, `/api/promo`, `/api/subscribe/check`, `/api/tasks/claim` | бонусы |
| POST | `/api/upgrade` | апгрейдер |
| POST | `/api/mines/start`, `/api/mines/open`, `/api/mines/cashout` | мины |
| POST | `/api/invoice`, `/api/withdraw` | пополнение Stars, вывод |

Все запросы подписаны `X-Init-Data` (проверка подписи Telegram).

## Админ-команды
`/addstars id кол-во` · `/givegift id gift_id` · `/promo КОД награда активаций` · `/stats`
