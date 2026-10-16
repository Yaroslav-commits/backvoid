"""Void Play — база данных (SQLite), проверка подписи Telegram и вся игровая логика.
Всё, что связано с деньгами, считается только здесь."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode

import config
from config import BY_ID, CATALOG, LEVELS, PACKS, TASKS, level_for


# =====================================================================
#  Проверка подписи Telegram Web App (initData)
#  https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
# =====================================================================
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
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    h = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": h})


# =====================================================================
#  База данных
# =====================================================================
SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY,
  username      TEXT,
  first_name    TEXT,
  last_name     TEXT,
  photo_url     TEXT,
  lang          TEXT,
  balance       INTEGER NOT NULL DEFAULT 0,
  subscribed    INTEGER NOT NULL DEFAULT 0,
  referrer      INTEGER,
  last_daily    TEXT,
  wallet        TEXT,
  banned        INTEGER NOT NULL DEFAULT 0,
  created       INTEGER NOT NULL,
  seen          INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS users_referrer ON users(referrer);

CREATE TABLE IF NOT EXISTS inventory (
  uid      TEXT PRIMARY KEY,
  user_id  INTEGER NOT NULL,
  gift_id  TEXT NOT NULL,
  created  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS inv_user ON inventory(user_id);

-- type: fee (ставка) | win (выигрыш) | dep (пополнение) | wd (вывод) | bonus | ref
CREATE TABLE IF NOT EXISTS transactions (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id   INTEGER NOT NULL,
  type      TEXT NOT NULL,
  amount    INTEGER NOT NULL,
  currency  TEXT,
  game      TEXT,
  item      TEXT,
  gift_id   TEXT,
  status    TEXT,
  extra     TEXT,
  created   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS tx_user ON transactions(user_id, id);
CREATE INDEX IF NOT EXISTS tx_type ON transactions(type, id);

CREATE TABLE IF NOT EXISTS task_claims (
  user_id INTEGER NOT NULL,
  task_id TEXT NOT NULL,
  created INTEGER NOT NULL,
  PRIMARY KEY (user_id, task_id)
);

CREATE TABLE IF NOT EXISTS promos (
  code      TEXT PRIMARY KEY,
  reward    INTEGER NOT NULL,
  uses_left INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS promo_uses (
  user_id INTEGER NOT NULL,
  code    TEXT NOT NULL,
  PRIMARY KEY (user_id, code)
);

-- id платежей Telegram, чтобы не зачислить дважды
CREATE TABLE IF NOT EXISTS payments (
  charge_id TEXT PRIMARY KEY,
  user_id   INTEGER NOT NULL,
  amount    INTEGER NOT NULL,
  created   INTEGER NOT NULL
);
"""


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.conn.executescript(SCHEMA)

    def q(self, sql: str, args=()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def one(self, sql: str, args=()):
        with self.lock:
            return self.conn.execute(sql, args).fetchone()

    def run(self, sql: str, args=()) -> int:
        with self.lock:
            cur = self.conn.execute(sql, args)
            return cur.lastrowid

    def tx(self):
        """with db.tx(): ... — атомарная транзакция (баланс + записи)."""
        return _Tx(self)


class _Tx:
    def __init__(self, db: DB):
        self.db = db

    def __enter__(self):
        self.db.lock.acquire()
        self.db.conn.execute("BEGIN IMMEDIATE")
        return self.db

    def __exit__(self, exc_type, exc, tb):
        try:
            self.db.conn.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self.db.lock.release()
        return False


# =====================================================================
#  Игровая логика
# =====================================================================
class GameError(Exception):
    """code уходит на фронт: no_stars, bad_amount, not_found, already, not_ready, banned"""

    def __init__(self, code: str, msg: str = ""):
        super().__init__(msg or code)
        self.code = code


def now() -> int:
    return int(time.time())


def season_bounds(ts: int | None = None) -> tuple[int, int]:
    d = datetime.fromtimestamp(ts or now(), tz=timezone.utc)
    start = datetime(d.year, d.month, 1, tzinfo=timezone.utc)
    end = datetime(d.year + (d.month == 12), d.month % 12 + 1, 1, tzinfo=timezone.utc)
    return int(start.timestamp()), int(end.timestamp())


class Game:
    def __init__(self, db: DB):
        self.db = db

    # ---------------- пользователи ----------------
    def upsert_user(self, tg: dict, start_param: str | None = None) -> tuple[dict, bool]:
        uid = int(tg["id"])
        row = self.db.one("SELECT * FROM users WHERE id=?", (uid,))
        fields = (tg.get("username") or "", tg.get("first_name") or "", tg.get("last_name") or "",
                  tg.get("photo_url") or "", tg.get("language_code") or "")
        if row:
            self.db.run("UPDATE users SET username=?, first_name=?, last_name=?, "
                        "photo_url=COALESCE(NULLIF(?, ''), photo_url), lang=?, seen=? WHERE id=?", (*fields, now(), uid))
            return dict(self.db.one("SELECT * FROM users WHERE id=?", (uid,))), False
        referrer = None
        if start_param and start_param.startswith("ref_"):
            try:
                r = int(start_param[4:])
                if r != uid and self.db.one("SELECT 1 FROM users WHERE id=?", (r,)):
                    referrer = r
            except ValueError:
                pass
        self.db.run("INSERT INTO users (id, username, first_name, last_name, photo_url, lang, referrer, created, seen) "
                    "VALUES (?,?,?,?,?,?,?,?,?)", (uid, *fields, referrer, now(), now()))
        return dict(self.db.one("SELECT * FROM users WHERE id=?", (uid,))), True

    def user(self, uid: int) -> dict:
        row = self.db.one("SELECT * FROM users WHERE id=?", (uid,))
        if not row:
            raise GameError("not_found", "user not found")
        if row["banned"]:
            raise GameError("banned")
        return dict(row)

    @staticmethod
    def public_user(row) -> dict:
        r = dict(row)
        name = " ".join(x for x in (r.get("first_name"), r.get("last_name")) if x) or (r.get("username") or "Игрок")
        return {"id": r["id"], "username": r.get("username") or "", "name": name, "avatar": f"/api/avatar/{r['id']}"}

    # ---------------- состояние для фронта ----------------
    def gift_view(self, gift_id: str, uid: str | None = None) -> dict:
        g = BY_ID.get(gift_id) or {"id": gift_id, "name": gift_id, "price": 0, "img": "assets/img/placeholder.svg"}
        return {**g, "uid": uid} if uid else dict(g)

    def inventory(self, uid: int) -> list[dict]:
        rows = self.db.q("SELECT uid, gift_id FROM inventory WHERE user_id=? ORDER BY created DESC", (uid,))
        return [self.gift_view(r["gift_id"], r["uid"]) for r in rows]

    def transactions(self, uid: int, limit: int = 30) -> list[dict]:
        rows = self.db.q("SELECT * FROM transactions WHERE user_id=? AND type IN ('fee','win','dep','wd','bonus','ref') "
                         "ORDER BY id DESC LIMIT ?", (uid, limit))
        out = []
        for r in rows:
            t = r["type"]
            out.append({
                "type": t,
                "amount": r["amount"], "currency": r["currency"] or "STARS",
                "game": r["game"], "item": r["item"] if t in ("fee", "win") else None,
                "nft": BY_ID[r["gift_id"]]["img"] if r["gift_id"] in BY_ID else None,
                "status": r["status"], "date": r["created"] * 1000,
            })
        return out

    def referrals(self, uid: int) -> dict:
        friends = self.db.q("SELECT * FROM users WHERE referrer=? ORDER BY created DESC LIMIT 100", (uid,))
        earned_rows = self.db.q("SELECT item, SUM(amount) s FROM transactions WHERE user_id=? AND type='ref' GROUP BY item", (uid,))
        earned_by = {r["item"]: r["s"] for r in earned_rows}
        dep = self.db.one("SELECT COALESCE(SUM(t.amount),0) s FROM transactions t JOIN users u ON u.id=t.user_id "
                          "WHERE u.referrer=? AND t.type='dep'", (uid,))["s"]
        lvl = level_for(dep)
        count = self.db.one("SELECT COUNT(*) c FROM users WHERE referrer=?", (uid,))["c"]
        return {
            "count": count,
            "earned": sum(earned_by.values()),
            "level": lvl["n"], "percent": lvl["pct"], "perFriend": config.REF_PER_FRIEND, "refDeposits": dep,
            "list": [{**self.public_user(f), "earned": earned_by.get(str(f["id"]), 0)} for f in friends],
        }

    def task_progress(self, uid: int, kind: str) -> int:
        if kind == "bets":
            return self.db.one("SELECT COUNT(*) c FROM transactions WHERE user_id=? AND type='fee'", (uid,))["c"]
        if kind == "subscribed":
            return self.user(uid)["subscribed"]
        if kind == "deposits":
            return self.db.one("SELECT COUNT(*) c FROM transactions WHERE user_id=? AND type='dep'", (uid,))["c"]
        if kind == "referrals":
            return self.db.one("SELECT COUNT(*) c FROM users WHERE referrer=?", (uid,))["c"]
        return 0

    def tasks(self, uid: int) -> list[dict]:
        claimed = {r["task_id"] for r in self.db.q("SELECT task_id FROM task_claims WHERE user_id=?", (uid,))}
        _, end = season_bounds()
        return [{
            "id": t["id"], "title": t["title"], "text": t["text"], "reward": t["reward"], "goal": t["goal"],
            "progress": min(t["goal"], self.task_progress(uid, t["kind"])),
            "claimed": t["id"] in claimed, "endsIn": max(0, end - now()) * 1000,
        } for t in TASKS]

    def state(self, uid: int) -> dict:
        u = self.user(uid)
        return {
            "user": {**self.public_user(u), "photo": u["photo_url"] or ""},
            "balance": {"stars": u["balance"]},
            "subscribed": bool(u["subscribed"]),
            "wallet": u["wallet"],
            "inventory": self.inventory(uid),
            "transactions": self.transactions(uid),
            "referrals": self.referrals(uid),
            "tasks": self.tasks(uid),
            "catalog": CATALOG,
            "packs": PACKS,
            "levels": LEVELS,
            "rates": {"usdToStars": 50, "tonToStars": 120},
            "config": {
                "channelUrl": config.CHANNEL_URL, "supportUrl": config.SUPPORT_URL, "giftsAccount": config.GIFTS_ACCOUNT,
                "subscribeReward": config.SUBSCRIBE_REWARD, "minWithdraw": config.MIN_WITHDRAW,
                "houseEdge": config.HOUSE_EDGE, "maxChance": config.MAX_CHANCE,
            },
        }

    # ---------------- баланс ----------------
    def _add_tx(self, uid, type_, amount, currency="STARS", game=None, item=None, gift_id=None, status=None, extra=None):
        return self.db.run("INSERT INTO transactions (user_id, type, amount, currency, game, item, gift_id, status, extra, created) "
                           "VALUES (?,?,?,?,?,?,?,?,?,?)", (uid, type_, amount, currency, game, item, gift_id, status, extra, now()))

    def _change_balance(self, uid: int, delta: int):
        if delta < 0:
            row = self.db.one("SELECT balance FROM users WHERE id=?", (uid,))
            if not row or row["balance"] < -delta:
                raise GameError("no_stars")
        self.db.run("UPDATE users SET balance=balance+? WHERE id=?", (delta, uid))

    def balance(self, uid: int) -> int:
        return self.db.one("SELECT balance FROM users WHERE id=?", (uid,))["balance"]

    # ---------------- бонусы ----------------
    def daily(self, uid: int) -> int:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self.db.tx():
            u = self.user(uid)
            if u["last_daily"] == today:
                raise GameError("already")
            self.db.run("UPDATE users SET last_daily=? WHERE id=?", (today, uid))
            self._change_balance(uid, config.DAILY_REWARD)
            self._add_tx(uid, "bonus", config.DAILY_REWARD, item="daily")
        return config.DAILY_REWARD

    def reward_subscription(self, uid: int) -> int:
        with self.db.tx():
            u = self.user(uid)
            if u["subscribed"]:
                return 0
            self.db.run("UPDATE users SET subscribed=1 WHERE id=?", (uid,))
            self._change_balance(uid, config.SUBSCRIBE_REWARD)
            self._add_tx(uid, "bonus", config.SUBSCRIBE_REWARD, item="subscribe")
        return config.SUBSCRIBE_REWARD

    def promo(self, uid: int, code: str) -> int:
        code = code.strip().upper()
        with self.db.tx():
            p = self.db.one("SELECT * FROM promos WHERE code=?", (code,))
            if not p or p["uses_left"] <= 0:
                raise GameError("not_found")
            if self.db.one("SELECT 1 FROM promo_uses WHERE user_id=? AND code=?", (uid, code)):
                raise GameError("already")
            self.db.run("INSERT INTO promo_uses (user_id, code) VALUES (?,?)", (uid, code))
            self.db.run("UPDATE promos SET uses_left=uses_left-1 WHERE code=?", (code,))
            self._change_balance(uid, p["reward"])
            self._add_tx(uid, "bonus", p["reward"], item=f"promo:{code}")
        return p["reward"]

    def claim_task(self, uid: int, task_id: str) -> int:
        t = next((x for x in TASKS if x["id"] == task_id), None)
        if not t:
            raise GameError("not_found")
        with self.db.tx():
            if self.db.one("SELECT 1 FROM task_claims WHERE user_id=? AND task_id=?", (uid, task_id)):
                raise GameError("already")
            if self.task_progress(uid, t["kind"]) < t["goal"]:
                raise GameError("not_ready")
            self.db.run("INSERT INTO task_claims (user_id, task_id, created) VALUES (?,?,?)", (uid, task_id, now()))
            self._change_balance(uid, t["reward"])
            self._add_tx(uid, "bonus", t["reward"], item=f"task:{task_id}")
        return t["reward"]

    # ---------------- апгрейдер ----------------
    @staticmethod
    def chance(stake: int, price: int) -> float:
        if stake <= 0 or price <= 0:
            return 0.0
        return max(0.0001, min(config.MAX_CHANCE, stake / price * (1 - config.HOUSE_EDGE)))

    def upgrade(self, uid: int, target_id: str, stake: int = 0, gift_uid: str | None = None) -> dict:
        target = BY_ID.get(target_id)
        if not target:
            raise GameError("not_found")
        with self.db.tx():
            self.user(uid)
            if gift_uid:
                g = self.db.one("SELECT * FROM inventory WHERE uid=? AND user_id=?", (gift_uid, uid))
                if not g:
                    raise GameError("not_found")
                value = BY_ID.get(g["gift_id"], {"price": 0})["price"]
                self.db.run("DELETE FROM inventory WHERE uid=?", (gift_uid,))
                self._add_tx(uid, "fee", value, game="Апгрейдер", item="nft", gift_id=g["gift_id"])
            else:
                value = int(stake)
                if value < config.MIN_BET:
                    raise GameError("bad_amount")
                self._change_balance(uid, -value)
                self._add_tx(uid, "fee", value, game="Апгрейдер", item="stars")
            ch = self.chance(value, target["price"])
            roll = secrets.randbelow(1_000_000) / 1_000_000
            win = roll < ch
            if win:
                self.db.run("INSERT INTO inventory (uid, user_id, gift_id, created) VALUES (?,?,?,?)",
                            (secrets.token_hex(8), uid, target_id, now()))
                self._add_tx(uid, "win", target["price"], game="Апгрейдер", item="nft", gift_id=target_id)
        return {"win": win, "chance": ch, "roll": roll, "balance": self.balance(uid), "inventory": self.inventory(uid)}

    # ---------------- пополнение / вывод ----------------
    def credit_deposit(self, uid: int, amount: int, charge_id: str, bonus: int = 0) -> bool:
        """Зачислить оплату Stars. Возвращает False, если платёж уже был зачислен."""
        with self.db.tx():
            if self.db.one("SELECT 1 FROM payments WHERE charge_id=?", (charge_id,)):
                return False
            self.db.run("INSERT INTO payments (charge_id, user_id, amount, created) VALUES (?,?,?,?)", (charge_id, uid, amount, now()))
            first = not self.db.one("SELECT 1 FROM transactions WHERE user_id=? AND type='dep'", (uid,))
            self._change_balance(uid, amount + bonus)
            self._add_tx(uid, "dep", amount + bonus, currency="STARS", extra=charge_id)
            u = self.user(uid)
            if u["referrer"]:
                ref = self.referrals(u["referrer"])
                share = amount * ref["percent"] // 100 + (config.REF_PER_FRIEND if first else 0)
                if share > 0:
                    self._change_balance(u["referrer"], share)
                    self._add_tx(u["referrer"], "ref", share, item=str(uid))
        return True

    def withdraw(self, uid: int, amount: int, method: str = "stars") -> int:
        if amount < config.MIN_WITHDRAW:
            raise GameError("bad_amount")
        with self.db.tx():
            self._change_balance(uid, -amount)
            return self._add_tx(uid, "wd", amount, currency=method.upper(), status="pending")

    # ---------------- лента, лидеры ----------------
    def feed(self, limit: int = 20) -> list[dict]:
        rows = self.db.q("SELECT t.id AS tid, t.amount AS tamount, t.gift_id AS tgift, t.created AS tcreated, u.* "
                         "FROM transactions t JOIN users u ON u.id=t.user_id "
                         "WHERE t.type='win' ORDER BY t.id DESC LIMIT ?", (limit,))
        return [{"id": r["tid"], "price": r["tamount"], "gift": self.gift_view(r["tgift"]), "date": r["tcreated"] * 1000,
                 "user": self.public_user(r)} for r in rows]

    def leaders(self, limit: int = 50) -> dict:
        start, end = season_bounds()
        rows = self.db.q("SELECT u.*, SUM(t.amount) total FROM transactions t JOIN users u ON u.id=t.user_id "
                         "WHERE t.type='fee' AND t.created>=? GROUP BY u.id ORDER BY total DESC LIMIT ?", (start, limit))
        out = []
        for r in rows:
            best = self.db.one("SELECT gift_id FROM inventory WHERE user_id=?", (r["id"],))
            items = self.inventory(r["id"])
            top = max(items, key=lambda g: g["price"]) if items else (self.gift_view(best["gift_id"]) if best else None)
            out.append({**self.public_user(r), "value": r["total"], "nft": top})
        players = self.db.one("SELECT COUNT(DISTINCT user_id) c FROM transactions WHERE type='fee' AND created>=?", (start,))["c"]
        n = datetime.fromtimestamp(start, tz=timezone.utc).month
        return {"season": n, "start": start * 1000, "end": end * 1000, "players": players, "top": out}

    # ---------------- админка ----------------
    def admin_add_balance(self, uid: int, amount: int):
        with self.db.tx():
            self.user(uid)
            self._change_balance(uid, amount)
            self._add_tx(uid, "bonus" if amount > 0 else "wd", abs(amount), item="admin")

    def admin_give_gift(self, uid: int, gift_id: str):
        if gift_id not in BY_ID:
            raise GameError("not_found")
        self.user(uid)
        self.db.run("INSERT INTO inventory (uid, user_id, gift_id, created) VALUES (?,?,?,?)", (secrets.token_hex(8), uid, gift_id, now()))
        self._add_tx(uid, "dep", BY_ID[gift_id]["price"], currency="NFT", gift_id=gift_id)

    def admin_promo(self, code: str, reward: int, uses: int):
        self.db.run("INSERT OR REPLACE INTO promos (code, reward, uses_left) VALUES (?,?,?)", (code.strip().upper(), reward, uses))
