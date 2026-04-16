import os
import time
import traceback
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import requests
import yfinance as yf
from flask import Flask, jsonify, request
from apscheduler.schedulers.background import BackgroundScheduler
from pytz import timezone

app = Flask(__name__)

# =========================================================
# ENV CONFIG
# =========================================================
PORT = int(os.getenv("PORT", "8080"))
TZ_NAME = os.getenv("TIMEZONE", "Asia/Kuala_Lumpur")
ENABLE_TELEGRAM = os.getenv("ENABLE_TELEGRAM", "true").lower() == "true"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
INTERNAL_SCAN_TOKEN = os.getenv("INTERNAL_SCAN_TOKEN", "").strip()
ENABLE_INTERNAL_SCHEDULER = os.getenv("ENABLE_INTERNAL_SCHEDULER", "false").lower() == "true"
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "10"))
TOP_N_MAIN = int(os.getenv("TOP_N_MAIN", "15"))
TOP_N_EARLY = int(os.getenv("TOP_N_EARLY", "10"))

# Scan params
MIN_PRICE = float(os.getenv("MIN_PRICE", "2"))
MAX_PRICE = float(os.getenv("MAX_PRICE", "100"))
MIN_AVG_DOLLAR_VOLUME = float(os.getenv("MIN_AVG_DOLLAR_VOLUME", "3000000"))
MAX_DAILY_GAIN_FILTER = float(os.getenv("MAX_DAILY_GAIN_FILTER", "8.0"))
BREAKOUT_DISTANCE_PCT = float(os.getenv("BREAKOUT_DISTANCE_PCT", "5.0"))
EARLY_SIGNAL_DISTANCE_PCT = float(os.getenv("EARLY_SIGNAL_DISTANCE_PCT", "8.0"))
VOLUME_RATIO_MIN = float(os.getenv("VOLUME_RATIO_MIN", "1.8"))
STRONG_VOLUME_RATIO = float(os.getenv("STRONG_VOLUME_RATIO", "2.5"))
ACCUM_NEAR_BREAKOUT_PCT = float(os.getenv("ACCUM_NEAR_BREAKOUT_PCT", "5.0"))
PREMARKET_TOP_CANDIDATES = int(os.getenv("PREMARKET_TOP_CANDIDATES", "25"))
NEWS_TOP_CANDIDATES = int(os.getenv("NEWS_TOP_CANDIDATES", "40"))

# =========================================================
# 200-stock universe (curated high-volatility / liquid / thematic)
# =========================================================
TICKERS = [
    # Mega cap / AI / semis
    "NVDA", "AMD", "AVGO", "ARM", "MU", "QCOM", "MRVL", "ANET", "VRT", "TSM",
    "AMAT", "LRCX", "KLAC", "ASML", "SMCI", "INTC", "TXN", "ADI", "NXPI", "MCHP",
    "ON", "CRDO", "ALAB", "MPWR", "ENTG",

    # Software / cloud / cyber / data
    "MSFT", "META", "AMZN", "GOOGL", "AAPL", "PLTR", "SNOW", "CRM", "NOW", "DDOG",
    "NET", "CRWD", "PANW", "ZS", "OKTA", "MDB", "ESTC", "HUBS", "TEAM", "SHOP",
    "TTD", "APP", "PATH", "AI", "BBAI",

    # Fintech / brokers / consumer tech
    "AFRM", "UPST", "SOFI", "NU", "HOOD", "COIN", "MSTR", "PYPL", "SQ", "GTLB",
    "DUOL", "CELH", "HIMS", "ROKU", "PINS", "RDDT", "DASH", "UBER", "ABNB", "CAVA",

    # EV / auto / clean tech / infra
    "TSLA", "RIVN", "LCID", "NIO", "XPEV", "LI", "FSLR", "ENPH", "SEDG", "RUN",
    "ARRY", "BE", "CHPT", "BLDP", "PWR", "ETN", "HUBB", "NVT", "AESI", "FLNC",

    # Space / defense / quantum / frontier tech
    "RKLB", "LUNR", "ASTS", "PL", "IONQ", "QBTS", "RGTI", "QUBT", "SOUN", "SERV",
    "KTOS", "AVAV", "ACHR", "JOBY", "EVTL", "SPIR", "BKSY", "SATS", "INTA", "S",

    # Nuclear / uranium / energy themes
    "OKLO", "SMR", "NNE", "CCJ", "UUUU", "LEU", "CEG", "VST", "TLN", "NRG",
    "BWXT", "GEV", "VRT", "PWR", "ETR", "PCG", "PEG", "AEP", "NEE", "XEL",

    # Biotech / medtech momentum names
    "TEM", "DXCM", "TMDX", "INSP", "ALNY", "VRTX", "ARGX", "MRNA", "NTRA", "EXAS",
    "CRSP", "BEAM", "NTLA", "RXRX", "CGON", "VKTX", "HIMS", "OSCR", "ELV", "UNH",

    # Retail / consumer momentum
    "ONON", "ELF", "ANF", "DECK", "BIRK", "WING", "CMG", "COST", "LULU", "ULTA",
    "NKE", "ETSY", "RVLV", "BOOT", "SFM", "FIVE", "CVNA", "CAR", "BKNG", "EXPE",

    # Industrials / logistics / construction / materials
    "GE", "CAT", "URI", "PH", "TT", "EMR", "JCI", "HWM", "AXON", "NXT",
    "FIX", "STRL", "MTZ", "ROAD", "MLM", "VMC", "X", "CLF", "FCX", "TECK",

    # China / internet ADR high beta
    "BABA", "PDD", "JD", "BIDU", "TME", "IQ", "DADA", "KWEB", "YINN", "FUTU"
]

