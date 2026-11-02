"""SQLite: one file, shipped with the deploy, so a cold start is never an empty app."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path

from .net import DATA_DIR

# FINN_DB points an experiment at its own database, so it can't touch the one that ships.
DB_PATH = Path(os.environ.get("FINN_DB", Path(DATA_DIR) / "finn.db"))
_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS filings (
  id TEXT PRIMARY KEY, symbol TEXT, name TEXT, ts TEXT, raw_category TEXT, text TEXT,
  pdf_url TEXT, industry TEXT,
  category TEXT, score INTEGER, tier TEXT, title TEXT, summary TEXT, reason TEXT,
  thread TEXT, detail TEXT, enriched INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS filings_ts ON filings(ts);
CREATE INDEX IF NOT EXISTS filings_sym ON filings(symbol, ts);
CREATE TABLE IF NOT EXISTS quarters (symbol TEXT, period_end TEXT, data TEXT, PRIMARY KEY(symbol, period_end));
CREATE TABLE IF NOT EXISTS prices (symbol TEXT, date TEXT, close REAL, volume REAL, PRIMARY KEY(symbol, date));
CREATE TABLE IF NOT EXISTS insider (id TEXT PRIMARY KEY, symbol TEXT, ts TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS companies (symbol TEXT PRIMARY KEY, name TEXT, market_cap REAL, ttm_revenue REAL, ttm_pat REAL, banking INTEGER, in_portfolio INTEGER DEFAULT 0, updated TEXT);
CREATE TABLE IF NOT EXISTS dossiers (symbol TEXT PRIMARY KEY, data TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS jobs (symbol TEXT PRIMARY KEY, status TEXT, step TEXT, error TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS llm_cache (key TEXT PRIMARY KEY, task TEXT, response TEXT);
CREATE TABLE IF NOT EXISTS llm_calls (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, task TEXT, provider TEXT, model TEXT,
  input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL, ms INTEGER);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS doc_text (url TEXT PRIMARY KEY, pages BLOB);
"""


def db() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        _local.conn = conn
    return conn


def set_meta(key: str, value) -> None:
    db().execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, json.dumps(value)))
    db().commit()


def get_meta(key: str, default=None):
    row = db().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default
