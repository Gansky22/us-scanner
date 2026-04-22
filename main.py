# ============================================================
# 美股爆发扫描器 V11.2 + V11.5 智能买点版
# Railway 付费版 / Flask / Telegram
#
# 功能：
# 1) 盘前扫描：Top 5 + 预备股 5
# 2) 盘中突破提醒
# 3) 收盘明日预备股
# 4) 500股扩容池
# 5) V11.2 过滤过热股 / 假突破 / 涨太多
# 6) V11.5 智能买点：可追突破 / 等回踩买 / 不要追
# ============================================================

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
from flask import Flask, jsonify

app = Flask(__name__)

# ============================================================
# ENV
# ============================================================
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
PORT = int(os.getenv("PORT", "5000"))

MAX_WORKERS = int(os.getenv("MAX_WORKERS", "20"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "50"))
BATCH_SLEEP = float(os.getenv("BATCH_SLEEP", "1.2"))

TELEGRAM_MAX_LEN = 3500

TZ_KL = pytz.timezone("Asia/Kuala_Lumpur")
TZ_ET = pytz.timezone("US/Eastern")

STATE_FILE = "scanner_state.json"

LAST_PREMARKET_SIGNATURE = ""
LAST_CLOSE_SIGNATURE = ""
LAST_MARKET_REGIME = ""
INTRADAY_BREAKOUT_SENT = set()