# dedupe while preserving order
_seen = set()
TICKERS = [x for x in TICKERS if not (x in _seen or _seen.add(x))]

LAST_SCAN_SUMMARY = {
    "status": "never_run",
    "last_run": None,
    "main_count": 0,
    "early_count": 0,
    "top_tickers": []
}

scheduler = None

# =========================================================
# Helpers
# =========================================================
def safe_float(x, default=0.0):
    try:
        if pd.isna(x):
            return default
        return float(x)
    except Exception:
        return default


def format_pct(v):
    return f"{v:.2f}%"


def format_price(v):
    return f"${v:.2f}"


def calc_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


def send_telegram_message(text: str):
    if not ENABLE_TELEGRAM or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(text)
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        r = requests.post(url, json=payload, timeout=20)
        print("telegram:", r.status_code)
        return r.status_code == 200
    except Exception as e:
        print("Telegram error:", e)
        return False


def split_text(text, limit=3800):
    if len(text) <= limit:
        return [text]
    parts = []
    current = []
    size = 0
    for line in text.splitlines(True):
        if size + len(line) > limit and current:
            parts.append("".join(current))
            current = [line]
            size = len(line)
        else:
            current.append(line)
            size += len(line)
    if current:
        parts.append("".join(current))
    return parts


# =========================================================
# Data acquisition
# =========================================================
def download_daily_data(ticker):
    try:
        df = yf.download(
            ticker,
            period="8mo",
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=False,
            prepost=False,
        )
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]
        df = df[[c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]].copy()
        if len(df) < 60:
            return None
        df = df.dropna()
        return df
    except Exception:
        return None


def enrich_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["MA5"] = df["Close"].rolling(5).mean()
    df["MA10"] = df["Close"].rolling(10).mean()
    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA50"] = df["Close"].rolling(50).mean()
    df["VOL5"] = df["Volume"].rolling(5).mean()
    df["VOL20"] = df["Volume"].rolling(20).mean()
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - df["Close"].shift(1)).abs(),
        (df["Low"] - df["Close"].shift(1)).abs()
    ], axis=1).max(axis=1)
    df["ATR14"] = tr.rolling(14).mean()
    df["RSI14"] = calc_rsi(df["Close"], 14)
    df["DailyPct"] = df["Close"].pct_change() * 100
    df["DollarVolume"] = df["Close"] * df["Volume"]
    return df


