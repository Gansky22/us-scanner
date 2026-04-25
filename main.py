import os 
import time 
import math 
import json 
import threading from datetime 
import datetime from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd 
import pytz 
import requests 
import schedule 
import yfinance as yf 
from flask import Flask, jsonify, request

app = Flask(name)

============================================================

ENV

============================================================

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip() CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip() PORT = int(os.getenv("PORT", "5000"))

MAX_WORKERS = int(os.getenv("MAX_WORKERS", "20")) BATCH_SIZE = int(os.getenv("BATCH_SIZE", "50")) BATCH_SLEEP = float(os.getenv("BATCH_SLEEP", "1.2"))

TELEGRAM_MAX_LEN = 3500

ACCOUNT_SIZE = float(os.getenv("ACCOUNT_SIZE", "5000")) MAX_RISK_PER_TRADE = float(os.getenv("MAX_RISK_PER_TRADE", "0.02")) MAX_POSITION_RATIO = float(os.getenv("MAX_POSITION_RATIO", "0.30"))

MAX_LOSS_AMOUNT = ACCOUNT_SIZE * MAX_RISK_PER_TRADE TZ_KL = pytz.timezone("Asia/Kuala_Lumpur") TZ_ET = pytz.timezone("US/Eastern")

STATE_FILE = "scanner_state.json"

LAST_PREMARKET_SIGNATURE = "" LAST_CLOSE_SIGNATURE = "" LAST_BOTTOM_SIGNATURE = "" LAST_MARKET_REGIME = "" INTRADAY_BREAKOUT_SENT = set()

BAD_SYMBOLS = { "X", "ZI", "ATVI", "FRC", "SIVB", "SVB", "BBBY", "TTCF", "TWTR", "FB", "BRK.B", "BRK/B", "BF.B", "BF/B" }

============================================================

股票池

============================================================