# ============================================================
# 500股扩容版股票池
# ============================================================
SECTOR_POOLS = {
    "AI_软件": [
        "PLTR","SOUN","BBAI","AI","PATH","U","APP","CRM","NOW","MDB",
        "SNOW","DDOG","NET","HUBS","TEAM","ASAN","BOX","ESTC","CFLT","DOCN",
        "GTLB","DUOL","SPOT","ADBE","ORCL","SAP","WDAY","INTU","SMAR","S",
        "RBRK","PEGA","PAYC","TWLO","ZI","FIVN","BILL","TOST","PCOR","PCTY"
    ],
    "半导体": [
        "NVDA","AMD","AVGO","QCOM","MU","MRVL","ON","ARM","AMAT","LRCX",
        "KLAC","ADI","NXPI","MCHP","TXN","SWKS","QRVO","MPWR","ENTG","COHR",
        "ONTO","AEHR","ACLS","GFS","TSM","INTC","WOLF","LSCC","ALAB","SMTC",
        "SITM","CRUS","FORM","POWI","NVTS"
    ],
    "量子": [
        "IONQ","RGTI","QBTS","QUBT","ARQQ","IBM","HON","GOOG","MSFT","AMZN"
    ],
    "太空_卫星_航天": [
        "LUNR","RKLB","ASTS","RDW","PL","SPIR","SATL","BA","LHX","NOC",
        "KTOS","AVAV","IRDM","GSAT","VSAT","MDAI","MNTS","BKSY","ACHR","JOBY"
    ],
    "金融科技_支付_交易所": [
        "HOOD","SOFI","AFRM","COIN","PYPL","SQ","ALLY","NU","UPST","LC",
        "PAGS","FOUR","BILL","TOST","INTU","MQ","GPN","FIS","FI","JEF",
        "SCHW","IBKR","ICE","CME","NDAQ","MKTX","RKT","TREE","ENV","EXFY",
        "DLO","STNE","MELI"
    ],
    "网络安全": [
        "CRWD","PANW","ZS","OKTA","S","FTNT","CYBR","RBRK","TENB","GEN",
        "CHKP","QLYS","VRNS","NET","AKAM","DDOG","RSKD","BLZE","SAIL","OSPN",
        "CSCO","INFY"
    ],
    "电动车_新能源_电网": [
        "TSLA","RIVN","LCID","NIO","XPEV","LI","QS","ENVX","BE","PLUG",
        "FSLR","ENPH","SEDG","ARRY","RUN","CHPT","BLNK","EVGO","SLDP","STEM",
        "SHLS","NXT","FLNC","AESI","NEE","GEV","VRT","ETN","HUBB","PWR",
        "CEG","SMR","OKLO","NNE","CCJ","UUUU","LEU","BWXT","GE"
    ],
    "生物科技_医疗成长": [
        "MRNA","VRTX","REGN","ALNY","EXEL","BEAM","CRSP","NTLA","RXRX","DNA",
        "SANA","NTRA","ILMN","BNTX","ARWR","NBIX","INSM","HALO","SRPT","VKTX",
        "HIMS","TEM","ABCL","GH","TWST","PACB","TXG","EDIT","ADMA","OMER",
        "XENE","CYTK","MDGL","AKRO","MRUS","ARGX","ROIV","TGTX","ALKS"
    ],
    "加密概念_矿股": [
        "MSTR","COIN","MARA","RIOT","CLSK","CIFR","IREN","HUT","BTDR","BITF",
        "WULF","CORZ","CAN","BTBT","GREE","HOOD","SQ","PYPL"
    ],
    "云计算_SaaS": [
        "CRM","NOW","SNOW","DDOG","NET","MDB","HUBS","TEAM","ASAN","BOX",
        "WDAY","INTU","ADBE","ORCL","SAP","DOCN","CFLT","ZI","SMAR","ESTC",
        "PCTY","PAYC","PCOR","APPF","BL","U","SHOP","BIGC","WIX","BAND",
        "TWLO","FIVN","SPSC","HCP","FRSH","MNDY","GTLB","DUOL","PATH"
    ],
    "消费成长_平台_品牌": [
        "AMZN","MELI","ONON","CELH","CAVA","CMG","LULU","ABNB","DASH","RBLX",
        "ETSY","PINS","ROKU","DUOL","SPOT","NFLX","AEO","ANF","DECK","NKE",
        "ADDYY","BIRK","ELF","ULTA","CROX","YETI","DKS","WING","SBUX","COST",
        "WMT","TGT","CVNA","KMX","TXRH","SG","CART","BKNG","EXPE","LYFT"
    ],
    "工业_国防_基建": [
        "GE","GEV","RTX","LMT","NOC","GD","BA","KTOS","AVAV","HON",
        "CAT","DE","URI","PH","ETN","PWR","VRT","HWM","TDG","CW",
        "IR","XYL","EMR","ROK","JCI","HUBB","TT","PCAR","NSC","UNP",
        "CSX","JBHT","EXPD","CHRW","WSC","WAB","GWW","FAST","ODFL","SAIA"
    ],
    "大盘科技_通信": [
        "AAPL","MSFT","META","AMZN","GOOGL","NFLX","TSLA","NVDA","AMD","AVGO",
        "ORCL","ADBE","CRM","INTC","QCOM","CSCO","TMUS","T","VZ","ARM",
        "UBER","LYFT","SPOT","SNAP","PINS","RDDT","SHOP","SE","BABA","JD",
        "PDD","BIDU","NTES","TME","ZM"
    ],
    "小盘成长_高波动": [
        "JOBY","ACHR","BBAI","SOUN","UPST","LMND","OPEN","RUN","APP","GCT",
        "ASTS","ENVX","TOST","CELH","DUOL","RKLB","LUNR","IONQ","RGTI","QBTS",
        "QUBT","SMR","OKLO","NNE","TEM","HIMS","VKTX","ALAB","AEHR","ACLS",
        "STNE","DLO","CAVA","ELF","BIRK","CORT","PRCH","RUM","GRAB","HSAI"
    ],
    "金融传统_银行_保险": [
        "JPM","BAC","WFC","C","GS","MS","AXP","BLK","BX","KKR",
        "AIG","PGR","CB","TRV","ALL","MET","PRU","MMC","AJG","BRO",
        "USB","PNC","TFC","COF","DFS","MCO","SPGI","CBRE","AON","RJF"
    ],
    "能源_油气_LNG": [
        "XOM","CVX","COP","SLB","HAL","BKR","EOG","DVN","OXY","MPC",
        "VLO","PSX","KMI","WMB","LNG","CTRA","FANG","EQT","AR","APA",
        "MRO","NOV","RRC","TPL"
    ],
    "材料_金属_矿业": [
        "FCX","NUE","STLD","X","AA","CLF","RS","CMC","MLM","VMC",
        "NEM","GOLD","AEM","SCCO","MP","LTHM","ALB","SQM","TECK","RIO",
        "BHP","MOS","CF","NTR","IP"
    ],
    "医疗大盘_器械_服务": [
        "UNH","LLY","JNJ","ABBV","ISRG","SYK","BSX","MDT","TMO","DHR",
        "ABT","GILD","AMGN","CVS","CI","ELV","HCA","HUM","EW","BAX",
        "ZBH","IDXX","RMD","GEHC","MCK"
    ],
    "地产_REIT_公用": [
        "PLD","AMT","EQIX","WELL","DLR","CCI","SPG","O","PSA","AVB",
        "EQR","VICI","EXR","ARE","CBRE","NEE","SO","DUK","AEP","XEL",
        "SRE","PEG","ED","AWK","PCG"
    ],
    "旅游_航空_娱乐": [
        "UBER","BKNG","EXPE","CCL","RCL","NCLH","AAL","DAL","UAL","LUV",
        "ALGT","MAR","HLT","WYNN","LVS","MGM","CHDN","DIS","WBD","PARA",
        "JBLU"
    ]
}

