"""Nasdaq-100 paper-only short scanner. No broker connectivity."""
import json
import math
import time
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

CAPITAL = 100.0
RISK_PER_TRADE = 0.05
MAX_PORTFOLIO_RISK = 0.10
MAX_POSITIONS = 3
STOP_PCT = 0.05
TAKE_PCT = 0.10
MAX_GROSS_EXPOSURE = 1.0
STATE = Path("paper_state_v4.json")
REPORT = Path("report_v4.json")
NASDAQ_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"


def save(path, obj):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(obj, indent=2, allow_nan=False) + "\n")
    temp.replace(path)


def constituents():
    """Fetch the full Nasdaq-100 universe from independent sources; never use a partial list."""
    import re
    errors = []

    # Primary: Wikipedia's Nasdaq-100 constituent table.
    try:
        response = requests.get(
            NASDAQ_URL,
            headers={"User-Agent": "Mozilla/5.0 (compatible; research-scanner/1.0)"},
            timeout=25,
        )
        response.raise_for_status()
        tables = pd.read_html(StringIO(response.text))
        for table in tables:
            columns = {
                c: " ".join(map(str, c)).lower() if isinstance(c, tuple)
                else str(c).lower()
                for c in table.columns
            }
            symbol_col = next(
                (c for c, label in columns.items()
                 if any(word in label for word in ("ticker", "symbol"))),
                None,
            )
            if symbol_col is None:
                continue
            values = [
                str(x).strip().upper().replace(".", "-")
                for x in table[symbol_col].dropna()
            ]
            symbols = sorted({
                x for x in values
                if re.fullmatch(r"[A-Z]{1,5}(?:-[A-Z])?", x)
            })
            if 90 <= len(symbols) <= 110:
                print(f"Universe: Wikipedia, {len(symbols)} symbols", flush=True)
                return symbols
        errors.append("Wikipedia: no valid constituent table found")
    except Exception as exc:
        errors.append(f"Wikipedia: {type(exc).__name__}: {exc}")

    # Secondary: Nasdaq's public Nasdaq-100 constituent API.
    try:
        response = requests.get(
            "https://api.nasdaq.com/api/quote/list-type/nasdaq100",
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://www.nasdaq.com",
                "Referer": "https://www.nasdaq.com/",
            },
            timeout=25,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") or {}
        rows = data.get("rows") or data.get("data") or []
        if isinstance(rows, dict):
            rows = rows.get("rows") or []
        symbols = sorted({
            str(row.get("symbol") or row.get("ticker") or "").strip().upper().replace(".", "-")
            for row in rows if isinstance(row, dict)
        })
        symbols = [x for x in symbols if re.fullmatch(r"[A-Z]{1,5}(?:-[A-Z])?", x)]
        if 90 <= len(symbols) <= 110:
            print(f"Universe: Nasdaq API, {len(symbols)} symbols", flush=True)
            return symbols
        errors.append(f"Nasdaq API: {len(symbols)} valid symbols")
    except Exception as exc:
        errors.append(f"Nasdaq API: {type(exc).__name__}: {exc}")

    raise RuntimeError(
        "Nasdaq-100 constituent sources unavailable; refusing partial universe. "
        + " | ".join(errors)
    )


def load_state():
    if STATE.exists():
        state = json.loads(STATE.read_text())
    else:
        state = {"starting_capital_usd": CAPITAL, "realized_pnl_usd": 0.0,
                 "positions": [], "trades": [], "last_processed_bar": {}}
    state.setdefault("positions", [])
    state.setdefault("trades", [])
    state.setdefault("last_processed_bar", {})
    state.setdefault("realized_pnl_usd", 0.0)
    return state


def fetch_prices(symbols):
    quotes, errors = {}, {}
    for start in range(0, len(symbols), 20):
        chunk = symbols[start:start + 20]
        data = None
        for attempt in range(3):
            try:
                data = yf.download(
                    chunk, period="4mo", interval="1d", auto_adjust=True,
                    progress=False, threads=False, group_by="ticker",
                )
                if data is not None and not data.empty:
                    break
                raise ValueError("Empty batch")
            except Exception as exc:
                if attempt == 2:
                    for s in chunk:
                        errors[s] = str(exc)
                else:
                    time.sleep(2 * (attempt + 1))
        if data is None or data.empty:
            continue
        for symbol in chunk:
            try:
                frame = data[symbol] if isinstance(data.columns, pd.MultiIndex) else data
                close = frame["Close"].dropna()
                volume = frame["Volume"].reindex(close.index).fillna(0)
                if len(close) < 50:
                    raise ValueError("Insufficient history")
                price = float(close.iloc[-1])
                previous = float(close.iloc[-2])
                bar_date = close.index[-1].date()
                age = (datetime.now(timezone.utc).date() - bar_date).days
                if not (0 <= age <= 5 and math.isfinite(price) and price > 0
                        and previous > 0 and abs(price / previous - 1) <= 0.25):
                    raise ValueError("Stale or implausible market data")
                ema20 = close.ewm(span=20, adjust=False).mean()
                ema50 = close.ewm(span=50, adjust=False).mean()
                delta = close.diff()
                gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
                loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
                last_gain, last_loss = float(gain.iloc[-1]), float(loss.iloc[-1])
                rsi = 100.0 if last_loss == 0 else 100 - 100 / (1 + last_gain / last_loss)
                high_volume = float(volume.iloc[-1]) > float(volume.tail(20).mean())
                bearish = (price < float(ema20.iloc[-1]) < float(ema50.iloc[-1])
                           and rsi < 45 and high_volume)
                quotes[symbol] = {
                    "symbol": symbol, "price": round(price, 4), "rsi": round(rsi, 2),
                    "ema20": round(float(ema20.iloc[-1]), 4),
                    "ema50": round(float(ema50.iloc[-1]), 4),
                    "high_volume": bool(high_volume),
                    "signal": "SHORT" if bearish else "WAIT",
                    "bar_date": str(bar_date),
                }
            except Exception as exc:
                errors[symbol] = str(exc)
    return quotes, errors