SECTOR_POOLS = { "AI_软件": [ "PLTR","SOUN","BBAI","AI","PATH","U","APP","CRM","NOW","MDB", "SNOW","DDOG","NET","HUBS","TEAM","ASAN","BOX","ESTC","CFLT","DOCN", "GTLB","DUOL","SPOT","ADBE","ORCL","SAP","WDAY","INTU","S", "RBRK","PEGA","PAYC","TWLO","FIVN","BILL","TOST","PCOR","PCTY" ], "半导体": [ "NVDA","AMD","AVGO","QCOM","MU","MRVL","ON","ARM","AMAT","LRCX", "KLAC","ADI","NXPI","MCHP","TXN","SWKS","QRVO","MPWR","ENTG","COHR", "ONTO","AEHR","ACLS","GFS","TSM","INTC","WOLF","LSCC","ALAB","SMTC", "SITM","CRUS","FORM","POWI","NVTS" ], "量子": [ "IONQ","RGTI","QBTS","QUBT","ARQQ","IBM","HON","GOOG","MSFT","AMZN" ], "太空_卫星_航天": [ "LUNR","RKLB","ASTS","RDW","PL","SPIR","SATL","BA","LHX","NOC", "KTOS","AVAV","IRDM","GSAT","VSAT","MDAI","MNTS","BKSY","ACHR","JOBY" ], "金融科技_支付_交易所": [ "HOOD","SOFI","AFRM","COIN","PYPL","ALLY","NU","UPST","LC", "PAGS","FOUR","BILL","TOST","INTU","MQ","GPN","FIS","JEF", "SCHW","IBKR","ICE","CME","NDAQ","MKTX","RKT","TREE","EXFY", "DLO","STNE","MELI" ], "网络安全": [ "CRWD","PANW","ZS","OKTA","S","FTNT","RBRK","TENB","GEN", "CHKP","QLYS","VRNS","NET","AKAM","DDOG","RSKD","BLZE","SAIL","OSPN", "CSCO","INFY" ], "电动车_新能源_电网": [ "TSLA","RIVN","LCID","NIO","XPEV","LI","QS","ENVX","BE","PLUG", "FSLR","ENPH","SEDG","ARRY","RUN","CHPT","BLNK","EVGO","SLDP","STEM", "SHLS","NXT","FLNC","AESI","NEE","GEV","VRT","ETN","HUBB","PWR", "CEG","SMR","OKLO","NNE","CCJ","UUUU","LEU","BWXT","GE" ], "生物科技_医疗成长": [ "MRNA","VRTX","REGN","ALNY","EXEL","BEAM","CRSP","NTLA","RXRX","DNA", "SANA","NTRA","ILMN","BNTX","ARWR","NBIX","INSM","HALO","SRPT","VKTX", "HIMS","TEM","ABCL","GH","TWST","PACB","TXG","EDIT","ADMA","OMER", "XENE","CYTK","MDGL","ARGX","ROIV","TGTX","ALKS" ], "加密概念_矿股": [ "MSTR","COIN","MARA","RIOT","CLSK","CIFR","IREN","HUT","BTDR","BITF", "WULF","CORZ","CAN","BTBT","GREE","HOOD","PYPL" ], "云计算_SaaS": [ "CRM","NOW","SNOW","DDOG","NET","MDB","HUBS","TEAM","ASAN","BOX", "WDAY","INTU","ADBE","ORCL","SAP","DOCN","CFLT","ESTC", "PCTY","PAYC","PCOR","APPF","BL","U","SHOP","WIX","BAND", "TWLO","FIVN","SPSC","FRSH","MNDY","GTLB","DUOL","PATH" ], "消费成长_平台_品牌": [ "AMZN","MELI","ONON","CELH","CAVA","CMG","LULU","ABNB","DASH","RBLX", "ETSY","PINS","ROKU","DUOL","SPOT","NFLX","AEO","ANF","DECK","NKE", "ADDYY","BIRK","ELF","ULTA","CROX","YETI","DKS","WING","SBUX","COST", "WMT","TGT","CVNA","KMX","TXRH","SG","CART","BKNG","EXPE","LYFT" ], "工业_国防_基建": [ "GE","GEV","RTX","LMT","NOC","GD","BA","KTOS","AVAV","HON", "CAT","DE","URI","PH","ETN","PWR","VRT","HWM","TDG","CW", "IR","XYL","EMR","ROK","JCI","HUBB","TT","PCAR","NSC","UNP", "CSX","JBHT","EXPD","CHRW","WSC","WAB","GWW","FAST","ODFL","SAIA" ], "大盘科技_通信": [ "AAPL","MSFT","META","AMZN","GOOGL","NFLX","TSLA","NVDA","AMD","AVGO", "ORCL","ADBE","CRM","INTC","QCOM","CSCO","TMUS","T","VZ","ARM", "UBER","LYFT","SPOT","SNAP","PINS","RDDT","SHOP","SE","BABA","JD", "PDD","BIDU","NTES","TME","ZM" ], "小盘成长_高波动": [ "JOBY","ACHR","BBAI","SOUN","UPST","LMND","OPEN","RUN","APP","GCT", "ASTS","ENVX","TOST","CELH","DUOL","RKLB","LUNR","IONQ","RGTI","QBTS", "QUBT","SMR","OKLO","NNE","TEM","HIMS","VKTX","ALAB","AEHR","ACLS", "STNE","DLO","CAVA","ELF","BIRK","CORT","PRCH","RUM","GRAB","HSAI" ], "金融传统_银行_保险": [ "JPM","BAC","WFC","C","GS","MS","AXP","BLK","BX","KKR", "AIG","PGR","CB","TRV","ALL","MET","PRU","AJG","BRO", "USB","PNC","TFC","COF","MCO","SPGI","CBRE","AON","RJF" ], "能源_油气_LNG": [ "XOM","CVX","COP","SLB","HAL","BKR","EOG","DVN","OXY","MPC", "VLO","PSX","KMI","WMB","LNG","CTRA","FANG","EQT","AR","APA", "NOV","RRC","TPL" ], "材料_金属_矿业": [ "FCX","NUE","STLD","AA","CLF","RS","CMC","MLM","VMC", "NEM","GOLD","AEM","SCCO","MP","ALB","SQM","TECK","RIO", "BHP","MOS","CF","NTR","IP" ], "医疗大盘_器械_服务": [ "UNH","LLY","JNJ","ABBV","ISRG","SYK","BSX","MDT","TMO","DHR", "ABT","GILD","AMGN","CVS","CI","ELV","HCA","HUM","EW","BAX", "ZBH","IDXX","RMD","GEHC","MCK" ], "地产_REIT_公用": [ "PLD","AMT","EQIX","WELL","DLR","CCI","SPG","O","PSA","AVB", "EQR","VICI","EXR","ARE","CBRE","NEE","SO","DUK","AEP","XEL", "SRE","PEG","ED","AWK","PCG" ], "旅游_航空_娱乐": [ "UBER","BKNG","EXPE","CCL","RCL","NCLH","AAL","DAL","UAL","LUV", "ALGT","MAR","HLT","WYNN","LVS","MGM","CHDN","DIS","WBD", "JBLU" ] }

ALL_SYMBOLS = sorted(set([ s for arr in SECTOR_POOLS.values() for s in arr if s not in BAD_SYMBOLS ]))

============================================================

工具

============================================================

def now_kl(): return datetime.now(TZ_KL)

def now_et(): return datetime.now(TZ_ET)

def now_kl_str(): return now_kl().strftime("%Y-%m-%d %H:%M:%S")

def is_weekend_et(): return now_et().weekday() >= 5

def chunk_list(items, size): for i in range(0, len(items), size): yield items[i:i + size]

def load_state(): global LAST_PREMARKET_SIGNATURE, LAST_CLOSE_SIGNATURE, LAST_BOTTOM_SIGNATURE global LAST_MARKET_REGIME, INTRADAY_BREAKOUT_SENT

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
except Exception as e:
    print("load_state error:", e)