ALL_SYMBOLS = sorted(set([s for arr in SECTOR_POOLS.values() for s in arr]))

# ============================================================
# 工具
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

def load_state():
    global LAST_PREMARKET_SIGNATURE, LAST_CLOSE_SIGNATURE, LAST_MARKET_REGIME, INTRADAY_BREAKOUT_SENT
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        LAST_PREMARKET_SIGNATURE = data.get("last_premarket_signature", "")
        LAST_CLOSE_SIGNATURE = data.get("last_close_signature", "")
        LAST_MARKET_REGIME = data.get("last_market_regime", "")
        INTRADAY_BREAKOUT_SENT = set(data.get("intraday_breakout_sent", []))
    except Exception as e:
        print("load_state error:", e)

def save_state():
    data = {
        "last_premarket_signature": LAST_PREMARKET_SIGNATURE,
        "last_close_signature": LAST_CLOSE_SIGNATURE,
        "last_market_regime": LAST_MARKET_REGIME,
        "intraday_breakout_sent": list(INTRADAY_BREAKOUT_SENT),
    }
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("save_state error:", e)

def reset_daily_intraday_flags():
    marker = "intraday_date.txt"
    today = now_et().strftime("%Y-%m-%d")
    old = ""
    if os.path.exists(marker):
        try:
            old = open(marker, "r", encoding="utf-8").read().strip()
        except Exception:
            old = ""
    if old != today:
        INTRADAY_BREAKOUT_SENT.clear()
        try:
            with open(marker, "w", encoding="utf-8") as f:
                f.write(today)
        except Exception:
            pass
        save_state()

# ============================================================
# Telegram
# ============================================================
def send_telegram(msg):
    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram env missing")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    chunks = []
    text = msg.strip()
    while len(text) > TELEGRAM_MAX_LEN:
        split_at = text.rfind("\n", 0, TELEGRAM_MAX_LEN)
        if split_at == -1:
            split_at = TELEGRAM_MAX_LEN
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip()
    if text:
        chunks.append(text)

    for chunk in chunks:
        payload = {"chat_id": CHAT_ID, "text": chunk}
        ok = False
        for _ in range(3):
            try:
                r = requests.post(url, data=payload, timeout=12)
                if r.status_code == 200:
                    ok = True
                    break
            except Exception:
                pass
            time.sleep(2)
        if not ok:
            print("Telegram send failed")

# ============================================================
# 下载
# ============================================================
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
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        return df.dropna()
    except Exception:
        return None

# ============================================================
# 指标
# ============================================================
def calc_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, math.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)

def calc_atr(df, period=14):
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    close = df["Close"].astype(float)

    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    return atr

def find_sector(symbol):
    for sector, arr in SECTOR_POOLS.items():
        if symbol in arr:
            return sector
    return "其他"

