import os
import time
import math
import json
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import pytz
import requests
import schedule
import yfinance as yf
from flask import Flask, jsonify, request

app = Flask(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
PORT = int(os.getenv("PORT", "5000"))

MAX_WORKERS = int(os.getenv("MAX_WORKERS", "20"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "50"))
BATCH_SLEEP = float(os.getenv("BATCH_SLEEP", "1.2"))

TELEGRAM_MAX_LEN = 3500

ACCOUNT_SIZE = float(os.getenv("ACCOUNT_SIZE", "5000"))
MAX_RISK_PER_TRADE = float(os.getenv("MAX_RISK_PER_TRADE", "0.02"))
MAX_POSITION_RATIO = float(os.getenv("MAX_POSITION_RATIO", "0.30"))

MAX_LOSS_AMOUNT = ACCOUNT_SIZE * MAX_RISK_PER_TRADE

TZ_KL = pytz.timezone("Asia/Kuala_Lumpur")
TZ_ET = pytz.timezone("US/Eastern")

STATE_FILE = "scanner_state.json"

LAST_PREMARKET_SIGNATURE = ""
LAST_CLOSE_SIGNATURE = ""
LAST_BOTTOM_SIGNATURE = ""
LAST_MARKET_REGIME = ""
INTRADAY_BREAKOUT_SENT = set()

BAD_SYMBOLS = {
    "X","ZI","ATVI","FRC","SIVB","SVB","BBBY","TTCF","TWTR","FB",
    "BRK.B","BRK/B","BF.B","BF/B"
}

SECTOR_POOLS = {
    "AI_软件": [
        "PLTR","SOUN","BBAI","AI","PATH","APP","CRM","NOW","MDB",
        "SNOW","DDOG","NET","TEAM","ASAN","BOX","DOCN","GTLB",
        "DUOL","SPOT","ADBE","ORCL","WDAY","INTU","RBRK","TWLO"
    ],
    "半导体": [
        "NVDA","AMD","AVGO","QCOM","MU","MRVL","ON","ARM","AMAT",
        "LRCX","KLAC","ADI","NXPI","TXN","TSM","INTC","WOLF","SMTC"
    ],
    "量子": [
        "IONQ","RGTI","QBTS","QUBT","IBM","GOOG","MSFT"
    ],
    "太空": [
        "LUNR","RKLB","ASTS","RDW","PL","SPIR","SATL","KTOS","JOBY","ACHR"
    ],
    "金融科技": [
        "HOOD","SOFI","AFRM","COIN","PYPL","ALLY","NU","UPST",
        "IBKR","ICE","CME","NDAQ"
    ],
    "网络安全": [
        "CRWD","PANW","ZS","OKTA","FTNT","RBRK","TENB","NET"
    ],
    "新能源": [
        "TSLA","RIVN","LCID","NIO","LI","FSLR","ENPH","RUN",
        "CHPT","BLNK","NEE","SMR","OKLO"
    ],
    "生物科技": [
        "MRNA","VRTX","REGN","CRSP","NTLA","RXRX","BNTX","VKTX",
        "HIMS","TEM"
    ],
    "加密概念": [
        "MSTR","COIN","MARA","RIOT","CLSK","HUT","BTDR"
    ],
    "消费成长": [
        "AMZN","MELI","CELH","CAVA","CMG","LULU","ABNB","DASH",
        "NFLX","ELF","BIRK","CVNA"
    ],
    "工业": [
        "GE","RTX","LMT","NOC","CAT","DE","URI","PH","ETN","PWR","VRT"
    ],
    "大盘科技": [
        "AAPL","MSFT","META","AMZN","GOOGL","NFLX","TSLA","NVDA",
        "AMD","AVGO","ORCL","ADBE","CRM"
    ]
}

ALL_SYMBOLS = sorted(set([
    s for arr in SECTOR_POOLS.values()
    for s in arr
    if s not in BAD_SYMBOLS
]))

def now_kl():
    return datetime.now(TZ_KL)

def now_et():
    return datetime.now(TZ_ET)

def now_kl_str():
    return now_kl().strftime("%Y-%m-%d %H:%M:%S")

def is_weekend_et():
    return now_et().weekday() >= 5

def chunk_list(items, size):
    for i in range(0, len(items), size):
        yield items[i:i+size]

def load_state():
    global LAST_PREMARKET_SIGNATURE
    global LAST_CLOSE_SIGNATURE
    global LAST_BOTTOM_SIGNATURE
    global LAST_MARKET_REGIME
    global INTRADAY_BREAKOUT_SENT

    if not os.path.exists(STATE_FILE):
        return

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        LAST_PREMARKET_SIGNATURE = data.get("last_premarket_signature", "")
        LAST_CLOSE_SIGNATURE = data.get("last_close_signature", "")
        LAST_BOTTOM_SIGNATURE = data.get("last_bottom_signature", "")
        LAST_MARKET_REGIME = data.get("last_market_regime", "")
        INTRADAY_BREAKOUT_SENT = set(data.get("intraday_breakout_sent", []))

    except:
        pass

def save_state():
    data = {
        "last_premarket_signature": LAST_PREMARKET_SIGNATURE,
        "last_close_signature": LAST_CLOSE_SIGNATURE,
        "last_bottom_signature": LAST_BOTTOM_SIGNATURE,
        "last_market_regime": LAST_MARKET_REGIME,
        "intraday_breakout_sent": list(INTRADAY_BREAKOUT_SENT)
    }

    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except:
        pass

def send_telegram(msg):
    if not BOT_TOKEN or not CHAT_ID:
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": CHAT_ID,
        "text": msg[:3500]
    }

    try:
        r = requests.post(url, data=payload, timeout=15)
        return r.status_code == 200
    except:
        return False