def detect_accumulation(df: pd.DataFrame):
    if len(df) < 30:
        return False, "C", 0

    recent = df.tail(10).copy()
    today = recent.iloc[-1]
    recent["RangePct"] = (recent["High"] - recent["Low"]) / recent["Close"] * 100
    avg_range_10 = safe_float(recent["RangePct"].mean())
    avg_range_5 = safe_float(recent["RangePct"].tail(5).mean())
    vol5 = safe_float(today["VOL5"])
    vol20 = safe_float(today["VOL20"])
    volume_ratio = today["Volume"] / vol20 if vol20 > 0 else 0

    body_high = max(today["Open"], today["Close"])
    upper_wick = today["High"] - body_high
    candle_range = max(today["High"] - today["Low"], 0.0001)
    upper_wick_ratio = upper_wick / candle_range

    box_high = recent["High"].max()
    box_low = recent["Low"].min()
    box_mid = (box_high + box_low) / 2 if (box_high + box_low) != 0 else today["Close"]
    close = today["Close"]

    score = 0
    if avg_range_5 <= avg_range_10:
        score += 20
    if avg_range_5 < 4.5:
        score += 20
    if vol5 < vol20:
        score += 15
    if 0.95 <= close / box_mid <= 1.05:
        score += 15
    if volume_ratio >= 1.3:
        score += 15
    if upper_wick_ratio < 0.35:
        score += 15

    if score >= 80:
        return True, "A", score
    if score >= 60:
        return True, "B", score
    return False, "C", score


def smart_money_score(close, high, low, volume_ratio):
    rng = max(high - low, 0.0001)
    close_location = (close - low) / rng
    score = 0
    if close_location >= 0.8:
        score += 10
    elif close_location >= 0.65:
        score += 6
    else:
        score += 2

    if volume_ratio >= 3.0:
        score += 10
    elif volume_ratio >= 2.0:
        score += 7
    elif volume_ratio >= 1.5:
        score += 4
    else:
        score += 1

    return score, close_location


def get_news_score(ticker):
    """Try yfinance news. If unavailable, return neutral score 0."""
    try:
        tk = yf.Ticker(ticker)
        news_items = []
        if hasattr(tk, "news"):
            news_items = tk.news or []
        count = len(news_items)
        if count >= 5:
            return 15, count
        if count >= 2:
            return 10, count
        if count >= 1:
            return 6, count
        return 0, 0
    except Exception:
        return 0, 0


def get_premarket_signal(ticker):
    """Use 1m pre/post data. Return (score, pct, volume, note)."""
    try:
        df = yf.download(
            ticker,
            period="5d",
            interval="1m",
            auto_adjust=False,
            progress=False,
            threads=False,
            prepost=True,
        )
        if df is None or df.empty:
            return 0, 0.0, 0, "无盘前数据"
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]
        if "Close" not in df.columns or "Volume" not in df.columns:
            return 0, 0.0, 0, "无有效盘前数据"

        idx = df.index
        if getattr(idx, "tz", None) is not None:
            idx_local = idx.tz_convert("America/New_York")
        else:
            idx_local = idx.tz_localize("America/New_York")
        df = df.copy()
        df.index = idx_local

        today_date = idx_local[-1].date()
        today_df = df[df.index.date == today_date]
        if today_df.empty:
            return 0, 0.0, 0, "今日无数据"

        pre = today_df.between_time("04:00", "09:29")
        regular = today_df.between_time("09:30", "16:00")
        if pre.empty:
            return 0, 0.0, 0, "暂无盘前成交"

        pre_last = safe_float(pre["Close"].iloc[-1])
        pre_vol = int(safe_float(pre["Volume"].sum(), 0))

        ref_close = None
        if not regular.empty:
            ref_close = safe_float(regular["Close"].iloc[0])
        else:
            # fallback to previous non-today close before today starts
            prev_df = df[df.index.date < today_date]
            if not prev_df.empty:
                prev_regular = prev_df.between_time("09:30", "16:00")
                if not prev_regular.empty:
                    ref_close = safe_float(prev_regular["Close"].iloc[-1])
                else:
                    ref_close = safe_float(prev_df["Close"].iloc[-1])

        if not ref_close or ref_close <= 0:
            return 0, 0.0, pre_vol, "盘前基准价不足"

        pre_pct = (pre_last - ref_close) / ref_close * 100
        score = 0
        if pre_pct >= 3.0:
            score += 15
        elif pre_pct >= 1.5:
            score += 10
        elif pre_pct > 0:
            score += 4
        elif pre_pct <= -2.0:
            score -= 5

        if pre_vol >= 500000:
            score += 8
        elif pre_vol >= 100000:
            score += 5
        elif pre_vol >= 30000:
            score += 2

        note = f"盘前{pre_pct:.2f}% / 量{pre_vol:,}"
        return score, pre_pct, pre_vol, note
    except Exception:
        return 0, 0.0, 0, "盘前抓取失败"