def save_state(): data = { "last_premarket_signature": LAST_PREMARKET_SIGNATURE, "last_close_signature": LAST_CLOSE_SIGNATURE, "last_bottom_signature": LAST_BOTTOM_SIGNATURE, "last_market_regime": LAST_MARKET_REGIME, "intraday_breakout_sent": list(INTRADAY_BREAKOUT_SENT), }

try:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
except Exception as e:
    print("save_state error:", e)

def reset_daily_intraday_flags(): marker = "intraday_date.txt" today = now_et().strftime("%Y-%m-%d") old = ""

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

============================================================

Telegram

============================================================

def send_telegram(msg): if not BOT_TOKEN or not CHAT_ID: print("Telegram env missing") return False

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

success = True

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
        success = False
        print("Telegram send failed")

return success

============================================================

下载

============================================================

def safe_download(symbol, period="6mo", interval="1d"): if symbol in BAD_SYMBOLS: return None

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

    needed = {"Open", "High", "Low", "Close", "Volume"}
    if not needed.issubset(set(df.columns)):
        return None

    df = df.dropna()

    if len(df) < 30:
        return None

    return df

except Exception:
    return None

============================================================

指标

============================================================

def calc_rsi(series, period=14): delta = series.diff() gain = delta.clip(lower=0).rolling(period).mean() loss = (-delta.clip(upper=0)).rolling(period).mean() rs = gain / loss.replace(0, math.nan) rsi = 100 - (100 / (1 + rs)) return rsi.fillna(50)

def calc_atr(df, period=14): high = df["High"].astype(float) low = df["Low"].astype(float) close = df["Close"].astype(float)

prev_close = close.shift(1)
tr1 = high - low
tr2 = (high - prev_close).abs()
tr3 = (low - prev_close).abs()

tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
atr = tr.rolling(period).mean()
return atr

def calc_obv(close, volume): obv = [0]

for i in range(1, len(close)):
    if close.iloc[i] > close.iloc[i - 1]:
        obv.append(obv[-1] + volume.iloc[i])
    elif close.iloc[i] < close.iloc[i - 1]:
        obv.append(obv[-1] - volume.iloc[i])
    else:
        obv.append(obv[-1])

return pd.Series(obv, index=close.index)

def find_sector(symbol): for sector, arr in SECTOR_POOLS.items(): if symbol in arr: return sector return "其他"

============================================================

V12 隔日上涨评分

============================================================

def calc_nextday_score_from_values(vol_ratio, buy_plan, rsi, change5, extension, change1, breakout_dist): score = 0

if vol_ratio >= 2.5:
    score += 25
elif vol_ratio >= 2.0:
    score += 22
elif vol_ratio >= 1.5:
    score += 16
elif vol_ratio >= 1.2:
    score += 8

if buy_plan == "可追突破":
    score += 25
elif buy_plan == "接近买点":
    score += 16
elif buy_plan == "等回踩买":
    score += 8
elif buy_plan == "不要追":
    score -= 12

if 55 <= rsi <= 68:
    score += 20
elif 68 < rsi <= 75:
    score += 12
elif 50 <= rsi < 55:
    score += 6
elif rsi > 80:
    score -= 10

if 3 <= change5 <= 10:
    score += 15
elif 1 <= change5 < 3:
    score += 8
elif 10 < change5 <= 15:
    score += 5
elif change5 > 15:
    score -= 12

if extension <= 5:
    score += 15
elif 5 < extension <= 9:
    score += 8
elif 9 < extension <= 14:
    score += 2
elif extension > 14:
    score -= 12

if change1 > 12:
    score -= 10
elif change1 < -3:
    score -= 8

if 0 <= breakout_dist <= 3:
    score += 8

return max(0, min(100, round(score, 1)))

def nextday_grade(score): if score >= 80: return "A+ 隔日强势候选" if score >= 70: return "A 可重点观察" if score >= 60: return "B 可观察" return "C 暂不优先"

============================================================

风控与买点

============================================================

def build_v116_risk_plan(symbol, entry_price, sl, tp1, tp2): if entry_price <= 0 or sl <= 0: return None

risk_per_share = entry_price - sl

if risk_per_share <= 0:
    return None

suggested_shares = int(MAX_LOSS_AMOUNT / risk_per_share)

max_position_value = ACCOUNT_SIZE * MAX_POSITION_RATIO
max_shares_by_capital = int(max_position_value / entry_price)

suggested_shares = min(suggested_shares, max_shares_by_capital)

if suggested_shares < 1:
    suggested_shares = 1

position_value = suggested_shares * entry_price
estimated_max_loss = suggested_shares * risk_per_share

return {
    "suggested_shares": suggested_shares,
    "position_value": round(position_value, 2),
    "risk_per_share": round(risk_per_share, 2),
    "estimated_max_loss": round(estimated_max_loss, 2),
    "max_loss_allowed": round(MAX_LOSS_AMOUNT, 2)
}

