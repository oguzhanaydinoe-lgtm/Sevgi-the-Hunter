"""V4 isolated paper-only short portfolio engine; no broker connectivity."""
import json
import math
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

CAPITAL = 100.0
RISK_PER_TRADE = 0.05
STOP_PCT = 0.05
TAKE_PCT = 0.10
MAX_POSITIONS = 3
MAX_TOTAL_RISK_PCT = 0.10
MAX_GROSS_EXPOSURE = 1.0
MIN_VALID_COVERAGE = 0.80
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
SCAN_PATH = DATA_DIR / "hunter_scan_v4.json"
STATE_PATH = DATA_DIR / "paper_state_v4.json"
REPORT_PATH = DATA_DIR / "paper_report_v4.json"


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(obj, indent=2, allow_nan=False) + "\n")
    temp.replace(path)


def new_state():
    return {"capital": CAPITAL, "realized_pnl": 0.0, "positions": [],
            "trades": [], "last_entry_bar": {}, "last_scan_timestamp": None}


def finite_positive(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    except (ValueError, TypeError):
        return None


def run(scan, state=None, now=None):
    now = now or datetime.now(timezone.utc)
    state = state if state is not None else new_state()
    if state.get("capital") != CAPITAL:
        raise ValueError("V4 state capital mismatch")
    positions = state["positions"]
    if not isinstance(positions, list) or len(positions) > MAX_POSITIONS:
        raise ValueError("Invalid existing positions")
    stamp = datetime.fromisoformat(scan["timestamp"].replace("Z", "+00:00"))
    if stamp.tzinfo is None or not timedelta(minutes=-1) <= now - stamp <= timedelta(minutes=20):
        raise ValueError("Scan is stale or has no timezone")
    if scan.get("mode") != "V4_SCANNER_ONLY_NO_ORDERS":
        raise ValueError("Unexpected scan mode")
    universe_count = scan.get("universe_count", 0)
    if not 90 <= universe_count <= 110:
        raise ValueError("Invalid Nasdaq-100 universe size")
    results = scan.get("results", [])
    if not isinstance(results, list):
        raise ValueError("Invalid results")
    quotes = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        price = finite_positive(item.get("price"))
        try:
            bar = datetime.fromisoformat(item["bar_time"].replace("Z", "+00:00"))
        except (KeyError, ValueError, TypeError):
            continue
        if price and bar.tzinfo and timedelta(minutes=-1) <= now - bar <= timedelta(minutes=20):
            quotes[item.get("symbol")] = item
    if len(quotes) < universe_count * MIN_VALID_COVERAGE:
        raise ValueError("Insufficient valid scan coverage; no state changes")
    if state.get("last_scan_timestamp") == scan["timestamp"]:
        raise ValueError("Scan already processed; no state changes")

    events = []
    remaining = []
    for pos in positions:
        quote = quotes.get(pos["symbol"])
        if quote is None:
            remaining.append(pos)
            continue
        price = float(quote["price"])
        if price >= pos["stop_price"] or price <= pos["take_price"]:
            pnl = (pos["entry_price"] - price) * pos["quantity"]
            trade = {**pos, "exit_price": price, "exit_time": now.isoformat(),
                     "pnl": round(pnl, 6),
                     "reason": "STOP" if price >= pos["stop_price"] else "TAKE"}
            state["trades"].append(trade)
            state["realized_pnl"] = round(state["realized_pnl"] + pnl, 6)
            events.append({"action": "CLOSE_SHORT", **trade})
        else:
            remaining.append(pos)
    state["positions"] = remaining
    equity = CAPITAL + state["realized_pnl"] + sum(
        (p["entry_price"] - float(quotes[p["symbol"]]["price"])) * p["quantity"]
        for p in remaining if p["symbol"] in quotes)
    if equity <= 0:
        raise ValueError("Non-positive equity")
    gross = sum(p["entry_price"] * p["quantity"] for p in remaining)
    risk = sum(max(0.0, (p["stop_price"] - p["entry_price"]) * p["quantity"])
               for p in remaining)
    candidates = sorted((q for q in quotes.values() if q.get("signal") == "SHORT"),
                        key=lambda q: float(q.get("score", 0)), reverse=True)
    held = {p["symbol"] for p in remaining}
    for q in candidates:
        if len(remaining) >= MAX_POSITIONS:
            break
        symbol = q["symbol"]
        if symbol in held or state["last_entry_bar"].get(symbol) == q["bar_time"]:
            continue
        price = float(q["price"])
        remaining_risk = max(0.0, equity * MAX_TOTAL_RISK_PCT - risk)
        remaining_notional = max(0.0, equity * MAX_GROSS_EXPOSURE - gross)
        target_risk = min(equity * RISK_PER_TRADE, remaining_risk)
        notional = min(remaining_notional, target_risk / STOP_PCT)
        if notional < 0.01:
            break
        quantity = round(notional / price, 8)
        if quantity <= 0:
            continue
        actual_notional = quantity * price
        actual_risk = actual_notional * STOP_PCT
        pos = {"symbol": symbol, "entry_price": price, "quantity": quantity,
               "stop_price": price * (1 + STOP_PCT),
               "take_price": price * (1 - TAKE_PCT),
               "entry_time": now.isoformat(), "entry_bar_time": q["bar_time"]}
        remaining.append(pos)
        held.add(symbol)
        gross += actual_notional
        risk += actual_risk
        state["last_entry_bar"][symbol] = q["bar_time"]
        events.append({"action": "OPEN_SHORT", **pos})
    state["last_scan_timestamp"] = scan["timestamp"]
    unrealized = sum((p["entry_price"] - float(quotes[p["symbol"]]["price"])) * p["quantity"]
                     for p in remaining if p["symbol"] in quotes)
    report = {"timestamp": now.isoformat(), "mode": "PAPER_ONLY_SHORT_V4",
              "scan_timestamp": scan["timestamp"], "scanned": len(quotes),
              "universe_count": universe_count, "positions": remaining,
              "position_count": len(remaining), "gross_notional": round(gross, 4),
              "stop_risk_usd": round(risk, 4),
              "realized_pnl_usd": state["realized_pnl"],
              "unrealized_pnl_usd": round(unrealized, 4),
              "estimated_equity_usd": round(CAPITAL + state["realized_pnl"] + unrealized, 4),
              "events": events,
              "assumptions": "Paper only; fractional shares; next available 5m quote; no fees, borrow costs, slippage, guaranteed stop execution, or broker orders."}
    return state, report


def main():
    scan = json.loads(SCAN_PATH.read_text())
    state = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else new_state()
    updated, report = run(scan, state)
    save(STATE_PATH, updated)
    save(REPORT_PATH, report)
    print(json.dumps({"positions": report["position_count"],
                      "events": len(report["events"]), "scanned": report["scanned"]}))


if __name__ == "__main__":
    main()