def analyze_ticker(ticker: str):
    df = download_daily_data(ticker)
    if df is None:
        return None

    df = enrich_indicators(df)
    today = df.iloc[-1]

    close = safe_float(today["Close"])
    high = safe_float(today["High"])
    low = safe_float(today["Low"])
    volume = safe_float(today["Volume"])
    vol20 = safe_float(today["VOL20"])
    vol5 = safe_float(today["VOL5"])
    avg_dollar_vol = safe_float(df["DollarVolume"].tail(20).mean())

    if close < MIN_PRICE or close > MAX_PRICE:
        return None
    if avg_dollar_vol < MIN_AVG_DOLLAR_VOLUME:
        return None

    resistance_20 = safe_float(df["High"].tail(21).iloc[:-1].max())
    support_20 = safe_float(df["Low"].tail(21).iloc[:-1].min())
    if resistance_20 <= 0:
        return None

    distance_to_breakout = (resistance_20 - close) / resistance_20 * 100
    daily_gain = safe_float(today["DailyPct"])
    vol_ratio_20 = volume / vol20 if vol20 > 0 else 0

    ma5 = safe_float(today["MA5"])
    ma10 = safe_float(today["MA10"])
    ma20 = safe_float(today["MA20"])
    ma50 = safe_float(today["MA50"])
    atr = safe_float(today["ATR14"])
    rsi = safe_float(today["RSI14"])

    if close > ma10 > ma20 > 0:
        trend_label = "强势多头"
    elif close > ma5 and ma5 > ma20 > 0:
        trend_label = "反弹转强"
    elif close > ma20 > 0:
        trend_label = "中性偏强"
    else:
        trend_label = "偏弱"

    candle_range = max(high - low, 0.0001)
    upper_wick = high - max(today["Open"], close)
    upper_wick_ratio = upper_wick / candle_range

    is_breakout = close > resistance_20 and vol_ratio_20 >= VOLUME_RATIO_MIN
    is_near_breakout = 0 <= distance_to_breakout <= BREAKOUT_DISTANCE_PCT
    early_signal = (
        0 <= distance_to_breakout <= EARLY_SIGNAL_DISTANCE_PCT
        and close > ma5 > ma20 > 0
        and daily_gain < 4.5
    )

    accum_flag, accum_grade, accum_score_raw = detect_accumulation(df)
    sm_score, close_location = smart_money_score(close, high, low, vol_ratio_20)

    # score components
    if vol_ratio_20 >= 3.0:
        volume_score = 30
    elif vol_ratio_20 >= 2.5:
        volume_score = 26
    elif vol_ratio_20 >= 2.0:
        volume_score = 22
    elif vol_ratio_20 >= 1.8:
        volume_score = 18
    elif vol_ratio_20 >= 1.5:
        volume_score = 12
    else:
        volume_score = 5

    if is_breakout:
        breakout_score = 30
    elif distance_to_breakout <= 2:
        breakout_score = 26
    elif distance_to_breakout <= 3.5:
        breakout_score = 22
    elif distance_to_breakout <= 5:
        breakout_score = 18
    elif distance_to_breakout <= 8:
        breakout_score = 10
    else:
        breakout_score = 3

    if close > ma5 > ma10 > ma20 > ma50 > 0:
        trend_score = 20
    elif close > ma5 > ma10 > ma20 > 0:
        trend_score = 17
    elif close > ma5 > ma20 > 0:
        trend_score = 13
    elif close > ma20 > 0:
        trend_score = 8
    else:
        trend_score = 2

    if upper_wick_ratio < 0.2:
        candle_score = 10
    elif upper_wick_ratio < 0.35:
        candle_score = 7
    elif upper_wick_ratio < 0.5:
        candle_score = 4
    else:
        candle_score = 1

    if 55 <= rsi <= 68:
        rsi_score = 10
    elif 50 <= rsi < 55 or 68 < rsi <= 75:
        rsi_score = 7
    else:
        rsi_score = 4

    if accum_grade == "A":
        accum_bonus = 18
    elif accum_grade == "B":
        accum_bonus = 12
    else:
        accum_bonus = 4

    penalty = 0
    risk_flags = []
    if daily_gain > MAX_DAILY_GAIN_FILTER:
        penalty += 12
        risk_flags.append("当天涨幅过大")
    if upper_wick_ratio > 0.45:
        penalty += 8
        risk_flags.append("长上影偏重")
    if close < ma20:
        penalty += 8
        risk_flags.append("仍在MA20下方")
    if vol_ratio_20 < 1.2:
        penalty += 10
        risk_flags.append("量能不足")

    base_score = max(volume_score + breakout_score + trend_score + candle_score + rsi_score + accum_bonus + sm_score - penalty, 0)

    if base_score >= 88:
        signal_grade = "S"
    elif base_score >= 78:
        signal_grade = "A"
    elif base_score >= 68:
        signal_grade = "B"
    elif base_score >= 58:
        signal_grade = "C"
    else:
        signal_grade = "D"

    breakout_price = resistance_20
    buy_trigger = max(breakout_price * 1.002, close * 1.003)
    chase_limit = breakout_price * 1.03
    stop_loss = max(ma10, close - 1.2 * atr) if atr > 0 else ma10
    take_profit_1 = close * 1.03
    take_profit_2 = close * 1.05

    return {
        "ticker": ticker,
        "close": close,
        "daily_gain": daily_gain,
        "volume_ratio": vol_ratio_20,
        "distance_to_breakout": distance_to_breakout,
        "resistance": resistance_20,
        "support": support_20,
        "ma10": ma10,
        "ma20": ma20,
        "atr": atr,
        "rsi": rsi,
        "trend_label": trend_label,
        "accum_flag": accum_flag,
        "accum_grade": accum_grade,
        "accum_score": accum_score_raw,
        "smart_money_score": sm_score,
        "close_location": close_location,
        "is_breakout": is_breakout,
        "is_near_breakout": is_near_breakout,
        "early_signal": early_signal,
        "signal_grade": signal_grade,
        "base_score": base_score,
        "score": base_score,
        "potential_5pct": False,
        "action": "先观察",
        "buy_trigger": buy_trigger,
        "chase_limit": chase_limit,
        "stop_loss": stop_loss,
        "take_profit_1": take_profit_1,
        "take_profit_2": take_profit_2,
        "risk_flags": risk_flags,
        "news_score": 0,
        "news_count": 0,
        "premarket_score": 0,
        "premarket_pct": 0.0,
        "premarket_volume": 0,
        "premarket_note": "未执行盘前确认",
    }