def build_smart_entry_plan(last, high10, high20, ma20, atr, rsi14, change5, vol_ratio):
    breakout_dist_pct = ((last - high10) / high10) * 100 if high10 > 0 else 0
    extension_pct = ((last - ma20) / ma20) * 100 if ma20 > 0 else 0

    plan = "接近买点"
    note = "可小仓观察"
    entry_low = round(last * 0.995, 2)
    entry_high = round(last * 1.005, 2)

    if rsi14 >= 85 or change5 >= 18 or extension_pct >= 15:
        plan = "不要追"
        note = "股价过热，等回踩MA20或前高再看"
        entry_low = round(max(high10, ma20), 2)
        entry_high = round(max(high10, ma20) * 1.01, 2)

    elif 0 <= breakout_dist_pct <= 2.0 and vol_ratio >= 1.5 and rsi14 < 80:
        plan = "可追突破"
        note = "突破不远，量能配合，可等确认后进"
        entry_low = round(high10, 2)
        entry_high = round(last * 1.01, 2)

    elif extension_pct >= 6:
        plan = "等回踩买"
        note = "离均线有点远，等回踩更安全"
        entry_low = round(max(ma20, high10 * 0.995), 2)
        entry_high = round(max(ma20, high10 * 1.005), 2)

    else:
        plan = "接近买点"
        note = "位置还可以，但仍建议等确认"
        entry_low = round(last * 0.995, 2)
        entry_high = round(last * 1.01, 2)

    atr_val = atr if atr and not math.isnan(atr) else last * 0.03
    sl = round(max(0.01, min(entry_low - atr_val * 1.2, last * 0.97)), 2)
    tp1 = round(last + atr_val * 2.0, 2)
    tp2 = round(last + atr_val * 3.5, 2)

    return {
        "plan": plan,
        "note": note,
        "entry_low": entry_low,
        "entry_high": entry_high,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "breakout_dist_pct": round(breakout_dist_pct, 2),
        "extension_pct": round(extension_pct, 2),
    }

