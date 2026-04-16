import os
import time
import math
import traceback
from datetime import datetime
from threading import Lock

import numpy as np
import pandas as pd
import requests
import yfinance as yf
from flask import Flask, jsonify, request

# =========================================================
# 基础配置
# =========================================================

APP_NAME = "US Stock Scanner V7.2"
TIMEZONE = os.getenv("TIMEZONE", "Asia/Kuala_Lumpur")

ENABLE_TELEGRAM = os.getenv("ENABLE_TELEGRAM", "true").lower() == "true"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

INTERNAL_SCAN_TOKEN = os.getenv("INTERNAL_SCAN_TOKEN", "").strip()

ENABLE_INTERNAL_SCHEDULER = os.getenv("ENABLE_INTERNAL_SCHEDULER", "false").lower() == "true"

# =========================================================
# 稳定版扫描参数
# =========================================================

MIN_PRICE = 2
MAX_PRICE = 80
LOOKBACK_PERIOD = "6mo"

MIN_AVG_DOLLAR_VOLUME = 4_000_000
MAX_DAILY_GAIN_FILTER = 8.0

BREAKOUT_DISTANCE_PCT = 5.0
EARLY_SIGNAL_DISTANCE_PCT = 8.0
ACCUM_NEAR_BREAKOUT_PCT = 5.0

VOLUME_RATIO_MIN = 1.8
STRONG_VOLUME_RATIO = 2.5

TOP_N_MAIN = 10
TOP_N_EARLY = 8

# ====== V7.2 限速参数 ======
PER_TICKER_SLEEP = 0.35      # 每支股票之间停顿
BATCH_SIZE = 20              # 每批20支
BATCH_SLEEP = 3.0            # 每批后休息3秒
DOWNLOAD_RETRY = 3           # 下载重试次数
DOWNLOAD_RETRY_SLEEP = 1.5   # 重试间隔
NEWS_CHECK_LIMIT = 15        # 只对前15名候选股查新闻
PREMARKET_CHECK_LIMIT = 15   # 只对前15名候选股查盘前
REQUEST_TIMEOUT = 10

# =========================================================
# 200股股票池（稳健版）
# =========================================================

TICKERS = [
    # Mega / AI / Tech
    "NVDA", "AMD", "SMCI", "PLTR", "TSLA", "META", "AMZN", "MSFT", "AAPL", "GOOGL",
    "AVGO", "ARM", "MU", "QCOM", "CRM", "SNOW", "NET", "CRWD", "SHOP", "UBER",
    "TTD", "DDOG", "ZS", "PANW", "NOW", "MDB", "ADBE", "ORCL", "INTC", "ANET",

    # Fintech / Payment / Crypto
    "COIN", "MSTR", "MARA", "RIOT", "HUT", "SOFI", "NU", "UPST", "AFRM", "HOOD",
    "PYPL", "SQ", "RBLX", "GTLB", "DOCU", "BILL", "TOST", "CFLT", "PATH", "AI",

    # AI / Robotics / Emerging Tech
    "BBAI", "SERV", "IONQ", "QBTS", "RGTI", "SOUN", "TEM", "APP", "RDDT", "C3AI",
    "ESTC", "S", "FROG", "ASAN", "IOT", "CLS", "SPSC", "FSLY", "WK", "AIOT",

    # Space / Defense / High Beta
    "RKLB", "ASTS", "LUNR", "JOBY", "ACHR", "KTOS", "AVAV", "SPCE", "BLDE", "IRDM",
    "MAXN", "ARQQ", "BKSY", "PL", "MDAI", "DXYZ", "SIDU", "MNTS", "SATL", "RDW",

    # Energy / Nuclear / Power / AI Infra
    "VRT", "PWR", "ENPH", "SEDG", "FSLR", "RUN", "SMR", "OKLO", "NNE", "CEG",
    "NRG", "VST", "GEV", "ETN", "HUBB", "FIX", "MYRG", "POWL", "AES", "BE",

    # Consumer / Growth / Momentum
    "CAVA", "ELF", "ONON", "CELH", "DUOL", "HIMS", "OLLI", "BROS", "CMG", "NKE",
    "LULU", "ULTA", "CROX", "ANF", "ABNB", "DASH", "CVNA", "CAR", "BKNG", "EXPE",

    # Biotech / Health High Beta
    "VKTX", "ALT", "HIMS", "OSCR", "RXRX", "CRSP", "EDIT", "BEAM", "NTLA", "VERV",
    "MDGL", "CYTK", "MRNA", "DNA", "SDGR", "XBI", "TGTX", "ALKS", "EXEL", "GH",

    # Semiconductor / Hardware / Infra
    "AMAT", "KLAC", "LRCX", "ASML", "ONTO", "COHR", "MRVL", "MPWR", "ENTG", "WOLF",
    "AEHR", "ACLS", "FORM", "HIMX", "SYNA", "ALAB", "CAMT", "IPGP", "OLED", "TER",

    # Industrial / Transport / cyclicals
    "DE", "CAT", "URI", "PCAR", "ODFL", "XPO", "SAIA", "UBER", "PINS", "ETSY",
    "ROKU", "SNAP", "SPOT", "NFLX", "DIS", "PARA", "WBD", "DJT", "BABA", "PDD",

    # Extra momentum / tradable
    "FUBO", "PLUG", "CHPT", "QS", "LCID", "RIVN", "NIO", "XPEV", "LI", "GRAB",
    "OPEN", "ARRY", "W", "GME", "AMC", "BB", "SIRI", "INTA", "ZI", "APPS"
]

