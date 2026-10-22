"""قاعدة البيانات: ملف SQLite واحد على جهازك (data/banke.db)."""

import os
import sqlite3
from pathlib import Path

DATA_DIR = Path(os.environ.get("BANKE_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
DB_PATH = DATA_DIR / "banke.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    bank            TEXT,
    currency        TEXT NOT NULL DEFAULT 'TRY',
    opening_balance REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS categories (
    id    INTEGER PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE,
    kind  TEXT NOT NULL CHECK (kind IN ('expense', 'income')),
    color TEXT NOT NULL DEFAULT '#888888'
);

-- قواعد التصنيف التلقائي: إذا الوصف فيه الكلمة، بتنحط المعاملة بالفئة
CREATE TABLE IF NOT EXISTS rules (
    id          INTEGER PRIMARY KEY,
    keyword     TEXT NOT NULL UNIQUE,
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE
);

-- المبلغ بإشارة: سالب = مصروف، موجب = دخل. بعملة الحساب.
CREATE TABLE IF NOT EXISTS transactions (
    id          INTEGER PRIMARY KEY,
    account_id  INTEGER NOT NULL REFERENCES accounts(id),
    date        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    amount      REAL NOT NULL,
    category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    note        TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT 'manual',
    import_hash TEXT UNIQUE,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(date);

-- سعر صرف العملة مقابل الليرة، من البنك المركزي التركي
CREATE TABLE IF NOT EXISTS fx_rates (
    currency TEXT NOT NULL,
    date     TEXT NOT NULL,
    rate     REAL NOT NULL,
    PRIMARY KEY (currency, date)
);
"""

DEFAULT_ACCOUNTS = [
    ("Kuveyt Türk", "Kuveyt Türk", "TRY"),
    ("VakıfBank", "VakıfBank", "TRY"),
    ("كاش ليرة", None, "TRY"),
    ("كاش دولار", None, "USD"),
]

DEFAULT_CATEGORIES = [
    ("سوبرماركت", "expense", "#2e7d32"),
    ("أكل برّا", "expense", "#ef6c00"),
    ("مواصلات", "expense", "#1565c0"),
    ("سكن وإيجار", "expense", "#6a1b9a"),
    ("فواتير", "expense", "#00838f"),
    ("جامعة ودراسة", "expense", "#283593"),
    ("صحة", "expense", "#c62828"),
    ("اتصالات وإنترنت", "expense", "#4e342e"),
    ("ملابس", "expense", "#ad1457"),
    ("اشتراكات", "expense", "#5d4037"),
    ("ترفيه", "expense", "#f9a825"),
    ("تحويلات", "expense", "#546e7a"),
    ("أخرى", "expense", "#757575"),
    ("مصروف من الأهل", "income", "#2e7d32"),
    ("منحة", "income", "#1565c0"),
    ("شغل", "income", "#6a1b9a"),
    ("أرباح (Kâr payı)", "income", "#00838f"),
    ("دخل آخر", "income", "#757575"),
]

DEFAULT_RULES = {
    "سوبرماركت": ["migros", "a101", "bim", "sok market", "carrefour", "file market", "macrocenter", "metro grosmarket"],
    "أكل برّا": ["yemeksepeti", "getir", "trendyol yemek", "starbucks", "burger", "mcdonald", "dominos", "kahve", "cafe", "restoran"],
    "مواصلات": ["istanbulkart", "kentkart", "ankarakart", "uber", "bitaksi", "marti", "shell", "opet", "petrol ofisi", "pegasus", "thy", "obilet"],
    "سكن وإيجار": ["kira", "aidat"],
    "فواتير": ["elektrik", "dogalgaz", "igdas", "enerjisa", "iski", "su fatura"],
    "اتصالات وإنترنت": ["turkcell", "vodafone", "turk telekom", "superonline", "turknet"],
    "صحة": ["eczane", "hastane", "klinik"],
    "اشتراكات": ["netflix", "spotify", "youtube", "apple.com", "icloud", "disney", "chatgpt", "claude.ai"],
    "ملابس": ["lc waikiki", "defacto", "zara", "h&m", "koton", "mavi"],
    "أرباح (Kâr payı)": ["kar payi", "kâr payi"],
}


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        if conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0:
            conn.executemany("INSERT INTO accounts (name, bank, currency) VALUES (?, ?, ?)", DEFAULT_ACCOUNTS)
        if conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0:
            conn.executemany("INSERT INTO categories (name, kind, color) VALUES (?, ?, ?)", DEFAULT_CATEGORIES)
            for cat_name, keywords in DEFAULT_RULES.items():
                cat_id = conn.execute("SELECT id FROM categories WHERE name = ?", (cat_name,)).fetchone()[0]
                conn.executemany(
                    "INSERT OR IGNORE INTO rules (keyword, category_id) VALUES (?, ?)",
                    [(k, cat_id) for k in keywords],
                )


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
