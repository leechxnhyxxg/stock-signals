import numpy as np
import pandas as pd


def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_macd(close: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    signal_line = macd.ewm(span=signal, adjust=False).mean()
    histogram = macd - signal_line
    return macd, signal_line, histogram


def calc_ma(close: pd.Series, short=5, long=20):
    return close.rolling(short).mean(), close.rolling(long).mean()


def generate_signal(df: pd.DataFrame) -> dict:
    close = df["Close"].dropna()
    if len(close) < 60:
        return {"signal": "데이터부족", "score": 0, "rsi": None, "macd_cross": None, "ma_cross": None}

    rsi = calc_rsi(close)
    macd, signal_line, hist = calc_macd(close)
    ma5, ma20 = calc_ma(close)

    def f(s, i=-1): return float(s.to_numpy()[i])

    rsi_val = round(f(rsi), 2)
    hist_prev = f(hist, -2)
    hist_curr = f(hist, -1)
    ma5_val = f(ma5)
    ma20_val = f(ma20)
    ma5_prev = f(ma5, -2)
    ma20_prev = f(ma20, -2)

    score = 0

    # RSI
    if rsi_val < 30:
        rsi_signal = "매수"
        score += 1
    elif rsi_val > 70:
        rsi_signal = "매도"
        score -= 1
    else:
        rsi_signal = "중립"

    # MACD cross
    if hist_prev < 0 and hist_curr >= 0:
        macd_signal = "골든크로스(매수)"
        score += 1
    elif hist_prev > 0 and hist_curr <= 0:
        macd_signal = "데드크로스(매도)"
        score -= 1
    else:
        macd_signal = "추세중" if hist_curr > 0 else "하락추세"

    # MA cross
    if ma5_prev < ma20_prev and ma5_val >= ma20_val:
        ma_signal = "골든크로스(매수)"
        score += 1
    elif ma5_prev > ma20_prev and ma5_val <= ma20_val:
        ma_signal = "데드크로스(매도)"
        score -= 1
    elif ma5_val > ma20_val:
        ma_signal = "상승추세"
    else:
        ma_signal = "하락추세"

    if score >= 2:
        overall = "강력매수"
    elif score == 1:
        overall = "매수"
    elif score == -1:
        overall = "매도"
    elif score <= -2:
        overall = "강력매도"
    else:
        overall = "중립"

    return {
        "signal": overall,
        "score": score,
        "rsi": rsi_val,
        "rsi_signal": rsi_signal,
        "macd_signal": macd_signal,
        "ma_signal": ma_signal,
        "price": round(f(close), 2),
        "price_change": round(f(close) - f(close, -2), 2),
        "price_change_pct": round((f(close) - f(close, -2)) / f(close, -2) * 100, 2),
        "ma5": round(ma5_val, 2),
        "ma20": round(ma20_val, 2),
    }
