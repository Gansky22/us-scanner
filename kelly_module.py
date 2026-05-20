from collections import defaultdict

def analyze_setup_kelly(trades, setup=None, market_regime=None, min_trades=20):
    inventory = defaultdict(list)
    r_list = []

    for t in trades:
        sym = t.get("symbol", "")
        mode = t.get("mode", "T")
        side = str(t.get("side", "")).upper()

        shares = int(t.get("shares", 0))
        price = float(t.get("price", 0))
        fees = float(t.get("fees", 0))

        stop_loss = float(t.get("stop_loss", 0))
        regime = str(t.get("market_regime", "ALL")).lower()

        if setup and mode.lower() != setup.lower():
            continue

        if market_regime and regime != market_regime.lower():
            continue

        # BUY
        if side in ["BUY", "B"]:
            inventory[sym].append({
                "shares": shares,
                "entry": price,
                "fees": fees,
                "stop_loss": stop_loss,
                "mode": mode,
                "regime": regime
            })

        # SELL
        elif side in ["SELL", "S"]:
            remaining = shares

            while remaining > 0 and inventory[sym]:
                lot = inventory[sym][0]

                used = min(remaining, lot["shares"])

                entry = lot["entry"]
                stop = lot["stop_loss"]

                risk = abs(entry - stop)

                if risk > 0:
                    profit = price - entry
                    r = profit / risk
                    r_list.append(r)

                lot["shares"] -= used
                remaining -= used

                if lot["shares"] <= 0:
                    inventory[sym].pop(0)

    total = len(r_list)

    if total < min_trades:
        return {
            "status": "insufficient_data",
            "trades": total,
            "message": f"Only {total} closed trades."
        }

    wins = [r for r in r_list if r > 0]
    losses = [r for r in r_list if r <= 0]

    win_rate = len(wins) / total
    loss_rate = 1 - win_rate

    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0

    if avg_loss == 0:
        return {
            "status": "invalid",
            "message": "No losses found."
        }

    payoff_ratio = avg_win / avg_loss

    # Kelly
    full_kelly = win_rate - (loss_rate / payoff_ratio)
    full_kelly = max(0, full_kelly)

    half_kelly = full_kelly * 0.5
    quarter_kelly = full_kelly * 0.25

    expectancy = (win_rate * avg_win) - (loss_rate * avg_loss)

    return {
        "status": "ok",
        "setup": setup or "ALL",
        "market_regime": market_regime or "ALL",
        "trades": total,
        "win_rate": round(win_rate * 100, 2),
        "avg_win_r": round(avg_win, 2),
        "avg_loss_r": round(avg_loss, 2),
        "payoff_ratio": round(payoff_ratio, 2),
        "expectancy_r": round(expectancy, 2),
        "full_kelly_pct": round(full_kelly * 100, 2),
        "half_kelly_pct": round(half_kelly * 100, 2),
        "quarter_kelly_pct": round(quarter_kelly * 100, 2),
        "recommended_position_pct": round(quarter_kelly * 100, 2)
    }
