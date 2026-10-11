"""SQLite: пользователи, инвентарь, транзакции, промокоды, задания."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

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
