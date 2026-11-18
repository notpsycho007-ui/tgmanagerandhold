import os
import sqlite3
from contextlib import contextmanager

from config import DATABASE_PATH


@contextmanager
def connect():
    conn = sqlite3.connect(DATABASE_PATH, timeout=10)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    folder = os.path.dirname(DATABASE_PATH)
    if folder:
        os.makedirs(folder, exist_ok=True)

    with connect() as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                phone TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def add_account(name, phone):
    """Duplicate phone হলে sqlite3.IntegrityError raise করে।"""
    with connect() as db:
        db.execute(
            "INSERT INTO accounts (name, phone) VALUES (?, ?)", (name, phone)
        )


def get_account(account_id):
    with connect() as db:
        return db.execute(
            "SELECT id, name, phone FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()


def get_accounts_page(limit, offset):
    with connect() as db:
        return db.execute(
            "SELECT id, name, phone FROM accounts ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()


def get_all_accounts():
    with connect() as db:
        return db.execute(
            "SELECT id, name, phone, created_at FROM accounts ORDER BY id"
        ).fetchall()


def search_accounts(query, limit):
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    with connect() as db:
        return db.execute(
            """
            SELECT id, name, phone FROM accounts
            WHERE name LIKE ? ESCAPE '\\' OR phone LIKE ? ESCAPE '\\'
            ORDER BY id DESC LIMIT ?
            """,
            (pattern, pattern, limit),
        ).fetchall()


def count_accounts():
    with connect() as db:
        return db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]


def count_today():
    with connect() as db:
        return db.execute(
            "SELECT COUNT(*) FROM accounts WHERE date(created_at) = date('now')"
        ).fetchone()[0]


def update_account(account_id, name, phone):
    """Duplicate phone হলে sqlite3.IntegrityError raise করে।"""
    with connect() as db:
        cursor = db.execute(
            "UPDATE accounts SET name = ?, phone = ? WHERE id = ?",
            (name, phone, account_id),
        )
        return cursor.rowcount > 0


def delete_account(account_id):
    with connect() as db:
        cursor = db.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        return cursor.rowcount > 0
