"""V4.1 stop-loss research: offline, read-only comparison; no live orders/state writes.

Uses historical OHLC bars, next-session open entries, and conservative stop/target
ordering (stop first if both touched). Results are hypotheses, not executable fills.
"""
import argparse
import json
import math
from datetime import datetime, timezone
import pandas as pd
import yfinance as yf

VARIANTS = ("fixed_5pct", "atr_2x", "trailing_atr_2x")


def analyze(frame, variant, horizon=20):
    df = frame[["Open", "High", "Low", "Close"]].dropna().copy()
    if len(df) < 80:
        raise ValueError("Need >=80 complete OHLC bars")
    previous_close = df["Close"].shift()
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - previous_close).abs(),
        (df["Low"] - previous_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(14, min_periods=14).mean().shift(1)
    ema20 = df["Close"].ewm(span=20, adjust=False).mean()
    ema50 = df["Close"].ewm(span=50, adjust=False).mean()
    trades = []
    i = 60
    while i < len(df) - 1:
        # Signal on completed bar i; entry at next bar's open (no lookahead).
        if not (df["Close"].iloc[i] > ema20.iloc[i] > ema50.iloc[i]):
            i += 1
            continue
        entry_index = i + 1
        entry = float(df["Open"].iloc[entry_index])
        a = float(atr.iloc[entry_index])
        if not all(math.isfinite(v) and v > 0 for v in (entry, a)):
            i += 1
            continue
        distance = entry * 0.05 if variant == "fixed_5pct" else 2 * a
        stop = max(0.01, entry - distance)
        target = entry + 2 * distance
        peak = entry
        last = min(entry_index + horizon - 1, len(df) - 1)
        exit_price = float(df["Close"].iloc[last])
        reason = "TIME"
        exit_index = last
        for j in range(entry_index, last + 1):
            op = float(df["Open"].iloc[j])
            lo = float(df["Low"].iloc[j])
            hi = float(df["High"].iloc[j])
            # Gap-through stops fill at the worse opening price.
            if op <= stop:
                exit_price, reason, exit_index = op, "STOP_GAP", j
                break
            if op >= target:
                exit_price, reason, exit_index = target, "TARGET_GAP", j
                break
            if lo <= stop:
                exit_price, reason, exit_index = stop, "STOP", j
                break
            if hi >= target:
                exit_price, reason, exit_index = target, "TARGET", j
                break
            if variant == "trailing_atr_2x":
                # Update after bar closes, only effective on the NEXT bar.
                peak = max(peak, float(df["Close"].iloc[j]))
                stop = max(stop, peak - 2 * a)
        trades.append({
            "entry_date": str(df.index[entry_index].date()),
            "exit_date": str(df.index[exit_index].date()),
            "entry": round(entry, 4), "exit": round(exit_price, 4),
            "return_pct": round(100 * (exit_price / entry - 1), 4),
            "reason": reason,
        })
        i = exit_index + 1  # Non-overlapping positions per symbol.
    returns = [t["return_pct"] for t in trades]
    return {
        "variant": variant, "trades": len(trades),
        "win_rate_pct": round(100 * sum(r > 0 for r in returns) / len(returns), 2) if returns else None,
        "mean_trade_return_pct": round(sum(returns) / len(returns), 4) if returns else None,
        "trade_log": trades,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="QQQ")
    parser.add_argument("--period", default="2y")
    args = parser.parse_args()
    df = yf.download(args.symbol, period=args.period, interval="1d",
                     auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if df.empty:
        raise RuntimeError("No OHLC history")
    results = [analyze(df, variant) for variant in VARIANTS]
    print(json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": args.symbol, "mode": "OFFLINE_RESEARCH_ONLY",
        "results": results,
        "limitations": "Illustrative long-only trend-entry comparison; no fees, slippage, borrow, dividends or portfolio risk; in-sample only. Stop-first OHLC ambiguity; no claim of validated alpha.",
    }, indent=2))


if __name__ == "__main__":
    main()