def build_smart_entry_plan(last, high10, high20, ma20, atr, rsi14, change5, vol_ratio): breakout_dist_pct = ((last - high10) / high10) * 100 if high10 > 0 else 0 extension_pct = ((last - ma20) / ma20) * 100 if ma20 > 0 else 0

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

============================================================

每日爆发评分

============================================================

def score_stock_daily(symbol, df): if df is None or len(df) < 70: return None

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

rsi14 = float(calc_rsi(close, 14).iloc[-1])
atr14_series = calc_atr(df, 14)
atr14 = float(atr14_series.iloc[-1]) if not atr14_series.empty and not math.isnan(float(atr14_series.iloc[-1])) else last * 0.03

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

nextday_score = calc_nextday_score_from_values(
    vol_ratio=vol_ratio,
    buy_plan=entry_plan["plan"],
    rsi=rsi14,
    change5=change5,
    extension=extension,
    change1=change1,
    breakout_dist=breakout_dist
)

risk_plan = build_v116_risk_plan(
    symbol=symbol,
    entry_price=round((entry_plan["entry_low"] + entry_plan["entry_high"]) / 2, 2),
    sl=entry_plan["sl"],
    tp1=entry_plan["tp1"],
    tp2=entry_plan["tp2"]
)

return {
    "symbol": symbol,
    "sector": sector,
    "score": round(score, 2),
    "nextday_score": nextday_score,
    "nextday_grade": nextday_grade(nextday_score),
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
    "risk_plan": risk_plan,
}

def analyze_symbol(symbol): df = safe_download(symbol, period="6mo", interval="1d") if df is None: return None return score_stock_daily(symbol, df)

============================================================

V13 底部吸筹评分

============================================================

def score_bottom_accumulation(symbol, df): if df is None or len(df) < 120: return None

close = df["Close"].astype(float)
high = df["High"].astype(float)
low = df["Low"].astype(float)
vol = df["Volume"].astype(float)

last = float(close.iloc[-1])
prev = float(close.iloc[-2])

if last < 3:
    return None

ma20 = float(close.rolling(20).mean().iloc[-1])
ma50 = float(close.rolling(50).mean().iloc[-1])
avg_vol20 = float(vol.rolling(20).mean().iloc[-1])
avg_vol50 = float(vol.rolling(50).mean().iloc[-1])

if avg_vol50 < 500_000:
    return None

low52 = float(low.rolling(252).min().iloc[-1]) if len(low) >= 252 else float(low.min())
high60 = float(high.iloc[-60:].max())
low60 = float(low.iloc[-60:].min())
high20 = float(high.iloc[-20:].max())
low20 = float(low.iloc[-20:].min())

distance_from_low52 = ((last - low52) / low52) * 100 if low52 > 0 else 999
range20 = ((high20 - low20) / low20) * 100 if low20 > 0 else 999
range60 = ((high60 - low60) / low60) * 100 if low60 > 0 else 999

vol_ratio = float(vol.iloc[-1] / avg_vol20) if avg_vol20 > 0 else 0
change1 = ((last - prev) / prev) * 100 if prev > 0 else 0
change5 = ((last - float(close.iloc[-6])) / float(close.iloc[-6])) * 100 if len(close) >= 6 else 0
change20 = ((last - float(close.iloc[-21])) / float(close.iloc[-21])) * 100 if len(close) >= 21 else 0
rsi14 = float(calc_rsi(close, 14).iloc[-1])

obv = calc_obv(close, vol)
obv20 = float(obv.iloc[-1] - obv.iloc[-21]) if len(obv) >= 21 else 0
obv60 = float(obv.iloc[-1] - obv.iloc[-61]) if len(obv) >= 61 else 0

recent_close = close.iloc[-20:]
recent_vol = vol.iloc[-20:]
prev_recent_close = close.shift(1).iloc[-20:]

up_days = recent_close > prev_recent_close
down_days = recent_close < prev_recent_close

up_vol = float(recent_vol[up_days].sum())
down_vol = float(recent_vol[down_days].sum())
accumulation_ratio = up_vol / down_vol if down_vol > 0 else 1

breakout_level = high20
breakout_dist = ((breakout_level - last) / last) * 100 if last > 0 else 999

score = 0
reasons = []
warnings = []

if 5 <= distance_from_low52 <= 45:
    score += 20
    reasons.append("靠近52周低位")
elif distance_from_low52 < 5:
    score += 8
    warnings.append("太靠近低点，需确认止跌")
elif distance_from_low52 <= 70:
    score += 8

if range20 <= 18:
    score += 18
    reasons.append("20日窄幅横盘")
elif range20 <= 25:
    score += 10
    reasons.append("波动开始收窄")

if range60 <= 45:
    score += 10
    reasons.append("60日结构稳定")