def enrich_candidates_with_news(results):
    ranked = sorted(results, key=lambda x: x["score"], reverse=True)[:NEWS_TOP_CANDIDATES]
    selected = {r["ticker"]: r for r in ranked}
    for ticker, row in selected.items():
        score, count = get_news_score(ticker)
        row["news_score"] = score
        row["news_count"] = count
        row["score"] += score
    return results


def enrich_candidates_with_premarket(results):
    ranked = sorted(results, key=lambda x: x["score"], reverse=True)[:PREMARKET_TOP_CANDIDATES]
    selected = {r["ticker"]: r for r in ranked}
    for ticker, row in selected.items():
        score, pct, vol, note = get_premarket_signal(ticker)
        row["premarket_score"] = score
        row["premarket_pct"] = pct
        row["premarket_volume"] = vol
        row["premarket_note"] = note
        row["score"] += score
    return results


def finalize_actions(results):
    for r in results:
        if (
            r["score"] >= 85
            and r["volume_ratio"] >= 2
            and r["distance_to_breakout"] <= 4
            and r["news_score"] >= 6
            and r["smart_money_score"] >= 12
        ):
            r["potential_5pct"] = True

        if r["is_breakout"] and r["volume_ratio"] >= STRONG_VOLUME_RATIO and r["daily_gain"] <= 6:
            r["action"] = "可列为明日重点，盘中放量站稳可顺势跟进"
        elif r["is_near_breakout"] and r["volume_ratio"] >= VOLUME_RATIO_MIN:
            r["action"] = "接近突破区，优先观察是否放量过前高"
        elif r["accum_flag"] and r["distance_to_breakout"] <= ACCUM_NEAR_BREAKOUT_PCT:
            r["action"] = "主力吸筹中，未正式突破，可列入提前预警"
        else:
            r["action"] = "先观察，不急着追"

        if r["score"] >= 95:
            r["signal_grade"] = "S"
        elif r["score"] >= 85:
            r["signal_grade"] = "A"
        elif r["score"] >= 72:
            r["signal_grade"] = "B"
        elif r["score"] >= 60:
            r["signal_grade"] = "C"
        else:
            r["signal_grade"] = "D"
    return results


