import sqlite3
import time
from datetime import datetime

import os
DB_PATH = os.environ.get("DB_PATH", "stocks.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS stocks (
    ticker   TEXT PRIMARY KEY,
    name     TEXT NOT NULL,
    market   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stocks_market ON stocks(market);

CREATE TABLE IF NOT EXISTS signals (
    ticker           TEXT PRIMARY KEY,
    signal           TEXT,
    score            INTEGER,
    price            REAL,
    price_change     REAL,
    price_change_pct REAL,
    rsi              REAL,
    rsi_signal       TEXT,
    macd_signal      TEXT,
    ma_signal        TEXT,
    ma5              REAL,
    ma20             REAL,
    error            TEXT,
    updated_at       REAL
);
CREATE INDEX IF NOT EXISTS idx_signals_signal ON signals(signal);

CREATE TABLE IF NOT EXISTS update_progress (
    id          INTEGER PRIMARY KEY CHECK (id=1),
    total       INTEGER DEFAULT 0,
    done        INTEGER DEFAULT 0,
    status      TEXT DEFAULT 'idle',
    started_at  REAL,
    finished_at REAL
);
INSERT OR IGNORE INTO update_progress VALUES (1,0,0,'idle',NULL,NULL);
"""


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def upsert_universe(stocks: list):
    if not stocks:
        return
    conn = get_conn()
    conn.executemany(
        "INSERT OR REPLACE INTO stocks (ticker, name, market) VALUES (?,?,?)",
        [(s["ticker"], s["name"], s["market"]) for s in stocks],
    )
    conn.commit()
    conn.close()


def upsert_signals(rows: list):
    if not rows:
        return
    conn = get_conn()
    conn.executemany(
        """INSERT OR REPLACE INTO signals
           (ticker,signal,score,price,price_change,price_change_pct,
            rsi,rsi_signal,macd_signal,ma_signal,ma5,ma20,error,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [
            (
                r.get("ticker"), r.get("signal"), r.get("score"),
                r.get("price"), r.get("price_change"), r.get("price_change_pct"),
                r.get("rsi"), r.get("rsi_signal"), r.get("macd_signal"),
                r.get("ma_signal"), r.get("ma5"), r.get("ma20"),
                r.get("error"), r.get("updated_at", time.time()),
            )
            for r in rows
        ],
    )
    conn.commit()
    conn.close()


def get_stocks_page(market: str, page: int, per_page: int, search: str = "", signal_filter: str = "") -> dict:
    conn = get_conn()
    params = [market]
    where = "WHERE s.market = ?"
    if search:
        where += " AND (s.name LIKE ? OR s.ticker LIKE ?)"
        params += [f"%{search}%", f"%{search}%"]
    if signal_filter:
        where += " AND sig.signal = ?"
        params.append(signal_filter)

    total = conn.execute(
        f"SELECT COUNT(*) FROM stocks s LEFT JOIN signals sig ON s.ticker=sig.ticker {where}",
        params,
    ).fetchone()[0]

    offset = (page - 1) * per_page
    rows = conn.execute(
        f"""SELECT s.ticker, s.name, s.market,
               sig.signal, sig.score, sig.price, sig.price_change, sig.price_change_pct,
               sig.rsi, sig.rsi_signal, sig.macd_signal, sig.ma_signal,
               sig.ma5, sig.ma20, sig.error, sig.updated_at
            FROM stocks s
            LEFT JOIN signals sig ON s.ticker = sig.ticker
            {where}
            ORDER BY (sig.updated_at IS NOT NULL) DESC, sig.score DESC NULLS LAST, s.ticker ASC
            LIMIT ? OFFSET ?""",
        params + [per_page, offset],
    ).fetchall()
    conn.close()

    items = [dict(r) for r in rows]
    pages = max(1, (total + per_page - 1) // per_page)
    return {"items": items, "total": total, "page": page, "pages": pages, "per_page": per_page}


def get_stock_detail(ticker: str) -> dict | None:
    conn = get_conn()
    row = conn.execute(
        """SELECT s.ticker, s.name, s.market,
               sig.signal, sig.score, sig.price, sig.price_change, sig.price_change_pct,
               sig.rsi, sig.rsi_signal, sig.macd_signal, sig.ma_signal,
               sig.ma5, sig.ma20, sig.error, sig.updated_at
            FROM stocks s
            LEFT JOIN signals sig ON s.ticker = sig.ticker
            WHERE s.ticker = ?""",
        (ticker,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_market_summary(market: str) -> dict:
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) FROM stocks WHERE market=?", (market,)).fetchone()[0]
    rows = conn.execute(
        """SELECT sig.signal, COUNT(*) as cnt
           FROM stocks s
           LEFT JOIN signals sig ON s.ticker = sig.ticker
           WHERE s.market=?
           GROUP BY sig.signal""",
        (market,),
    ).fetchall()
    last_updated = conn.execute(
        "SELECT MAX(sig.updated_at) FROM stocks s LEFT JOIN signals sig ON s.ticker=sig.ticker WHERE s.market=?",
        (market,),
    ).fetchone()[0]
    conn.close()

    counts = {"강력매수": 0, "매수": 0, "중립": 0, "매도": 0, "강력매도": 0, "오류": 0, "미계산": 0}
    for r in rows:
        sig = r["signal"] or "미계산"
        if sig in counts:
            counts[sig] += r["cnt"]
        else:
            counts["미계산"] += r["cnt"]

    updated_str = datetime.fromtimestamp(last_updated).isoformat() if last_updated else None
    return {**counts, "total": total, "last_updated": updated_str}


def get_progress() -> dict:
    conn = get_conn()
    row = conn.execute("SELECT * FROM update_progress WHERE id=1").fetchone()
    conn.close()
    return dict(row) if row else {"total": 0, "done": 0, "status": "idle", "started_at": None, "finished_at": None}


def set_progress(**kwargs):
    conn = get_conn()
    fields = {k: v for k, v in kwargs.items() if v is not None}
    if not fields:
        conn.close()
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE update_progress SET {sets} WHERE id=1", list(fields.values()))
    conn.commit()
    conn.close()


def get_all_tickers() -> list:
    conn = get_conn()
    rows = conn.execute("""
        SELECT ticker FROM stocks
        ORDER BY CASE market
            WHEN 'SP500'  THEN 1
            WHEN 'NASDAQ' THEN 2
            WHEN 'KOSPI'  THEN 3
            WHEN 'KOSDAQ' THEN 4
            ELSE 5 END, ticker
    """).fetchall()
    conn.close()
    return [r["ticker"] for r in rows]
