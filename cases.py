"""AstraRoll — Кейсы. Игрок покупает кейс за звёзды и получает случайный приз.

Как проект зарабатывает
  Для каждого кейса шанс самого дешёвого приза подбирается автоматически так, чтобы средний выигрыш был ровно
  CASE_RTP от цены (0.88 → игрок в среднем получает назад 88%, проекту остаётся 12% с каждого открытия на дистанции).
  Призы-«NFT» выдаются настоящими подарками со склада бота подходящей цены; если такого нет — звёздами на ту же сумму,
  поэтому средняя выплата не меняется.
"""
from __future__ import annotations

import secrets
import time

import config

# Кейсы: цена и призы. kind: stars — звёзды, nft — NFT со склада стоимостью около value (иначе value звёздами).
# Первый приз — «утешительный»: его шанс считается сам, остальные шансы задаются весами.
CASES = [
    {"id": "start", "name": "Стартовый", "price": 25, "color": "blue",
     "items": [("stars", 5), ("stars", 15, 30), ("stars", 25, 20), ("stars", 50, 10), ("stars", 100, 3), ("stars", 250, 0.6)]},
    {"id": "star", "name": "Звёздный", "price": 100, "color": "violet",
     "items": [("stars", 30), ("stars", 60, 30), ("stars", 100, 20), ("stars", 200, 8), ("stars", 500, 2), ("stars", 1000, 0.4)]},
    {"id": "gift", "name": "Подарочный", "price": 500, "color": "pink",
     "items": [("stars", 150), ("stars", 300, 30), ("stars", 500, 20), ("stars", 1000, 8), ("nft", 2500, 2), ("nft", 5000, 0.6)]},
    {"id": "legend", "name": "Легенда", "price": 2500, "color": "gold",
     "items": [("stars", 800), ("stars", 1500, 30), ("stars", 2500, 18), ("stars", 5000, 7), ("nft", 12000, 1.5), ("nft", 25000, 0.5)]},
]


def _build(case: dict) -> dict:
    """Досчитать шанс утешительного приза так, чтобы средний выигрыш = CASE_RTP × цена."""
    target = config.CASE_RTP * case["price"]
    kind0, v0 = case["items"][0][:2]
    rest = [(k, v, w) for k, v, w in case["items"][1:]]
    W = sum(w for _, _, w in rest)
    S = sum(v * w for _, v, w in rest)
    if not (v0 < target < S / W):
        raise ValueError(f"кейс {case['id']}: не получается выйти на CASE_RTP={config.CASE_RTP} — поменяй призы")
    w0 = (S - target * W) / (target - v0)
    items = [(kind0, v0, w0)] + rest
    total = sum(w for _, _, w in items)
    return {**case, "items": [{"i": i, "kind": k, "value": v, "chance": w / total} for i, (k, v, w) in enumerate(items)]}


BUILT = {c["id"]: _build(c) for c in CASES}


class CasesMixin:
    """Подмешивается в Game: использует self.db, self.user, self._change_balance, self._add_tx, self.nft_view."""

    def _case_nft(self, value: int) -> str | None:
        """NFT со склада в пределах ±25% от value (не заблокированный и не приз ТОПа)."""
        lo, hi = int(value * 0.75), int(value * 1.25)
        cand = [s for s in self.db.q("SELECT slug FROM nfts WHERE status='house'")
                if lo <= self.nft_view(s["slug"])["price"] <= hi]
        if not cand:
            return None
        return min(cand, key=lambda r: abs(self.nft_view(r["slug"])["price"] - value))["slug"]

    def cases_list(self) -> list[dict]:
        out = []
        for c in BUILT.values():
            items = []
            for it in c["items"]:
                slug = self._case_nft(it["value"]) if it["kind"] == "nft" else None
                items.append({**it, "gift": self.nft_view(slug) if slug else None})
            out.append({"id": c["id"], "name": c["name"], "price": c["price"], "color": c["color"], "items": items,
                        "top": max(it["value"] for it in c["items"])})
        return out

    def case_open(self, uid: int, case_id: str) -> dict:
        from game import GameError  # noqa: PLC0415
        c = BUILT.get(case_id)
        if not c:
            raise GameError("not_found")
        self.require_open("cases")
        with self.db.tx():
            self.user(uid)
            self._change_balance(uid, -c["price"])
            self._add_tx(uid, "fee", c["price"], game="Кейсы", item="stars", extra=f"case:{case_id}")
            r = secrets.randbelow(10 ** 9) / 10 ** 9
            acc, it = 0.0, c["items"][-1]
            for x in c["items"]:
                acc += x["chance"]
                if r < acc:
                    it = x
                    break
            prize = {"i": it["i"], "kind": "stars", "value": it["value"], "gift": None}
            paid = it["value"]
            if it["kind"] == "nft":
                slug = self._case_nft(it["value"])
                if slug:   # настоящий подарок со склада
                    view = self.nft_view(slug)
                    self._give(uid, f"nft:{slug}")
                    self._add_tx(uid, "win", view["price"], game="Кейсы", item="nft", gift_id=f"nft:{slug}")
                    prize = {"i": it["i"], "kind": "nft", "value": view["price"], "gift": view}
                    paid = view["price"]
            if prize["kind"] == "stars":
                self._change_balance(uid, it["value"])
                self._add_tx(uid, "win", it["value"], game="Кейсы", item="stars")
            self.db.run("INSERT INTO house (game, amount, created) VALUES (?,?,?)", ("cases", c["price"] - paid, int(time.time())))
        return {"prize": prize, "balance": self.balance(uid), "inventory": self.inventory(uid)}
