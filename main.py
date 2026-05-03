import asyncio
import json
import logging
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime

import yfinance as yf
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse

import database
import universe
from updater import StockUpdater

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

_updater: StockUpdater = None


def _bootstrap():
    log.info("종목 유니버스 수집 중...")
    stocks = universe.load_all_universes()
    database.upsert_universe(stocks)
    log.info(f"유니버스 저장 완료: {len(stocks)}개")
    # 유니버스 로드 후 즉시 업데이트 사이클 시작
    if _updater:
        threading.Thread(target=_updater._run_cycle, daemon=True, name="initial-update").start()


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    threading.Thread(target=_bootstrap, daemon=True, name="bootstrap").start()
    global _updater
    _updater = StockUpdater()
    _updater.start()
    yield
    if _updater:
        _updater.stop()


app = FastAPI(title="Stock Signal API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

VALID_MARKETS = {"KOSPI", "KOSDAQ", "NASDAQ", "SP500"}


@app.get("/api/stocks/{market}")
def get_stocks(
    market: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=10, le=200),
    search: str = Query(""),
    signal: str = Query(""),
):
    if market not in VALID_MARKETS:
        raise HTTPException(400, f"유효하지 않은 시장: {market}")
    return database.get_stocks_page(market, page, per_page, search, signal)


@app.get("/api/summary/{market}")
def get_summary(market: str):
    if market not in VALID_MARKETS:
        raise HTTPException(400, f"유효하지 않은 시장: {market}")
    return database.get_market_summary(market)


@app.get("/api/stock/{ticker}")
def get_stock(ticker: str):
    row = database.get_stock_detail(ticker.upper())
    if not row:
        raise HTTPException(404, "종목을 찾을 수 없습니다")
    return row


@app.get("/api/chart/{ticker}")
def get_chart(ticker: str, period: str = "3mo"):
    try:
        df = yf.download(ticker, period=period, interval="1d", progress=False, auto_adjust=True)
        if df.empty:
            raise HTTPException(404, "데이터 없음")
        closes = df["Close"].dropna()
        dates = closes.index.strftime("%Y-%m-%d").tolist()
        prices = [round(float(v), 4) for v in closes.to_numpy()]
        return {"ticker": ticker, "dates": dates, "prices": prices}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/progress")
def get_progress():
    p = database.get_progress()
    total = p["total"] or 0
    done = p["done"] or 0
    pct = round(done / total * 100, 1) if total > 0 else 0
    elapsed = (time.time() - p["started_at"]) if p.get("started_at") else 0
    eta = round(elapsed / done * (total - done)) if done > 0 and elapsed > 0 else None
    return {**p, "pct": pct, "elapsed_sec": round(elapsed), "eta_sec": eta}


@app.get("/api/progress/stream")
async def stream_progress():
    async def gen():
        while True:
            p = database.get_progress()
            total = p["total"] or 0
            done = p["done"] or 0
            pct = round(done / total * 100, 1) if total > 0 else 0
            elapsed = (time.time() - p["started_at"]) if p.get("started_at") else 0
            eta = round(elapsed / done * (total - done)) if done > 0 and elapsed > 0 else None
            data = json.dumps({**p, "pct": pct, "elapsed_sec": round(elapsed), "eta_sec": eta}, ensure_ascii=False)
            yield f"event: progress\ndata: {data}\n\n"
            status = p.get("status", "idle")
            if status in ("done", "error", "idle"):
                break
            await asyncio.sleep(2)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/", response_class=HTMLResponse)
def index():
    with open("index.html", encoding="utf-8") as f:
        return f.read()
