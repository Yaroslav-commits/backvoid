"""Void Play — «Самолётик» (краш). Игроки играют против проекта.

Как это работает
  • Раунд: CRASH_BET_SECONDS идёт приём ставок, потом самолётик взлетает, множитель растёт с x1.00.
  • В случайный момент самолётик улетает. Кто успел нажать «Забрать» (или сработал автовывод) — получает ставку × множитель,
    остальные теряют ставку.

Почему проект в плюсе (а не «повезёт — не повезёт»)
  Точка краша считается так, что ВЕРОЯТНОСТЬ долететь до xC ровно (1 − CRASH_EDGE) / C.
  Значит, на какой бы множитель игрок ни целился, в среднем он получает назад (1 − CRASH_EDGE) от ставки:
  при CRASH_EDGE = 0.04 с каждых 1000 ⭐ ставок проекту в среднем остаётся 40 ⭐. Это преимущество открытое — как в любом казино,
  и честное: точка краша зашифрована в hash до начала раунда, после раунда раскрывается seed — любой может пересчитать.
  Защита от крупного разового выигрыша: CRASH_MAX_BET, CRASH_MAX_WIN (потолок выплаты одной ставки) и CRASH_MAX_MULT.
"""
from __future__ import annotations

import hashlib
import math
import secrets
import time

import config

CRASH_SCHEMA = """
CREATE TABLE IF NOT EXISTS crash_rounds (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  status    TEXT NOT NULL,            -- open (ставки/полёт) | done
  seed      TEXT NOT NULL,
  hash      TEXT NOT NULL,
  crash     REAL NOT NULL,            -- точка краша (наружу — только после раунда)
  created   INTEGER NOT NULL,         -- мс
  start_ms  INTEGER NOT NULL,         -- взлёт
  crash_ms  INTEGER NOT NULL,         -- момент краша
  pot       INTEGER NOT NULL DEFAULT 0,
  paid      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS crash_bets (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  round_id INTEGER NOT NULL,
  user_id  INTEGER NOT NULL,
  amount   INTEGER NOT NULL,
  auto     REAL,                      -- автовывод на этом множителе (NULL — нет)
  cashout  REAL,                      -- на каком множителе забрал
  payout   INTEGER NOT NULL DEFAULT 0,
  created  INTEGER NOT NULL,
  UNIQUE (round_id, user_id)
);
CREATE INDEX IF NOT EXISTS crash_bets_round ON crash_bets(round_id);
"""


def now_ms() -> int:
    return int(time.time() * 1000)


def crash_point(seed: str, round_id: int) -> float:
    """Точка краша из seed: P(краш ≥ x) = (1 − EDGE) / x. Около EDGE раундов заканчиваются сразу на x1.00."""
    h = hashlib.sha256(f"{seed}:{round_id}".encode()).hexdigest()
    u = int(h[:13], 16) / 16 ** 13                       # 0 ≤ u < 1
    x = (1 - config.CRASH_EDGE) / (1 - u)
    x = math.floor(x * 100) / 100
    return max(1.0, min(config.CRASH_MAX_MULT, x))


def mult_at(ms: int) -> float:
    """Множитель через ms миллисекунд после взлёта: x2 примерно через 7 секунд."""
    return math.floor(math.exp(config.CRASH_RATE * max(0, ms)) * 100) / 100


def ms_for(mult: float) -> int:
    return int(math.ceil(math.log(max(1.0, mult)) / config.CRASH_RATE))


