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
# 美股爆发扫描器 V23 专业做T系统 + 事件风控 + 动态热点池 + 交易数据库版
# 基于 V17：自选股 + 持仓追踪 + 买卖监控 + 防追高 + 提前爆发扫描
# V18 新增：
# 1) 主力慢吸筹评分 accumulation_score
# 2) 资金流确认 smart_money_flow_score
# 3) 假突破 / 高位派发扣分 fake_breakout_score
# 4) A/B级提前爆发分级
# 5) Telegram 文案升级：主力资金、假突破风险、信号等级
# Railway / Flask / Telegram 可直接部署
# V20 新增：做T雷达、VWAP、日均振幅、流动性、A/B/C做T分级、低吸/高抛剧本
# V21 新增：双止损、实时VWAP、ORB开盘区间、入场/止损/高抛信号、盘中做T提醒
# V21.5 新增：严格A级过滤、RVOL强制降级、VWAP过远禁追、ORB失败禁做、Telegram更清晰
# V22 新增：事件风险过滤、Gap风控、动态热点池、市场风险增强
# V23 新增：交易数据库、模式统计、胜率/盈亏总结、系统化复盘
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

DEFAULT_MY_STOCKS = os.getenv("MY_STOCKS", "IONQ,APP,AFRM,PLTR,NVDA,TSLA,AMD,META,AMZN,CRM,SOUN,LUNR,SMCI,SOFI,TEM").strip()
T_RADAR_STOCKS_ENV = os.getenv("T_RADAR_STOCKS", "").strip()
T_MIN_DOLLAR_VOLUME = float(os.getenv("T_MIN_DOLLAR_VOLUME", "80000000"))
T_MIN_AVG_RANGE = float(os.getenv("T_MIN_AVG_RANGE", "3.0"))
T_MAX_STOCKS = int(os.getenv("T_MAX_STOCKS", "15"))

# V21 做T风控参数
T_DAYTRADE_STOP_PCT = float(os.getenv("T_DAYTRADE_STOP_PCT", "2.5"))   # 日内做T最大容忍亏损%
T_VWAP_STOP_BUFFER = float(os.getenv("T_VWAP_STOP_BUFFER", "0.8"))     # VWAP下方缓冲%
T_ORB_MINUTES = int(os.getenv("T_ORB_MINUTES", "30"))                 # 开盘区间分钟数，默认30分钟
T_ALERT_MIN_SCORE = float(os.getenv("T_ALERT_MIN_SCORE", "62"))       # B级以上才重点提醒

# V21.5 严格做T参数：A级必须满足更严格条件，避免“分数高但没量/离VWAP太远”
T_A_MIN_RVOL = float(os.getenv("T_A_MIN_RVOL", "1.3"))                  # A级最低有效RVOL
T_A_MAX_DIST_VWAP = float(os.getenv("T_A_MAX_DIST_VWAP", "1.5"))        # A级允许离VWAP最大%
T_B_MIN_RVOL = float(os.getenv("T_B_MIN_RVOL", "0.8"))                  # B级最低有效RVOL
T_NO_TRADE_RVOL = float(os.getenv("T_NO_TRADE_RVOL", "0.55"))           # 低于这个，基本不做T
T_NO_CHASE_DIST_VWAP = float(os.getenv("T_NO_CHASE_DIST_VWAP", "3.5"))  # 离VWAP超过这个，不追，只等回踩

# V22/V23 专业风控参数
EVENT_RISK_ENABLED = os.getenv("EVENT_RISK_ENABLED", "1") == "1"
EARNINGS_BLOCK_DAYS = int(os.getenv("EARNINGS_BLOCK_DAYS", "1"))          # 财报前后N天降级
GAP_RISK_PCT = float(os.getenv("GAP_RISK_PCT", "5.0"))                   # Gap超过这个，不做A级
HOT_POOL_ENABLED = os.getenv("HOT_POOL_ENABLED", "1") == "1"
HOT_POOL_TOP_N = int(os.getenv("HOT_POOL_TOP_N", "5"))
HOT_POOL_MIN_RVOL = float(os.getenv("HOT_POOL_MIN_RVOL", "1.8"))
HOT_POOL_MIN_RANGE = float(os.getenv("HOT_POOL_MIN_RANGE", "5.0"))
HOT_POOL_MIN_DOLLAR_VOLUME = float(os.getenv("HOT_POOL_MIN_DOLLAR_VOLUME", "150000000"))

TZ_KL = pytz.timezone("Asia/Kuala_Lumpur")
TZ_ET = pytz.timezone("US/Eastern")

STATE_FILE = "scanner_state.json"
WATCHLIST_FILE = "watchlist.json"
POSITIONS_FILE = "positions.json"
TRADE_JOURNAL_FILE = "trade_journal.json"
HOT_POOL_FILE = "hot_pool.json"

LAST_PREMARKET_SIGNATURE = ""
LAST_CLOSE_SIGNATURE = ""
LAST_BOTTOM_SIGNATURE = ""
LAST_WATCHLIST_SIGNATURE = ""
LAST_PREBREAKOUT_SIGNATURE = ""
LAST_MARKET_REGIME = ""
LAST_T_RADAR_SIGNATURE = ""
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



