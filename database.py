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


def _column_exists(conn, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def _migrate(conn):
    """Non-destructive: add new columns / tables to existing DB."""
    for table in ("shopping", "learning", "others"):
        if not _column_exists(conn, table, "reminder_time"):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN reminder_time TEXT")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)


def init_db():
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS shopping (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                items         TEXT    NOT NULL,
                date          TEXT,
                reminder_time TEXT,
                platform      TEXT,
                notes         TEXT,
                raw_text      TEXT,
                done          INTEGER DEFAULT 0,
                notified      INTEGER DEFAULT 0,
                created_at    TEXT    DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS learning (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                topic         TEXT    NOT NULL,
                date          TEXT,
                reminder_time TEXT,
                resource      TEXT,
                notes         TEXT,
                raw_text      TEXT,
                done          INTEGER DEFAULT 0,
                notified      INTEGER DEFAULT 0,
                created_at    TEXT    DEFAULT (datetime('now'))
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
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                description   TEXT    NOT NULL,
                date          TEXT,
                reminder_time TEXT,
                notes         TEXT,
                raw_text      TEXT,
                done          INTEGER DEFAULT 0,
                notified      INTEGER DEFAULT 0,
                created_at    TEXT    DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        _migrate(conn)  # safe no-op if columns already exist


# ── Write operations ──────────────────────────────────────────────────────────

def add_shopping(entry) -> int:
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO shopping (items, date, reminder_time, platform, notes, raw_text) VALUES (?,?,?,?,?,?)",
            (json.dumps(entry.items),
             str(entry.date) if entry.date else None,
             entry.reminder_time,
             entry.platform, entry.notes, entry.raw_text)
        )
        return cur.lastrowid


def add_learning(entry) -> int:
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO learning (topic, date, reminder_time, resource, notes, raw_text) VALUES (?,?,?,?,?,?)",
            (entry.topic,
             str(entry.date) if entry.date else None,
             entry.reminder_time,
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
            "INSERT INTO others (description, date, reminder_time, notes, raw_text) VALUES (?,?,?,?,?)",
            (entry.description,
             str(entry.date) if entry.date else None,
             entry.reminder_time,
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


def get_others_list() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, description, date, notes FROM others WHERE done=0 ORDER BY date"
        ).fetchall()
    return [dict(r) for r in rows]


def get_summary_stats() -> dict:
    """Counts for the summary dashboard."""
    with _conn() as conn:
        shopping_count  = conn.execute("SELECT COUNT(*) FROM shopping WHERE done=0").fetchone()[0]
        learning_count  = conn.execute("SELECT COUNT(*) FROM learning WHERE done=0").fetchone()[0]
        reminder_count  = conn.execute("SELECT COUNT(*) FROM others WHERE done=0").fetchone()[0]
        month_str       = date.today().strftime("%Y-%m")
        expense_total   = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM expenses WHERE strftime('%Y-%m',date)=?",
            (month_str,)
        ).fetchone()[0]
        expense_count   = conn.execute(
            "SELECT COUNT(*) FROM expenses WHERE strftime('%Y-%m',date)=?",
            (month_str,)
        ).fetchone()[0]
    return {
        "shopping":     shopping_count,
        "learning":     learning_count,
        "reminders":    reminder_count,
        "expense_total": expense_total,
        "expense_count": expense_count,
    }


# ── Update operations ─────────────────────────────────────────────────────────

def save_chat_id(chat_id: int):
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('chat_id', ?)",
            (str(chat_id),)
        )


def get_chat_id() -> int | None:
    with _conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key='chat_id'").fetchone()
    return int(row[0]) if row else None


def get_pending_timed_reminders() -> list[dict]:
    """Return all entries with a specific reminder_time that haven't fired yet."""
    today = str(date.today())
    results = []
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, 'shopping' AS tbl, items AS description, date, reminder_time "
            "FROM shopping WHERE reminder_time IS NOT NULL AND done=0 AND notified=0 AND date >= ?",
            (today,)
        ).fetchall()
        results.extend([dict(r) for r in rows])

        rows = conn.execute(
            "SELECT id, 'learning' AS tbl, topic AS description, date, reminder_time "
            "FROM learning WHERE reminder_time IS NOT NULL AND done=0 AND notified=0 AND date >= ?",
            (today,)
        ).fetchall()
        results.extend([dict(r) for r in rows])

        rows = conn.execute(
            "SELECT id, 'others' AS tbl, description, date, reminder_time "
            "FROM others WHERE reminder_time IS NOT NULL AND done=0 AND notified=0 AND date >= ?",
            (today,)
        ).fetchall()
        results.extend([dict(r) for r in rows])
    return results


def get_all_pending(limit: int = 25) -> list[dict]:
    """All pending items across the 3 task tables — used by /manage."""
    results = []
    with _conn() as conn:
        for table, label_col in (
            ("shopping", "items"), ("learning", "topic"), ("others", "description")
        ):
            rows = conn.execute(
                f"SELECT id, '{table}' AS tbl, {label_col} AS label, date "
                f"FROM {table} WHERE done=0 ORDER BY date"
            ).fetchall()
            results.extend([dict(r) for r in rows])
    return results[:limit]


def count_done() -> int:
    """How many completed rows are sitting in the DB."""
    with _conn() as conn:
        return sum(
            conn.execute(f"SELECT COUNT(*) FROM {t} WHERE done=1").fetchone()[0]
            for t in ("shopping", "learning", "others")
        )


def purge_done() -> int:
    """Permanently DELETE all rows marked done=1. Returns number removed."""
    removed = 0
    with _conn() as conn:
        for table in ("shopping", "learning", "others"):
            cur = conn.execute(f"DELETE FROM {table} WHERE done=1")
            removed += cur.rowcount
    return removed


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