def score_stock_daily(symbol, df):
    if df is None or len(df) < 70:
        return None

    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    vol = df["Volume"].astype(float)

    last = float(close.iloc[-1])
    prev = float(close.iloc[-2])

    ma20 = float(close.rolling(20).mean().iloc[-1])
    ma50 = float(close.rolling(50).mean().iloc[-1])
    ma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else ma50

    avg_vol20 = float(vol.rolling(20).mean().iloc[-1])
    avg_vol50 = float(vol.rolling(50).mean().iloc[-1])

    high10 = float(high.iloc[-10:-1].max())
    high20 = float(high.iloc[-20:-1].max())
    low5 = float(low.iloc[-5:].min())

    rsi14 = float(calc_rsi(close, 14).iloc[-1])
    atr14_series = calc_atr(df, 14)
    atr14 = float(atr14_series.iloc[-1]) if not atr14_series.empty else last * 0.03

    vol_ratio = float(vol.iloc[-1] / avg_vol20) if avg_vol20 > 0 else 0
    change1 = ((last - prev) / prev) * 100 if prev > 0 else 0
    change5 = ((last - float(close.iloc[-6])) / float(close.iloc[-6])) * 100 if len(close) >= 6 else 0
    change20 = ((last - float(close.iloc[-21])) / float(close.iloc[-21])) * 100 if len(close) >= 21 else 0
    extension = ((last - ma20) / ma20) * 100 if ma20 > 0 else 0
    breakout_dist = ((last - high10) / high10) * 100 if high10 > 0 else 0

    score = 0
    reasons = []
    warnings = []

    if last > ma20:
        score += 10
        reasons.append("站上MA20")
    if ma20 > ma50:
        score += 10
        reasons.append("MA20>MA50")
    if ma50 > ma200:
        score += 6
        reasons.append("MA50>MA200")

    if last >= high10:
        score += 18
        reasons.append("突破10日高")
    if last >= high20:
        score += 10
        reasons.append("突破20日高")

    if vol_ratio >= 2.0:
        score += 16
        reasons.append("量比>=2")
    elif vol_ratio >= 1.5:
        score += 10
        reasons.append("量比>=1.5")
    elif vol_ratio >= 1.2:
        score += 6
        reasons.append("量比>=1.2")
    else:
        score -= 4
        warnings.append("量能不足")

    if 3 <= change5 <= 10:
        score += 12
        reasons.append("5日强势适中")
    elif 10 < change5 <= 15:
        score += 8
        reasons.append("5日偏热")
    elif change5 > 15:
        score -= 8
        warnings.append("5日涨幅过大")
    elif change5 > 1:
        score += 4
        reasons.append("5日转强")

    if change20 >= 12:
        score += 8
        reasons.append("20日趋势好")
    elif change20 >= 5:
        score += 4
        reasons.append("20日有升势")

    if 55 <= rsi14 <= 72:
        score += 10
        reasons.append("RSI健康")
    elif 50 <= rsi14 < 55:
        score += 4
        reasons.append("RSI转强")
    elif 72 < rsi14 <= 80:
        score += 2
        warnings.append("RSI偏热")
    elif 80 < rsi14 <= 85:
        score -= 6
        warnings.append("RSI过热")
    elif rsi14 > 85:
        score -= 15
        warnings.append("RSI极热")

    if last < 3:
        score -= 15
        warnings.append("股价太低")
    elif last < 5:
        score -= 8
        warnings.append("低价股")

    if avg_vol50 < 800_000:
        score -= 10
        warnings.append("均量偏低")
    elif avg_vol50 < 1_500_000:
        score -= 4
        warnings.append("均量一般")

    if extension > 15:
        score -= 12
        warnings.append("偏离MA20过大")
    elif extension > 10:
        score -= 6
        warnings.append("离MA20偏远")

    if 0 <= breakout_dist <= 1.0 and vol_ratio < 1.2:
        score -= 8
        warnings.append("疑似假突破")

    sector = find_sector(symbol)
    if sector in {
        "AI_软件", "半导体", "量子", "太空_卫星_航天",
        "金融科技_支付_交易所", "小盘成长_高波动", "加密概念_矿股"
    }:
        score += 2

    entry_plan = build_smart_entry_plan(
        last=last,
        high10=high10,
        high20=high20,
        ma20=ma20,
        atr=atr14,
        rsi14=rsi14,
        change5=change5,
        vol_ratio=vol_ratio
    )

    return {
        "symbol": symbol,
        "sector": sector,
        "score": round(score, 2),
        "price": round(last, 2),
        "buy": round((entry_plan["entry_low"] + entry_plan["entry_high"]) / 2, 2),
        "buy_low": entry_plan["entry_low"],
        "buy_high": entry_plan["entry_high"],
        "buy_plan": entry_plan["plan"],
        "buy_note": entry_plan["note"],
        "sl": entry_plan["sl"],
        "tp1": entry_plan["tp1"],
        "tp2": entry_plan["tp2"],
        "rsi": round(rsi14, 1),
        "vol_ratio": round(vol_ratio, 2),
        "change1": round(change1, 2),
        "change5": round(change5, 2),
        "change20": round(change20, 2),
        "extension": round(extension, 2),
        "breakout_dist": round(breakout_dist, 2),
        "reasons": reasons[:5],
        "warnings": warnings[:4],
    }

def analyze_symbol(symbol):
    df = safe_download(symbol, period="6mo", interval="1d")
    if df is None:
        return None
    result = score_stock_daily(symbol, df)
    if not result:
        return None
    return result