if last >= ma20:
    score += 12
    reasons.append("站上MA20")
elif last >= ma20 * 0.97:
    score += 6
    reasons.append("接近MA20")

if ma20 >= ma50 * 0.95:
    score += 8
    reasons.append("MA20接近转强")

if accumulation_ratio >= 1.4:
    score += 18
    reasons.append("上涨日量能大于下跌日")
elif accumulation_ratio >= 1.15:
    score += 10
    reasons.append("疑似资金吸收")

if obv20 > 0:
    score += 10
    reasons.append("OBV 20日转强")
if obv60 > 0:
    score += 8
    reasons.append("OBV 60日改善")

if 45 <= rsi14 <= 62:
    score += 10
    reasons.append("RSI底部转强")
elif 62 < rsi14 <= 70:
    score += 5
    reasons.append("RSI偏强")
elif rsi14 > 75:
    score -= 10
    warnings.append("RSI偏热")

if 0 <= breakout_dist <= 6:
    score += 12
    reasons.append("接近平台突破")
elif breakout_dist <= 10:
    score += 6
    reasons.append("接近压力区")

if vol_ratio >= 1.5 and change1 > 0:
    score += 10
    reasons.append("今日放量转强")
elif vol_ratio >= 1.2:
    score += 5
    reasons.append("量能开始放大")

if change20 < -12:
    score -= 12
    warnings.append("20日仍偏弱")

if last < low60 * 1.08:
    warnings.append("仍在底部低位，需突破确认")

sector = find_sector(symbol)

atr14_series = calc_atr(df, 14)
atr14 = float(atr14_series.iloc[-1]) if not atr14_series.empty and not math.isnan(float(atr14_series.iloc[-1])) else last * 0.04

entry_low = round(max(last * 0.98, ma20 * 0.98), 2)
entry_high = round(min(breakout_level * 1.01, last * 1.04), 2)

sl = round(max(0.01, min(low20 * 0.97, last - atr14 * 1.2)), 2)
tp1 = round(last + atr14 * 2.2, 2)
tp2 = round(last + atr14 * 4.0, 2)

if score >= 75:
    grade = "A 底部吸筹强"
    action = "等突破确认 / 小仓埋伏"
elif score >= 62:
    grade = "B 底部吸筹观察"
    action = "加入观察，等放量突破"
elif score >= 52:
    grade = "C 早期观察"
    action = "还没确认，不急买"
else:
    return None

risk_plan = build_v116_risk_plan(
    symbol=symbol,
    entry_price=round((entry_low + entry_high) / 2, 2),
    sl=sl,
    tp1=tp1,
    tp2=tp2
)

return {
    "symbol": symbol,
    "sector": sector,
    "bottom_score": round(score, 1),
    "grade": grade,
    "action": action,
    "price": round(last, 2),
    "entry_low": entry_low,
    "entry_high": entry_high,
    "sl": sl,
    "tp1": tp1,
    "tp2": tp2,
    "rsi": round(rsi14, 1),
    "vol_ratio": round(vol_ratio, 2),
    "change5": round(change5, 2),
    "change20": round(change20, 2),
    "distance_from_low52": round(distance_from_low52, 2),
    "range20": round(range20, 2),
    "range60": round(range60, 2),
    "breakout_level": round(breakout_level, 2),
    "breakout_dist": round(breakout_dist, 2),
    "accumulation_ratio": round(accumulation_ratio, 2),
    "reasons": reasons[:5],
    "warnings": warnings[:4],
    "risk_plan": risk_plan,
}

def analyze_bottom_symbol(symbol): df = safe_download(symbol, period="1y", interval="1d") if df is None: return None return score_bottom_accumulation(symbol, df)

============================================================

大盘过滤

============================================================

def get_market_regime(): qqq = safe_download("QQQ", period="3mo", interval="1d") spy = safe_download("SPY", period="3mo", interval="1d")

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

============================================================

统计

============================================================

def build_sector_strength(results): m = {} for r in results: m.setdefault(r["sector"], []).append(r["score"])

ranked = []
for sector, scores in m.items():
    avg_score = sum(scores) / len(scores)
    ranked.append((sector, round(avg_score, 2), len(scores)))

ranked.sort(key=lambda x: x[1], reverse=True)
return ranked[:5]

============================================================

分批并发扫描

============================================================

def scan_batch(symbols): batch_results = []

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

def scan_all_daily(): all_results = [] batches = list(chunk_list(ALL_SYMBOLS, BATCH_SIZE)) total_batches = len(batches)

for idx, batch in enumerate(batches, start=1):
    print(f"[{now_kl_str()}] scanning batch {idx}/{total_batches}, symbols={len(batch)}")
    batch_results = scan_batch(batch)
    all_results.extend(batch_results)

    if idx < total_batches:
        time.sleep(BATCH_SLEEP)

all_results.sort(
    key=lambda x: (x.get("nextday_score", 0), x.get("score", 0)),
    reverse=True
)

