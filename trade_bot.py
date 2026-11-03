
import os
import json
from datetime import datetime, timezone

import yfinance as yf
import pandas as pd

CAPITAL = 100.0
RISK_PER_TRADE = 0.02
SYMBOLS = ["NVDA", "TSLA", "AMD", "PLTR", "AAPL", "QQQ"]


def analyze(symbol):
    df = yf.download(
        symbol,
        period="3mo",
        interval="1d",
        auto_adjust=True,
        progress=False
    )

    if df.empty or len(df) < 50:
        return None

    close = df["Close"].squeeze()
    volume = df["Volume"].squeeze()

    ema20 = close.ewm(span=20).mean()
    ema50 = close.ewm(span=50).mean()

    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14).mean()
    rs = gain / loss.replace(0, float("nan"))
    rsi = 100 - 100 / (1 + rs)

    price = float(close.iloc[-1])
    bearish = (
        price < float(ema20.iloc[-1])
        and float(ema20.iloc[-1]) < float(ema50.iloc[-1])
        and float(rsi.iloc[-1]) < 45
        and float(volume.iloc[-1]) >
        float(volume.tail(20).mean())
    )

    return {
        "symbol": symbol,
        "price": round(price, 2),
        "rsi": round(float(rsi.iloc[-1]), 2),
        "signal": "SHORT" if bearish else "WAIT",
        "risk_budget_usd": round(CAPITAL * RISK_PER_TRADE, 2)
    }


def main():
    results = []

    for symbol in SYMBOLS:
        try:
            result = analyze(symbol)
            if result:
                results.append(result)
                print(result)
        except Exception as error:
            print(f"{symbol}: ERROR - {error}")

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "capital_usd": CAPITAL,
        "mode": "PAPER_ONLY",
        "results": results
    }

    with open("report.json", "w") as file:
        json.dump(report, file, indent=2)


if __name__ == "__main__":
    main()
    
