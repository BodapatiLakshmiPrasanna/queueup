import os
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS appointments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,
    phone      TEXT    NOT NULL,
    date       TEXT    NOT NULL,           -- YYYY-MM-DD (local time)
    time       TEXT    NOT NULL,           -- HH:MM
    token      INTEGER NOT NULL,           -- queue number for that day
    status     TEXT    NOT NULL DEFAULT 'booked',
    reminded   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
-- The database itself blocks double booking, even for simultaneous requests.
CREATE UNIQUE INDEX IF NOT EXISTS uniq_active_slot
    ON appointments(date, time) WHERE status != 'cancelled';
CREATE INDEX IF NOT EXISTS idx_date ON appointments(date);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path: str) -> None:
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    conn = connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.close()