def analyze_universe():
    results = []
    errors = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(analyze_ticker, t): t for t in TICKERS}
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                res = future.result()
                if res:
                    results.append(res)
            except Exception as e:
                errors.append((ticker, str(e)))
    return results, errors


def build_message(results):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    main_list = []
    early_list = []

    for r in results:
        if r["score"] >= 72 and r["daily_gain"] < MAX_DAILY_GAIN_FILTER:
            if r["is_breakout"] or r["is_near_breakout"] or r["potential_5pct"]:
                main_list.append(r)

    for r in results:
        if (
            r["accum_grade"] in ["A", "B"]
            and not r["is_breakout"]
            and r["distance_to_breakout"] <= EARLY_SIGNAL_DISTANCE_PCT
            and r["score"] >= 60
        ):
            early_list.append(r)

    main_list = sorted(main_list, key=lambda x: (
        x["potential_5pct"],
        x["signal_grade"] == "S",
        x["score"],
        x["volume_ratio"],
        x["premarket_pct"],
    ), reverse=True)[:TOP_N_MAIN]

    early_list = sorted(early_list, key=lambda x: (
        x["accum_grade"] == "A",
        x["score"],
        -x["distance_to_breakout"],
    ), reverse=True)[:TOP_N_EARLY]

    lines = []
    lines.append(f"🚀 <b>美股爆发扫描器 V7</b>")
    lines.append(f"🕒 时间：{now_str}")
    lines.append(f"📦 扫描池：{len(TICKERS)} 支")
    lines.append("")

    if main_list:
        lines.append("🔥 <b>主名单：次日重点观察</b>")
        for i, r in enumerate(main_list, 1):
            tag = "🎯5%潜力" if r["potential_5pct"] else "观察"
            breakout_text = "已突破" if r["is_breakout"] else "近突破"
            risk_text = "；".join(r["risk_flags"]) if r["risk_flags"] else "无明显异常"
            news_text = f"新闻{r['news_count']}条/+{r['news_score']}"
            pre_text = f"{r['premarket_note']}/+{r['premarket_score']}"
            lines.append(
                f"{i}. <b>{r['ticker']}</b> | 等级 {r['signal_grade']} | 分数 {r['score']:.1f} | {tag}\n"
                f"   收盘：{format_price(r['close'])} ({format_pct(r['daily_gain'])})\n"
                f"   量比：{r['volume_ratio']:.2f}x | {breakout_text} | 距离突破：{format_pct(r['distance_to_breakout'])}\n"
                f"   趋势：{r['trend_label']} | 吸筹：{r['accum_grade']}({r['accum_score']}) | 资金：+{r['smart_money_score']}\n"
                f"   催化：{news_text} | 盘前：{pre_text}\n"
                f"   明天买点：>{format_price(r['buy_trigger'])}\n"
                f"   不追价区：>{format_price(r['chase_limit'])}\n"
                f"   止损参考：{format_price(r['stop_loss'])}\n"
                f"   止盈参考：{format_price(r['take_profit_1'])} / {format_price(r['take_profit_2'])}\n"
                f"   策略：{r['action']}\n"
                f"   风险：{risk_text}"
            )
            lines.append("")

    if early_list:
        lines.append("🧲 <b>提前预警名单：主力吸筹但未突破</b>")
        for i, r in enumerate(early_list, 1):
            lines.append(
                f"{i}. <b>{r['ticker']}</b> | 吸筹 {r['accum_grade']} | 分数 {r['score']:.1f}\n"
                f"   收盘：{format_price(r['close'])} | 距离突破：{format_pct(r['distance_to_breakout'])}\n"
                f"   量比：{r['volume_ratio']:.2f}x | 趋势：{r['trend_label']} | 催化：{r['news_count']}条\n"
                f"   策略：先列观察名单，等放量突破再出手"
            )
            lines.append("")

    if not main_list and not early_list:
        lines.append("📭 今天没有强信号。")
        lines.append("建议：继续观察，避免硬追。")

    lines.append("📌 <b>纪律</b>")
    lines.append("1. 开盘直接高开太多不追")
    lines.append("2. 只做放量突破，不做无量冲高")
    lines.append("3. 主名单优先，提前预警次之")
    lines.append("4. 跌回关键位就执行纪律")

    return "\n".join(lines), main_list, early_list