return all_results

def scan_all_bottom(): all_results = [] batches = list(chunk_list(ALL_SYMBOLS, BATCH_SIZE)) total_batches = len(batches)

for idx, batch in enumerate(batches, start=1):
    print(f"[{now_kl_str()}] V13 bottom scanning batch {idx}/{total_batches}, symbols={len(batch)}")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(analyze_bottom_symbol, s): s for s in batch}

        for future in as_completed(futures):
            try:
                res = future.result()
                if res:
                    all_results.append(res)
            except Exception:
                pass

    if idx < total_batches:
        time.sleep(BATCH_SLEEP)

all_results.sort(key=lambda x: x["bottom_score"], reverse=True)
return all_results

============================================================

消息

============================================================

def build_premarket_message(results, regime): results = sorted( results, key=lambda x: (x.get("nextday_score", 0), x.get("score", 0)), reverse=True )

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
    "🚀 V13 隔日上涨 + 智能买点盘前扫描",
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
    lines.append(f"隔日上涨分数：{r.get('nextday_score', '-')}/100 | {r.get('nextday_grade', '-')}")
    lines.append(f"现价 {r['price']} | 策略：{r['buy_plan']}")
    lines.append(f"买区 {r['buy_low']} - {r['buy_high']} | 止损 {r['sl']}")
    lines.append(f"TP1 {r['tp1']} | TP2 {r['tp2']}")

    rp = r.get("risk_plan")
    if rp:
        lines.append(
            f"仓位建议：{rp['suggested_shares']}股 | "
            f"仓位 ${rp['position_value']} | "
            f"本单最大亏损约 ${rp['estimated_max_loss']}"
        )

    lines.append(f"量比 {r['vol_ratio']} | RSI {r['rsi']} | 5日 {r['change5']}%")
    lines.append(f"逻辑：{logic}")
    lines.append(f"提醒：{warn}")
    lines.append(f"备注：{r['buy_note']}")
    lines.append("")

if reserve5:
    lines.append("🌙 预备股 5只：")
    lines.append(", ".join([f"{x['symbol']}({x.get('nextday_score', x['score'])})" for x in reserve5]))
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
    lines.append("✅ 建议：优先看 nextday_score 高 + 可追突破 / 接近买点。")

return "\n".join(lines)

def build_close_message(results, regime): reserve = sorted( results, key=lambda x: (x.get("nextday_score", 0), x.get("score", 0)), reverse=True )[:10]

lines = [
    "🌙 V13 隔日上涨收盘扫描",
    f"{now_kl_str()} (KL)",
    "",
    f"📈 大盘状态：{regime['label']}",
    "",
    "明日预备爆发股 Top 10：",
    ""
]

for i, r in enumerate(reserve, start=1):
    rp = r.get("risk_plan")
    risk_text = ""

    if rp:
        risk_text = f" | 建议{rp['suggested_shares']}股 | 最大亏损${rp['estimated_max_loss']}"

    lines.append(
        f"{i}. {r['symbol']} | 隔日分 {r.get('nextday_score','-')}/100 | "
        f"{r.get('nextday_grade','-')} | 原分 {r['score']} | "
        f"策略 {r['buy_plan']} | 买区 {r['buy_low']}-{r['buy_high']} | "
        f"止损 {r['sl']} | TP1 {r['tp1']}{risk_text}"
    )

lines.append("")
lines.append("说明：下一交易日优先看隔日分数高、量能配合、不过热的票。")

return "\n".join(lines)

def build_bottom_message(results, regime): top = results[:10]

lines = [
    "📦 V13 底部吸筹扫描",
    f"{now_kl_str()} (KL)",
    "",
    f"📈 大盘状态：{regime['label']}",
    f"说明：{regime['text']}",
    "",
    "🟡 底部吸筹观察名单 Top 10：",
    ""
]

for i, r in enumerate(top, start=1):
    logic = " / ".join(r["reasons"][:3]) if r["reasons"] else "底部结构观察"
    warn = " / ".join(r["warnings"][:2]) if r["warnings"] else "暂无明显风险"

    lines.append(f"{i}. {r['symbol']} | {r['bottom_score']}分 | {r['grade']} [{r['sector']}]")
    lines.append(f"现价 {r['price']} | 行动：{r['action']}")
    lines.append(f"买区 {r['entry_low']} - {r['entry_high']} | 止损 {r['sl']}")
    lines.append(f"TP1 {r['tp1']} | TP2 {r['tp2']}")
    lines.append(f"52周低位距离 {r['distance_from_low52']}% | 20日波动 {r['range20']}%")
    lines.append(f"平台压力 {r['breakout_level']} | 距离突破 {r['breakout_dist']}%")
    lines.append(f"吸筹量比 {r['accumulation_ratio']} | 今日量比 {r['vol_ratio']} | RSI {r['rsi']}")

    rp = r.get("risk_plan")
    if rp:
        lines.append(
            f"仓位建议：{rp['suggested_shares']}股 | "
            f"仓位 ${rp['position_value']} | "
            f"本单最大亏损约 ${rp['estimated_max_loss']}"
        )

    lines.append(f"逻辑：{logic}")
    lines.append(f"提醒：{warn}")
    lines.append("")