def safe_download(symbol, period="6mo", interval="1d"):
    try:
        df = yf.download(
            symbol,
            period=period,
            interval=interval,
            auto_adjust=True,
            progress=False,
            threads=False
        )

        if df is None or df.empty:
            return None

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]

        need = {"Open", "High", "Low", "Close", "Volume"}
        if not need.issubset(set(df.columns)):
            return None

        df = df.dropna()

        if len(df) < 30:
            return None

        return df

    except:
        return None


def calc_rsi(series, period=14):
    delta = series.diff()

    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()

    rs = gain / loss.replace(0, math.nan)
    rsi = 100 - (100 / (1 + rs))

    return rsi.fillna(50)


def calc_atr(df, period=14):
    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    return tr.rolling(period).mean()


def calc_obv(close, vol):
    obv = [0]

    for i in range(1, len(close)):
        if close.iloc[i] > close.iloc[i-1]:
            obv.append(obv[-1] + vol.iloc[i])
        elif close.iloc[i] < close.iloc[i-1]:
            obv.append(obv[-1] - vol.iloc[i])
        else:
            obv.append(obv[-1])

    return pd.Series(obv, index=close.index)


def find_sector(symbol):
    for sector, arr in SECTOR_POOLS.items():
        if symbol in arr:
            return sector
    return "其他"


def build_risk(entry_price, sl):
    if entry_price <= sl:
        return None

    risk_per_share = entry_price - sl

    shares = int(MAX_LOSS_AMOUNT / risk_per_share)

    max_value = ACCOUNT_SIZE * MAX_POSITION_RATIO
    max_share_cap = int(max_value / entry_price)

    shares = min(shares, max_share_cap)

    if shares < 1:
        shares = 1

    return {
        "shares": shares,
        "position_value": round(shares * entry_price, 2),
        "max_loss": round(shares * risk_per_share, 2)
    }


def score_stock(symbol, df):
    if df is None or len(df) < 70:
        return None

    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    vol = df["Volume"]

    last = float(close.iloc[-1])
    prev = float(close.iloc[-2])

    ma20 = float(close.rolling(20).mean().iloc[-1])
    ma50 = float(close.rolling(50).mean().iloc[-1])

    high10 = float(high.iloc[-10:-1].max())

    avg_vol20 = float(vol.rolling(20).mean().iloc[-1])

    vol_ratio = float(vol.iloc[-1] / avg_vol20) if avg_vol20 > 0 else 0

    rsi = float(calc_rsi(close).iloc[-1])

    change1 = (last - prev) / prev * 100
    change5 = (last - float(close.iloc[-6])) / float(close.iloc[-6]) * 100

    score = 0
    reasons = []

    if last > ma20:
        score += 10
        reasons.append("站上MA20")

    if ma20 > ma50:
        score += 10
        reasons.append("MA20>MA50")

    if last >= high10:
        score += 18
        reasons.append("突破10日高")

    if vol_ratio >= 2:
        score += 15
        reasons.append("量比强")

    if 55 <= rsi <= 72:
        score += 10
        reasons.append("RSI健康")

    if 3 <= change5 <= 12:
        score += 10
        reasons.append("5日强势")

    if last < 3:
        score -= 15

    buy_low = round(last * 0.995, 2)
    buy_high = round(last * 1.01, 2)

    atr = calc_atr(df).iloc[-1]
    if pd.isna(atr):
        atr = last * 0.03

    sl = round(last - atr * 1.2, 2)
    tp1 = round(last + atr * 2, 2)
    tp2 = round(last + atr * 3.5, 2)

    risk = build_risk((buy_low + buy_high)/2, sl)

    return {
        "symbol": symbol,
        "sector": find_sector(symbol),
        "score": round(score, 1),
        "price": round(last, 2),
        "buy_low": buy_low,
        "buy_high": buy_high,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "rsi": round(rsi, 1),
        "vol_ratio": round(vol_ratio, 2),
        "change1": round(change1, 2),
        "change5": round(change5, 2),
        "reasons": reasons[:5],
        "risk": risk
    }


