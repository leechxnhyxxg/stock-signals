import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import yfinance as yf

import database
from signals import generate_signal

log = logging.getLogger(__name__)

BATCH_SIZE = 100
MAX_WORKERS = 2
CYCLE_INTERVAL = 3600  # 1시간마다 재실행
RETRY_LIMIT = 2


def _process_batch(batch: list) -> list:
    for attempt in range(RETRY_LIMIT + 1):
        try:
            df = yf.download(
                batch,
                period="6mo",
                interval="1d",
                progress=False,
                auto_adjust=True,
                group_by="ticker",
                threads=False,
                timeout=15,
            )
            results = []
            for ticker in batch:
                try:
                    if len(batch) == 1:
                        ticker_df = df
                    else:
                        ticker_df = df[ticker] if ticker in df.columns.get_level_values(0) else None
                    if ticker_df is None or ticker_df.empty or ticker_df["Close"].isna().all():
                        raise ValueError("가격 데이터 없음")
                    sig = generate_signal(ticker_df)
                    results.append({"ticker": ticker, **sig, "error": None, "updated_at": time.time()})
                except Exception as e:
                    results.append({
                        "ticker": ticker, "signal": "오류", "score": 0,
                        "price": None, "price_change": None, "price_change_pct": None,
                        "rsi": None, "rsi_signal": None, "macd_signal": None,
                        "ma_signal": None, "ma5": None, "ma20": None,
                        "error": str(e), "updated_at": time.time(),
                    })
            return results
        except Exception as e:
            if attempt < RETRY_LIMIT:
                log.warning(f"배치 재시도 {attempt+1}/{RETRY_LIMIT}: {e}")
                time.sleep(5)
            else:
                log.error(f"배치 실패: {e}")
                return [
                    {"ticker": t, "signal": "오류", "score": 0,
                     "price": None, "price_change": None, "price_change_pct": None,
                     "rsi": None, "rsi_signal": None, "macd_signal": None,
                     "ma_signal": None, "ma5": None, "ma20": None,
                     "error": str(e), "updated_at": time.time()}
                    for t in batch
                ]
    return []


class StockUpdater:
    def __init__(self):
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True, name="stock-updater")
        self._thread.start()
        log.info("StockUpdater 시작됨")

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._run_cycle()
            except Exception as e:
                log.error(f"업데이트 사이클 오류: {e}")
                database.set_progress(status="error")
            # CYCLE_INTERVAL 동안 대기 (1초씩 체크해서 중단 가능)
            for _ in range(CYCLE_INTERVAL):
                if self._stop.is_set():
                    return
                time.sleep(1)

    def _run_cycle(self):
        tickers = database.get_all_tickers()
        if not tickers:
            log.info("종목 없음, 업데이트 건너뜀")
            time.sleep(30)
            return

        total = len(tickers)
        batches = [tickers[i:i + BATCH_SIZE] for i in range(0, total, BATCH_SIZE)]
        log.info(f"업데이트 시작: {total}개 종목, {len(batches)}개 배치")

        database.set_progress(total=total, done=0, status="running", started_at=time.time(), finished_at=None)

        done_count = 0
        lock = threading.Lock()

        def process_and_save(batch):
            nonlocal done_count
            results = _process_batch(batch)
            database.upsert_signals(results)
            with lock:
                done_count += len(batch)
                database.set_progress(done=done_count)

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(process_and_save, b) for b in batches]
            for future in as_completed(futures):
                if self._stop.is_set():
                    break
                try:
                    future.result()
                except Exception as e:
                    log.error(f"배치 처리 오류: {e}")

        database.set_progress(status="done", finished_at=time.time())
        log.info(f"업데이트 완료: {done_count}개 처리")