# 去重后保留顺序
_seen = set()
TICKERS = [x for x in TICKERS if not (x in _seen or _seen.add(x))]

# =========================================================
# 全局状态
# =========================================================

app = Flask(__name__)

SCAN_LOCK = Lock()
SCAN_STATE = {
    "status": "running",
    "last_run": None,
    "last_scan_summary": "never_run",
    "main_count": 0,
    "early_count": 0,
    "top_tickers": [],
    "tickers": len(TICKERS),
    "scheduler_enabled": ENABLE_INTERNAL_SCHEDULER,
    "service": APP_NAME,
    "timezone": TIMEZONE,
}

# =========================================================
# 工具函数
# =========================================================

def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def safe_float(x, default=0.0):
    try:
        if pd.isna(x):
            return default
        return float(x)
    except Exception:
        return default

def pct_change(a, b):
    if b is None or b == 0:
        return 0.0
    return (a - b) / b * 100.0

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
    if not ENABLE_TELEGRAM:
        print(text)
        return

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram 未配置，改为本地输出")
        print(text)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    try:
        requests.post(url, json=payload, timeout=20)
    except Exception as e:
        print("Telegram 发送失败:", e)

# =========================================================
# 稳定版数据下载
# =========================================================

def normalize_ohlcv(df: pd.DataFrame):
    if df is None or df.empty:
        return None

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]

    cols = {}
    for c in df.columns:
        lc = str(c).lower()
        if "open" == lc or lc.endswith("open"):
            cols[c] = "Open"
        elif "high" == lc or lc.endswith("high"):
            cols[c] = "High"
        elif "low" == lc or lc.endswith("low"):
            cols[c] = "Low"
        elif "close" == lc or lc.endswith("close"):
            cols[c] = "Close"
        elif "volume" == lc or lc.endswith("volume"):
            cols[c] = "Volume"

    df = df.rename(columns=cols)

    needed = ["Open", "High", "Low", "Close", "Volume"]
    if not all(c in df.columns for c in needed):
        return None

    df = df[needed].copy().dropna()
    if len(df) < 40:
        return None
    return df

