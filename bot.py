import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import yfinance as yf

CAPITAL = float(os.getenv("CAPITAL", "100"))
RISK_PER_TRADE = float(os.getenv("RISK_PER_TRADE", "0.05"))
INTERVAL = max(60, int(os.getenv("INTERVAL", "300")))
SYMBOLS = ["AAPL", "TSLA", "NVDA", "AMD", "SPY"]
OUTPUT = Path(os.getenv("SIGNALS_PATH", "/tmp/signals.json"))


def analyze(symbol):
    df = yf.download(symbol, period="3mo", interval="1d",
                     auto_adjust=True, progress=False, threads=False)
    if df.empty or len(df) < 25:
        return {"symbol": symbol, "signal": "NO_DATA"}
    close = df["Close"].squeeze()
    volume = df["Volume"].squeeze()
    ma20 = close.rolling(20).mean()
    avg_volume = volume.rolling(20).mean()
    price = float(close.iloc[-1])
    moving_avg = float(ma20.iloc[-1])
    high_volume = float(volume.iloc[-1]) > float(avg_volume.iloc[-1])
    signal = "SHORT" if price < moving_avg and high_volume else "WAIT"
    return {"symbol": symbol, "price": round(price, 2),
            "ma20": round(moving_avg, 2), "signal": signal,
            "risk_budget": round(CAPITAL * RISK_PER_TRADE, 2),
            "data_date": str(close.index[-1])}


def scan():
    results = []
    for symbol in SYMBOLS:
        try:
            result = analyze(symbol)
            results.append(result)
            print(json.dumps({"event": "signal", **result}), flush=True)
        except Exception as error:
            print(json.dumps({"event": "symbol_error", "symbol": symbol,
                              "error": str(error)}), flush=True)
    report = {"timestamp": datetime.now(timezone.utc).isoformat(),
              "capital": CAPITAL, "mode": "PAPER_SIGNAL_ONLY",
              "risk_per_trade": RISK_PER_TRADE, "results": results}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2))
    temporary.replace(OUTPUT)
    print(json.dumps({"event": "scan_complete", "timestamp": report["timestamp"],
                      "symbols_scanned": len(results), "output": str(OUTPUT)}),
          flush=True)


def main():
    print(json.dumps({"event": "startup", "mode": "PAPER_SIGNAL_ONLY",
                      "interval_seconds": INTERVAL, "symbols": SYMBOLS,
                      "risk_per_trade": RISK_PER_TRADE}), flush=True)
    while True:
        try:
            scan()
        except Exception as error:
            print(json.dumps({"event": "scan_error", "error": str(error)}),
                  flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