# ============================================================
# 大盘过滤
# ============================================================
def get_market_regime():
    qqq = safe_download("QQQ", period="3mo", interval="1d")
    spy = safe_download("SPY", period="3mo", interval="1d")

    if qqq is None or spy is None or len(qqq) < 30 or len(spy) < 30:
        return {"label": "NEUTRAL", "text": "大盘数据不足，默认中性"}

    q = qqq["Close"].astype(float)
    s = spy["Close"].astype(float)

    q_last = float(q.iloc[-1])
    s_last = float(s.iloc[-1])
    q_ma20 = float(q.rolling(20).mean().iloc[-1])
    s_ma20 = float(s.rolling(20).mean().iloc[-1])

    q5 = ((q_last - float(q.iloc[-6])) / float(q.iloc[-6])) * 100 if len(q) >= 6 else 0
    s5 = ((s_last - float(s.iloc[-6])) / float(s.iloc[-6])) * 100 if len(s) >= 6 else 0

    bull = 0
    bear = 0

    bull += 1 if q_last > q_ma20 else 0
    bear += 1 if q_last <= q_ma20 else 0
    bull += 1 if s_last > s_ma20 else 0
    bear += 1 if s_last <= s_ma20 else 0
    bull += 1 if q5 > 1 else 0
    bear += 1 if q5 < -1 else 0
    bull += 1 if s5 > 1 else 0
    bear += 1 if s5 < -1 else 0

    if bull >= 3:
        return {"label": "BULLISH", "text": "大盘偏强，可正常出手"}
    if bear >= 3:
        return {"label": "DEFENSIVE", "text": "大盘偏弱，建议减仓或只做前2名"}
    return {"label": "NEUTRAL", "text": "大盘中性，精选做前3名"}

# ============================================================
# 统计
# ============================================================
def build_sector_strength(results):
    m = {}
    for r in results:
        m.setdefault(r["sector"], []).append(r["score"])

    ranked = []
    for sector, scores in m.items():
        avg_score = sum(scores) / len(scores)
        ranked.append((sector, round(avg_score, 2), len(scores)))

    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked[:5]

# ============================================================
# 分批并发扫描
# ============================================================
def scan_batch(symbols):
    batch_results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(analyze_symbol, s): s for s in symbols}
        for future in as_completed(futures):
            try:
                res = future.result()
                if res and res["score"] >= 42:
                    batch_results.append(res)
            except Exception:
                pass
    return batch_results

def scan_all_daily():
    all_results = []
    batches = list(chunk_list(ALL_SYMBOLS, BATCH_SIZE))
    total_batches = len(batches)

    for idx, batch in enumerate(batches, start=1):
        print(f"[{now_kl_str()}] scanning batch {idx}/{total_batches}, symbols={len(batch)}")
        batch_results = scan_batch(batch)
        all_results.extend(batch_results)

        if idx < total_batches:
            time.sleep(BATCH_SLEEP)

    all_results.sort(key=lambda x: x["score"], reverse=True)
    return all_results

# ============================================================
# 消息
# ============================================================
def build_premarket_message(results, regime):
    results = sorted(results, key=lambda x: x["score"], reverse=True)

    filtered = []
    hot_names = []
    for r in results:
        if r["buy_plan"] == "不要追":
            hot_names.append(r["symbol"])
            continue
        filtered.append(r)

    display_top5 = filtered[:5] if len(filtered) >= 5 else results[:5]
    reserve5 = filtered[5:10] if len(filtered) >= 10 else results[5:10]
    hot = build_sector_strength(results)

    lines = [
        "🚀 V11.2 + V11.5 智能买点盘前扫描",
        f"{now_kl_str()} (KL)",
        "",
        f"📈 大盘状态：{regime['label']}",
        f"说明：{regime['text']}",
        ""
    ]

    if hot:
        lines.append("🔥 今日最热板块：")
        for i, (sector, avg_score, count) in enumerate(hot[:3], start=1):
            lines.append(f"{i}. {sector} | 平均分 {avg_score} | 入选 {count}")
        lines.append("")

    lines.append("🏆 今日较优先关注 Top 5：")
    lines.append("")

    for i, r in enumerate(display_top5, start=1):
        logic = " / ".join(r["reasons"][:3]) if r["reasons"] else "趋势观察"
        warn = " / ".join(r["warnings"][:2]) if r["warnings"] else "无明显过热"
        lines.append(f"{i}. {r['symbol']} ({r['score']}分) [{r['sector']}]")
        lines.append(f"现价 {r['price']} | 策略：{r['buy_plan']}")
        lines.append(f"买区 {r['buy_low']} - {r['buy_high']} | 止损 {r['sl']}")
        lines.append(f"TP1 {r['tp1']} | TP2 {r['tp2']}")
        lines.append(f"量比 {r['vol_ratio']} | RSI {r['rsi']} | 5日 {r['change5']}%")
        lines.append(f"逻辑：{logic}")
        lines.append(f"提醒：{warn}")
        lines.append(f"备注：{r['buy_note']}")
        lines.append("")

    if reserve5:
        lines.append("🌙 预备股 5只：")
        lines.append(", ".join([f"{x['symbol']}({x['score']})" for x in reserve5]))
        lines.append("")

    if hot_names:
        lines.append("⚠️ 过热暂不追名单：")
        lines.append(", ".join(hot_names[:5]))
        lines.append("")

    if regime["label"] == "DEFENSIVE":
        lines.append("⚠️ 建议：只做 Top1~2，仓位减半，不追高。")
    elif regime["label"] == "NEUTRAL":
        lines.append("⚠️ 建议：精选 Top1~3，不要全买。")
    else:
        lines.append("✅ 建议：优先看“可追突破 / 等回踩买”的票，不做‘不要追’。")

    return "\n".join(lines)

