"""Void Play — PvP: «Рулетка» и «Огонь и лёд». Игроки играют друг против друга, проект берёт комиссию.

Рулетка      — общий банк; шанс каждого = его ставка / банк; победитель забирает банк.
Огонь и лёд  — две стороны; шанс стороны = её банк / общий банк; победившая сторона делит
               банк проигравших пропорционально своим ставкам.

Комиссия (config.PVP_FEE) берётся только с выигрыша — со ставок проигравших.
Честность: в начале раунда показываем hash = sha256(seed), после раунда раскрываем seed.
Случайное число раунда = sha256(seed:id раунда) → любой может перепроверить.
"""
from __future__ import annotations

import hashlib
import secrets
import time

import config

PVP_GAMES = {"roulette": "Рулетка", "fireice": "Огонь и лёд"}
SIDES = ("fire", "ice")


def now_ms() -> int:
    return int(time.time() * 1000)


def roll_of(seed: str, round_id: int) -> float:
    """Число 0..1, по которому выбирается победитель."""
    h = hashlib.sha256(f"{seed}:{round_id}".encode()).hexdigest()
    return int(h[:13], 16) / 16 ** 13


class PvpMixin:
    """Подмешивается в Game: использует self.db, self.user, self._change_balance, self._add_tx, self.public_user."""

    # ---------------- раунды ----------------
    def _pvp_round(self, game: str):
        """Текущий раунд: открытый, либо только что сыгранный (пока показываем результат). Иначе создаём новый."""
        row = self.db.one("SELECT * FROM pvp_rounds WHERE game=? ORDER BY id DESC LIMIT 1", (game,))
        if row and (row["status"] == "open" or now_ms() < row["resolved_at"] + config.PVP_SHOW_SECONDS * 1000):
            return row
        seed = secrets.token_hex(16)
        rid = self.db.run("INSERT INTO pvp_rounds (game, status, seed, hash, created) VALUES (?,?,?,?,?)",
                          (game, "open", seed, hashlib.sha256(seed.encode()).hexdigest(), now_ms()))
        return self.db.one("SELECT * FROM pvp_rounds WHERE id=?", (rid,))

    def _pvp_bets(self, round_id: int) -> list:
        return self.db.q("SELECT b.id AS bid, b.amount AS bamount, b.side AS bside, b.payout AS bpayout, u.* "
                         "FROM pvp_bets b JOIN users u ON u.id=b.user_id WHERE b.round_id=? ORDER BY b.id", (round_id,))

    def pvp_tick(self, game: str) -> bool:
        """Разыграть раунд, если таймер вышел. Вызывается и по таймеру сервера, и при каждом запросе."""
        with self.db.tx():
            row = self.db.one("SELECT * FROM pvp_rounds WHERE game=? ORDER BY id DESC LIMIT 1", (game,))
            if row and row["status"] == "open" and row["ends_at"] and now_ms() >= row["ends_at"]:
                self._pvp_resolve(row)
                return True
        return False

    def _pvp_resolve(self, r):
        bets = self._pvp_bets(r["id"])
        pot = sum(b["bamount"] for b in bets)
        roll = roll_of(r["seed"], r["id"])
        title = PVP_GAMES[r["game"]]
        paid = 0
        if r["game"] == "roulette":
            # «билет» roll*банк попадает в отрезок одного из игроков (отрезки идут в порядке входа в раунд)
            ticket, acc, w = roll * pot, 0, bets[-1]
            for b in bets:
                acc += b["bamount"]
                if ticket < acc:
                    w = b
                    break
            # подарки из банка целиком уходят победителю; комиссия берётся только из звёзд банка
            gifts = self.db.q("SELECT * FROM pvp_gifts WHERE round_id=?", (r["id"],))
            stars_in = pot - sum(g["value"] for g in gifts)
            fee = min(int((pot - w["bamount"]) * config.PVP_FEE), stars_in)
            payouts = {w["bid"]: pot - fee}
            for g in gifts:
                self.db.run("INSERT INTO inventory (uid, user_id, gift_id, created) VALUES (?,?,?,?)", (g["uid"], w["id"], g["gift_id"], int(time.time())))
                if g["gift_id"].startswith("nft:"):
                    self.db.run("UPDATE nfts SET user_id=?, status='in' WHERE slug=?", (w["id"], g["gift_id"][4:]))
            gift_value = pot - stars_in
            winner, chance = str(w["id"]), w["bamount"] / pot
        else:
            pools = {s: sum(b["bamount"] for b in bets if b["bside"] == s) for s in SIDES}
            winner = "fire" if roll < pools["fire"] / pot else "ice"
            win_pool, lose_pool = pools[winner], pot - pools[winner]
            share = lose_pool - int(lose_pool * config.PVP_FEE)
            payouts = {b["bid"]: b["bamount"] + share * b["bamount"] // win_pool for b in bets if b["bside"] == winner}
            chance = win_pool / pot
            gift_value = 0
        for b in bets:
            p = payouts.get(b["bid"], 0)
            if p:
                self._change_balance(b["id"], p - gift_value)
                self._add_tx(b["id"], "win", p, game=title, item="stars", extra=f"pvp:{r['id']}")
                self.db.run("UPDATE pvp_bets SET payout=? WHERE id=?", (p, b["bid"]))
                paid += p
        self.db.run("DELETE FROM pvp_gifts WHERE round_id=?", (r["id"],))
        fee = pot - paid  # комиссия + копейки от округления
        self.db.run("UPDATE pvp_rounds SET status='done', resolved_at=?, winner=?, win_chance=?, roll=?, pot=?, fee=? WHERE id=?",
                    (now_ms(), winner, chance, roll, pot, fee, r["id"]))
        if fee > 0:
            self.db.run("INSERT INTO house (game, amount, created) VALUES (?,?,?)", (r["game"], fee, int(time.time())))

    # ---------------- действия игрока ----------------
    def pvp_bet(self, uid: int, game: str, amount: int, side: str | None = None, gift_uid: str | None = None) -> dict:
        from game import GameError  # noqa: PLC0415 — game импортирует этот модуль
        if game not in PVP_GAMES:
            raise GameError("not_found")
        amount = int(amount)
        if game == "fireice" and side not in SIDES:
            raise GameError("bad_side")
        if gift_uid and game != "roulette":   # подарок нельзя поделить между победителями стороны
            raise GameError("bad_amount")
        if not gift_uid and amount < config.PVP_MIN_BET:
            raise GameError("bad_amount")
        self.require_open(game)
        self.pvp_tick(game)
        with self.db.tx():
            self.user(uid)
            r = self._pvp_round(game)
            if r["status"] != "open" or (r["ends_at"] and now_ms() >= r["ends_at"]):
                raise GameError("wait")
            bets = self._pvp_bets(r["id"])
            mine = next((b for b in bets if b["id"] == uid), None)
            if mine and game == "fireice" and mine["bside"] != side:
                raise GameError("side_locked")
            if not gift_uid and (mine["bamount"] if mine else 0) + amount > config.PVP_MAX_BET:
                raise GameError("bad_amount")
            if not mine and len(bets) >= config.PVP_MAX_PLAYERS:
                raise GameError("full")
            if gift_uid:
                it = self.db.one("SELECT * FROM inventory WHERE uid=? AND user_id=?", (gift_uid, uid))
                if not it:
                    raise GameError("not_found")
                amount = self.gift_view(it["gift_id"])["price"]
                if amount <= 0:
                    raise GameError("no_price")
                self.db.run("DELETE FROM inventory WHERE uid=?", (gift_uid,))
                self.db.run("INSERT INTO pvp_gifts (uid, round_id, user_id, gift_id, value) VALUES (?,?,?,?,?)",
                            (gift_uid, r["id"], uid, it["gift_id"], amount))
                self._add_tx(uid, "fee", amount, game=PVP_GAMES[game], item="nft", gift_id=it["gift_id"], extra=f"pvp:{r['id']}")
            else:
                self._change_balance(uid, -amount)
                self._add_tx(uid, "fee", amount, game=PVP_GAMES[game], item="stars", extra=f"pvp:{r['id']}")
            if mine:
                self.db.run("UPDATE pvp_bets SET amount=amount+? WHERE id=?", (amount, mine["bid"]))
            else:
                self.db.run("INSERT INTO pvp_bets (round_id, user_id, side, amount, created) VALUES (?,?,?,?,?)",
                            (r["id"], uid, side if game == "fireice" else None, amount, now_ms()))
            # таймер стартует, когда есть с кем играть: 2 игрока в рулетке / обе стороны в «Огонь и лёд»
            if not r["ends_at"]:
                bets = self._pvp_bets(r["id"])
                ready = len(bets) >= 2 if game == "roulette" else len({b["bside"] for b in bets}) == 2
                if ready:
                    self.db.run("UPDATE pvp_rounds SET ends_at=? WHERE id=?", (now_ms() + config.PVP_ROUND_SECONDS * 1000, r["id"]))
        return self.pvp_state(game, uid)

    def pvp_cancel(self, uid: int, game: str) -> dict:
        """Забрать ставку, пока раунд не начался (не с кем играть)."""
        from game import GameError  # noqa: PLC0415
        if game not in PVP_GAMES:
            raise GameError("not_found")
        self.pvp_tick(game)
        with self.db.tx():
            r = self._pvp_round(game)
            bet = self.db.one("SELECT * FROM pvp_bets WHERE round_id=? AND user_id=?", (r["id"], uid))
            if r["status"] != "open" or r["ends_at"] or not bet:
                raise GameError("not_ready")
            self.db.run("DELETE FROM pvp_bets WHERE id=?", (bet["id"],))
            # ставка не состоялась — убираем её из истории и оборота
            self.db.run("DELETE FROM transactions WHERE user_id=? AND type='fee' AND extra=?", (uid, f"pvp:{r['id']}"))
            back = 0
            for g in self.db.q("SELECT * FROM pvp_gifts WHERE round_id=? AND user_id=?", (r["id"], uid)):
                self.db.run("INSERT INTO inventory (uid, user_id, gift_id, created) VALUES (?,?,?,?)", (g["uid"], uid, g["gift_id"], int(time.time())))
                back += g["value"]
            self.db.run("DELETE FROM pvp_gifts WHERE round_id=? AND user_id=?", (r["id"], uid))
            self._change_balance(uid, bet["amount"] - back)
        return self.pvp_state(game, uid)

    # ---------------- состояние для сайта ----------------
    def pvp_state(self, game: str, uid: int | None = None) -> dict:
        from game import GameError  # noqa: PLC0415
        if game not in PVP_GAMES:
            raise GameError("not_found")
        self.pvp_tick(game)
        with self.db.tx():
            r = self._pvp_round(game)
        bets = self._pvp_bets(r["id"])
        pot = sum(b["bamount"] for b in bets)
        done = r["status"] == "done"
        pools = {s: sum(b["bamount"] for b in bets if b["bside"] == s) for s in SIDES}
        staked: dict[int, list] = {}
        for g in self.db.q("SELECT * FROM pvp_gifts WHERE round_id=?", (r["id"],)):
            staked.setdefault(g["user_id"], []).append({**self.gift_view(g["gift_id"]), "price": g["value"]})
        players = [{**self.public_user(b), "amount": b["bamount"], "side": b["bside"], "gifts": staked.get(b["id"], []),
                    "chance": round(b["bamount"] / pot, 4) if pot else 0, "payout": b["bpayout"] if done else 0} for b in bets]
        mine = next((p for p in players if p["id"] == uid), None)
        out = {
            "game": game, "id": r["id"], "open": self.game_open(game),
            "phase": "result" if done else "countdown" if r["ends_at"] else "waiting",
            "now": now_ms(), "endsAt": r["ends_at"], "roundSeconds": config.PVP_ROUND_SECONDS,
            "pot": pot, "hash": r["hash"], "fee": config.PVP_FEE,
            "minBet": config.PVP_MIN_BET, "maxBet": config.PVP_MAX_BET,
            "players": players,
            "my": {"amount": mine["amount"], "side": mine["side"], "chance": mine["chance"]} if mine else None,
            "canCancel": bool(mine) and not done and not r["ends_at"],
        }
        if game == "fireice":
            out["sides"] = {s: {"pool": pools[s], "count": sum(1 for b in bets if b["bside"] == s),
                                "chance": round(pools[s] / pot, 4) if pot else 0.5} for s in SIDES}
        if done:
            w = next((p for p in players if str(p["id"]) == r["winner"]), None)
            out["result"] = {
                "winner": w if game == "roulette" else r["winner"], "roll": r["roll"], "seed": r["seed"],
                "pot": r["pot"], "fee": r["fee"], "at": r["resolved_at"],
                "nextAt": r["resolved_at"] + config.PVP_SHOW_SECONDS * 1000,
                "myPayout": mine["payout"] if mine else 0,
            }
        out["history"] = self.pvp_history(game)
        return out

    def pvp_history(self, game: str, limit: int = 8) -> list[dict]:
        rows = self.db.q("SELECT * FROM pvp_rounds WHERE game=? AND status='done' ORDER BY id DESC LIMIT ?", (game, limit))
        out = []
        for r in rows:
            item = {"id": r["id"], "pot": r["pot"], "chance": round(r["win_chance"] or 0, 4)}
            if game == "roulette":
                u = self.db.one("SELECT * FROM users WHERE id=?", (int(r["winner"]),))
                item["winner"] = self.public_user(u) if u else None
            else:
                item["winner"] = r["winner"]
            out.append(item)
        return out

    def house_total(self) -> dict:
        rows = self.db.q("SELECT game, COALESCE(SUM(amount),0) s FROM house GROUP BY game")
        return {r["game"]: r["s"] for r in rows}