class CrashMixin:
    """Подмешивается в Game: использует self.db, self.user, self._change_balance, self._add_tx, self.public_user."""

    def _crash_round(self):
        """Текущий раунд; если прошлый закончился и показан — начинаем новый."""
        r = self.db.one("SELECT * FROM crash_rounds ORDER BY id DESC LIMIT 1")
        t = now_ms()
        if r and (r["status"] == "open" or t < r["crash_ms"] + config.CRASH_SHOW_SECONDS * 1000):
            return r
        seed = secrets.token_hex(16)
        rid = self.db.run("INSERT INTO crash_rounds (status, seed, hash, crash, created, start_ms, crash_ms) VALUES ('open',?,?,1,?,?,?)",
                          (seed, "", t, t + config.CRASH_BET_SECONDS * 1000, 0))
        x = crash_point(seed, rid)
        start = t + config.CRASH_BET_SECONDS * 1000
        self.db.run("UPDATE crash_rounds SET hash=?, crash=?, crash_ms=? WHERE id=?",
                    (hashlib.sha256(seed.encode()).hexdigest(), x, start + ms_for(x), rid))
        return self.db.one("SELECT * FROM crash_rounds WHERE id=?", (rid,))

    def _crash_pay(self, r, b, mult: float):
        pay = min(int(b["amount"] * mult), config.CRASH_MAX_WIN)
        self.db.run("UPDATE crash_bets SET cashout=?, payout=? WHERE id=?", (mult, pay, b["id"]))
        self._change_balance(b["user_id"], pay)
        self._add_tx(b["user_id"], "win", pay, game="Самолётик", item="stars", extra=f"crash:{r['id']}:x{mult:.2f}")

    def crash_tick(self) -> bool:
        """Автовыводы, которые уже пройдены, и закрытие раунда после краша. True — раунд только что закрылся."""
        with self.db.tx():
            r = self.db.one("SELECT * FROM crash_rounds WHERE status='open' ORDER BY id DESC LIMIT 1")
            if not r:
                return False
            t = now_ms()
            if t < r["start_ms"]:
                return False
            flying = t < r["crash_ms"]
            reached = mult_at(t - r["start_ms"]) if flying else r["crash"]
            for b in self.db.q("SELECT * FROM crash_bets WHERE round_id=? AND cashout IS NULL AND auto IS NOT NULL AND auto<=?",
                               (r["id"], reached)):
                self._crash_pay(r, b, b["auto"])
            if flying:
                return False
            pot = self.db.one("SELECT COALESCE(SUM(amount),0) s FROM crash_bets WHERE round_id=?", (r["id"],))["s"]
            paid = self.db.one("SELECT COALESCE(SUM(payout),0) s FROM crash_bets WHERE round_id=?", (r["id"],))["s"]
            self.db.run("UPDATE crash_rounds SET status='done', pot=?, paid=? WHERE id=?", (pot, paid, r["id"]))
            if pot or paid:   # заработок проекта за раунд (бывает и минус — он перекрывается на дистанции)
                self.db.run("INSERT INTO house (game, amount, created) VALUES (?,?,?)", ("crash", pot - paid, int(time.time())))
            return True

    # ---------------- действия игрока ----------------
    def crash_bet(self, uid: int, amount: int, auto: float | None = None) -> dict:
        from game import GameError  # noqa: PLC0415 — game импортирует этот модуль
        amount = int(amount)
        auto = round(float(auto), 2) if auto else None
        if amount < config.MIN_BET or amount > config.CRASH_MAX_BET:
            raise GameError("bad_amount")
        if auto is not None and not 1.01 <= auto <= config.CRASH_MAX_MULT:
            raise GameError("bad_amount")
        self.require_open("crash")
        self.crash_tick()
        with self.db.tx():
            self.user(uid)
            r = self._crash_round()
            if r["status"] != "open" or now_ms() >= r["start_ms"]:
                raise GameError("wait")
            if self.db.one("SELECT 1 FROM crash_bets WHERE round_id=? AND user_id=?", (r["id"], uid)):
                raise GameError("already")
            self._change_balance(uid, -amount)
            self._add_tx(uid, "fee", amount, game="Самолётик", item="stars", extra=f"crash:{r['id']}")
            self.db.run("INSERT INTO crash_bets (round_id, user_id, amount, auto, created) VALUES (?,?,?,?,?)",
                        (r["id"], uid, amount, auto, now_ms()))
        return self.crash_state(uid)

    def crash_cancel(self, uid: int) -> dict:
        """Забрать ставку, пока самолётик не взлетел."""
        from game import GameError  # noqa: PLC0415
        with self.db.tx():
            r = self._crash_round()
            b = self.db.one("SELECT * FROM crash_bets WHERE round_id=? AND user_id=?", (r["id"], uid))
            if not b or r["status"] != "open" or now_ms() >= r["start_ms"] - 300:
                raise GameError("not_ready")
            self.db.run("DELETE FROM crash_bets WHERE id=?", (b["id"],))
            self.db.run("DELETE FROM transactions WHERE user_id=? AND type='fee' AND extra=?", (uid, f"crash:{r['id']}"))
            self._change_balance(uid, b["amount"])
        return self.crash_state(uid)

    def crash_cashout(self, uid: int) -> dict:
        """«Забрать»: по множителю на момент запроса на сервере — если самолётик ещё в воздухе."""
        from game import GameError  # noqa: PLC0415
        self.crash_tick()
        with self.db.tx():
            r = self.db.one("SELECT * FROM crash_rounds WHERE status='open' ORDER BY id DESC LIMIT 1")
            t = now_ms()
            if not r:   # раунд уже закрыт: если игрок в нём был — он просто опоздал
                last = self.db.one("SELECT r.id FROM crash_rounds r JOIN crash_bets b ON b.round_id=r.id AND b.user_id=? "
                                   "WHERE b.cashout IS NULL ORDER BY r.id DESC LIMIT 1", (uid,))
                raise GameError("crashed" if last else "not_ready")
            if t < r["start_ms"]:
                raise GameError("not_ready")
            b = self.db.one("SELECT * FROM crash_bets WHERE round_id=? AND user_id=?", (r["id"], uid))
            if not b:
                raise GameError("not_found")
            if b["cashout"] is not None:
                raise GameError("already")
            if t >= r["crash_ms"]:
                raise GameError("crashed")
            m = min(mult_at(t - r["start_ms"]), r["crash"])
            if m < 1.01:
                m = 1.0
            self._crash_pay(r, b, m)
        return self.crash_state(uid)

    # ---------------- состояние для сайта ----------------
    def crash_state(self, uid: int | None = None) -> dict:
        self.crash_tick()
        with self.db.tx():
            r = self._crash_round()
        t = now_ms()
        done = r["status"] == "done" or t >= r["crash_ms"]
        phase = "bet" if t < r["start_ms"] else "crashed" if done else "fly"
        bets = self.db.q("SELECT b.id AS bid, b.amount AS bamount, b.auto AS bauto, b.cashout AS bcash, b.payout AS bpay, u.* "
                         "FROM crash_bets b JOIN users u ON u.id=b.user_id WHERE b.round_id=? ORDER BY b.amount DESC LIMIT 60", (r["id"],))
        players = [{**self.public_user(b), "amount": b["bamount"], "cashout": b["bcash"], "payout": b["bpay"]} for b in bets]
        mine = self.db.one("SELECT * FROM crash_bets WHERE round_id=? AND user_id=?", (r["id"], uid)) if uid else None
        stats = self.db.one("SELECT COUNT(*) c, COALESCE(SUM(amount),0) s FROM crash_bets WHERE round_id=?", (r["id"],))
        hist = self.db.q("SELECT id, crash FROM crash_rounds WHERE status='done' ORDER BY id DESC LIMIT 24")
        out = {
            "id": r["id"], "phase": phase, "now": t, "startAt": r["start_ms"], "rate": config.CRASH_RATE,
            "betSeconds": config.CRASH_BET_SECONDS, "hash": r["hash"], "open": self.game_open("crash"),
            "players": players, "count": stats["c"], "total": stats["s"],
            "my": {"amount": mine["amount"], "auto": mine["auto"], "cashout": mine["cashout"], "payout": mine["payout"]} if mine else None,
            "history": [{"id": h["id"], "x": h["crash"]} for h in hist],
            "minBet": config.MIN_BET, "maxBet": config.CRASH_MAX_BET, "maxWin": config.CRASH_MAX_WIN,
            "maxMult": config.CRASH_MAX_MULT, "edge": config.CRASH_EDGE,
        }
        if phase == "fly":
            out["mult"] = mult_at(t - r["start_ms"])
        if done:   # точку краша и seed отдаём только после краша
            out.update({"crash": r["crash"], "crashAt": r["crash_ms"], "seed": r["seed"],
                        "nextAt": r["crash_ms"] + config.CRASH_SHOW_SECONDS * 1000})
        return out

    def crash_prune(self):
        """Старые пустые раунды не нужны: удаляем те, где никто не играл, старше суток."""
        self.db.run("DELETE FROM crash_rounds WHERE status='done' AND created < ? AND NOT EXISTS "
                    "(SELECT 1 FROM crash_bets b WHERE b.round_id=crash_rounds.id)", (now_ms() - 86400_000,))
