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

# ============================================================
# 美股爆发扫描器 V17 提前爆发股扫描版
# 基于 V16：自选股 + 持仓追踪 + 买卖监控 + 防追高
# 新增 V17：提前爆发股扫描 / 横盘收窄 / 接近突破位 / 主力吸筹 / 量能温和放大
# Railway / Flask / Telegram 可直接部署
# ============================================================

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
PORT = int(os.getenv("PORT", "5000"))

MAX_WORKERS = int(os.getenv("MAX_WORKERS", "20"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "50"))
BATCH_SLEEP = float(os.getenv("BATCH_SLEEP", "1.2"))
TELEGRAM_MAX_LEN = int(os.getenv("TELEGRAM_MAX_LEN", "3500"))

ACCOUNT_SIZE = float(os.getenv("ACCOUNT_SIZE", "5000"))
MAX_RISK_PER_TRADE = float(os.getenv("MAX_RISK_PER_TRADE", "0.02"))
MAX_POSITION_RATIO = float(os.getenv("MAX_POSITION_RATIO", "0.30"))
MAX_LOSS_AMOUNT = ACCOUNT_SIZE * MAX_RISK_PER_TRADE

DEFAULT_MY_STOCKS = os.getenv("MY_STOCKS", "NDAQ,PANW,CRWD,IONQ,LUNR").strip()

TZ_KL = pytz.timezone("Asia/Kuala_Lumpur")
TZ_ET = pytz.timezone("US/Eastern")

STATE_FILE = "scanner_state.json"
WATCHLIST_FILE = "watchlist.json"
POSITIONS_FILE = "positions.json"

LAST_PREMARKET_SIGNATURE = ""
LAST_CLOSE_SIGNATURE = ""
LAST_BOTTOM_SIGNATURE = ""
LAST_WATCHLIST_SIGNATURE = ""
LAST_PREBREAKOUT_SIGNATURE = ""
LAST_MARKET_REGIME = ""
INTRADAY_BREAKOUT_SENT = set()
MY_STOCKS = []

BAD_SYMBOLS = {
    "X", "ZI", "ATVI", "FRC", "SIVB", "SVB", "BBBY", "TTCF", "TWTR", "FB",
    "BRK.B", "BRK/B", "BF.B", "BF/B"
}

SECTOR_POOLS = {
    "AI_软件": [
        "PLTR", "SOUN", "BBAI", "AI", "PATH", "APP", "CRM", "NOW", "MDB",
        "SNOW", "DDOG", "NET", "TEAM", "ASAN", "BOX", "DOCN", "GTLB",
        "DUOL", "SPOT", "ADBE", "ORCL", "WDAY", "INTU", "RBRK", "TWLO"
    ],
    "AI_数据中心": [
        "CRWV", "VRT", "SMCI", "DELL", "HPE", "ANET", "PSTG", "APLD", "IREN", "DXYZ"
    ],
    "半导体": [
        "NVDA", "AMD", "AVGO", "QCOM", "MU", "MRVL", "ON", "ARM", "AMAT",
        "LRCX", "KLAC", "ADI", "NXPI", "TXN", "TSM", "INTC", "WOLF", "SMTC", "SNDK"
    ],
    "量子": ["IONQ", "RGTI", "QBTS", "QUBT", "IBM", "GOOG", "MSFT"],
    "太空": ["LUNR", "RKLB", "ASTS", "RDW", "PL", "SPIR", "SATL", "KTOS", "JOBY", "ACHR"],
    "金融科技": ["HOOD", "SOFI", "AFRM", "COIN", "PYPL", "ALLY", "NU", "UPST", "IBKR", "ICE", "CME", "NDAQ"],
    "网络安全": ["CRWD", "PANW", "ZS", "OKTA", "FTNT", "RBRK", "TENB", "NET"],
    "新能源": ["TSLA", "RIVN", "LCID", "NIO", "LI", "FSLR", "ENPH", "RUN", "CHPT", "BLNK", "NEE", "SMR", "OKLO"],
    "生物科技": ["MRNA", "VRTX", "REGN", "CRSP", "NTLA", "RXRX", "BNTX", "VKTX", "HIMS", "TEM"],
    "加密概念": ["MSTR", "COIN", "MARA", "RIOT", "CLSK", "HUT", "BTDR", "IREN"],
    "消费成长": ["AMZN", "MELI", "CELH", "CAVA", "CMG", "LULU", "ABNB", "DASH", "NFLX", "ELF", "BIRK", "CVNA"],
    "工业": ["GE", "RTX", "LMT", "NOC", "CAT", "DE", "URI", "PH", "ETN", "PWR", "VRT"],
    "大盘科技": ["AAPL", "MSFT", "META", "AMZN", "GOOGL", "NFLX", "TSLA", "NVDA", "AMD", "AVGO", "ORCL", "ADBE", "CRM"]
}

ALL_SYMBOLS = sorted(set([s for arr in SECTOR_POOLS.values() for s in arr if s not in BAD_SYMBOLS]))

# ============================================================
# 时间 / 状态
# ============================================================

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
        yield items[i:i + size]


def clean_symbol(symbol):
    return str(symbol).strip().upper().replace(" ", "")


def parse_symbols(raw):
    if not raw:
        return []
    return sorted(set([clean_symbol(x) for x in raw.split(",") if clean_symbol(x)]))


def load_watchlist():
    global MY_STOCKS
    if os.path.exists(WATCHLIST_FILE):
        try:
            with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            MY_STOCKS = sorted(set([clean_symbol(x) for x in data.get("my_stocks", []) if clean_symbol(x)]))
            if MY_STOCKS:
                return
        except Exception:
            pass
    MY_STOCKS = parse_symbols(DEFAULT_MY_STOCKS)
    save_watchlist()


