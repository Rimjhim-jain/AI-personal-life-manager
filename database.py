import sqlite3
import json
from datetime import date
from contextlib import contextmanager
from config import DB_PATH


@contextmanager
def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS shopping (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                items       TEXT    NOT NULL,
                date        TEXT,
                platform    TEXT,
                notes       TEXT,
                raw_text    TEXT,
                done        INTEGER DEFAULT 0,
                notified    INTEGER DEFAULT 0,
                created_at  TEXT    DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS learning (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                topic       TEXT    NOT NULL,
                date        TEXT,
                resource    TEXT,
                notes       TEXT,
                raw_text    TEXT,
                done        INTEGER DEFAULT 0,
                notified    INTEGER DEFAULT 0,
                created_at  TEXT    DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS expenses (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                amount      REAL    NOT NULL,
                currency    TEXT    DEFAULT 'INR',
                category    TEXT,
                description TEXT,
                date        TEXT    NOT NULL,
                raw_text    TEXT,
                created_at  TEXT    DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS others (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                description TEXT    NOT NULL,
                date        TEXT,
                notes       TEXT,
                raw_text    TEXT,
                done        INTEGER DEFAULT 0,
                notified    INTEGER DEFAULT 0,
                created_at  TEXT    DEFAULT (datetime('now'))
            );
        """)


# ── Write operations ──────────────────────────────────────────────────────────

def add_shopping(entry) -> int:
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO shopping (items, date, platform, notes, raw_text) VALUES (?,?,?,?,?)",
            (json.dumps(entry.items),
             str(entry.date) if entry.date else None,
             entry.platform, entry.notes, entry.raw_text)
        )
        return cur.lastrowid


def add_learning(entry) -> int:
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO learning (topic, date, resource, notes, raw_text) VALUES (?,?,?,?,?)",
            (entry.topic,
             str(entry.date) if entry.date else None,
             entry.resource, entry.notes, entry.raw_text)
        )
        return cur.lastrowid


def add_expense(entry) -> int:
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO expenses (amount, currency, category, description, date, raw_text) VALUES (?,?,?,?,?,?)",
            (entry.amount, entry.currency, entry.category,
             entry.description, str(entry.date), entry.raw_text)
        )
        return cur.lastrowid


def add_other(entry) -> int:
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO others (description, date, notes, raw_text) VALUES (?,?,?,?)",
            (entry.description,
             str(entry.date) if entry.date else None,
             entry.notes, entry.raw_text)
        )
        return cur.lastrowid


# ── Read operations ───────────────────────────────────────────────────────────

def get_due_today() -> list[dict]:
    today = str(date.today())
    results = []
    with _conn() as conn:
        for table, label in [("shopping", "shopping"), ("learning", "learning"), ("others", "others")]:
            rows = conn.execute(
                f"SELECT id, '{table}' AS tbl, '{label}' AS label, * FROM {table} "
                "WHERE date=? AND done=0 AND notified=0",
                (today,)
            ).fetchall()
            results.extend([dict(r) for r in rows])
    return results


def get_shopping_list() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, items, date, platform, notes FROM shopping WHERE done=0 ORDER BY date"
        ).fetchall()
    return [dict(r) for r in rows]


def get_learning_list() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, topic, date, resource FROM learning WHERE done=0 ORDER BY date"
        ).fetchall()
    return [dict(r) for r in rows]


def get_expenses(month: str = None) -> list[dict]:
    from datetime import date as d
    target_month = month or d.today().strftime("%Y-%m")
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, amount, currency, category, description, date "
            "FROM expenses WHERE strftime('%Y-%m', date)=? ORDER BY date DESC",
            (target_month,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_all_today() -> list[dict]:
    today = str(date.today())
    results = []
    with _conn() as conn:
        for table, label in [("shopping", "shopping"), ("learning", "learning"), ("others", "others")]:
            rows = conn.execute(
                f"SELECT id, '{table}' AS tbl, * FROM {table} WHERE date=? AND done=0",
                (today,)
            ).fetchall()
            results.extend([dict(r) for r in rows])
    return results


# ── Update operations ─────────────────────────────────────────────────────────

def mark_done(table: str, row_id: int):
    if table not in ("shopping", "learning", "others"):
        raise ValueError(f"Invalid table: {table}")
    with _conn() as conn:
        conn.execute(f"UPDATE {table} SET done=1 WHERE id=?", (row_id,))


def mark_notified(table: str, row_id: int):
    if table not in ("shopping", "learning", "others"):
        raise ValueError(f"Invalid table: {table}")
    with _conn() as conn:
        conn.execute(f"UPDATE {table} SET notified=1 WHERE id=?", (row_id,))