def run_scan(send_telegram=True):
    global LAST_SCAN_SUMMARY
    start = time.time()
    results, errors = analyze_universe()
    results = enrich_candidates_with_news(results)
    results = enrich_candidates_with_premarket(results)
    results = finalize_actions(results)
    results = sorted(results, key=lambda x: (
        x["potential_5pct"],
        x["signal_grade"] == "S",
        x["score"],
        x["volume_ratio"],
        x["premarket_pct"],
    ), reverse=True)

    msg, main_list, early_list = build_message(results)

    if send_telegram:
        for chunk in split_text(msg):
            send_telegram_message(chunk)

    LAST_SCAN_SUMMARY = {
        "status": "ok",
        "last_run": datetime.now().isoformat(timespec="seconds"),
        "elapsed_seconds": round(time.time() - start, 2),
        "main_count": len(main_list),
        "early_count": len(early_list),
        "top_tickers": [x["ticker"] for x in main_list[:5]],
        "errors": errors[:10],
    }

    return {
        "message": msg,
        "results": results,
        "main_list": main_list,
        "early_list": early_list,
        "errors": errors,
        "summary": LAST_SCAN_SUMMARY,
    }


# =========================================================
# Scheduler
# =========================================================
def scheduled_scan(label):
    try:
        print(f"Running scheduled scan: {label}")
        run_scan(send_telegram=True)
    except Exception as e:
        err = f"❌ 定时扫描异常\n{label}\n{str(e)}\n\n{traceback.format_exc()[:2500]}"
        print(err)
        send_telegram_message(err)


def setup_scheduler():
    global scheduler
    if not ENABLE_INTERNAL_SCHEDULER:
        return None
    scheduler = BackgroundScheduler(timezone=timezone(TZ_NAME))
    # Malaysia time examples: 22:00 and 04:00 daily
    scheduler.add_job(lambda: scheduled_scan("22:00 daily"), "cron", hour=22, minute=0)
    scheduler.add_job(lambda: scheduled_scan("04:00 daily"), "cron", hour=4, minute=0)
    scheduler.start()
    return scheduler


# =========================================================
# Routes
# =========================================================
@app.route("/")
def index():
    return jsonify({
        "service": "US Stock Scanner V7",
        "status": "running",
        "tickers": len(TICKERS),
        "timezone": TZ_NAME,
        "scheduler_enabled": ENABLE_INTERNAL_SCHEDULER,
        "last_scan": LAST_SCAN_SUMMARY,
    })


@app.route("/api/health")
def health():
    return jsonify({
        "ok": True,
        "service": "US Stock Scanner V7",
        "tickers": len(TICKERS),
        "last_scan": LAST_SCAN_SUMMARY,
    })


@app.route("/run-scan", methods=["GET", "POST"])
def route_run_scan():
    try:
        token = request.args.get("token", "") or request.headers.get("X-Scan-Token", "")
        if INTERNAL_SCAN_TOKEN and token != INTERNAL_SCAN_TOKEN:
            return jsonify({"ok": False, "error": "Unauthorized"}), 401

        data = run_scan(send_telegram=True)
        return jsonify({
            "ok": True,
            "summary": data["summary"],
            "main_list": [
                {
                    "ticker": x["ticker"],
                    "score": x["score"],
                    "grade": x["signal_grade"],
                    "potential_5pct": x["potential_5pct"],
                }
                for x in data["main_list"]
            ],
            "early_list": [
                {
                    "ticker": x["ticker"],
                    "score": x["score"],
                    "grade": x["accum_grade"],
                }
                for x in data["early_list"]
            ],
        })
    except Exception as e:
        err = traceback.format_exc()
        send_telegram_message(f"❌ /run-scan 异常\n{str(e)}")
        return jsonify({"ok": False, "error": str(e), "trace": err[:3000]}), 500


@app.route("/run-scan-local", methods=["GET"])
def route_run_scan_local():
    try:
        data = run_scan(send_telegram=False)
        return f"<pre>{data['message']}</pre>"
    except Exception as e:
        return f"<pre>ERROR\n{str(e)}\n\n{traceback.format_exc()}</pre>", 500


if __name__ == "__main__":
    setup_scheduler()
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