def save_watchlist():
    try:
        with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
            json.dump({"my_stocks": MY_STOCKS}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_positions():
    if not os.path.exists(POSITIONS_FILE):
        return {}
    try:
        with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_positions(positions):
    try:
        with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(positions, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def add_or_update_position(symbol, shares, price, note=""):
    symbol = clean_symbol(symbol)
    positions = load_positions()
    positions[symbol] = {
        "symbol": symbol,
        "shares": int(shares),
        "avg_price": float(price),
        "note": note,
        "updated_at": now_kl_str()
    }
    save_positions(positions)
    load_watchlist()
    if symbol not in MY_STOCKS and symbol not in BAD_SYMBOLS:
        MY_STOCKS.append(symbol)
        MY_STOCKS.sort()
        save_watchlist()
    return positions[symbol]


def remove_position(symbol):
    symbol = clean_symbol(symbol)
    positions = load_positions()
    existed = symbol in positions
    if existed:
        del positions[symbol]
        save_positions(positions)
    return existed


def load_state():
    global LAST_PREMARKET_SIGNATURE, LAST_CLOSE_SIGNATURE, LAST_BOTTOM_SIGNATURE
    global LAST_WATCHLIST_SIGNATURE, LAST_PREBREAKOUT_SIGNATURE, LAST_MARKET_REGIME, INTRADAY_BREAKOUT_SENT
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        LAST_PREMARKET_SIGNATURE = data.get("last_premarket_signature", "")
        LAST_CLOSE_SIGNATURE = data.get("last_close_signature", "")
        LAST_BOTTOM_SIGNATURE = data.get("last_bottom_signature", "")
        LAST_WATCHLIST_SIGNATURE = data.get("last_watchlist_signature", "")
        LAST_PREBREAKOUT_SIGNATURE = data.get("last_prebreakout_signature", "")
        LAST_MARKET_REGIME = data.get("last_market_regime", "")
        INTRADAY_BREAKOUT_SENT = set(data.get("intraday_breakout_sent", []))
    except Exception:
        pass


def save_state():
    data = {
        "last_premarket_signature": LAST_PREMARKET_SIGNATURE,
        "last_close_signature": LAST_CLOSE_SIGNATURE,
        "last_bottom_signature": LAST_BOTTOM_SIGNATURE,
        "last_watchlist_signature": LAST_WATCHLIST_SIGNATURE,
        "last_prebreakout_signature": LAST_PREBREAKOUT_SIGNATURE,
        "last_market_regime": LAST_MARKET_REGIME,
        "intraday_breakout_sent": list(INTRADAY_BREAKOUT_SENT)
    }
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

# ============================================================
# Telegram
# ============================================================

def send_telegram(msg):
    if not BOT_TOKEN or not CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    chunks = [msg[i:i + TELEGRAM_MAX_LEN] for i in range(0, len(msg), TELEGRAM_MAX_LEN)]
    ok_all = True
    for part in chunks:
        try:
            r = requests.post(url, data={"chat_id": CHAT_ID, "text": part}, timeout=15)
            ok_all = ok_all and (r.status_code == 200)
            time.sleep(0.2)
        except Exception:
            ok_all = False
    return ok_all

# ============================================================
# 数据 / 技术指标
# ============================================================

def safe_download(symbol, period="6mo", interval="1d"):
    try:
        df = yf.download(symbol, period=period, interval=interval, auto_adjust=True, progress=False, threads=False)
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
    except Exception:
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
        if close.iloc[i] > close.iloc[i - 1]:
            obv.append(obv[-1] + vol.iloc[i])
        elif close.iloc[i] < close.iloc[i - 1]:
            obv.append(obv[-1] - vol.iloc[i])
        else:
            obv.append(obv[-1])
    return pd.Series(obv, index=close.index)


def pct(a, b):
    if b == 0:
        return 0
    return (a - b) / b * 100


def find_sector(symbol):
    for sector, arr in SECTOR_POOLS.items():
        if symbol in arr:
            return sector
    return "自选/其他"


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
    return {"shares": shares, "position_value": round(shares * entry_price, 2), "max_loss": round(shares * risk_per_share, 2)}


def count_green_days(close, lookback=6):
    count = 0
    for i in range(len(close) - 1, max(len(close) - lookback - 1, 0), -1):
        if close.iloc[i] > close.iloc[i - 1]:
            count += 1
        else:
            break
    return count


def capital_grade(score):
    if score >= 80:
        return "A 强主力"
    if score >= 65:
        return "B 有资金"
    if score >= 50:
        return "C 观察"
    return "D 弱"


def calc_position_info(symbol, current_price, suggested_sl=None, tp1=None, tp2=None):
    positions = load_positions()
    symbol = clean_symbol(symbol)
    if symbol not in positions:
        return None
    pos = positions[symbol]
    shares = int(pos.get("shares", 0))
    avg_price = float(pos.get("avg_price", pos.get("price", 0)))
    if shares <= 0 or avg_price <= 0:
        return None
    market_value = current_price * shares
    cost = avg_price * shares
    pnl_amount = market_value - cost
    pnl_pct = pct(current_price, avg_price)
    alerts = []
    position_action = "持有观察"
    if suggested_sl and current_price <= suggested_sl:
        alerts.append("🛑 跌破防守SL")
        position_action = "🔴 必须止损/减仓"
    if tp2 and current_price >= tp2:
        alerts.append("🚀 到TP2")
        position_action = "🔴 建议清仓/锁定利润"
    elif tp1 and current_price >= tp1:
        alerts.append("🎯 到TP1")
        position_action = "🟠 建议卖出30%-50%"
    if pnl_pct <= -8:
        alerts.append("⚠️ 亏损超过8%")
        position_action = "🔴 检查是否止损"
    elif pnl_pct >= 15:
        alerts.append("💰 盈利超过15%")
        if position_action == "持有观察":
            position_action = "🟠 可分批止盈"
    return {
        "shares": shares, "avg_price": round(avg_price, 2), "market_value": round(market_value, 2),
        "cost": round(cost, 2), "pnl_amount": round(pnl_amount, 2), "pnl_pct": round(pnl_pct, 2),
        "alerts": alerts, "position_action": position_action, "note": pos.get("note", "")
    }

# ============================================================
# 大盘模式
# ============================================================

def market_regime():
    qqq = safe_download("QQQ", "3mo", "1d")
    spy = safe_download("SPY", "3mo", "1d")
    if qqq is None or spy is None:
        return "NEUTRAL"
    q = qqq["Close"]
    s = spy["Close"]
    q_last = float(q.iloc[-1]); s_last = float(s.iloc[-1])
    q_ma20 = float(q.rolling(20).mean().iloc[-1]); s_ma20 = float(s.rolling(20).mean().iloc[-1])
    q_rsi = float(calc_rsi(q).iloc[-1]); s_rsi = float(calc_rsi(s).iloc[-1])
    q_change5 = pct(q_last, float(q.iloc[-6])); s_change5 = pct(s_last, float(s.iloc[-6]))
    bull = 0; bear = 0
    if q_last > q_ma20: bull += 1
    else: bear += 1
    if s_last > s_ma20: bull += 1
    else: bear += 1
    if q_change5 > 1: bull += 1
    if s_change5 > 1: bull += 1
    if q_rsi >= 75 or s_rsi >= 75: bear += 1
    if q_change5 < -2 or s_change5 < -2: bear += 1
    if bear >= 3: return "DEFENSIVE"
    if bull >= 3 and q_rsi < 72: return "BULLISH"
    return "NEUTRAL"

# ============================================================
# 防追高 / 普通扫描
# ============================================================

def anti_chase_status(last, ma10, ma20, rsi, change5, green_days, market_mode):
    extension_ma10 = pct(last, ma10) if ma10 > 0 else 0
    extension_ma20 = pct(last, ma20) if ma20 > 0 else 0
    danger = []
    if rsi >= 78: danger.append("RSI过热")
    if green_days >= 4: danger.append("连续上涨过多")
    if extension_ma10 >= 8: danger.append("离MA10过远")
    if extension_ma20 >= 15: danger.append("离MA20过远")
    if change5 >= 15: danger.append("5日涨幅过大")
    if market_mode in ["DEFENSIVE", "NEUTRAL"] and len(danger) >= 2:
        status = "禁追"
    elif len(danger) >= 2:
        status = "等回踩"
    elif len(danger) == 1:
        status = "观察"
    else:
        status = "可买"
    return status, danger, round(extension_ma10, 2), round(extension_ma20, 2)


def score_stock(symbol, df, market_mode=None):
    if df is None or len(df) < 70:
        return None
    if market_mode is None:
        market_mode = "NEUTRAL"
    close = df["Close"]; high = df["High"]; vol = df["Volume"]
    last = float(close.iloc[-1]); prev = float(close.iloc[-2])
    ma10 = float(close.rolling(10).mean().iloc[-1])
    ma20 = float(close.rolling(20).mean().iloc[-1])
    ma50 = float(close.rolling(50).mean().iloc[-1])
    high10 = float(high.iloc[-10:-1].max())
    avg_vol20 = float(vol.rolling(20).mean().iloc[-1])
    vol_ratio = float(vol.iloc[-1] / avg_vol20) if avg_vol20 > 0 else 0
    rsi = float(calc_rsi(close).iloc[-1])
    change1 = pct(last, prev)
    change5 = pct(last, float(close.iloc[-6]))
    green_days = count_green_days(close)
    chase_status, chase_warnings, extension_ma10, extension_ma20 = anti_chase_status(last, ma10, ma20, rsi, change5, green_days, market_mode)
    obv = calc_obv(close, vol)
    obv20 = float(obv.iloc[-1] - obv.iloc[-21])
    obv50 = float(obv.iloc[-1] - obv.iloc[-51])
    recent_close = close.iloc[-20:]
    recent_vol = vol.iloc[-20:]
    prev_close = close.shift(1).iloc[-20:]
    up_days = recent_close > prev_close
    down_days = recent_close < prev_close
    up_vol = float(recent_vol[up_days].sum())
    down_vol = float(recent_vol[down_days].sum())
    absorb_ratio = up_vol / down_vol if down_vol > 0 else 1
    money_score = 0
    if vol_ratio >= 2: money_score += 25
    elif vol_ratio >= 1.5: money_score += 18
    elif vol_ratio >= 1.2: money_score += 10
    if obv20 > 0: money_score += 20
    if obv50 > 0: money_score += 15
    if absorb_ratio >= 1.5: money_score += 25
    elif absorb_ratio >= 1.2: money_score += 15
    if change1 > 0 and vol_ratio >= 1.3: money_score += 15
    money_score = max(0, min(100, round(money_score, 1)))
    score = 0; reasons = []
    if last > ma20: score += 10; reasons.append("站上MA20")
    if ma20 > ma50: score += 10; reasons.append("MA20>MA50")
    if last >= high10: score += 18; reasons.append("突破10日高")
    if vol_ratio >= 2: score += 15; reasons.append("量比强")
    if 55 <= rsi <= 72: score += 10; reasons.append("RSI健康")
    if 3 <= change5 <= 12: score += 10; reasons.append("5日强势")
    if money_score >= 65: score += 12; reasons.append("主力资金流入")
    if last < 3: score -= 15
    if chase_status == "禁追": score -= 25; reasons.append("高位禁追")
    elif chase_status == "等回踩": score -= 12; reasons.append("等回踩更安全")
    elif chase_status == "观察": score -= 5; reasons.append("轻微过热")
    breakout_prob = min(95, max(20, round(score * 0.7 + money_score * 0.3, 1)))
    if breakout_prob >= 80: launch_time = "1-3天内可能启动"
    elif breakout_prob >= 65: launch_time = "3-7天观察"
    elif breakout_prob >= 50: launch_time = "等待确认"
    else: launch_time = "暂时不优先"
    atr = calc_atr(df).iloc[-1]
    if pd.isna(atr): atr = last * 0.03
    buy_low = round(last * 0.995, 2); buy_high = round(last * 1.01, 2)
    sl = round(last - atr * 1.2, 2)
    tp1 = round(last + atr * 2, 2)
    tp2 = round(last + atr * 3.5, 2)
    risk = build_risk((buy_low + buy_high) / 2, sl)
    return {
        "symbol": symbol, "sector": find_sector(symbol), "score": round(score, 1), "price": round(last, 2),
        "buy_low": buy_low, "buy_high": buy_high, "sl": sl, "tp1": tp1, "tp2": tp2,
        "rsi": round(rsi, 1), "vol_ratio": round(vol_ratio, 2), "absorb_ratio": round(absorb_ratio, 2),
        "force_sell": False, "change1": round(change1, 2), "change5": round(change5, 2),
        "money_score": money_score, "money_level": capital_grade(money_score), "breakout_prob": breakout_prob,
        "launch_time": launch_time, "reasons": reasons[:5], "market_mode": market_mode,
        "chase_status": chase_status, "chase_warnings": chase_warnings, "green_days": green_days,
        "extension_ma10": extension_ma10, "extension_ma20": extension_ma20, "risk": risk
    }

# ============================================================
# V17 提前爆发股扫描
# ============================================================

def score_prebreakout(symbol, df):
    """抓涨5%之前的蓄力股：横盘收窄 + 接近突破 + 主力吸筹 + 不过热。"""
    if df is None or len(df) < 120:
        return None
    close = df["Close"]; high = df["High"]; low = df["Low"]; vol = df["Volume"]
    last = float(close.iloc[-1])
    if last < 3:
        return None
    ma5 = float(close.rolling(5).mean().iloc[-1])
    ma10 = float(close.rolling(10).mean().iloc[-1])
    ma20 = float(close.rolling(20).mean().iloc[-1])
    ma50 = float(close.rolling(50).mean().iloc[-1])
    ema200 = float(close.ewm(span=200, adjust=False).mean().iloc[-1])
    rsi = float(calc_rsi(close).iloc[-1])
    avg_vol20 = float(vol.rolling(20).mean().iloc[-1])
    vol_ratio = float(vol.iloc[-1] / avg_vol20) if avg_vol20 > 0 else 0
    high20_prev = float(high.iloc[-21:-1].max())
    high60_prev = float(high.iloc[-61:-1].max())
    low20 = float(low.iloc[-20:].min())
    high20 = float(high.iloc[-20:].max())
    low60 = float(low.iloc[-60:].min())
    range20 = pct(high20, low20)
    range60 = pct(high60_prev, low60)
    dist_ma20 = pct(last, ma20)
    dist_breakout = pct(high20_prev, last)
    change1 = pct(last, float(close.iloc[-2]))
    change5 = pct(last, float(close.iloc[-6]))
    change20 = pct(last, float(close.iloc[-21]))
    obv = calc_obv(close, vol)
    obv20 = float(obv.iloc[-1] - obv.iloc[-21])
    obv60 = float(obv.iloc[-1] - obv.iloc[-61])
    recent_close = close.iloc[-20:]
    recent_vol = vol.iloc[-20:]
    prev_close = close.shift(1).iloc[-20:]
    up_days = recent_close > prev_close
    down_days = recent_close < prev_close
    up_vol = float(recent_vol[up_days].sum())
    down_vol = float(recent_vol[down_days].sum())
    absorb_ratio = up_vol / down_vol if down_vol > 0 else 1
    green_days = count_green_days(close)
    score = 0; reasons = []
    if last > ma20: score += 15; reasons.append("站上MA20")
    if ma5 > ma10 > ma20: score += 15; reasons.append("短均线多头排列")
    if ma20 >= ma50 * 0.98: score += 10; reasons.append("MA20贴近/站上MA50")
    if last > ema200: score += 8; reasons.append("站上EMA200")
    if range20 <= 12: score += 18; reasons.append("20日横盘收窄")
    elif range20 <= 18: score += 10; reasons.append("横盘整理")
    if -1 <= dist_breakout <= 4: score += 20; reasons.append("距离突破位0-4%")
    elif 4 < dist_breakout <= 7: score += 10; reasons.append("接近突破位")
    if obv20 > 0: score += 12; reasons.append("OBV20转强")
    if obv60 > 0: score += 8; reasons.append("OBV60转强")
    if absorb_ratio >= 1.5: score += 15; reasons.append("上涨量大于下跌量")
    elif absorb_ratio >= 1.2: score += 8; reasons.append("疑似吸筹")
    if 1.1 <= vol_ratio <= 2.2: score += 12; reasons.append("量能温和放大")
    elif 2.2 < vol_ratio <= 3.5: score += 6; reasons.append("量能放大")
    if 50 <= rsi <= 68: score += 12; reasons.append("RSI健康")
    elif 68 < rsi <= 72: score += 4; reasons.append("RSI偏强")
    if rsi >= 75: score -= 25; reasons.append("RSI过热扣分")
    if change5 >= 12: score -= 20; reasons.append("5日涨幅过大扣分")
    if dist_ma20 >= 12: score -= 20; reasons.append("离MA20太远扣分")
    if range20 >= 25: score -= 15; reasons.append("波动过大扣分")
    if change20 >= 35: score -= 15; reasons.append("20日涨幅过大扣分")
    if green_days >= 4: score -= 8; reasons.append("连涨过多扣分")
    score = max(0, min(100, round(score, 1)))
    if score < 60:
        return None
    atr = calc_atr(df).iloc[-1]
    if pd.isna(atr): atr = last * 0.04
    breakout_price = round(high20_prev, 2)
    buy_low = round(last * 0.985, 2)
    buy_high = round(min(last * 1.015, breakout_price * 1.01), 2)
    sl = round(max(last - atr * 1.3, ma20 * 0.97), 2)
    tp1 = round(last + atr * 2.0, 2)
    tp2 = round(last + atr * 3.5, 2)
    risk = build_risk((buy_low + buy_high) / 2, sl)
    if score >= 82: launch_time = "1-3天内可能爆发"
    elif score >= 72: launch_time = "3-5天重点观察"
    else: launch_time = "等待放量突破"
    return {
        "symbol": symbol, "sector": find_sector(symbol), "score": score, "price": round(last, 2),
        "breakout_price": breakout_price, "buy_low": buy_low, "buy_high": buy_high,
        "sl": sl, "tp1": tp1, "tp2": tp2, "rsi": round(rsi, 1), "vol_ratio": round(vol_ratio, 2),
        "absorb_ratio": round(absorb_ratio, 2), "change1": round(change1, 2), "change5": round(change5, 2),
        "change20": round(change20, 2), "dist_ma20": round(dist_ma20, 2), "range20": round(range20, 2),
        "range60": round(range60, 2), "obv20": round(obv20, 2), "obv60": round(obv60, 2),
        "green_days": green_days, "launch_time": launch_time, "reasons": reasons[:7], "risk": risk
    }

# ============================================================
# 底部吸筹扫描
# ============================================================

def score_bottom(symbol, df):
    if df is None or len(df) < 120:
        return None
    close = df["Close"]; high = df["High"]; low = df["Low"]; vol = df["Volume"]
    last = float(close.iloc[-1])
    if last < 3: return None
    ma20 = float(close.rolling(20).mean().iloc[-1]); ma50 = float(close.rolling(50).mean().iloc[-1])
    low52 = float(low.min()); high20 = float(high.iloc[-20:].max()); low20 = float(low.iloc[-20:].min())
    avg_vol20 = float(vol.rolling(20).mean().iloc[-1])
    vol_ratio = float(vol.iloc[-1] / avg_vol20) if avg_vol20 > 0 else 0
    rsi = float(calc_rsi(close).iloc[-1])
    obv = calc_obv(close, vol); obv20 = float(obv.iloc[-1] - obv.iloc[-21]); obv60 = float(obv.iloc[-1] - obv.iloc[-61])
    recent_close = close.iloc[-20:]; recent_vol = vol.iloc[-20:]; prev_close = close.shift(1).iloc[-20:]
    up_days = recent_close > prev_close; down_days = recent_close < prev_close
    up_vol = float(recent_vol[up_days].sum()); down_vol = float(recent_vol[down_days].sum())
    absorb_ratio = up_vol / down_vol if down_vol > 0 else 1
    dist_low = pct(last, low52); range20 = pct(high20, low20)
    score = 0; reasons = []; money_score = 0
    if 5 <= dist_low <= 45: score += 20; reasons.append("接近低位")
    if range20 <= 18: score += 18; reasons.append("横盘收窄")
    if last >= ma20: score += 12; reasons.append("站上MA20")
    if ma20 >= ma50 * 0.95: score += 8; reasons.append("均线改善")
    if obv20 > 0: score += 15; money_score += 20; reasons.append("OBV转强")
    if obv60 > 0: money_score += 20
    if 45 <= rsi <= 65: score += 10; reasons.append("RSI转强")
    if vol_ratio >= 1.3: score += 10; money_score += 15; reasons.append("量能增加")
    if absorb_ratio >= 1.5: score += 15; money_score += 30; reasons.append("上涨量大于下跌量")
    elif absorb_ratio >= 1.2: score += 8; money_score += 18; reasons.append("疑似吸筹")
    if 0 <= pct(high20, last) <= 6: score += 10; reasons.append("接近突破位")
    money_score = max(0, min(100, round(money_score, 1)))
    if score < 52: return None
    breakout_prob = min(95, max(20, round(score * 0.65 + money_score * 0.35, 1)))
    if breakout_prob >= 80: launch_time = "1-5天内可能启动"
    elif breakout_prob >= 65: launch_time = "3-10天观察"
    else: launch_time = "等放量突破"
    atr = calc_atr(df).iloc[-1]
    if pd.isna(atr): atr = last * 0.04
    buy_low = round(last * 0.98, 2); buy_high = round(last * 1.03, 2)
    sl = round(last - atr * 1.3, 2); tp1 = round(last + atr * 2.2, 2); tp2 = round(last + atr * 4.0, 2)
    return {"symbol": symbol, "sector": find_sector(symbol), "score": round(score, 1), "price": round(last, 2), "buy_low": buy_low, "buy_high": buy_high, "sl": sl, "tp1": tp1, "tp2": tp2, "rsi": round(rsi, 1), "vol_ratio": round(vol_ratio, 2), "money_score": money_score, "money_level": capital_grade(money_score), "breakout_prob": breakout_prob, "launch_time": launch_time, "absorb_ratio": round(absorb_ratio, 2), "dist_low": round(dist_low, 2), "range20": round(range20, 2), "reasons": reasons[:5], "risk": build_risk((buy_low + buy_high) / 2, sl)}

# ============================================================
# 自选股买卖分析
# ============================================================

def analyze_watch_stock(symbol, df):
    if df is None or len(df) < 120:
        return None
    close = df["Close"]; high = df["High"]; low = df["Low"]; open_ = df["Open"]; vol = df["Volume"]
    last = float(close.iloc[-1]); prev = float(close.iloc[-2]); day_open = float(open_.iloc[-1])
    day_high = float(high.iloc[-1]); day_low = float(low.iloc[-1])
    ma5 = float(close.rolling(5).mean().iloc[-1]); ma10 = float(close.rolling(10).mean().iloc[-1])
    ma20 = float(close.rolling(20).mean().iloc[-1]); ma50 = float(close.rolling(50).mean().iloc[-1])
    ema200 = float(close.ewm(span=200, adjust=False).mean().iloc[-1])
    rsi = float(calc_rsi(close).iloc[-1])
    atr = calc_atr(df).iloc[-1]
    if pd.isna(atr): atr = last * 0.04
    avg_vol20 = float(vol.rolling(20).mean().iloc[-1])
    vol_ratio = float(vol.iloc[-1] / avg_vol20) if avg_vol20 > 0 else 0
    recent_close_for_absorb = close.iloc[-20:]; recent_vol_for_absorb = vol.iloc[-20:]
    prev_close_for_absorb = close.shift(1).iloc[-20:]
    up_days_for_absorb = recent_close_for_absorb > prev_close_for_absorb
    down_days_for_absorb = recent_close_for_absorb < prev_close_for_absorb
    up_vol_for_absorb = float(recent_vol_for_absorb[up_days_for_absorb].sum())
    down_vol_for_absorb = float(recent_vol_for_absorb[down_days_for_absorb].sum())
    absorb_ratio = up_vol_for_absorb / down_vol_for_absorb if down_vol_for_absorb > 0 else 1
    high20 = float(high.iloc[-20:].max()); high60 = float(high.iloc[-60:].max()); low120 = float(low.iloc[-120:].min())
    change1 = pct(last, prev); change5 = pct(last, float(close.iloc[-6])); change20 = pct(last, float(close.iloc[-21]))
    dist_ema200 = pct(last, ema200); dist_ma20 = pct(last, ma20); dist_high60 = pct(last, high60); dist_low120 = pct(last, low120)
    green_days = count_green_days(close)
    day_range = day_high - day_low
    upper_shadow_pct = 0; close_position = 50
    if day_range > 0:
        upper_shadow_pct = ((day_high - max(last, day_open)) / day_range) * 100
        close_position = ((last - day_low) / day_range) * 100
    price_not_up = abs(change1) <= 0.6
    volume_spike = vol_ratio >= 1.5
    near_60_high = last >= high60 * 0.94
    new_20_high_reject = day_high >= high20 * 0.995 and close_position < 55
    recent5_high = float(high.iloc[-5:].max()); recent5_low = float(low.iloc[-5:].min())
    recent5_range = pct(recent5_high, recent5_low)
    sideways_high = near_60_high and recent5_range <= 8
    sell_score = 0; sell_reasons = []
    if rsi >= 78: sell_score += 35; sell_reasons.append("RSI严重过热")
    elif rsi >= 70: sell_score += 25; sell_reasons.append("RSI过热")
    if green_days >= 5: sell_score += 20; sell_reasons.append("连续上涨过多")
    elif green_days >= 3: sell_score += 12; sell_reasons.append("连续上涨")
    if change5 >= 12: sell_score += 18; sell_reasons.append("5日涨幅过大")
    elif change5 >= 8: sell_score += 10; sell_reasons.append("短线涨幅偏大")
    if dist_ma20 >= 15: sell_score += 18; sell_reasons.append("远离MA20")
    elif dist_ma20 >= 9: sell_score += 10; sell_reasons.append("离MA20偏远")
    if upper_shadow_pct >= 35: sell_score += 18; sell_reasons.append("上影线派发")
    if volume_spike and price_not_up: sell_score += 22; sell_reasons.append("放量不涨")
    if new_20_high_reject: sell_score += 18; sell_reasons.append("突破失败/假突破")
    if sideways_high: sell_score += 15; sell_reasons.append("高位横盘")
    if last < ma5 and rsi >= 65: sell_score += 12; sell_reasons.append("跌破MA5短线转弱")
    force_sell = (rsi >= 75 and dist_ma20 >= 12 and green_days >= 4 and absorb_ratio < 1.1)
    if force_sell:
        sell_score = max(sell_score, 88); sell_reasons.append("V17强制卖出条件")
    sell_score = max(0, min(100, round(sell_score, 1)))
    if force_sell: sell_action = "🔥 强制卖出 / 高位派发"; sell_level = "V17强制卖出"
    elif sell_score >= 80: sell_action = "🔴 全部卖出 / 锁定利润"; sell_level = "高危派发"
    elif sell_score >= 60: sell_action = "🟠 分批止盈 30%-50%"; sell_level = "明显派发"
    elif sell_score >= 40: sell_action = "🟡 收紧止损 / 不加仓"; sell_level = "轻微过热"
    else: sell_action = "🟢 继续持有"; sell_level = "健康"
    buy_score = 0; buy_reasons = []
    range20 = pct(float(high.iloc[-20:].max()), float(low.iloc[-20:].min()))
    if 5 <= dist_low120 <= 45: buy_score += 18; buy_reasons.append("接近低位")
    if range20 <= 18: buy_score += 16; buy_reasons.append("横盘收窄")
    if last >= ma20: buy_score += 12; buy_reasons.append("站上MA20")
    if ma20 >= ma50 * 0.95: buy_score += 8; buy_reasons.append("均线改善")
    obv = calc_obv(close, vol); obv20 = float(obv.iloc[-1] - obv.iloc[-21]); obv60 = float(obv.iloc[-1] - obv.iloc[-61])
    if obv20 > 0: buy_score += 14; buy_reasons.append("OBV转强")
    if obv60 > 0: buy_score += 10
    if 45 <= rsi <= 65: buy_score += 12; buy_reasons.append("RSI转强")
    if vol_ratio >= 1.3 and change1 >= 0: buy_score += 12; buy_reasons.append("放量转强")
    if 0 <= pct(high20, last) <= 6: buy_score += 10; buy_reasons.append("接近突破位")
    if sell_score >= 40: buy_score -= 25; buy_reasons.append("已有高位风险")
    buy_score = max(0, min(100, round(buy_score, 1)))
    if buy_score >= 75: buy_action = "🟢 可低吸 / 准备启动"; buy_level = "强吸筹"
    elif buy_score >= 60: buy_action = "🟢 观察买点 / 小仓试"; buy_level = "吸筹"
    elif buy_score >= 45: buy_action = "👀 等确认"; buy_level = "观察"
    else: buy_action = "⏳ 暂不买"; buy_level = "无买点"
    if sell_score >= 60: final_action = sell_action; signal_type = "SELL"
    elif buy_score >= 60: final_action = buy_action; signal_type = "BUY"
    elif sell_score >= 40: final_action = sell_action; signal_type = "WATCH_SELL"
    else: final_action = "🟢 持有观察"; signal_type = "HOLD"
    suggested_sl = round(max(last - atr * 1.5, ma20 * 0.97), 2)
    trail_sl = round(max(ma10 * 0.98, last - atr * 1.2), 2)
    tp1 = round(last + atr * 2, 2); tp2 = round(last + atr * 3.5, 2)
    buy_low = round(last * 0.98, 2); buy_high = round(last * 1.02, 2)
    return {"symbol": symbol, "sector": find_sector(symbol), "price": round(last, 2), "signal_type": signal_type, "final_action": final_action, "buy_score": buy_score, "buy_level": buy_level, "buy_reasons": buy_reasons[:5], "sell_score": sell_score, "sell_level": sell_level, "sell_reasons": sell_reasons[:6], "rsi": round(rsi, 1), "vol_ratio": round(vol_ratio, 2), "absorb_ratio": round(absorb_ratio, 2), "force_sell": force_sell, "change1": round(change1, 2), "change5": round(change5, 2), "change20": round(change20, 2), "green_days": green_days, "dist_ma20": round(dist_ma20, 2), "dist_ema200": round(dist_ema200, 2), "dist_high60": round(dist_high60, 2), "upper_shadow_pct": round(upper_shadow_pct, 1), "close_position": round(close_position, 1), "ma5": round(ma5, 2), "ma10": round(ma10, 2), "ma20": round(ma20, 2), "ma50": round(ma50, 2), "ema200": round(ema200, 2), "buy_low": buy_low, "buy_high": buy_high, "suggested_sl": suggested_sl, "trail_sl": trail_sl, "tp1": tp1, "tp2": tp2}

# ============================================================
# 扫描器
# ============================================================

def scan_normal():
    results = []
    mode = market_regime()
    for batch in chunk_list(ALL_SYMBOLS, BATCH_SIZE):
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = {ex.submit(score_stock, s, safe_download(s, "6mo", "1d"), mode): s for s in batch}
            for f in as_completed(futures):
                try:
                    r = f.result()
                    if r and r["score"] >= 35:
                        results.append(r)
                except Exception:
                    pass
        time.sleep(BATCH_SLEEP)
    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def scan_prebreakout():
    results = []
    for batch in chunk_list(ALL_SYMBOLS, BATCH_SIZE):
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = {ex.submit(score_prebreakout, s, safe_download(s, "1y", "1d")): s for s in batch}
            for f in as_completed(futures):
                try:
                    r = f.result()
                    if r:
                        results.append(r)
                except Exception:
                    pass
        time.sleep(BATCH_SLEEP)
    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def scan_bottom():
    results = []
    for batch in chunk_list(ALL_SYMBOLS, BATCH_SIZE):
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = {ex.submit(score_bottom, s, safe_download(s, "1y", "1d")): s for s in batch}
            for f in as_completed(futures):
                try:
                    r = f.result()
                    if r: results.append(r)
                except Exception:
                    pass
        time.sleep(BATCH_SLEEP)
    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def scan_watchlist():
    load_watchlist()
    results = []
    for symbol in MY_STOCKS:
        if not symbol or symbol in BAD_SYMBOLS:
            continue
        df = safe_download(symbol, "1y", "1d")
        r = analyze_watch_stock(symbol, df)
        if r:
            position = calc_position_info(symbol, r["price"], suggested_sl=r.get("suggested_sl"), tp1=r.get("tp1"), tp2=r.get("tp2"))
            r["position"] = position
            if position:
                if any("TP2" in x for x in position.get("alerts", [])):
                    r["signal_type"] = "SELL"; r["final_action"] = "🚀 TP2到达 / 建议清仓"
                elif any("TP1" in x for x in position.get("alerts", [])) and r.get("signal_type") != "SELL":
                    r["signal_type"] = "WATCH_SELL"; r["final_action"] = "🎯 TP1到达 / 建议减仓30%-50%"
                elif any("SL" in x for x in position.get("alerts", [])):
                    r["signal_type"] = "SELL"; r["final_action"] = "🛑 跌破防守SL / 必须处理"
            results.append(r)
        time.sleep(0.2)
    def sort_key(x):
        priority = {"SELL": 4, "BUY": 3, "WATCH_SELL": 2}.get(x["signal_type"], 1)
        return (priority, x["sell_score"], x["buy_score"])
    results.sort(key=sort_key, reverse=True)
    return results

# ============================================================
# Telegram 文案
# ============================================================

def build_text(title, arr):
    lines = [title, now_kl_str(), ""]
    for i, r in enumerate(arr, start=1):
        chase = r.get("chase_status", "可买")
        action_icon = "❌" if chase == "禁追" else "⚠️" if chase == "等回踩" else "👀" if chase == "观察" else "✅"
        lines.append(f"{i}. {r['symbol']} {r['score']}分 | {action_icon} {chase}")
        lines.append(f"价 {r['price']} | 买区 {r['buy_low']}-{r['buy_high']} | SL {r['sl']} | TP1 {r['tp1']} | TP2 {r['tp2']}")
        lines.append(f"主力资金: {r.get('money_level','-')} | 爆发概率: {r.get('breakout_prob','-')}% | 吸筹强度: {r.get('absorb_ratio','-')} | {r.get('launch_time','-')}")
        lines.append(f"市场: {r.get('market_mode','-')} | RSI {r.get('rsi','-')} | 5日 {r.get('change5','-')}% | 连涨 {r.get('green_days','-')}天")
        if r.get("chase_warnings"): lines.append("防追高: " + " / ".join(r["chase_warnings"]))
        if r.get("reasons"): lines.append("逻辑: " + " / ".join(r["reasons"]))
        risk = r.get("risk")
        if risk: lines.append(f"仓位: {risk['shares']}股 | 约${risk['position_value']} | 最大亏损约${risk['max_loss']}")
        lines.append("")
    return "\n".join(lines)


def build_prebreakout_text(title, arr):
    lines = [title, now_kl_str(), ""]
    if not arr:
        lines.append("暂无提前爆发股")
        return "\n".join(lines)
    for i, r in enumerate(arr, start=1):
        lines.append(f"{i}. {r['symbol']} | {r['score']}分 | 🚀 {r['launch_time']}")
        lines.append(f"价 {r['price']} | 突破位 {r['breakout_price']}")
        lines.append(f"埋伏区 {r['buy_low']}-{r['buy_high']} | SL {r['sl']} | TP1 {r['tp1']} | TP2 {r['tp2']}")
        lines.append(f"RSI {r['rsi']} | 量比 {r['vol_ratio']} | 吸筹 {r['absorb_ratio']} | 20日振幅 {r['range20']}%")
        lines.append(f"5日 {r['change5']}% | 20日 {r['change20']}% | 离MA20 {r['dist_ma20']}% | 连涨 {r.get('green_days','-')}天")
        if r.get("reasons"): lines.append("提前逻辑: " + " / ".join(r["reasons"]))
        risk = r.get("risk")
        if risk: lines.append(f"仓位建议: {risk['shares']}股 | 约${risk['position_value']} | 最大亏损约${risk['max_loss']}")
        lines.append("")
    lines.append("使用重点：V17名单是埋伏/等突破，不是看到大涨后追高。")
    return "\n".join(lines)


def build_watchlist_text(title, arr):
    lines = [title, now_kl_str(), ""]
    if not arr:
        lines.append("暂无有效自选股数据")
        return "\n".join(lines)
    for i, r in enumerate(arr, start=1):
        lines.append(f"{i}. {r['symbol']} | {r['final_action']}")
        lines.append(f"价 {r['price']} | 买分 {r['buy_score']}({r['buy_level']}) | 卖分 {r['sell_score']}({r['sell_level']})")
        position = r.get("position")
        if position:
            pnl_icon = "🟢" if position["pnl_pct"] >= 0 else "🔴"
            lines.append(f"持仓 {position['shares']}股 | 成本 {position['avg_price']} | 市值 ${position['market_value']} | {pnl_icon}盈亏 {position['pnl_pct']}% / ${position['pnl_amount']}")
            if position.get("alerts"): lines.append("实盘提醒: " + " / ".join(position["alerts"]))
        lines.append(f"RSI {r['rsi']} | 量比 {r['vol_ratio']} | 吸筹 {r.get('absorb_ratio','-')} | 5日 {r['change5']}% | 离MA20 {r['dist_ma20']}%")
        lines.append(f"低吸区 {r['buy_low']}-{r['buy_high']} | 防守SL {r['suggested_sl']} | 移动止盈 {r['trail_sl']}")
        lines.append(f"TP1 {r['tp1']} | TP2 {r['tp2']}")
        if r.get("sell_reasons"): lines.append("卖出逻辑: " + " / ".join(r["sell_reasons"]))
        if r.get("buy_reasons"): lines.append("买入逻辑: " + " / ".join(r["buy_reasons"]))
        lines.append("")
    lines.append("加入自选股：/watchlist/add?symbol=NDAQ")
    lines.append("移除自选股：/watchlist/remove?symbol=NDAQ")
    return "\n".join(lines)

# ============================================================
# 执行任务
# ============================================================

def run_premarket():
    global LAST_PREMARKET_SIGNATURE
    if is_weekend_et(): return {"status": "skip"}
    results = scan_normal()
    if not results: return {"status": "empty"}
    top = results[:5]
    sig = "|".join([x["symbol"] for x in top])
    if sig == LAST_PREMARKET_SIGNATURE: return {"status": "same"}
    LAST_PREMARKET_SIGNATURE = sig; save_state()
    send_telegram(build_text("🚀 V17 防追高盘前 Top5", top))
    return {"status": "ok", "top": top}


def run_close():
    global LAST_CLOSE_SIGNATURE
    if is_weekend_et(): return {"status": "skip"}
    results = scan_normal()
    if not results: return {"status": "empty"}
    top = results[:10]
    sig = "|".join([x["symbol"] for x in top])
    if sig == LAST_CLOSE_SIGNATURE: return {"status": "same"}
    LAST_CLOSE_SIGNATURE = sig; save_state()
    send_telegram(build_text("🌙 V17 防追高收盘预备股", top))
    return {"status": "ok", "top": top}


def run_prebreakout(force=False):
    global LAST_PREBREAKOUT_SIGNATURE
    if is_weekend_et(): return {"status": "skip"}
    results = scan_prebreakout()
    if not results: return {"status": "empty"}
    top = results[:10]
    sig = "|".join([f"{x['symbol']}:{x['score']}:{x['price']}" for x in top])
    if (not force) and sig == LAST_PREBREAKOUT_SIGNATURE:
        return {"status": "same", "top": top}
    LAST_PREBREAKOUT_SIGNATURE = sig; save_state()
    send_telegram(build_prebreakout_text("🚀 V17 提前爆发股 Top10", top))
    return {"status": "ok", "top": top}


def run_bottom():
    global LAST_BOTTOM_SIGNATURE
    if is_weekend_et(): return {"status": "skip"}
    results = scan_bottom()
    if not results: return {"status": "empty"}
    top = results[:10]
    sig = "|".join([x["symbol"] for x in top])
    if sig == LAST_BOTTOM_SIGNATURE: return {"status": "same"}
    LAST_BOTTOM_SIGNATURE = sig; save_state()
    send_telegram(build_text("📦 V17 底部吸筹 Top10", top))
    return {"status": "ok", "top": top}


def run_watchlist(force=False):
    global LAST_WATCHLIST_SIGNATURE
    if is_weekend_et(): return {"status": "skip", "watchlist": MY_STOCKS}
    results = scan_watchlist()
    if not results: return {"status": "empty", "watchlist": MY_STOCKS}
    important = [x for x in results if x["signal_type"] in ["SELL", "BUY", "WATCH_SELL"]]
    sig = "|".join([f"{x['symbol']}:{x['signal_type']}:{x['sell_score']}:{x['buy_score']}" for x in important])
    if force or (sig and sig != LAST_WATCHLIST_SIGNATURE):
        LAST_WATCHLIST_SIGNATURE = sig; save_state()
        send_telegram(build_watchlist_text("📊 V17 实盘自选股买卖监控", results))
        return {"status": "ok", "results": results}
    return {"status": "same_or_no_signal", "results": results}

# ============================================================
# Scheduler
# ============================================================

def scheduler_loop():
    weekdays = [schedule.every().monday, schedule.every().tuesday, schedule.every().wednesday, schedule.every().thursday, schedule.every().friday]
    for d in weekdays:
        d.at("20:45").do(run_prebreakout)
        d.at("21:00").do(run_premarket)
        d.at("22:05").do(run_watchlist)
    schedule.every().day.at("04:20").do(run_close)
    schedule.every().day.at("04:35").do(run_bottom)
    schedule.every().day.at("04:40").do(run_prebreakout)
    schedule.every().day.at("04:50").do(run_watchlist)
    while True:
        try:
            schedule.run_pending()
        except Exception:
            pass
        time.sleep(15)

# ============================================================
# Flask Routes
# ============================================================

@app.route("/")
def home():
    return "V17 Pre-Breakout Live Trading Monitor Running"

@app.route("/health")
def health():
    load_watchlist()
    return jsonify({"status": "healthy", "time_kl": now_kl_str(), "symbols": len(ALL_SYMBOLS), "my_stocks": MY_STOCKS, "positions_count": len(load_positions()), "version": "V17_prebreakout_live"})

@app.route("/run-premarket")
def route_premarket(): return jsonify(run_premarket())

@app.route("/run-scan")
def route_scan_alias(): return jsonify(run_premarket())

@app.route("/run-close")
def route_close(): return jsonify(run_close())

@app.route("/run-bottom")
def route_bottom(): return jsonify(run_bottom())

@app.route("/run-prebreakout")
def route_prebreakout():
    force = request.args.get("force", "1") == "1"
    return jsonify(run_prebreakout(force=force))

@app.route("/run-watchlist")
def route_watchlist_run():
    force = request.args.get("force", "1") == "1"
    return jsonify(run_watchlist(force=force))

@app.route("/watchlist")
def route_watchlist():
    load_watchlist()
    return jsonify({"count": len(MY_STOCKS), "my_stocks": MY_STOCKS, "usage_add": "/watchlist/add?symbol=NDAQ", "usage_remove": "/watchlist/remove?symbol=NDAQ", "usage_run": "/run-watchlist"})

@app.route("/watchlist/add")
def route_watchlist_add():
    load_watchlist(); symbol = clean_symbol(request.args.get("symbol", ""))
    if not symbol: return jsonify({"status": "error", "message": "missing symbol"}), 400
    if symbol in BAD_SYMBOLS: return jsonify({"status": "error", "message": "bad/delisted symbol"}), 400
    if symbol not in MY_STOCKS:
        MY_STOCKS.append(symbol); MY_STOCKS.sort(); save_watchlist()
    return jsonify({"status": "ok", "message": f"{symbol} added", "my_stocks": MY_STOCKS})

@app.route("/watchlist/remove")
def route_watchlist_remove():
    load_watchlist(); symbol = clean_symbol(request.args.get("symbol", ""))
    if not symbol: return jsonify({"status": "error", "message": "missing symbol"}), 400
    if symbol in MY_STOCKS:
        MY_STOCKS.remove(symbol); save_watchlist()
    return jsonify({"status": "ok", "message": f"{symbol} removed", "my_stocks": MY_STOCKS})

@app.route("/watchlist/set")
def route_watchlist_set():
    global MY_STOCKS
    raw = request.args.get("symbols", "")
    symbols = parse_symbols(raw)
    if not symbols: return jsonify({"status": "error", "message": "missing symbols"}), 400
    MY_STOCKS = sorted(set([s for s in symbols if s not in BAD_SYMBOLS])); save_watchlist()
    return jsonify({"status": "ok", "my_stocks": MY_STOCKS})

@app.route("/positions")
def route_positions():
    return jsonify({"count": len(load_positions()), "positions": load_positions(), "usage_add": "/position/add?symbol=NDAQ&shares=50&price=90", "usage_remove": "/position/remove?symbol=NDAQ", "usage_run": "/run-watchlist"})

@app.route("/position/add")
def route_position_add():
    symbol = clean_symbol(request.args.get("symbol", "")); shares_raw = request.args.get("shares", ""); price_raw = request.args.get("price", ""); note = request.args.get("note", "")
    if not symbol or not shares_raw or not price_raw:
        return jsonify({"status": "error", "message": "missing symbol/shares/price", "example": "/position/add?symbol=NDAQ&shares=50&price=90"}), 400
    if symbol in BAD_SYMBOLS: return jsonify({"status": "error", "message": "bad/delisted symbol"}), 400
    try:
        shares = int(float(shares_raw)); price = float(price_raw)
    except Exception:
        return jsonify({"status": "error", "message": "shares or price invalid"}), 400
    if shares <= 0 or price <= 0: return jsonify({"status": "error", "message": "shares and price must be > 0"}), 400
    pos = add_or_update_position(symbol, shares, price, note=note)
    return jsonify({"status": "ok", "message": f"{symbol} position saved", "position": pos, "my_stocks": MY_STOCKS})

@app.route("/position/remove")
def route_position_remove():
    symbol = clean_symbol(request.args.get("symbol", ""))
    if not symbol: return jsonify({"status": "error", "message": "missing symbol"}), 400
    existed = remove_position(symbol)
    return jsonify({"status": "ok", "message": f"{symbol} removed" if existed else f"{symbol} not found", "positions": load_positions()})

@app.route("/position/clear")
def route_position_clear():
    save_positions({})
    return jsonify({"status": "ok", "message": "all positions cleared"})

@app.route("/sectors")
def route_sectors(): return jsonify({"count": len(SECTOR_POOLS), "symbols": len(ALL_SYMBOLS), "sectors": SECTOR_POOLS})

@app.route("/api/test-telegram")
def route_test():
    ok = send_telegram("✅ V17 Telegram Test Success")
    return jsonify({"telegram_sent": ok})

# ============================================================
# Start
# ============================================================

if __name__ == "__main__":
    load_state()
    load_watchlist()
    t = threading.Thread(target=scheduler_loop, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=PORT)