def score_bottom(symbol, df):
    if df is None or len(df) < 120:
        return None

    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    vol = df["Volume"]

    last = float(close.iloc[-1])

    if last < 3:
        return None

    ma20 = float(close.rolling(20).mean().iloc[-1])
    ma50 = float(close.rolling(50).mean().iloc[-1])

    low52 = float(low.min())
    high20 = float(high.iloc[-20:].max())
    low20 = float(low.iloc[-20:].min())

    avg_vol20 = float(vol.rolling(20).mean().iloc[-1])
    vol_ratio = float(vol.iloc[-1] / avg_vol20) if avg_vol20 > 0 else 0

    rsi = float(calc_rsi(close).iloc[-1])

    obv = calc_obv(close, vol)
    obv20 = float(obv.iloc[-1] - obv.iloc[-21])

    dist_low = (last - low52) / low52 * 100

    range20 = (high20 - low20) / low20 * 100

    score = 0
    reasons = []

    if 5 <= dist_low <= 45:
        score += 20
        reasons.append("接近低位")

    if range20 <= 18:
        score += 18
        reasons.append("横盘收窄")

    if last >= ma20:
        score += 12
        reasons.append("站上MA20")

    if ma20 >= ma50 * 0.95:
        score += 8
        reasons.append("均线改善")

    if obv20 > 0:
        score += 15
        reasons.append("OBV转强")

    if 45 <= rsi <= 65:
        score += 10
        reasons.append("RSI转强")

    if vol_ratio >= 1.3:
        score += 10
        reasons.append("量能增加")

    if score < 52:
        return None

    buy_low = round(last * 0.98, 2)
    buy_high = round(last * 1.03, 2)

    atr = calc_atr(df).iloc[-1]
    if pd.isna(atr):
        atr = last * 0.04

    sl = round(last - atr * 1.3, 2)
    tp1 = round(last + atr * 2.2, 2)
    tp2 = round(last + atr * 4.0, 2)

    risk = build_risk((buy_low + buy_high)/2, sl)

    return {
        "symbol": symbol,
        "sector": find_sector(symbol),
        "score": round(score, 1),
        "price": round(last, 2),
        "buy_low": buy_low,
        "buy_high": buy_high,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "rsi": round(rsi, 1),
        "vol_ratio": round(vol_ratio, 2),
        "reasons": reasons[:5],
        "risk": risk
    }

def scan_normal():
    results = []

    for batch in chunk_list(ALL_SYMBOLS, BATCH_SIZE):
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = {
                ex.submit(
                    score_stock,
                    s,
                    safe_download(s, "6mo", "1d")
                ): s for s in batch
            }

            for f in as_completed(futures):
                try:
                    r = f.result()
                    if r and r["score"] >= 35:
                        results.append(r)
                except:
                    pass

        time.sleep(BATCH_SLEEP)

    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def scan_bottom():
    results = []

    for batch in chunk_list(ALL_SYMBOLS, BATCH_SIZE):
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = {
                ex.submit(
                    score_bottom,
                    s,
                    safe_download(s, "1y", "1d")
                ): s for s in batch
            }

            for f in as_completed(futures):
                try:
                    r = f.result()
                    if r:
                        results.append(r)
                except:
                    pass

        time.sleep(BATCH_SLEEP)

    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def market_regime():
    qqq = safe_download("QQQ", "3mo", "1d")
    spy = safe_download("SPY", "3mo", "1d")

    if qqq is None or spy is None:
        return "NEUTRAL"

    q = qqq["Close"]
    s = spy["Close"]

    q_last = float(q.iloc[-1])
    s_last = float(s.iloc[-1])

    q_ma20 = float(q.rolling(20).mean().iloc[-1])
    s_ma20 = float(s.rolling(20).mean().iloc[-1])

    bull = 0

    if q_last > q_ma20:
        bull += 1

    if s_last > s_ma20:
        bull += 1

    if bull >= 2:
        return "BULLISH"

    return "NEUTRAL"


