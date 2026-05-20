import math
import pandas as pd

TRADE_LOG_FILE = "trades.csv"

def safe_float(x, default=0.0):
    try:
        return float(x)
    except:
        return default


def load_trades():
    try:
        return pd.read_csv(TRADE_LOG_FILE)
    except:
        return pd.DataFrame()


def calculate_r_multiple(row):
    entry = safe_float(row.get("entry_price"))
    exit_price = safe_float(row.get("exit_price"))
    stop_loss = safe_float(row.get("stop_loss"))
    side = str(row.get("side", "BUY")).upper()

    if entry <= 0 or stop_loss <= 0 or exit_price <= 0:
        return None

    risk = abs(entry - stop_loss)
    if risk <= 0:
        return None

    if side in ["BUY", "LONG"]:
        profit = exit_price - entry
    else:
        profit = entry - exit_price

    return profit / risk


def analyze_setup_kelly(setup=None, market_regime=None, min_trades=20):
    df = load_trades()

    if df.empty:
        return {
            "status": "empty",
            "message": "No trade records found."
        }

    # 只分析已平仓交易
    if "exit_price" in df.columns:
        df = df[df["exit_price"].notna()]

    if setup and "setup" in df.columns:
        df = df[df["setup"].astype(str).str.lower() == setup.lower()]

    if market_regime and "market_regime" in df.columns:
        df = df[df["market_regime"].astype(str).str.lower() == market_regime.lower()]

    r_list = []

    for _, row in df.iterrows():
        r = calculate_r_multiple(row)
        if r is not None:
            r_list.append(r)

    total = len(r_list)

    if total < min_trades:
        return {
            "status": "insufficient_data",
            "trades": total,
            "message": f"Only {total} valid trades. Need at least {min_trades} trades for Kelly estimate."
        }

    wins = [r for r in r_list if r > 0]
    losses = [r for r in r_list if r <= 0]

    win_rate = len(wins) / total
    loss_rate = 1 - win_rate

    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0

    if avg_loss <= 0:
        return {
            "status": "invalid",
            "message": "Average loss is zero. Kelly cannot be calculated."
        }

    payoff_ratio = avg_win / avg_loss

    # Kelly Formula: f = p - q / b
    full_kelly = win_rate - (loss_rate / payoff_ratio)

    # 不允许负数
    full_kelly = max(0, full_kelly)

    half_kelly = full_kelly * 0.5
    quarter_kelly = full_kelly * 0.25

    expectancy = (win_rate * avg_win) - (loss_rate * avg_loss)

    max_consecutive_losses = calculate_max_consecutive_losses(r_list)

    risk_level = classify_kelly_risk(full_kelly, expectancy, max_consecutive_losses)

    return {
        "status": "ok",
        "setup": setup or "ALL",
        "market_regime": market_regime or "ALL",
        "trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate * 100, 2),
        "avg_win_r": round(avg_win, 2),
        "avg_loss_r": round(avg_loss, 2),
        "payoff_ratio": round(payoff_ratio, 2),
        "expectancy_r": round(expectancy, 2),
        "full_kelly_pct": round(full_kelly * 100, 2),
        "half_kelly_pct": round(half_kelly * 100, 2),
        "quarter_kelly_pct": round(quarter_kelly * 100, 2),
        "max_consecutive_losses": max_consecutive_losses,
        "recommended_position_pct": round(quarter_kelly * 100, 2),
        "risk_level": risk_level
    }


def calculate_max_consecutive_losses(r_list):
    max_loss_streak = 0
    current = 0

    for r in r_list:
        if r <= 0:
            current += 1
            max_loss_streak = max(max_loss_streak, current)
        else:
            current = 0

    return max_loss_streak


def classify_kelly_risk(full_kelly, expectancy, max_loss_streak):
    if expectancy <= 0:
        return "❌ Negative expectancy - Do not trade this setup"

    if full_kelly <= 0:
        return "❌ No edge - Kelly is zero"

    if max_loss_streak >= 5:
        return "⚠️ High drawdown risk - Use very small size"

    if full_kelly > 0.30:
        return "⚠️ Aggressive edge estimate - Use Quarter Kelly only"

    if full_kelly > 0.15:
        return "✅ Good edge - Quarter Kelly recommended"

    return "🟡 Small edge - Trade smaller size"