def build_close_message(results, regime):
    reserve = sorted(results, key=lambda x: x["score"], reverse=True)[:10]

    lines = [
        "🌙 V11.2 + V11.5 智能买点收盘扫描",
        f"{now_kl_str()} (KL)",
        "",
        f"📈 大盘状态：{regime['label']}",
        "",
        "明日预备爆发股 Top 10：",
        ""
    ]

    for i, r in enumerate(reserve, start=1):
        lines.append(
            f"{i}. {r['symbol']} ({r['score']}分) [{r['sector']}] | "
            f"策略 {r['buy_plan']} | 买区 {r['buy_low']}-{r['buy_high']} | "
            f"止损 {r['sl']} | TP1 {r['tp1']}"
        )

    lines.append("")
    lines.append("说明：下一交易日优先看“可追突破 / 等回踩买”，跳过“不要追”。")
    return "\n".join(lines)

# ============================================================
# 主任务
# ============================================================
def run_premarket_scan():
    global LAST_PREMARKET_SIGNATURE, LAST_MARKET_REGIME

    if is_weekend_et():
        return {"status": "skip", "message": "weekend"}

    reset_daily_intraday_flags()

    regime = get_market_regime()
    LAST_MARKET_REGIME = regime["label"]

    results = scan_all_daily()
    if not results:
        send_telegram("⚠️ V11.2 + V11.5 盘前扫描完成，但没有明显强势股。")
        return {"status": "ok", "count": 0}

    top5 = results[:5]
    signature = "|".join([f"{x['symbol']}:{x['score']}" for x in top5])

    if signature == LAST_PREMARKET_SIGNATURE:
        return {"status": "ok", "message": "unchanged"}

    LAST_PREMARKET_SIGNATURE = signature
    save_state()

    msg = build_premarket_message(results, regime)
    send_telegram(msg)

    return {"status": "ok", "count": len(results), "top5": [x["symbol"] for x in top5]}

def run_close_scan():
    global LAST_CLOSE_SIGNATURE

    if is_weekend_et():
        return {"status": "skip", "message": "weekend"}

    regime = get_market_regime()
    results = scan_all_daily()
    if not results:
        send_telegram("⚠️ V11.2 + V11.5 收盘扫描完成，但没有整理出明日预备股。")
        return {"status": "ok", "count": 0}

    reserve = results[:10]
    signature = "|".join([f"{x['symbol']}:{x['score']}" for x in reserve])

    if signature == LAST_CLOSE_SIGNATURE:
        return {"status": "ok", "message": "unchanged"}

    LAST_CLOSE_SIGNATURE = signature
    save_state()

    msg = build_close_message(results, regime)
    send_telegram(msg)

    return {"status": "ok", "count": len(results), "reserve10": [x["symbol"] for x in reserve]}

# ============================================================
# 盘中突破
# ============================================================
def analyze_intraday_breakout(symbol):
    try:
        df = safe_download(symbol, period="5d", interval="15m")
        if df is None or len(df) < 30:
            return None

        close = df["Close"].astype(float)
        high = df["High"].astype(float)
        vol = df["Volume"].astype(float)

        last = float(close.iloc[-1])
        prev_high = float(high.iloc[-9:-1].max())
        avg_vol8 = float(vol.iloc[-9:-1].mean())
        last_vol = float(vol.iloc[-1])

        if last > prev_high and avg_vol8 > 0 and last_vol >= avg_vol8 * 1.25:
            return {
                "symbol": symbol,
                "price": round(last, 2),
                "prev_high": round(prev_high, 2),
                "vol_ratio": round(last_vol / avg_vol8, 2)
            }
        return None
    except Exception:
        return None