def main():
    now = datetime.now(timezone.utc).isoformat()
    symbols = constituents()
    quotes, errors = fetch_prices(symbols)
    if len(quotes) < 80:
        raise RuntimeError(f"Only {len(quotes)}/{len(symbols)} valid quotes; no state changed")
    state = load_state()
    events = []
    kept = []
    for pos in state["positions"]:
        item = quotes.get(pos["symbol"])
        if not item:
            kept.append(pos)
            continue
        price = item["price"]
        if price >= pos["stop_price"] or price <= pos["take_price"]:
            pnl = (pos["entry_price"] - price) * pos["quantity"]
            trade = {**pos, "exit_price": price, "exit_time": now,
                     "realized_pnl_usd": round(pnl, 4),
                     "exit_reason": "STOP" if price >= pos["stop_price"] else "TAKE_PROFIT"}
            state["trades"].append(trade)
            state["realized_pnl_usd"] = round(state["realized_pnl_usd"] + pnl, 4)
            events.append({"action": "CLOSE_SHORT", **trade})
        else:
            kept.append(pos)
    state["positions"] = kept
    equity = CAPITAL + state["realized_pnl_usd"] + sum(
        (p["entry_price"] - quotes[p["symbol"]]["price"]) * p["quantity"]
        for p in kept if p["symbol"] in quotes
    )
    # Conservative allocation: no leverage; risk budget uses remaining stop loss.
    gross = sum(p["entry_price"] * p["quantity"] for p in kept)
    risk = sum(max(0.0, (p["stop_price"] - p["entry_price"]) * p["quantity"]) for p in kept)
    candidates = sorted(
        (q for q in quotes.values() if q["signal"] == "SHORT"
         and q["symbol"] not in {p["symbol"] for p in kept}
         and state["last_processed_bar"].get(q["symbol"]) != q["bar_date"]),
        key=lambda q: q["rsi"],
    )
    for item in candidates:
        if len(state["positions"]) >= MAX_POSITIONS or equity <= 0:
            break
        remaining_risk = max(0.0, equity * MAX_PORTFOLIO_RISK - risk)
        remaining_exposure = max(0.0, equity * MAX_GROSS_EXPOSURE - gross)
        notional = min(equity * RISK_PER_TRADE / STOP_PCT,
                       remaining_risk / STOP_PCT, remaining_exposure,
                       equity / MAX_POSITIONS)
        if notional < 1.0:
            break
        price = item["price"]
        qty = round(notional / price, 8)
        if qty <= 0:
            continue
        pos = {"symbol": item["symbol"], "entry_price": price, "quantity": qty,
               "entry_time": now, "stop_price": round(price * (1 + STOP_PCT), 4),
               "take_price": round(price * (1 - TAKE_PCT), 4),
               "entry_bar_date": item["bar_date"]}
        state["positions"].append(pos)
        state["last_processed_bar"][item["symbol"]] = item["bar_date"]
        gross += notional
        risk += notional * STOP_PCT
        events.append({"action": "OPEN_SHORT", **pos})
    unrealized = sum(
        (p["entry_price"] - quotes[p["symbol"]]["price"]) * p["quantity"]
        for p in state["positions"] if p["symbol"] in quotes
    )
    report = {
        "timestamp": now, "mode": "PAPER_ONLY_SHORT_V4",
        "universe_count": len(symbols), "analyzed_count": len(quotes),
        "short_signals": sum(q["signal"] == "SHORT" for q in quotes.values()),
        "starting_capital_usd": CAPITAL,
        "realized_pnl_usd": state["realized_pnl_usd"],
        "unrealized_pnl_usd": round(unrealized, 4),
        "estimated_equity_usd": round(CAPITAL + state["realized_pnl_usd"] + unrealized, 4),
        "positions": state["positions"], "events": events,
        "results": sorted(quotes.values(), key=lambda q: (q["signal"] != "SHORT", q["rsi"])),
        "errors": errors,
        "assumptions": "Daily close paper simulation, fractional shares; no borrow fees, slippage, commissions or intraday stop execution. No broker orders.",
    }
    save(STATE, state)
    save(REPORT, report)
    print(json.dumps({k: report[k] for k in (
        "universe_count", "analyzed_count", "short_signals", "positions",
        "events", "estimated_equity_usd")}, indent=2))


if __name__ == "__main__":
    main()