def build_text(title, arr):
    lines = [
        title,
        now_kl_str(),
        ""
    ]

    for i, r in enumerate(arr, start=1):
        lines.append(
            f"{i}. {r['symbol']} {r['score']}分 "
            f"| 价 {r['price']} "
            f"| 买区 {r['buy_low']}-{r['buy_high']} "
            f"| SL {r['sl']} "
            f"| TP1 {r['tp1']}"
        )

    return "\n".join(lines)


def run_premarket():
    global LAST_PREMARKET_SIGNATURE

    if is_weekend_et():
        return {"status": "skip"}

    results = scan_normal()

    if not results:
        return {"status": "empty"}

    top = results[:5]

    sig = "|".join([x["symbol"] for x in top])

    if sig == LAST_PREMARKET_SIGNATURE:
        return {"status": "same"}

    LAST_PREMARKET_SIGNATURE = sig
    save_state()

    send_telegram(build_text("🚀 V13 盘前 Top5", top))

    return {"status": "ok", "top": top}


def run_close():
    global LAST_CLOSE_SIGNATURE

    if is_weekend_et():
        return {"status": "skip"}

    results = scan_normal()

    if not results:
        return {"status": "empty"}

    top = results[:10]

    sig = "|".join([x["symbol"] for x in top])

    if sig == LAST_CLOSE_SIGNATURE:
        return {"status": "same"}

    LAST_CLOSE_SIGNATURE = sig
    save_state()

    send_telegram(build_text("🌙 V13 收盘预备股", top))

    return {"status": "ok", "top": top}


def run_bottom():
    global LAST_BOTTOM_SIGNATURE

    if is_weekend_et():
        return {"status": "skip"}

    results = scan_bottom()

    if not results:
        return {"status": "empty"}

    top = results[:10]

    sig = "|".join([x["symbol"] for x in top])

    if sig == LAST_BOTTOM_SIGNATURE:
        return {"status": "same"}

    LAST_BOTTOM_SIGNATURE = sig
    save_state()

    send_telegram(build_text("📦 V13 底部吸筹 Top10", top))

    return {"status": "ok", "top": top}


def scheduler_loop():
    weekdays = [
        schedule.every().monday,
        schedule.every().tuesday,
        schedule.every().wednesday,
        schedule.every().thursday,
        schedule.every().friday
    ]

    for d in weekdays:
        d.at("21:00").do(run_premarket)

    schedule.every().day.at("04:20").do(run_close)
    schedule.every().day.at("04:35").do(run_bottom)

    while True:
        try:
            schedule.run_pending()
        except:
            pass

        time.sleep(15)


@app.route("/")
def home():
    return "V13 Scanner Running"


@app.route("/health")
def health():
    return jsonify({
        "status": "healthy",
        "time_kl": now_kl_str(),
        "symbols": len(ALL_SYMBOLS),
        "version": "V13"
    })


@app.route("/run-premarket")
def route_premarket():
    return jsonify(run_premarket())


@app.route("/run-close")
def route_close():
    return jsonify(run_close())


@app.route("/run-bottom")
def route_bottom():
    return jsonify(run_bottom())


@app.route("/sectors")
def route_sectors():
    return jsonify({
        "count": len(SECTOR_POOLS),
        "symbols": len(ALL_SYMBOLS)
    })


@app.route("/api/test-telegram")
def route_test():
    ok = send_telegram("✅ V13 Telegram Test Success")

    return jsonify({
        "telegram_sent": ok
    })


if __name__ == "__main__":
    load_state()

    t = threading.Thread(
        target=scheduler_loop,
        daemon=True
    )
    t.start()

    app.run(
        host="0.0.0.0",
        port=PORT
    )