def download_daily_data(ticker: str):
    last_err = None

    for attempt in range(DOWNLOAD_RETRY):
        try:
            df = yf.download(
                ticker,
                period=LOOKBACK_PERIOD,
                interval="1d",
                auto_adjust=False,
                progress=False,
                threads=False
            )

            df = normalize_ohlcv(df)
            if df is not None and not df.empty:
                return df

        except Exception as e:
            last_err = e

        time.sleep(DOWNLOAD_RETRY_SLEEP * (attempt + 1))

    if last_err:
        print(f"[{ticker}] daily download failed: {last_err}")
    return None

def fetch_premarket_data(ticker: str):
    """
    只对高分候选股查，减少限流。
    """
    try:
        df = yf.download(
            ticker,
            period="2d",
            interval="1m",
            prepost=True,
            auto_adjust=False,
            progress=False,
            threads=False
        )

        if df is None or df.empty:
            return {
                "pm_change": 0.0,
                "pm_volume": 0.0,
                "pm_active": False
            }

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]

        if "Close" not in df.columns or "Volume" not in df.columns:
            return {"pm_change": 0.0, "pm_volume": 0.0, "pm_active": False}

        last_close = safe_float(df["Close"].dropna().iloc[-1])
        prev_close = safe_float(df["Close"].dropna().iloc[0])

        pm_change = pct_change(last_close, prev_close)
        pm_volume = safe_float(df["Volume"].sum())

        return {
            "pm_change": pm_change,
            "pm_volume": pm_volume,
            "pm_active": abs(pm_change) >= 1.0 or pm_volume > 100000
        }
    except Exception:
        return {
            "pm_change": 0.0,
            "pm_volume": 0.0,
            "pm_active": False
        }

def check_news_quick(ticker: str):
    """
    轻量新闻检查，只对候选股查。
    """
    try:
        url = f"https://query1.finance.yahoo.com/v1/finance/search?q={ticker}"
        r = requests.get(url, timeout=REQUEST_TIMEOUT)
        if r.status_code != 200:
            return {"has_news": False, "news_score": 0}

        data = r.json()
        news = data.get("news", [])
        count = len(news)

        if count >= 5:
            return {"has_news": True, "news_score": 15}
        elif count >= 2:
            return {"has_news": True, "news_score": 10}
        elif count >= 1:
            return {"has_news": True, "news_score": 6}
        else:
            return {"has_news": False, "news_score": 0}
    except Exception:
        return {"has_news": False, "news_score": 0}

# =========================================================
# 指标计算
# =========================================================