lines.append("📌 V13说明：这些不是马上追高股，是提前观察底部吸筹股。")
lines.append("最佳买点：放量突破平台，或突破后回踩不破再进。")

return "\n".join(lines)

============================================================

主任务

============================================================

def run_premarket_scan(): global LAST_PREMARKET_SIGNATURE, LAST_MARKET_REGIME

if is_weekend_et():
    return {"status": "skip", "message": "weekend"}

reset_daily_intraday_flags()

regime = get_market_regime()
LAST_MARKET_REGIME = regime["label"]

results = scan_all_daily()

if not results:
    send_telegram("⚠️ V13 盘前扫描完成，但没有明显强势股。")
    return {"status": "ok", "count": 0}

top5 = results[:5]
signature = "|".join([f"{x['symbol']}:{x.get('nextday_score', x['score'])}" for x in top5])

if signature == LAST_PREMARKET_SIGNATURE:
    return {"status": "ok", "message": "unchanged"}

LAST_PREMARKET_SIGNATURE = signature
save_state()

msg = build_premarket_message(results, regime)
send_telegram(msg)

return {
    "status": "ok",
    "count": len(results),
    "top5": [x["symbol"] for x in top5],
    "results": top5
}

def run_close_scan(): global LAST_CLOSE_SIGNATURE

if is_weekend_et():
    return {"status": "skip", "message": "weekend"}

regime = get_market_regime()
results = scan_all_daily()

if not results:
    send_telegram("⚠️ V13 收盘扫描完成，但没有整理出明日预备股。")
    return {"status": "ok", "count": 0}

reserve = results[:10]
signature = "|".join([f"{x['symbol']}:{x.get('nextday_score', x['score'])}" for x in reserve])

if signature == LAST_CLOSE_SIGNATURE:
    return {"status": "ok", "message": "unchanged"}

LAST_CLOSE_SIGNATURE = signature
save_state()

msg = build_close_message(results, regime)
send_telegram(msg)

return {
    "status": "ok",
    "count": len(results),
    "reserve10": [x["symbol"] for x in reserve],
    "results": reserve
}

def run_bottom_scan(): global LAST_BOTTOM_SIGNATURE

if is_weekend_et():
    return {"status": "skip", "message": "weekend"}

regime = get_market_regime()
results = scan_all_bottom()

if not results:
    send_telegram("⚠️ V13 底部吸筹扫描完成，但暂时没有明显底部吸筹股。")
    return {"status": "ok", "count": 0}

top10 = results[:10]
signature = "|".join([f"{x['symbol']}:{x['bottom_score']}" for x in top10])

if signature == LAST_BOTTOM_SIGNATURE:
    return {"status": "ok", "message": "unchanged"}

LAST_BOTTOM_SIGNATURE = signature
save_state()

msg = build_bottom_message(results, regime)
send_telegram(msg)

return {
    "status": "ok",
    "count": len(results),
    "bottom10": [x["symbol"] for x in top10],
    "results": top10
}

============================================================

盘中突破

============================================================

def analyze_intraday_breakout(symbol): try: df = safe_download(symbol, period="5d", interval="15m")

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

def run_intraday_breakout_scan(): if is_weekend_et(): return {"status": "skip", "message": "weekend"}

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
        "⚡ V13 盘中突破提醒",
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

============================================================

回测

============================================================

def calc_backtest_score_at_index(df, i): if df is None or i < 70 or i + 1 >= len(df): return None

try:
    sub = df.iloc[:i+1].copy()
    result = score_stock_daily("BACKTEST", sub)

    if not result:
        return None

    return result.get("nextday_score", 0)

except Exception:
    return None

def run_backtest_core(days=90, top_n=5, min_score=0, period="6mo"): all_data = {}

for symbol in ALL_SYMBOLS:
    df = safe_download(symbol, period=period, interval="1d")

    if df is not None and len(df) >= 80:
        all_data[symbol] = df

if not all_data:
    return {
        "status": "error",
        "message": "No stock data downloaded"
    }

min_len = min(len(df) for df in all_data.values())

start_index = max(70, min_len - days - 1)
end_index = min_len - 2

results_by_day = []

