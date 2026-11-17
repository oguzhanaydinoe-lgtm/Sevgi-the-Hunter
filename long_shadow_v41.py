"""V4.1 independent LONG shadow simulation. Does not modify V4 files or place broker orders."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from trade_bot import constituents, fetch_prices, save

DATA_DIR = Path(os.getenv("DATA_DIR", ".")).resolve()
STATE = DATA_DIR / "paper_state_v41_long_shadow.json"
REPORT = DATA_DIR / "report_v41_long_shadow.json"
START_CAPITAL = 100.0
STOP_PCT = 0.05
TAKE_PCT = 0.10
MAX_POSITIONS = 10
MAX_SINGLE_NOTIONAL_FRACTION = 0.10
MAX_GROSS_FRACTION = 1.0


def main():
    if os.getenv("DATA_DIR") is None or not DATA_DIR.is_dir():
        raise RuntimeError("Persistent DATA_DIR required; shadow state cannot use ephemeral storage")
    now = datetime.now(timezone.utc).isoformat()
    ny = datetime.now(ZoneInfo("America/New_York"))
    market_open = ny.weekday() < 5 and (9, 30) <= (ny.hour, ny.minute) < (16, 0)
    symbols = constituents()
    quotes, errors = fetch_prices(symbols)
    if len(quotes) != len(symbols):
        raise RuntimeError(f"Incomplete universe {len(quotes)}/{len(symbols)}; no state change")
    if STATE.exists():
        state = json.loads(STATE.read_text())
    else:
        state = {"realized_pnl_usd": 0.0, "positions": [], "trades": [], "last_processed_bar": {}}
    for field, default in (("positions", []), ("trades", []), ("last_processed_bar", {})):
        state.setdefault(field, default)
    state.setdefault("realized_pnl_usd", 0.0)
    current = str(ny.date())
    eligible = {s: q for s, q in quotes.items() if q["bar_date"] == current}
    events, kept = [], []
    for pos in state["positions"]:
        q = eligible.get(pos["symbol"]) if market_open else None
        if q and (q["price"] <= pos["stop_price"] or q["price"] >= pos["take_price"]):
            pnl = (q["price"] - pos["entry_price"]) * pos["quantity"]
            trade = {**pos, "exit_price": q["price"], "exit_time": now,
                     "realized_pnl_usd": round(pnl, 4),
                     "exit_reason": "STOP" if q["price"] <= pos["stop_price"] else "TAKE_PROFIT"}
            state["trades"].append(trade)
            state["realized_pnl_usd"] = round(state["realized_pnl_usd"] + pnl, 4)
            events.append({"action": "CLOSE_LONG", **trade})
        else:
            kept.append(pos)
    state["positions"] = kept
    unrealized_before = sum(
        (quotes[p["symbol"]]["price"] - p["entry_price"]) * p["quantity"]
        for p in kept if p["symbol"] in quotes
    )
    equity = START_CAPITAL + state["realized_pnl_usd"] + unrealized_before
    gross = sum(p["entry_price"] * p["quantity"] for p in kept)
    signals = [
        q for q in quotes.values()
        if q["price"] > q["ema20"] > q["ema50"] and q["rsi"] > 55
        and q["rsi"] < 75 and q["high_volume"]
    ]
    held = {p["symbol"] for p in kept}
    if market_open:
        for q in sorted(signals, key=lambda item: item["rsi"], reverse=True):
            if len(state["positions"]) >= MAX_POSITIONS or equity <= 0:
                break
            symbol = q["symbol"]
            if symbol in held or symbol not in eligible or state["last_processed_bar"].get(symbol) == current:
                continue
            notional = min(equity * MAX_SINGLE_NOTIONAL_FRACTION,
                           max(0, equity * MAX_GROSS_FRACTION - gross))
            if notional < 1:
                break
            qty = round(notional / q["price"], 8)
            if qty <= 0:
                continue
            pos = {"symbol": symbol, "entry_price": q["price"], "quantity": qty,
                   "entry_time": now, "entry_bar_date": current,
                   "stop_price": round(q["price"] * (1 - STOP_PCT), 4),
                   "take_price": round(q["price"] * (1 + TAKE_PCT), 4)}
            state["positions"].append(pos)
            state["last_processed_bar"][symbol] = current
            held.add(symbol)
            gross += notional
            events.append({"action": "OPEN_LONG", **pos})
    unrealized = sum(
        (quotes[p["symbol"]]["price"] - p["entry_price"]) * p["quantity"]
        for p in state["positions"] if p["symbol"] in quotes
    )
    report = {
        "timestamp": now, "mode": "V41_LONG_SHADOW_PAPER_ONLY",
        "market_open": market_open, "universe_count": len(symbols),
        "analyzed_count": len(quotes), "same_day_bars": len(eligible),
        "long_signals": len(signals), "starting_capital_usd": START_CAPITAL,
        "realized_pnl_usd": state["realized_pnl_usd"],
        "unrealized_pnl_usd": round(unrealized, 4),
        "estimated_equity_usd": round(START_CAPITAL + state["realized_pnl_usd"] + unrealized, 4),
        "positions": state["positions"], "events": events, "errors": errors,
        "assumptions": "Daily adjusted close simulation, not executable intraday quotes; no fees, slippage, dividends accounting, or actual intraday stops. No broker orders.",
    }
    save(STATE, state)
    save(REPORT, report)
    print(json.dumps({"event": "long_shadow_scan_complete", **report}, default=str), flush=True)


if __name__ == "__main__":
    main()