def enrich_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    df["MA5"] = df["Close"].rolling(5).mean()
    df["MA10"] = df["Close"].rolling(10).mean()
    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA50"] = df["Close"].rolling(50).mean()

    df["VOL5"] = df["Volume"].rolling(5).mean()
    df["VOL10"] = df["Volume"].rolling(10).mean()
    df["VOL20"] = df["Volume"].rolling(20).mean()

    tr = pd.concat([
        (df["High"] - df["Low"]),
        (df["High"] - df["Close"].shift(1)).abs(),
        (df["Low"] - df["Close"].shift(1)).abs(),
    ], axis=1).max(axis=1)

    df["ATR"] = tr.rolling(14).mean()
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
    box_mid = (box_high + box_low) / 2
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
    if volume_ratio >= 1.2:
        score += 15
    if upper_wick_ratio < 0.35:
        score += 15

    if score >= 80:
        return True, "A", score
    elif score >= 60:
        return True, "B", score
    return False, "C", score

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
    vol_ratio_5 = volume / vol5 if vol5 > 0 else 0

    ma5 = safe_float(today["MA5"])
    ma10 = safe_float(today["MA10"])
    ma20 = safe_float(today["MA20"])
    ma50 = safe_float(today["MA50"])
    atr = safe_float(today["ATR"])
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

    accum_flag, accum_grade, accum_score = detect_accumulation(df)

    # ===== 基础评分 =====
    volume_score = 5
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

    breakout_score = 3
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

    trend_score = 2
    if close > ma5 > ma10 > ma20 > ma50 > 0:
        trend_score = 20
    elif close > ma5 > ma10 > ma20 > 0:
        trend_score = 17
    elif close > ma5 > ma20 > 0:
        trend_score = 13
    elif close > ma20 > 0:
        trend_score = 8

    candle_score = 1
    if upper_wick_ratio < 0.2:
        candle_score = 10
    elif upper_wick_ratio < 0.35:
        candle_score = 7
    elif upper_wick_ratio < 0.5:
        candle_score = 4

    rsi_score = 4
    if 55 <= rsi <= 68:
        rsi_score = 10
    elif 50 <= rsi < 55 or 68 < rsi <= 75:
        rsi_score = 7

    accum_bonus = 4
    if accum_grade == "A":
        accum_bonus = 18
    elif accum_grade == "B":
        accum_bonus = 12

    # 主力资金流简化判断
    money_flow_ratio = (close - low) / (high - low + 0.001)
    smart_money = money_flow_ratio > 0.7 and vol_ratio_20 >= 1.8
    smart_money_score = 12 if smart_money else 0

    total_score = (
        volume_score
        + breakout_score
        + trend_score
        + candle_score
        + rsi_score
        + accum_bonus
        + smart_money_score
    )

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
        risk_flags.append("MA20下方")
    if vol_ratio_20 < 1.2:
        penalty += 10
        risk_flags.append("量能不足")

    final_score = max(total_score - penalty, 0)

    if final_score >= 88:
        signal_grade = "S"
    elif final_score >= 78:
        signal_grade = "A"
    elif final_score >= 68:
        signal_grade = "B"
    elif final_score >= 58:
        signal_grade = "C"
    else:
        signal_grade = "D"

    buy_trigger = max(resistance_20 * 1.002, close * 1.003)
    chase_limit = resistance_20 * 1.03
    stop_loss = max(ma10, close - 1.2 * atr) if atr > 0 else ma10
    take_profit_1 = close * 1.03
    take_profit_2 = close * 1.05

    potential_5pct = (
        final_score >= 78
        and vol_ratio_20 >= 1.8
        and distance_to_breakout <= 5
        and daily_gain < 6
        and close > ma5 > ma20 > 0
    )

    action = "先观察，不急追"
    if is_breakout and vol_ratio_20 >= STRONG_VOLUME_RATIO and daily_gain <= 6:
        action = "可列为重点观察，盘中放量站稳可考虑跟进"
    elif is_near_breakout and vol_ratio_20 >= VOLUME_RATIO_MIN:
        action = "接近突破区，明天观察是否放量过前高"
    elif accum_flag and distance_to_breakout <= ACCUM_NEAR_BREAKOUT_PCT:
        action = "主力吸筹中，未突破，可列提前预警"

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
        "accum_score": accum_score,
        "is_breakout": is_breakout,
        "is_near_breakout": is_near_breakout,
        "early_signal": early_signal,
        "signal_grade": signal_grade,
        "score": final_score,
        "potential_5pct": potential_5pct,
        "action": action,
        "buy_trigger": buy_trigger,
        "chase_limit": chase_limit,
        "stop_loss": stop_loss,
        "take_profit_1": take_profit_1,
        "take_profit_2": take_profit_2,
        "risk_flags": risk_flags,
        "smart_money": smart_money,
        "news_score": 0,
        "has_news": False,
        "pm_change": 0.0,
        "pm_volume": 0.0,
        "pm_active": False,
    }

# =========================================================
# 候选股增强检查：只查高分股，减少限流
# =========================================================

