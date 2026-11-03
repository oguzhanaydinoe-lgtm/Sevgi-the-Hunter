
import json
from datetime import datetime, timezone

import yfinance as yf

CAPITAL = 100.0
RISK_PER_TRADE = 0.02

SYMBOLS = ["AAPL", "TSLA", "NVDA", "AMD", "SPY"]

def analyze(symbol):
    df = yf.download(
        symbol,
        period="3mo",
        interval="1d",
        auto_adjust=True,
        progress=False
    )

    if df.empty or len(df) < 25:
        return None

    close = df["Close"].squeeze()
    volume = df["Volume"].squeeze()

    ma20 = close.rolling(20).mean()
    avg_volume = volume.rolling(20).mean()

    price = float(close.iloc[-1])
    moving_avg = float(ma20.iloc[-1])
    current_volume = float(volume.iloc[-1])
    mean_volume = float(avg_volume.iloc[-1])

    bearish = price < moving_avg
    high_volume = current_volume > mean_volume

    signal = "SHORT" if bearish and high_volume else "WAIT"

    return {
        "symbol": symbol,
        "price": round(price, 2),
        "ma20": round(moving_avg, 2),
        "signal": signal,
        "risk_budget": round(CAPITAL * RISK_PER_TRADE, 2)
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
            print(f"{symbol}: {error}")

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "capital": CAPITAL,
        "mode": "PAPER_SHORT_ONLY",
        "results": results
    }

    with open("signals.json", "w") as file:
        json.dump(report, file, indent=2)

if __name__ == "__main__":
    main()
