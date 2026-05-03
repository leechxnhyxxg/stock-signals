import logging
from datetime import datetime

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)


def _fetch_naver_market(sosok: int, market: str, suffix: str) -> list:
    """Naver Finance 시장요약 페이지에서 종목 목록 스크래핑 (인증 불필요)."""
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        # 1페이지에서 전체 페이지 수 파악
        r = requests.get(
            f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}&page=1",
            headers=headers, timeout=15,
        )
        soup = BeautifulSoup(r.content.decode("euc-kr", errors="replace"), "html.parser")
        last_page_a = soup.select("td.pgRR a")
        total_pages = 1
        if last_page_a:
            import re as _re
            m = _re.search(r"page=(\d+)", last_page_a[0]["href"])
            if m:
                total_pages = int(m.group(1))

        result = []
        import re as re2
        for page in range(1, total_pages + 1):
            try:
                rp = requests.get(
                    f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}&page={page}",
                    headers=headers, timeout=15,
                )
                sp = BeautifulSoup(rp.content.decode("euc-kr", errors="replace"), "html.parser")
                for row in sp.select("table.type_2 tr"):
                    a = row.find("a", href=lambda h: h and "code=" in h)
                    if not a:
                        continue
                    code_m = re2.search(r"code=([0-9]{6})", a["href"])
                    if not code_m:
                        continue
                    code = code_m.group(1)
                    name = a.text.strip()
                    if code and name and re2.match(r"^\d{6}$", code):
                        result.append({"ticker": f"{code}{suffix}", "name": name, "market": market})
            except Exception:
                pass

        log.info(f"{market}: {len(result)}개 수집 (총 {total_pages}페이지)")
        return result
    except Exception as e:
        log.warning(f"{market} 수집 실패: {e}")
        return []


def fetch_kospi_universe() -> list:
    return _fetch_naver_market(sosok=0, market="KOSPI", suffix=".KS")


def fetch_kosdaq_universe() -> list:
    return _fetch_naver_market(sosok=1, market="KOSDAQ", suffix=".KQ")


def fetch_sp500_universe() -> list:
    try:
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table", {"id": "constituents"})
        result = []
        for row in table.find_all("tr")[1:]:
            cols = row.find_all("td")
            if len(cols) < 2:
                continue
            ticker = cols[0].text.strip().replace(".", "-")
            name = cols[1].text.strip()
            result.append({"ticker": ticker, "name": name, "market": "SP500"})
        log.info(f"S&P500: {len(result)}개 수집")
        return result
    except Exception as e:
        log.warning(f"S&P500 수집 실패: {e}")
        return []


def fetch_nasdaq_universe() -> list:
    try:
        url = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
        resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        lines = resp.text.strip().split("\n")
        result = []
        for line in lines[1:]:  # skip header
            if line.startswith("File Creation Time"):
                continue
            parts = line.split("|")
            if len(parts) < 4:
                continue
            ticker = parts[0].strip()
            name = parts[1].strip()
            test_issue = parts[3].strip()
            if test_issue == "Y":
                continue
            if any(c in ticker for c in ["$", ".", "^", "/"]):
                continue
            if len(ticker) > 5:
                continue
            result.append({"ticker": ticker, "name": name, "market": "NASDAQ"})
        log.info(f"NASDAQ: {len(result)}개 수집")
        return result
    except Exception as e:
        log.warning(f"NASDAQ 수집 실패: {e}")
        return []


def load_all_universes() -> list:
    log.info("전체 종목 유니버스 수집 시작...")
    all_stocks = []
    seen = set()

    for fetcher in [fetch_sp500_universe, fetch_nasdaq_universe, fetch_kospi_universe, fetch_kosdaq_universe]:
        stocks = fetcher()
        for s in stocks:
            if s["ticker"] not in seen:
                seen.add(s["ticker"])
                all_stocks.append(s)

    log.info(f"총 {len(all_stocks)}개 종목 수집 완료")
    return all_stocks