def enrich_candidates_with_news_and_premarket(results):
    if not results:
        return results

    ranked = sorted(results, key=lambda x: x["score"], reverse=True)

    for i, row in enumerate(ranked):
        if i < NEWS_CHECK_LIMIT:
            news = check_news_quick(row["ticker"])
            row["has_news"] = news["has_news"]
            row["news_score"] = news["news_score"]
            row["score"] += news["news_score"]

            time.sleep(0.4)

        if i < PREMARKET_CHECK_LIMIT:
            pm = fetch_premarket_data(row["ticker"])
            row["pm_change"] = pm["pm_change"]
            row["pm_volume"] = pm["pm_volume"]
            row["pm_active"] = pm["pm_active"]

            if row["pm_change"] >= 1.5:
                row["score"] += 12
            elif row["pm_change"] >= 0.8:
                row["score"] += 7

            time.sleep(0.4)

        if (
            row["score"] >= 85
            and row["volume_ratio"] >= 1.8
            and row["distance_to_breakout"] <= 4
            and row["has_news"]
            and (row["smart_money"] or row["pm_change"] >= 1.2)
        ):
            row["potential_5pct"] = True

    return sorted(ranked, key=lambda x: (x["potential_5pct"], x["score"]), reverse=True)

# =========================================================
# 消息生成
# =========================================================

def build_message(results):
    if not results:
        return (
            f"📭 <b>{APP_NAME}</b>\n"
            f"时间：{now_str()}\n\n"
            f"今天没有符合条件的股票。"
        )

    main_list = []
    early_list = []

    for r in results:
        if r["score"] >= 68 and r["daily_gain"] < MAX_DAILY_GAIN_FILTER:
            if r["is_breakout"] or r["is_near_breakout"] or r["potential_5pct"]:
                main_list.append(r)

    for r in results:
        if (
            r["accum_grade"] in ["A", "B"]
            and not r["is_breakout"]
            and r["distance_to_breakout"] <= EARLY_SIGNAL_DISTANCE_PCT
            and r["score"] >= 58
        ):
            early_list.append(r)

    main_list = sorted(
        main_list,
        key=lambda x: (
            x["potential_5pct"],
            x["score"],
            x["volume_ratio"]
        ),
        reverse=True
    )[:TOP_N_MAIN]

    early_list = sorted(
        early_list,
        key=lambda x: (
            x["accum_grade"] == "A",
            x["score"]
        ),
        reverse=True
    )[:TOP_N_EARLY]

    lines = []
    lines.append(f"🚀 <b>{APP_NAME}</b>")
    lines.append(f"🕒 时间：{now_str()}")
    lines.append("")

    if main_list:
        lines.append("🔥 <b>主名单：次日重点观察</b>")
        for i, r in enumerate(main_list, 1):
            tag = "🎯5%潜力" if r["potential_5pct"] else "观察"
            breakout_text = "已突破" if r["is_breakout"] else "近突破"
            risk_text = "；".join(r["risk_flags"]) if r["risk_flags"] else "无明显异常"

            lines.append(
                f"{i}. <b>{r['ticker']}</b> | 等级 {r['signal_grade']} | 分数 {r['score']:.1f} | {tag}\n"
                f"   收盘：{format_price(r['close'])} ({format_pct(r['daily_gain'])})\n"
                f"   量比：{r['volume_ratio']:.2f}x | {breakout_text} | 距离突破：{format_pct(r['distance_to_breakout'])}\n"
                f"   新闻：{'有' if r['has_news'] else '无'} | 盘前：{format_pct(r['pm_change'])}\n"
                f"   趋势：{r['trend_label']} | 吸筹：{r['accum_grade']} | 主力资金：{'是' if r['smart_money'] else '否'}\n"
                f"   买点：>{format_price(r['buy_trigger'])}\n"
                f"   不追价：>{format_price(r['chase_limit'])}\n"
                f"   止损：{format_price(r['stop_loss'])}\n"
                f"   止盈：{format_price(r['take_profit_1'])} / {format_price(r['take_profit_2'])}\n"
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
                f"   量比：{r['volume_ratio']:.2f}x | 趋势：{r['trend_label']}\n"
                f"   策略：先列观察名单，等放量突破再出手"
            )
            lines.append("")

    if not main_list and not early_list:
        lines.append("📭 今天没有强信号，继续观察。")

    return "\n".join(lines), main_list, early_list

