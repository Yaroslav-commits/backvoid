"""Проверка подписи Telegram Web App (initData).

https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


class AuthError(Exception):
    pass


def verify_init_data(init_data: str, bot_token: str, max_age: int = 86400 * 7) -> dict:
    """Возвращает {'user': {...}, 'start_param': str|None}. Бросает AuthError, если подпись неверна."""
    if not init_data:
        raise AuthError("no init data")
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received = pairs.pop("hash", "")
    if not received:
        raise AuthError("no hash")
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, received):
        raise AuthError("bad hash")
    auth_date = int(pairs.get("auth_date", "0") or 0)
    if max_age and time.time() - auth_date > max_age:
        raise AuthError("init data expired")
    try:
        user = json.loads(pairs.get("user", "{}"))
    except json.JSONDecodeError as e:
        raise AuthError("bad user json") from e
    if not user.get("id"):
        raise AuthError("no user")
    return {"user": user, "start_param": pairs.get("start_param")}


def sign_init_data(fields: dict, bot_token: str) -> str:
    """Для тестов: собрать валидную строку initData."""
    from urllib.parse import urlencode
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    h = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": h})