def load_trade_journal():
    if not os.path.exists(TRADE_JOURNAL_FILE):
        return []
    try:
        with open(TRADE_JOURNAL_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_trade_journal(trades):
    try:
        with open(TRADE_JOURNAL_FILE, "w", encoding="utf-8") as f:
            json.dump(trades, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def add_trade_record(symbol, side, shares, price, reason="", mode="T", fees=0):
    trades = load_trade_journal()
    trade = {
        "id": len(trades) + 1,
        "time_kl": now_kl_str(),
        "symbol": clean_symbol(symbol),
        "side": str(side).upper(),
        "shares": int(shares),
        "price": float(price),
        "fees": float(fees),
        "reason": reason,
        "mode": mode
    }
    trades.append(trade)
    save_trade_journal(trades)
    return trade


def summarize_trades():
    trades = load_trade_journal()
    summary = {"total_trades": len(trades), "closed_rounds": 0, "wins": 0, "losses": 0, "win_rate": 0, "realized_pnl": 0, "by_symbol": {}, "by_mode": {}}
    inventory = {}
    for t in trades:
        sym = t.get("symbol", "")
        mode = t.get("mode", "T")
        side = t.get("side", "").upper()
        shares = int(t.get("shares", 0))
        price = float(t.get("price", 0))
        fees = float(t.get("fees", 0))
        summary["by_symbol"].setdefault(sym, {"pnl": 0, "trades": 0})
        summary["by_mode"].setdefault(mode, {"pnl": 0, "trades": 0})
        summary["by_symbol"][sym]["trades"] += 1
        summary["by_mode"][mode]["trades"] += 1
        inventory.setdefault(sym, [])
        if side in ["BUY", "B"]:
            inventory[sym].append([shares, price, fees])
        elif side in ["SELL", "S"]:
            remaining = shares
            pnl_trade = -fees
            while remaining > 0 and inventory[sym]:
                lot_shares, lot_price, lot_fees = inventory[sym][0]
                used = min(remaining, lot_shares)
                pnl_trade += (price - lot_price) * used
                lot_shares -= used
                remaining -= used
                if lot_shares <= 0:
                    inventory[sym].pop(0)
                else:
                    inventory[sym][0][0] = lot_shares
            summary["closed_rounds"] += 1
            if pnl_trade >= 0:
                summary["wins"] += 1
            else:
                summary["losses"] += 1
            summary["realized_pnl"] += pnl_trade
            summary["by_symbol"][sym]["pnl"] += pnl_trade
            summary["by_mode"][mode]["pnl"] += pnl_trade
    if summary["closed_rounds"] > 0:
        summary["win_rate"] = round(summary["wins"] / summary["closed_rounds"] * 100, 2)
    summary["realized_pnl"] = round(summary["realized_pnl"], 2)
    for d in [summary["by_symbol"], summary["by_mode"]]:
        for k in d:
            d[k]["pnl"] = round(d[k]["pnl"], 2)
    return summary

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
    global LAST_WATCHLIST_SIGNATURE, LAST_PREBREAKOUT_SIGNATURE, LAST_MARKET_REGIME, LAST_T_RADAR_SIGNATURE, INTRADAY_BREAKOUT_SENT
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
        LAST_T_RADAR_SIGNATURE = data.get("last_t_radar_signature", "")
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
        "last_t_radar_signature": LAST_T_RADAR_SIGNATURE,
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



def safe_download_intraday(symbol, period="5d", interval="5m", prepost=True):
    """V22/V23: 盘中/盘前数据，尽量包含pre-market/after-hours。"""
    try:
        df = yf.download(symbol, period=period, interval=interval, auto_adjust=True, progress=False, threads=False, prepost=prepost)
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]
        need = {"Open", "High", "Low", "Close", "Volume"}
        if not need.issubset(set(df.columns)):
            return None
        df = df.dropna()
        if len(df) < 3:
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


def prebreakout_level(score, rsi=50, vol_ratio=1, dist_ma20=0, fake_score=0, green_days=0):
    """
    V19.1 主力确认等级
    重点：不是分数高就一定A级，必须通过量能/风险确认
    """

    # 强制降级规则
    forced_cap = None

    if rsi >= 78:
        forced_cap = "C"
    elif rsi >= 75:
        forced_cap = "B"

    if dist_ma20 >= 12:
        forced_cap = "B" if forced_cap is None else forced_cap

    if vol_ratio < 1.0:
        forced_cap = "B" if forced_cap is None else forced_cap

    if fake_score >= 40:
        forced_cap = "C"

    if green_days >= 4:
        forced_cap = "B" if forced_cap is None else forced_cap

    # 原始等级
    if score >= 90 and vol_ratio >= 1.3 and fake_score < 25 and rsi < 72:
        level = "S"
        text = "🔥 S级 真主力进场"
    elif score >= 82:
        level = "A"
        text = "🟢 A级 主力提前吸筹"
    elif score >= 72:
        level = "B"
        text = "👀 B级 等突破确认"
    elif score >= 62:
        level = "C"
        text = "⚠️ C级 高风险观察"
    else:
        level = "D"
        text = "🚫 D级 不碰"

    # 套用强制降级
    rank = {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1}
    cap_text = {
        "B": "👀 B级 等突破确认",
        "C": "⚠️ C级 高风险观察",
        "D": "🚫 D级 不碰"
    }

    if forced_cap and rank[level] > rank[forced_cap]:
        text = cap_text[forced_cap]

    return text


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
# V18 核心新增：主力资金 / 假突破
# ============================================================

def accumulation_score(df):
    """V18 主力慢吸筹：低波动 + 温和增量 + OBV稳升。"""
    if df is None or len(df) < 80:
        return 0, []
    close = df["Close"]
    vol = df["Volume"]
    score = 0
    reasons = []

    volatility10 = float(close.pct_change().rolling(10).std().iloc[-1])
    if volatility10 < 0.025:
        score += 20
        reasons.append("低波动控盘")
    elif volatility10 < 0.04:
        score += 10
        reasons.append("波动收窄")

    vol_recent5 = float(vol.iloc[-5:].mean())
    vol_prev5 = float(vol.iloc[-10:-5].mean())
    if vol_prev5 > 0 and vol_recent5 > vol_prev5 * 1.08:
        score += 20
        reasons.append("成交量温和上升")
    elif vol_prev5 > 0 and vol_recent5 > vol_prev5:
        score += 10
        reasons.append("成交量小幅增加")

    obv = calc_obv(close, vol)
    obv_ma15 = float(obv.rolling(15).mean().iloc[-1])
    if float(obv.iloc[-1]) > obv_ma15:
        score += 20
        reasons.append("OBV站上15日均值")

    if float(obv.iloc[-1] - obv.iloc[-21]) > 0 and float(close.iloc[-1] - close.iloc[-21]) >= 0:
        score += 10
        reasons.append("价量同步改善")

    return max(0, min(70, round(score, 1))), reasons


def smart_money_flow_score(df):
    """V18 资金流确认：上涨日量能 > 下跌日量能，OBV20/60改善。"""
    if df is None or len(df) < 80:
        return 0, []
    close = df["Close"]
    vol = df["Volume"]
    score = 0
    reasons = []

    recent_close = close.iloc[-20:]
    recent_vol = vol.iloc[-20:]
    prev_close = close.shift(1).iloc[-20:]
    up_days = recent_close > prev_close
    down_days = recent_close < prev_close
    up_vol = float(recent_vol[up_days].sum())
    down_vol = float(recent_vol[down_days].sum())
    absorb_ratio = up_vol / down_vol if down_vol > 0 else 1

    if absorb_ratio >= 1.8:
        score += 30
        reasons.append("上涨量明显大于下跌量")
    elif absorb_ratio >= 1.5:
        score += 22
        reasons.append("上涨量大于下跌量")
    elif absorb_ratio >= 1.2:
        score += 12
        reasons.append("疑似吸筹")

    obv = calc_obv(close, vol)
    if float(obv.iloc[-1] - obv.iloc[-21]) > 0:
        score += 15
        reasons.append("OBV20资金流入")
    if float(obv.iloc[-1] - obv.iloc[-61]) > 0:
        score += 15
        reasons.append("OBV60资金流入")

    return max(0, min(60, round(score, 1))), reasons


def fake_breakout_score(df):
    """V18 假突破扣分：上影线、放量不涨、RSI过热、远离MA20。"""
    if df is None or len(df) < 80:
        return 0, []
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    open_ = df["Open"]
    vol = df["Volume"]

    score = 0
    reasons = []
    last = float(close.iloc[-1])
    day_open = float(open_.iloc[-1])
    day_high = float(high.iloc[-1])
    day_low = float(low.iloc[-1])
    day_range = day_high - day_low
    rsi = float(calc_rsi(close).iloc[-1])
    ma20 = float(close.rolling(20).mean().iloc[-1])
    avg_vol20 = float(vol.rolling(20).mean().iloc[-1])
    vol_ratio = float(vol.iloc[-1] / avg_vol20) if avg_vol20 > 0 else 0
    change1 = pct(last, float(close.iloc[-2]))
    high20_prev = float(high.iloc[-21:-1].max())

    if day_range > 0:
        upper_shadow_pct = ((day_high - max(last, day_open)) / day_range) * 100
        close_position = ((last - day_low) / day_range) * 100
    else:
        upper_shadow_pct = 0
        close_position = 50

    if upper_shadow_pct >= 40 and close_position < 60:
        score += 25
        reasons.append("上影线派发")
    elif upper_shadow_pct >= 30:
        score += 15
        reasons.append("上影线偏长")

    if day_high >= high20_prev * 0.995 and close_position < 55:
        score += 25
        reasons.append("突破失败/假突破")

    if vol_ratio >= 1.5 and change1 < 0.8:
        score += 20
        reasons.append("放量不涨")

    if rsi >= 78:
        score += 25
        reasons.append("RSI严重过热")
    elif rsi >= 75:
        score += 15
        reasons.append("RSI过热")

    if pct(last, ma20) >= 12:
        score += 15
        reasons.append("远离MA20")

    return max(0, min(100, round(score, 1))), reasons

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
# V18 提前爆发股扫描
# ============================================================

def score_prebreakout(symbol, df):
    """V18：横盘收窄 + 接近突破 + 主力吸筹 + 资金流确认 - 假突破扣分。"""
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

    score = 0
    reasons = []

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

    # 原本防追高扣分
    if rsi >= 75: score -= 25; reasons.append("RSI过热扣分")
    if change5 >= 12: score -= 20; reasons.append("5日涨幅过大扣分")
    if dist_ma20 >= 12: score -= 20; reasons.append("离MA20太远扣分")
    if range20 >= 25: score -= 15; reasons.append("波动过大扣分")
    if change20 >= 35: score -= 15; reasons.append("20日涨幅过大扣分")
    if green_days >= 4: score -= 8; reasons.append("连涨过多扣分")

    # V18 新增主力资金确认
    acc_score, acc_reasons = accumulation_score(df)
    smart_score, smart_reasons = smart_money_flow_score(df)
    fake_score, fake_reasons = fake_breakout_score(df)

    # ========================================================
    # V19.1 主力确认版：防分数通膨 + RVOL量能确认
    # ========================================================

    v19_score = (
        score * 0.55 +
        acc_score * 0.25 +
        smart_score * 0.30 -
        fake_score * 0.90
    )

    # RVOL 量能加权
    if vol_ratio >= 2.5:
        v19_score += 18
    elif vol_ratio >= 2.0:
        v19_score += 14
    elif vol_ratio >= 1.5:
        v19_score += 9
    elif vol_ratio >= 1.2:
        v19_score += 4
    elif vol_ratio < 1.0:
        v19_score -= 15

    # 过热扣分
    if rsi >= 78:
        v19_score -= 22
    elif rsi >= 75:
        v19_score -= 15
    elif rsi >= 70:
        v19_score -= 6

    # 离MA20太远扣分
    if dist_ma20 >= 12:
        v19_score -= 18
    elif dist_ma20 >= 9:
        v19_score -= 8

    # 连涨过多扣分
    if green_days >= 4:
        v19_score -= 12
    elif green_days >= 3:
        v19_score -= 6

    # 20日涨幅过大扣分
    if change20 >= 35:
        v19_score -= 12
    elif change20 >= 25:
        v19_score -= 6

    # 假突破风险强扣
    if fake_score >= 40:
        v19_score -= 15
    elif fake_score >= 25:
        v19_score -= 8

    v18_score = max(0, min(96, round(v19_score, 1)))

    if v18_score < 60:
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

    if v18_score >= 85:
        launch_time = "1-3天内可能爆发"
    elif v18_score >= 72:
        launch_time = "3-5天重点观察"
    else:
        launch_time = "等待放量突破"

    all_reasons = reasons[:7]
    v18_reasons = (acc_reasons + smart_reasons)[:6]
    risk_reasons = fake_reasons[:5]

    return {
        "symbol": symbol, "sector": find_sector(symbol), "score": v18_score, "base_score": round(score, 1),
        "accumulation_score": acc_score, "smart_money_score": smart_score, "fake_score": fake_score,
        "signal_level": prebreakout_level(
         v18_score,
         rsi=round(rsi, 1),
         vol_ratio=round(vol_ratio, 2),
         dist_ma20=round(dist_ma20, 2),
         fake_score=fake_score,
         green_days=green_days
        ),
        "price": round(last, 2), "breakout_price": breakout_price,
        "buy_low": buy_low, "buy_high": buy_high,
        "sl": sl, "tp1": tp1, "tp2": tp2, "rsi": round(rsi, 1), "vol_ratio": round(vol_ratio, 2),
        "absorb_ratio": round(absorb_ratio, 2), "change1": round(change1, 2), "change5": round(change5, 2),
        "change20": round(change20, 2), "dist_ma20": round(dist_ma20, 2), "range20": round(range20, 2),
        "range60": round(range60, 2), "obv20": round(obv20, 2), "obv60": round(obv60, 2),
        "green_days": green_days, "launch_time": launch_time, "reasons": all_reasons,
        "v18_reasons": v18_reasons, "risk_reasons": risk_reasons, "risk": risk
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
        sell_score = max(sell_score, 88); sell_reasons.append("V18强制卖出条件")
    sell_score = max(0, min(100, round(sell_score, 1)))
    if force_sell: sell_action = "🔥 强制卖出 / 高位派发"; sell_level = "V18强制卖出"
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
    results.sort(key=lambda x: (x["score"], x.get("smart_money_score", 0), -x.get("fake_score", 0)), reverse=True)
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
# V20 做T雷达：高流量 + 高波动 + VWAP 剧本
# ============================================================


def get_earnings_risk(symbol):
    """V22: 财报风险。yfinance日历有时缺资料；缺资料时不阻挡，只显示unknown。"""
    if not EVENT_RISK_ENABLED:
        return {"has_event_risk": False, "event_note": "事件过滤关闭", "earnings_date": None}
    try:
        cal = yf.Ticker(symbol).calendar
        dt = None
        if cal is None:
            return {"has_event_risk": False, "event_note": "财报资料暂无", "earnings_date": None}
        if isinstance(cal, dict):
            raw = cal.get("Earnings Date") or cal.get("EarningsDate")
            if isinstance(raw, (list, tuple)) and raw:
                dt = raw[0]
            else:
                dt = raw
        else:
            # DataFrame兼容
            try:
                if "Earnings Date" in cal.index:
                    dt = cal.loc["Earnings Date"].iloc[0]
            except Exception:
                pass
        if dt is None or str(dt).lower() in ["nan", "nat", "none"]:
            return {"has_event_risk": False, "event_note": "财报资料暂无", "earnings_date": None}
        d = pd.to_datetime(dt).date()
        diff = abs((d - now_et().date()).days)
        if diff <= EARNINGS_BLOCK_DAYS:
            return {"has_event_risk": True, "event_note": f"财报窗口{diff}天内", "earnings_date": str(d)}
        return {"has_event_risk": False, "event_note": f"财报日期 {d}", "earnings_date": str(d)}
    except Exception:
        return {"has_event_risk": False, "event_note": "财报资料读取失败", "earnings_date": None}


def calc_gap_pct(daily_df, current_price):
    try:
        if daily_df is None or len(daily_df) < 2:
            return 0
        prev_close = float(daily_df["Close"].iloc[-2])
        return round(pct(current_price, prev_close), 2)
    except Exception:
        return 0


def load_hot_pool():
    if not os.path.exists(HOT_POOL_FILE):
        return []
    try:
        with open(HOT_POOL_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("date") == str(now_et().date()):
            return data.get("symbols", [])
        return []
    except Exception:
        return []


def save_hot_pool(symbols):
    try:
        with open(HOT_POOL_FILE, "w", encoding="utf-8") as f:
            json.dump({"date": str(now_et().date()), "symbols": symbols, "updated_at": now_kl_str()}, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def scan_hot_pool():
    """V22: 自动热点池，轻量扫描全行业，找今天有量有波动的票。"""
    if not HOT_POOL_ENABLED:
        return []
    results = []
    symbols = ALL_SYMBOLS[:]
    for batch in chunk_list(symbols, BATCH_SIZE):
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futs = {}
            for sym in batch:
                futs[ex.submit(_score_hot_candidate, sym)] = sym
            for f in as_completed(futs):
                try:
                    r = f.result()
                    if r:
                        results.append(r)
                except Exception:
                    pass
        time.sleep(BATCH_SLEEP)
    results.sort(key=lambda x: (x["hot_score"], x["effective_rvol"], x["today_range"]), reverse=True)
    top = [x["symbol"] for x in results[:HOT_POOL_TOP_N]]
    save_hot_pool(top)
    return results[:HOT_POOL_TOP_N]


def _score_hot_candidate(symbol):
    daily = safe_download(symbol, "3mo", "1d")
    if daily is None or len(daily) < 30:
        return None
    intraday = safe_download_intraday(symbol, "5d", "5m", prepost=True)
    close = daily["Close"]; high = daily["High"]; low = daily["Low"]; vol = daily["Volume"]
    last = float(close.iloc[-1])
    if last < 5:
        return None
    avg_vol20 = float(vol.rolling(20).mean().iloc[-1])
    dollar_volume = last * avg_vol20
    if intraday is not None and not intraday.empty:
        today = _intraday_today_et(intraday)
        today_range = pct(float(today["High"].max()), float(today["Low"].min()))
        intraday_rvol = float(today["Volume"].sum()) / avg_vol20 if avg_vol20 > 0 else 0
    else:
        today_range = pct(float(high.iloc[-1]), float(low.iloc[-1]))
        intraday_rvol = float(vol.iloc[-1]) / avg_vol20 if avg_vol20 > 0 else 0
    avg_range20 = float(((high - low) / close * 100).rolling(20).mean().iloc[-1])
    effective_rvol = intraday_rvol
    if dollar_volume < HOT_POOL_MIN_DOLLAR_VOLUME or effective_rvol < HOT_POOL_MIN_RVOL or today_range < HOT_POOL_MIN_RANGE:
        return None
    hot_score = effective_rvol * 20 + today_range * 3 + min(dollar_volume / 100000000, 10)
    return {"symbol": symbol, "hot_score": round(hot_score, 1), "effective_rvol": round(effective_rvol, 2), "today_range": round(today_range, 2), "avg_range20": round(avg_range20, 2), "dollar_volume_m": round(dollar_volume/1000000, 1)}

def get_t_radar_symbols():
    """V23：核心观察池 + 自动热点池。总数仍控制在 T_MAX_STOCKS，避免看太多。"""
    load_watchlist()
    if T_RADAR_STOCKS_ENV:
        core = parse_symbols(T_RADAR_STOCKS_ENV)
    else:
        core = MY_STOCKS[:]
    hot = load_hot_pool() if HOT_POOL_ENABLED else []
    arr = []
    for s in core + hot:
        s = clean_symbol(s)
        if s and s not in BAD_SYMBOLS and s not in arr:
            arr.append(s)
    return arr[:max(1, T_MAX_STOCKS)]


def _intraday_today_et(df):
    """取最近一个交易日的5分钟数据，用来计算实时VWAP/ORB。"""
    if df is None or df.empty:
        return None
    try:
        x = df.copy()
        if getattr(x.index, "tz", None) is not None:
            x.index = x.index.tz_convert(TZ_ET)
        last_day = x.index[-1].date()
        today = x[x.index.date == last_day]
        return today if today is not None and not today.empty else x
    except Exception:
        return df


def calc_vwap_intraday(df):
    if df is None or df.empty:
        return None
    try:
        x = _intraday_today_et(df)
        if x is None or x.empty:
            return None
        typical = (x["High"] + x["Low"] + x["Close"]) / 3
        pv = typical * x["Volume"]
        total_vol = x["Volume"].replace(0, math.nan).cumsum()
        vwap = pv.cumsum() / total_vol
        last_vwap = float(vwap.dropna().iloc[-1])
        return round(last_vwap, 2)
    except Exception:
        return None


def calc_orb_levels(df, minutes=None):
    """V21 ORB：开盘前N分钟高低点。默认30分钟，5m线约6根K。"""
    if minutes is None:
        minutes = T_ORB_MINUTES
    if df is None or df.empty:
        return None
    try:
        x = _intraday_today_et(df)
        if x is None or x.empty or len(x) < 3:
            return None
        bars = max(1, int(minutes / 5))
        first = x.iloc[:bars]
        orb_high = float(first["High"].max())
        orb_low = float(first["Low"].min())
        last_price = float(x["Close"].iloc[-1])
        if last_price > orb_high:
            status = "突破ORB High"
        elif last_price < orb_low:
            status = "跌破ORB Low"
        else:
            status = "ORB区间内"
        return {
            "orb_high": round(orb_high, 2),
            "orb_low": round(orb_low, 2),
            "orb_status": status,
            "bars": len(x),
        }
    except Exception:
        return None


def t_level(score):
    if score >= 78:
        return "A 做T重点"
    if score >= 62:
        return "B 轻仓做T"
    if score >= 48:
        return "C 只观察"
    return "D 不做T"


def score_t_stock(symbol, daily_df, intraday_df=None, market_mode="NEUTRAL"):
    """V23 做T评分：严格A级 + 事件风控 + Gap过滤 + 动态热点池 + 双止损。"""
    if daily_df is None or len(daily_df) < 80:
        return None

    close = daily_df["Close"]
    high = daily_df["High"]
    low = daily_df["Low"]
    vol = daily_df["Volume"]

    daily_last = float(close.iloc[-1])
    current_price = daily_last
    today_intraday = _intraday_today_et(intraday_df)
    if today_intraday is not None and not today_intraday.empty:
        try:
            current_price = float(today_intraday["Close"].iloc[-1])
        except Exception:
            current_price = daily_last

    last = current_price
    if last < 3:
        return None

    gap_pct = calc_gap_pct(daily_df, last)
    event_risk = get_earnings_risk(symbol)

    ma10 = float(close.rolling(10).mean().iloc[-1])
    ma20 = float(close.rolling(20).mean().iloc[-1])
    ma50 = float(close.rolling(50).mean().iloc[-1])
    rsi = float(calc_rsi(close).iloc[-1])
    atr = calc_atr(daily_df).iloc[-1]
    if pd.isna(atr):
        atr = last * 0.04

    avg_vol20 = float(vol.rolling(20).mean().iloc[-1])
    vol_ratio = float(vol.iloc[-1] / avg_vol20) if avg_vol20 > 0 else 0
    dollar_volume = last * avg_vol20
    avg_range20 = float(((high - low) / close * 100).rolling(20).mean().iloc[-1])

    if today_intraday is not None and not today_intraday.empty:
        today_high = float(today_intraday["High"].max())
        today_low = float(today_intraday["Low"].min())
        today_volume = float(today_intraday["Volume"].sum())
        today_range = pct(today_high, today_low)
        intraday_rvol = today_volume / avg_vol20 if avg_vol20 > 0 else 0
    else:
        today_high = float(high.iloc[-1])
        today_low = float(low.iloc[-1])
        today_range = pct(today_high, today_low)
        intraday_rvol = vol_ratio

    change5 = pct(last, float(close.iloc[-6]))
    dist_ma20 = pct(last, ma20)

    obv = calc_obv(close, vol)
    obv20 = float(obv.iloc[-1] - obv.iloc[-21])

    vwap = calc_vwap_intraday(intraday_df)
    dist_vwap = pct(last, vwap) if vwap else None
    orb = calc_orb_levels(intraday_df)
    orb_high = orb.get("orb_high") if orb else None
    orb_low = orb.get("orb_low") if orb else None
    orb_status = orb.get("orb_status") if orb else "暂无ORB"

    support_20 = float(low.iloc[-20:].min())
    resistance_20 = float(high.iloc[-20:].max())
    support_5 = float(low.iloc[-5:].min())
    resistance_5 = float(high.iloc[-5:].max())

    support_candidates = [support_5, ma20 * 0.985]
    if orb_low:
        support_candidates.append(float(orb_low))
    support = round(max([x for x in support_candidates if x > 0]), 2)

    resistance_candidates = [resistance_20, last + atr * 2.0]
    if orb_high:
        resistance_candidates.append(float(orb_high))
    resistance = round(min([x for x in resistance_candidates if x > 0]), 2)

    # 做T低吸区：优先围绕VWAP和日内支撑；不是现价追高区
    if vwap:
        dip_low = round(max(min(vwap, support) - atr * 0.20, last * 0.94), 2)
        dip_high = round(max(vwap + atr * 0.15, support), 2)
        if dip_high > last and dist_vwap is not None and dist_vwap < 1.2:
            dip_high = round(last * 0.998, 2)
    else:
        dip_low = round(max(support, last - atr * 0.7), 2)
        dip_high = round(last - atr * 0.2, 2)

    sell_low = round(max(last + atr * 0.65, resistance - atr * 0.35), 2)
    sell_high = round(max(sell_low, resistance), 2)

    # V21 双止损：做T止损近，波段止损远
    if vwap:
        vwap_stop = vwap * (1 - T_VWAP_STOP_BUFFER / 100)
        pct_stop = last * (1 - T_DAYTRADE_STOP_PCT / 100)
        daytrade_stop = round(max(vwap_stop, pct_stop), 2) if last >= vwap else round(pct_stop, 2)
    else:
        daytrade_stop = round(last * (1 - T_DAYTRADE_STOP_PCT / 100), 2)
    if orb_low and last >= orb_low:
        daytrade_stop = round(max(daytrade_stop, float(orb_low) * 0.995), 2)

    swing_stop = round(min(support * 0.985, last - atr * 0.9), 2)
    structure_stop = swing_stop  # 兼容旧字段 stop_loss

    score = 0
    reasons = []
    warnings = []

    # 1) 波动：做T的生命线
    if avg_range20 >= 6:
        score += 25; reasons.append("高波动")
    elif avg_range20 >= T_MIN_AVG_RANGE:
        score += 18; reasons.append("波动足够")
    elif avg_range20 >= 2:
        score += 8; reasons.append("波动一般")
    else:
        score -= 15; warnings.append("振幅太小")

    # 2) 流动性：避免滑价
    if dollar_volume >= 500_000_000:
        score += 22; reasons.append("成交额强")
    elif dollar_volume >= T_MIN_DOLLAR_VOLUME:
        score += 16; reasons.append("流动性足够")
    elif dollar_volume >= 30_000_000:
        score += 6; warnings.append("流动性一般")
    else:
        score -= 20; warnings.append("成交额不足")

    # 3) 趋势过滤：强趋势回踩 > 弱势猜底
    if last > ma20 and ma20 > ma50:
        score += 18; reasons.append("主升趋势")
    elif last > ma20:
        score += 10; reasons.append("站上MA20")
    else:
        score -= 12; warnings.append("仍在MA20下方")

    # 4) VWAP：日内做T关键
    if dist_vwap is not None:
        if -0.8 <= dist_vwap <= 1.5:
            score += 22; reasons.append("贴近VWAP，容易做T")
        elif 1.5 < dist_vwap <= 3.5:
            score += 8; warnings.append("离VWAP偏高，等回踩")
        elif dist_vwap < -0.8:
            score -= 12; warnings.append("跌破VWAP偏弱")
        else:
            score -= 16; warnings.append("远离VWAP，容易追高")
    else:
        warnings.append("盘中VWAP暂无数据")

    # 5) ORB开盘区间：过滤假启动
    if orb_high and orb_low:
        if orb_status == "突破ORB High" and (not vwap or last >= vwap):
            score += 12; reasons.append("突破ORB High")
        elif orb_status == "跌破ORB Low":
            score -= 15; warnings.append("跌破ORB Low")
        else:
            reasons.append("ORB区间观察")

    # 6) 资金/量能
    effective_rvol = max(vol_ratio, intraday_rvol)
    if effective_rvol >= 2:
        score += 14; reasons.append("RVOL强")
    elif effective_rvol >= 1.3:
        score += 8; reasons.append("量能增加")
    elif effective_rvol < 0.8:
        score -= 8; warnings.append("量能不足")

    if obv20 > 0:
        score += 8; reasons.append("OBV资金流入")

    # 7) RSI：避免太冷/太热
    if 40 <= rsi <= 68:
        score += 10; reasons.append("RSI健康")
    elif rsi > 75:
        score -= 18; warnings.append("RSI过热")
    elif rsi < 32:
        score -= 10; warnings.append("弱势超跌，不急接")

    # 8) 市场环境
    if market_mode == "BULLISH":
        score += 8; reasons.append("大盘顺风")
    elif market_mode == "DEFENSIVE":
        score -= 15; warnings.append("大盘防守")

    # 风险强制降级
    if avg_range20 < T_MIN_AVG_RANGE or dollar_volume < T_MIN_DOLLAR_VOLUME:
        score = min(score, 58)
    if last < ma20 and market_mode == "DEFENSIVE":
        score = min(score, 45)
    if rsi >= 78:
        score = min(score, 60)
    if dist_vwap is not None and dist_vwap >= 6:
        score = min(score, 62)
    if orb_status == "跌破ORB Low":
        score = min(score, 48)

    # V21.5 更严格做T降级：避免“高分但无量/追高/ORB失败”
    strict_notes = []
    if effective_rvol < T_NO_TRADE_RVOL:
        score = min(score, 45)
        strict_notes.append("RVOL极低，禁止做T")
    elif effective_rvol < T_B_MIN_RVOL:
        score = min(score, 58)
        strict_notes.append("RVOL低于B级标准")

    if dist_vwap is not None:
        if dist_vwap > T_NO_CHASE_DIST_VWAP:
            score = min(score, 55)
            strict_notes.append("离VWAP太远，只等回踩")
        elif dist_vwap > T_A_MAX_DIST_VWAP:
            score = min(score, 70)
            strict_notes.append("不符合A级VWAP距离")

    if orb_status == "跌破ORB Low":
        score = min(score, 42)
        strict_notes.append("ORB失败，不做T")

    # A级必须：量能够、贴近VWAP、没有跌破ORB。否则最多B/C。
    a_qualified = (
        effective_rvol >= T_A_MIN_RVOL
        and (dist_vwap is not None and abs(dist_vwap) <= T_A_MAX_DIST_VWAP)
        and orb_status != "跌破ORB Low"
        and rsi < 75
        and last >= ma20
        and not event_risk.get("has_event_risk")
        and abs(gap_pct) < GAP_RISK_PCT
    )
    if not a_qualified:
        score = min(score, 74)

    # V22/V23 事件风险：财报窗口 / 大幅Gap 优先降级
    event_notes = []
    if event_risk.get("has_event_risk"):
        score = min(score, 58)
        event_notes.append(event_risk.get("event_note", "财报事件风险"))
    if abs(gap_pct) >= GAP_RISK_PCT:
        score = min(score, 55)
        event_notes.append(f"Gap {gap_pct}% 超过风控线")
    if gap_pct <= -GAP_RISK_PCT and last < ma20:
        score = min(score, 42)
        event_notes.append("大幅低开且跌破MA20，不接飞刀")

    if strict_notes:
        warnings.extend(strict_notes)
    if event_notes:
        warnings.extend(event_notes)

    score = max(0, min(100, round(score, 1)))
    level = t_level(score)

    # V21.5 动作信号：先风控，再机会；不允许“低量追高”
    if event_risk.get("has_event_risk") or abs(gap_pct) >= GAP_RISK_PCT:
        action_signal = "⚠️ 事件/GAP风险"
        strategy = "有财报窗口或大幅Gap风险，不给A级；只允许观察或极轻仓，先等盘中结构稳定。"
    elif last <= daytrade_stop or orb_status == "跌破ORB Low" or (vwap and last < vwap and effective_rvol >= 1.2):
        action_signal = "🔴 日内止损/不做T"
        strategy = "日内结构转弱，先退出或不碰；等重新站回VWAP再评估。"
    elif dist_vwap is not None and dist_vwap > T_NO_CHASE_DIST_VWAP:
        action_signal = "🟠 远离VWAP/只等回踩"
        strategy = "价格离VWAP太远，禁止追高；只等回踩VWAP或支撑确认。"
    elif effective_rvol < T_NO_TRADE_RVOL:
        action_signal = "⚪ 量能太低/不做T"
        strategy = "量能太低，容易假动作；今天不优先做T。"
    elif score >= 78 and a_qualified:
        action_signal = "🟢 A级低吸确认"
        strategy = "满足A级条件：量能、VWAP、趋势都合格；回踩不破可主动做T，拉近高抛区分批卖。"
    elif score >= 62 and dist_vwap is not None and -1.0 <= dist_vwap <= 2.2 and effective_rvol >= T_B_MIN_RVOL:
        action_signal = "🟡 B级轻仓低吸"
        strategy = "只做轻仓低吸高抛，不追突破；跌破做T止损马上退出。"
    elif last >= sell_low or (dist_vwap is not None and dist_vwap >= 2.8):
        action_signal = "🟠 接近高抛/等回踩"
        strategy = "价格已偏离VWAP，适合分批卖或等回踩，不适合追。"
    elif score >= 48:
        action_signal = "👀 C级等确认"
        strategy = "先观察，等量能/VWAP/趋势确认后再做。"
    else:
        action_signal = "⚪ D级不做T"
        strategy = "不做T，容易越做越亏。"

    return {
        "symbol": symbol,
        "sector": find_sector(symbol),
        "price": round(last, 2),
        "daily_last": round(daily_last, 2),
        "t_score": score,
        "t_level": level,
        "action_signal": action_signal,
        "a_qualified": a_qualified,
        "strict_notes": strict_notes,
        "strategy": strategy,
        "market_mode": market_mode,
        "gap_pct": gap_pct,
        "event_risk": event_risk,
        "avg_range20": round(avg_range20, 2),
        "today_range": round(today_range, 2),
        "dollar_volume_m": round(dollar_volume / 1_000_000, 1),
        "vol_ratio": round(vol_ratio, 2),
        "intraday_rvol": round(intraday_rvol, 2),
        "effective_rvol": round(effective_rvol, 2),
        "vwap": vwap,
        "dist_vwap": round(dist_vwap, 2) if dist_vwap is not None else None,
        "orb_high": orb_high,
        "orb_low": orb_low,
        "orb_status": orb_status,
        "rsi": round(rsi, 1),
        "change5": round(change5, 2),
        "dist_ma20": round(dist_ma20, 2),
        "ma20": round(ma20, 2),
        "support": support,
        "resistance": resistance,
        "dip_low": dip_low,
        "dip_high": dip_high,
        "sell_low": sell_low,
        "sell_high": sell_high,
        "daytrade_stop": daytrade_stop,
        "swing_stop": swing_stop,
        "stop_loss": structure_stop,
        "reasons": reasons[:7],
        "warnings": warnings[:7],
    }

def scan_t_radar():
    symbols = get_t_radar_symbols()
    mode = market_regime()
    results = []
    for symbol in symbols:
        daily = safe_download(symbol, "6mo", "1d")
        intraday = safe_download_intraday(symbol, "5d", "5m", prepost=True)
        r = score_t_stock(symbol, daily, intraday, market_mode=mode)
        if r:
            pos = calc_position_info(symbol, r["price"], suggested_sl=r.get("daytrade_stop"), tp1=r.get("sell_low"), tp2=r.get("sell_high"))
            r["position"] = pos
            results.append(r)
        time.sleep(0.15)
    results.sort(key=lambda x: (x["t_score"], x["vol_ratio"], x["avg_range20"]), reverse=True)
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
    lines = [title.replace("V18", "V19.1"), now_kl_str(), ""]

    if not arr:
        lines.append("暂无主力确认股")
        return "\n".join(lines)

    for i, r in enumerate(arr, start=1):
        lines.append(f"{i}. {r['symbol']}｜{r['score']}分｜{r.get('signal_level', '-')}")
        lines.append(f"价：{r['price']}")
        lines.append(f"突破：{r['breakout_price']}")
        lines.append(f"埋伏：{r['buy_low']} - {r['buy_high']}")
        lines.append(f"SL：{r['sl']}")
        lines.append(f"TP1：{r['tp1']}｜TP2：{r['tp2']}")
        lines.append("")
        lines.append(f"主力确认：慢吸筹 {r.get('accumulation_score','-')}｜资金流 {r.get('smart_money_score','-')}")
        lines.append(f"风险：假突破 {r.get('fake_score','-')}｜RVOL {r['vol_ratio']}｜RSI {r['rsi']}")
        lines.append(f"5日：{r['change5']}%｜20日：{r['change20']}%｜离MA20：{r['dist_ma20']}%｜连涨：{r.get('green_days','-')}天")
        lines.append(f"启动判断：{r['launch_time']}")
        lines.append("")

        if r.get("reasons"):
            lines.append("结构：")
            lines.append(" / ".join(r["reasons"]))

        if r.get("v18_reasons"):
            lines.append("主力逻辑：")
            lines.append(" / ".join(r["v18_reasons"]))

        if r.get("risk_reasons"):
            lines.append("风险提醒：")
            lines.append(" / ".join(r["risk_reasons"]))

        risk = r.get("risk")
        if risk:
            lines.append("")
            lines.append(f"仓位：{risk['shares']}股｜约${risk['position_value']}｜最大亏损约${risk['max_loss']}")

        lines.append("")
        lines.append("━━━━━━━━━━━━")
        lines.append("")

    lines.append("V19.1重点：S级/A级才是主力确认，B级等突破，C级只观察。")
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



def build_t_radar_text(title, arr):
    lines = [title.replace("V20", "V21.5").replace("V21", "V21.5"), now_kl_str(), ""]
    if not arr:
        lines.append("暂无做T数据")
        return "\n".join(lines)

    for i, r in enumerate(arr, start=1):
        lines.append(f"{i}. {r['symbol']}｜做T分 {r['t_score']}｜{r['t_level']}｜{r.get('action_signal','-')}")
        lines.append(f"价 {r['price']}｜VWAP {r.get('vwap','-')}｜离VWAP {r.get('dist_vwap','-')}%")
        lines.append(f"ORB：{r.get('orb_status','-')}｜高 {r.get('orb_high','-')}｜低 {r.get('orb_low','-')}")
        ev = r.get("event_risk", {}) or {}
        lines.append(f"A级合格：{'是' if r.get('a_qualified') else '否'}｜严格提醒：{' / '.join(r.get('strict_notes', [])) if r.get('strict_notes') else '-'}")
        lines.append(f"事件/GAP：{ev.get('event_note','-')}｜Gap {r.get('gap_pct','-')}%")
        lines.append(f"20日均振幅 {r['avg_range20']}%｜今日振幅 {r['today_range']}%｜RVOL {r.get('effective_rvol', r['vol_ratio'])}｜成交额约 ${r['dollar_volume_m']}M")
        lines.append(f"RSI {r['rsi']}｜5日 {r['change5']}%｜离MA20 {r['dist_ma20']}%｜市场 {r['market_mode']}")
        lines.append(f"低吸区：{r['dip_low']} - {r['dip_high']}")
        lines.append(f"高抛区：{r['sell_low']} - {r['sell_high']}")
        lines.append(f"做T止损：{r.get('daytrade_stop','-')}｜波段止损：{r.get('swing_stop','-')}")
        lines.append(f"支撑 {r['support']}｜压力 {r['resistance']}")
        lines.append(f"策略：{r['strategy']}")
        if r.get("position"):
            p = r["position"]
            pnl_icon = "🟢" if p["pnl_pct"] >= 0 else "🔴"
            lines.append(f"持仓：{p['shares']}股｜成本 {p['avg_price']}｜{pnl_icon}盈亏 {p['pnl_pct']}% / ${p['pnl_amount']}")
            if p.get("alerts"):
                lines.append("持仓提醒：" + " / ".join(p["alerts"]))
        if r.get("reasons"):
            lines.append("加分：" + " / ".join(r["reasons"]))
        if r.get("warnings"):
            lines.append("提醒：" + " / ".join(r["warnings"]))
        lines.append("")
        lines.append("━━━━━━━━━━━━")
        lines.append("")

    lines.append("V23做T规则：先看事件/GAP风险，再看市场环境、RVOL、VWAP、ORB。A级可重点，B级轻仓，C级观察，D级不做。")
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
    send_telegram(build_text("🚀 V18 防追高盘前 Top5", top))
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
    send_telegram(build_text("🌙 V18 防追高收盘预备股", top))
    return {"status": "ok", "top": top}


def run_prebreakout(force=False):
    global LAST_PREBREAKOUT_SIGNATURE
    if is_weekend_et(): return {"status": "skip"}
    results = scan_prebreakout()
    if not results: return {"status": "empty"}
    top = results[:10]
    sig = "|".join([f"{x['symbol']}:{x['score']}:{x['price']}:{x.get('fake_score',0)}" for x in top])
    if (not force) and sig == LAST_PREBREAKOUT_SIGNATURE:
        return {"status": "same", "top": top}
    LAST_PREBREAKOUT_SIGNATURE = sig; save_state()
    send_telegram(build_prebreakout_text("🚀 V19.1 主力确认 Top10", top))
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
    send_telegram(build_text("📦 V18 底部吸筹 Top10", top))
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
        send_telegram(build_watchlist_text("📊 V18 实盘自选股买卖监控", results))
        return {"status": "ok", "results": results}
    return {"status": "same_or_no_signal", "results": results}




def run_hot_pool(force=True):
    results = scan_hot_pool()
    if not results:
        send_telegram("🔥 V23 今日热点池\n" + now_kl_str() + "\n暂无符合条件热点股")
        return {"status": "empty", "hot_pool": []}
    lines = ["🔥 V23 今日自动热点池", now_kl_str(), ""]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['symbol']}｜热度 {r['hot_score']}｜RVOL {r['effective_rvol']}｜今日振幅 {r['today_range']}%｜成交额 ${r['dollar_volume_m']}M")
    lines.append("")
    lines.append("规则：热点池只做轻仓确认，不熟悉的票第一天只观察，第二天仍有量才提高优先级。")
    send_telegram("\n".join(lines))
    return {"status": "ok", "hot_pool": results}


def build_trade_summary_text():
    summary = summarize_trades()
    lines = ["📘 V23 交易数据库总结", now_kl_str(), ""]
    lines.append(f"交易记录：{summary['total_trades']}笔｜已闭合：{summary['closed_rounds']}次")
    lines.append(f"胜率：{summary['win_rate']}%｜已实现盈亏：${summary['realized_pnl']}")
    if summary.get("by_symbol"):
        lines.append("")
        lines.append("按股票：")
        for sym, d in sorted(summary["by_symbol"].items(), key=lambda x: x[1].get("pnl",0), reverse=True)[:8]:
            lines.append(f"{sym}: ${d['pnl']}｜{d['trades']}笔")
    if summary.get("by_mode"):
        lines.append("")
        lines.append("按模式：")
        for mode, d in sorted(summary["by_mode"].items(), key=lambda x: x[1].get("pnl",0), reverse=True):
            lines.append(f"{mode}: ${d['pnl']}｜{d['trades']}笔")
    lines.append("")
    lines.append("用途：记录每次买卖原因，长期找出你最赚钱和最容易亏的模式。")
    return "\n".join(lines)

def run_t_radar(force=False):
    global LAST_T_RADAR_SIGNATURE
    if is_weekend_et():
        return {"status": "skip", "watchlist": get_t_radar_symbols()}
    results = scan_t_radar()
    if not results:
        return {"status": "empty", "watchlist": get_t_radar_symbols()}
    top = results[:10]
    sig = "|".join([f"{x['symbol']}:{x['t_score']}:{x['price']}:{x.get('vwap','-')}" for x in top])
    if (not force) and sig == LAST_T_RADAR_SIGNATURE:
        return {"status": "same", "top": top}
    LAST_T_RADAR_SIGNATURE = sig
    save_state()
    send_telegram(build_t_radar_text("⚡ V23 专业做T雷达 Top10", top))
    return {"status": "ok", "top": top, "watchlist": get_t_radar_symbols()}

# ============================================================
# Scheduler
# ============================================================

def scheduler_loop():
    weekdays = [schedule.every().monday, schedule.every().tuesday, schedule.every().wednesday, schedule.every().thursday, schedule.every().friday]
    for d in weekdays:
        d.at("20:35").do(run_hot_pool)
        d.at("20:45").do(run_prebreakout)
        d.at("21:00").do(run_premarket)
        d.at("22:05").do(run_watchlist)
        d.at("22:10").do(run_t_radar)
    schedule.every().day.at("04:20").do(run_close)
    schedule.every().day.at("04:35").do(run_bottom)
    schedule.every().day.at("04:40").do(run_prebreakout)
    schedule.every().day.at("04:50").do(run_watchlist)
    schedule.every().day.at("04:55").do(run_t_radar)
    schedule.every().day.at("05:05").do(lambda: send_telegram(build_trade_summary_text()))
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
    return "V23 Professional T-Radar + Event Risk + Hot Pool + Trade Journal Running"

@app.route("/health")
def health():
    load_watchlist()
    return jsonify({"status": "healthy", "time_kl": now_kl_str(), "symbols": len(ALL_SYMBOLS), "my_stocks": MY_STOCKS, "positions_count": len(load_positions()), "version": "V23_event_hotpool_trade_journal"})

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


@app.route("/run-t-radar")
def route_t_radar_run():
    force = request.args.get("force", "1") == "1"
    return jsonify(run_t_radar(force=force))

@app.route("/run-tradar")
def route_t_radar_alias():
    force = request.args.get("force", "1") == "1"
    return jsonify(run_t_radar(force=force))

@app.route("/run-t-live")
def route_t_live_alias():
    force = request.args.get("force", "1") == "1"
    return jsonify(run_t_radar(force=force))


@app.route("/run-t-strict")
def route_t_strict_alias():
    force = request.args.get("force", "1") == "1"
    return jsonify(run_t_radar(force=force))

@app.route("/run-hot-pool")
def route_hot_pool_run():
    return jsonify(run_hot_pool(force=True))

@app.route("/hot-pool")
def route_hot_pool_info():
    return jsonify({"date": str(now_et().date()), "hot_pool": load_hot_pool(), "usage_run": "/run-hot-pool"})

@app.route("/trade/add")
def route_trade_add():
    symbol = clean_symbol(request.args.get("symbol", ""))
    side = request.args.get("side", "").upper()
    shares = request.args.get("shares", "")
    price = request.args.get("price", "")
    reason = request.args.get("reason", "")
    mode = request.args.get("mode", "T")
    fees = request.args.get("fees", "0")
    if not symbol or side not in ["BUY", "SELL", "B", "S"] or not shares or not price:
        return jsonify({"status": "error", "message": "missing/invalid params", "example": "/trade/add?symbol=IONQ&side=BUY&shares=10&price=50&mode=VWAP&reason=vwap_pullback"}), 400
    try:
        trade = add_trade_record(symbol, side, int(float(shares)), float(price), reason=reason, mode=mode, fees=float(fees))
        return jsonify({"status": "ok", "trade": trade})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route("/trade/summary")
def route_trade_summary():
    return jsonify(summarize_trades())

@app.route("/trade/report")
def route_trade_report():
    text = build_trade_summary_text()
    send_telegram(text)
    return jsonify({"status": "ok", "summary": summarize_trades()})

@app.route("/t-radar")
def route_t_radar_info():
    return jsonify({
        "count": len(get_t_radar_symbols()),
        "t_radar_stocks": get_t_radar_symbols(),
        "usage_run": "/run-t-radar",
        "usage_live": "/run-t-live",
        "usage_strict": "/run-t-strict",
        "usage_hot_pool": "/run-hot-pool",
        "usage_trade_add": "/trade/add?symbol=IONQ&side=BUY&shares=10&price=50&mode=VWAP&reason=vwap_pullback",
        "usage_trade_summary": "/trade/summary",
        "hot_pool": load_hot_pool(),
        "usage_set_watchlist": "/watchlist/set?symbols=APP,AFRM,IONQ,LUNR,PLTR,NVDA,SOUN,TSLA",
        "env_optional": "T_RADAR_STOCKS=APP,AFRM,IONQ,LUNR,PLTR,NVDA,SOUN,TSLA"
    })

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

@app.route("/b/<symbol>/<price>/<qty>")
@app.route("/b/<symbol>/<price>/<qty>/<setup>")
def quick_buy(symbol, price, qty, setup="manual"):

    trade = {
        "symbol": symbol.upper(),
        "side": "BUY",
        "price": float(price),
        "qty": int(qty),
        "setup": setup,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    trades.append(trade)

    msg = (
        f"🟢 买入记录\n"
        f"{symbol.upper()} | {qty}股 @ ${price}\n"
        f"Setup: {setup}"
    )

    send_telegram(msg)

    return jsonify({
        "ok": True,
        "trade": trade
    })

@app.route("/s/<symbol>/<price>/<qty>")
def quick_sell(symbol, price, qty):

    trade = {
        "symbol": symbol.upper(),
        "side": "SELL",
        "price": float(price),
        "qty": int(qty),
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    trades.append(trade)

    msg = (
        f"🔴 卖出记录\n"
        f"{symbol.upper()} | {qty}股 @ ${price}"
    )

    send_telegram(msg)

    return jsonify({
        "ok": True,
        "trade": trade
    })
 
@app.route("/position/clear")
def route_position_clear():
    save_positions({})
    return jsonify({"status": "ok", "message": "all positions cleared"})

@app.route("/sectors")
def route_sectors(): return jsonify({"count": len(SECTOR_POOLS), "symbols": len(ALL_SYMBOLS), "sectors": SECTOR_POOLS})

@app.route("/api/test-telegram")
def route_test():
    ok = send_telegram("✅ V23 Professional T-Radar Telegram Test Success")
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