# =========================================================
# 扫描主逻辑：V7.2 稳定版
# =========================================================

def run_scan(send_telegram=True):
    with SCAN_LOCK:
        start = time.time()
        results = []
        scanned_count = 0

        print(f"[{now_str()}] 开始扫描 {len(TICKERS)} 支股票...")

        for batch_start in range(0, len(TICKERS), BATCH_SIZE):
            batch = TICKERS[batch_start: batch_start + BATCH_SIZE]
            print(f"扫描批次: {batch_start + 1} - {batch_start + len(batch)}")

            for ticker in batch:
                try:
                    row = analyze_ticker(ticker)
                    scanned_count += 1
                    if row:
                        results.append(row)
                except Exception as e:
                    print(f"[{ticker}] analyze error: {e}")

                time.sleep(PER_TICKER_SLEEP)

            if batch_start + BATCH_SIZE < len(TICKERS):
                print(f"批次完成，休息 {BATCH_SLEEP} 秒")
                time.sleep(BATCH_SLEEP)

        # 先按基础分排序，再对候选股增强检查
        results = sorted(results, key=lambda x: x["score"], reverse=True)
        results = enrich_candidates_with_news_and_premarket(results)

        message, main_list, early_list = build_message(results)

        duration = round(time.time() - start, 2)

        SCAN_STATE["last_run"] = now_str()
        SCAN_STATE["last_scan_summary"] = f"done_in_{duration}s"
        SCAN_STATE["main_count"] = len(main_list)
        SCAN_STATE["early_count"] = len(early_list)
        SCAN_STATE["top_tickers"] = [x["ticker"] for x in main_list[:5]]

        if send_telegram:
            send_telegram_message(message)

        return {
            "ok": True,
            "message": message,
            "summary": {
                "duration_sec": duration,
                "scanned": scanned_count,
                "results": len(results),
                "main_count": len(main_list),
                "early_count": len(early_list),
            },
            "main_list": main_list,
            "early_list": early_list,
        }

# =========================================================
# Flask 路由
# =========================================================

@app.route("/", methods=["GET"])
def root():
    return jsonify({
        "service": APP_NAME,
        "status": "running",
        "timezone": TIMEZONE,
        "scheduler_enabled": ENABLE_INTERNAL_SCHEDULER,
        "tickers": len(TICKERS),
        "last_run": SCAN_STATE["last_run"],
        "last_scan": SCAN_STATE["last_scan_summary"],
        "main_count": SCAN_STATE["main_count"],
        "early_count": SCAN_STATE["early_count"],
        "top_tickers": SCAN_STATE["top_tickers"],
    })

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "ok": True,
        "service": APP_NAME,
        "time": now_str(),
        "tickers": len(TICKERS)
    })

@app.route("/run-scan", methods=["GET"])
def route_run_scan():
    token = request.args.get("token", "").strip()

    if not INTERNAL_SCAN_TOKEN:
        return jsonify({"ok": False, "error": "INTERNAL_SCAN_TOKEN not set"}), 500

    if token != INTERNAL_SCAN_TOKEN:
        return jsonify({"ok": False, "error": "invalid token"}), 403

    try:
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
        return jsonify({
            "ok": False,
            "error": str(e),
            "trace": err[:3000]
        }), 500

@app.route("/run-scan-local", methods=["GET"])
def route_run_scan_local():
    try:
        data = run_scan(send_telegram=False)
        return f"<pre>{data['message']}</pre>"
    except Exception as e:
        return f"<pre>ERROR\n{str(e)}\n\n{traceback.format_exc()}</pre>", 500

# =========================================================
# scheduler 占位
# =========================================================

def setup_scheduler():
    if ENABLE_INTERNAL_SCHEDULER:
        print("内部定时扫描已开启，但当前版本建议仍以 Railway Cron / 外部调用为主。")
    else:
        print("内部定时扫描关闭。")

# =========================================================
# main
# =========================================================

if __name__ == "__main__":
    setup_scheduler()
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