for i in range(start_index, end_index):
    daily_scores = []

    for symbol, df in all_data.items():
        if len(df) <= i + 1:
            continue

        try:
            score = calc_backtest_score_at_index(df, i)

            if score is None or score < min_score:
                continue

            today_close = float(df["Close"].iloc[i])
            next_close = float(df["Close"].iloc[i + 1])
            next_high = float(df["High"].iloc[i + 1])
            next_low = float(df["Low"].iloc[i + 1])

            if today_close <= 0:
                continue

            nextday_return = (next_close / today_close - 1) * 100
            nextday_high_return = (next_high / today_close - 1) * 100
            nextday_low_return = (next_low / today_close - 1) * 100

            daily_scores.append({
                "symbol": symbol,
                "score": round(score, 1),
                "nextday_return": round(nextday_return, 2),
                "nextday_high_return": round(nextday_high_return, 2),
                "nextday_low_return": round(nextday_low_return, 2)
            })

        except Exception:
            continue

    daily_scores = sorted(daily_scores, key=lambda x: x["score"], reverse=True)

    if daily_scores:
        results_by_day.append(daily_scores[:top_n])

flat = [item for day in results_by_day for item in day]

if not flat:
    return {
        "status": "error",
        "message": "No backtest result found"
    }

def stat(items):
    if not items:
        return {}

    total = len(items)

    return {
        "total_trades": total,
        "nextday_close_green_rate": round(sum(1 for x in items if x["nextday_return"] > 0) / total * 100, 2),
        "intraday_2pct_hit_rate": round(sum(1 for x in items if x["nextday_high_return"] >= 2) / total * 100, 2),
        "intraday_5pct_hit_rate": round(sum(1 for x in items if x["nextday_high_return"] >= 5) / total * 100, 2),
        "avg_nextday_close_return": round(sum(x["nextday_return"] for x in items) / total, 2),
        "avg_nextday_high_return": round(sum(x["nextday_high_return"] for x in items) / total, 2),
        "avg_nextday_low_return": round(sum(x["nextday_low_return"] for x in items) / total, 2),
        "best_trade": max(items, key=lambda x: x["nextday_high_return"]),
        "worst_trade": min(items, key=lambda x: x["nextday_return"])
    }

top1_flat = [day[0] for day in results_by_day if len(day) >= 1]
top3_flat = [item for day in results_by_day for item in day[:3]]
top5_flat = [item for day in results_by_day for item in day[:5]]

return {
    "status": "ok",
    "backtest_days": days,
    "period": period,
    "stock_count": len(all_data),
    "top_n": top_n,
    "min_score": min_score,
    "summary_all": stat(flat),
    "top1_stats": stat(top1_flat),
    "top3_stats": stat(top3_flat),
    "top5_stats": stat(top5_flat),
}

============================================================

排程

============================================================

def scheduler_loop(): weekdays = [ schedule.every().monday, schedule.every().tuesday, schedule.every().wednesday, schedule.every().thursday, schedule.every().friday ]

for d in weekdays:
    d.at("21:00").do(run_premarket_scan)
    d.at("22:20").do(run_intraday_breakout_scan)
    d.at("23:20").do(run_intraday_breakout_scan)

schedule.every().day.at("04:20").do(run_close_scan)
schedule.every().day.at("04:35").do(run_bottom_scan)

while True:
    try:
        schedule.run_pending()
    except Exception as e:
        print("scheduler error:", e)

    time.sleep(15)

============================================================

Routes

============================================================

@app.route("/") def home(): return "V13 Nextday + Bottom Accumulation Scanner Running"

@app.route("/health") def health(): return jsonify({ "status": "healthy", "time_kl": now_kl_str(), "time_et": now_et().strftime("%Y-%m-%d %H:%M:%S"), "symbols": len(ALL_SYMBOLS), "max_workers": MAX_WORKERS, "batch_size": BATCH_SIZE, "version": "V13_nextday_bottom_accumulation" })

@app.route("/run-premarket") def route_run_premarket(): return jsonify(run_premarket_scan())

@app.route("/run-intraday") def route_run_intraday(): return jsonify(run_intraday_breakout_scan())

@app.route("/run-close") def route_run_close(): return jsonify(run_close_scan())

@app.route("/run-bottom") def route_run_bottom(): return jsonify(run_bottom_scan())

@app.route("/run-backtest") def route_run_backtest(): days = int(request.args.get("days", 90)) top_n = int(request.args.get("top", 5)) min_score = int(request.args.get("min_score", 0)) period = request.args.get("period", "6mo")

return jsonify(run_backtest_core(
    days=days,
    top_n=top_n,
    min_score=min_score,
    period=period
))

@app.route("/sectors") def route_sectors(): return jsonify({ "sector_count": len(SECTOR_POOLS), "total_symbols": len(ALL_SYMBOLS), "sectors": {k: len(v) for k, v in SECTOR_POOLS.items()} })

@app.route("/api/test-telegram") def route_test_telegram(): ok = send_telegram("✅ V13 Telegram Test Success") return jsonify({ "status": "ok" if ok else "error", "telegram_sent": ok })

============================================================

Start

============================================================

if name == "main": load_state()

t = threading.Thread(target=scheduler_loop, daemon=True)
t.start()

app.run(host="0.0.0.0", port=PORT)