def run_intraday_breakout_scan():
    if is_weekend_et():
        return {"status": "skip", "message": "weekend"}

    reset_daily_intraday_flags()

    focus_symbols = sorted(set(
        SECTOR_POOLS["AI_软件"] +
        SECTOR_POOLS["半导体"] +
        SECTOR_POOLS["量子"] +
        SECTOR_POOLS["太空_卫星_航天"] +
        SECTOR_POOLS["金融科技_支付_交易所"] +
        SECTOR_POOLS["小盘成长_高波动"] +
        SECTOR_POOLS["加密概念_矿股"]
    ))

    alerts = []
    for batch in chunk_list(focus_symbols, 40):
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, 12)) as executor:
            futures = {executor.submit(analyze_intraday_breakout, s): s for s in batch}
            for future in as_completed(futures):
                try:
                    res = future.result()
                    if res:
                        key = f"{res['symbol']}_{now_et().strftime('%Y-%m-%d')}"
                        if key not in INTRADAY_BREAKOUT_SENT:
                            alerts.append(res)
                            INTRADAY_BREAKOUT_SENT.add(key)
                except Exception:
                    pass
        time.sleep(0.8)

    alerts.sort(key=lambda x: x["vol_ratio"], reverse=True)
    alerts = alerts[:5]

    if alerts:
        lines = [
            "⚡ V11.2 + V11.5 盘中突破提醒",
            f"{now_kl_str()} (KL)",
            ""
        ]
        for i, a in enumerate(alerts, start=1):
            lines.append(
                f"{i}. {a['symbol']} 突破中 | 现价 {a['price']} | 前高 {a['prev_high']} | 量比 {a['vol_ratio']}"
            )
        lines.append("")
        lines.append("提示：盘中突破波动快，尽量不要追太高。")

        send_telegram("\n".join(lines))
        save_state()

    return {"status": "ok", "alerts": alerts}

# ============================================================
# 排程
# ============================================================
def scheduler_loop():
    weekdays = [
        schedule.every().monday,
        schedule.every().tuesday,
        schedule.every().wednesday,
        schedule.every().thursday,
        schedule.every().friday
    ]

    for d in weekdays:
        d.at("21:00").do(run_premarket_scan)
        d.at("22:20").do(run_intraday_breakout_scan)
        d.at("23:20").do(run_intraday_breakout_scan)

    schedule.every().day.at("04:20").do(run_close_scan)

    while True:
        try:
            schedule.run_pending()
        except Exception as e:
            print("scheduler error:", e)
        time.sleep(15)

# ============================================================
# Routes
# ============================================================
@app.route("/")
def home():
    return "V11.2 + V11.5 Scanner Running"

@app.route("/health")
def health():
    return jsonify({
        "status": "healthy",
        "time_kl": now_kl_str(),
        "symbols": len(ALL_SYMBOLS),
        "max_workers": MAX_WORKERS,
        "batch_size": BATCH_SIZE
    })

@app.route("/run-premarket")
def route_run_premarket():
    return jsonify(run_premarket_scan())

@app.route("/run-intraday")
def route_run_intraday():
    return jsonify(run_intraday_breakout_scan())

@app.route("/run-close")
def route_run_close():
    return jsonify(run_close_scan())

@app.route("/sectors")
def route_sectors():
    return jsonify({
        "sector_count": len(SECTOR_POOLS),
        "total_symbols": len(ALL_SYMBOLS),
        "sectors": {k: len(v) for k, v in SECTOR_POOLS.items()}
    })

# ============================================================
# Start
# ============================================================
if __name__ == "__main__":
    load_state()

    t = threading.Thread(target=scheduler_loop, daemon=True)
    t.start()

    app.run(host="0.0.0.0", port=PORT)
