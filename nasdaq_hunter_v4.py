"""V4 Nasdaq-100 paper-only scanner. Does not place trades."""
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from io import StringIO

import pandas as pd
import requests
import yfinance as yf

LOG = logging.getLogger("hunter")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
OUTPUT = DATA_DIR / "hunter_scan_v4.json"
WIKI = "https://en.wikipedia.org/wiki/Nasdaq-100"
BATCH_SIZE = 15
MAX_QUOTE_AGE_MINUTES = 20


def universe():
    """Fail closed if the Nasdaq-100 membership source cannot be validated."""
    response = requests.get(WIKI, headers={"User-Agent": "NasdaqHunterResearch/0.1 (paper trading)"}, timeout=25)
    response.raise_for_status()
    tables = pd.read_html(StringIO(response.text))
    for table in tables:
        cols = {str(c).strip().lower(): c for c in table.columns}
        key = next((cols[k] for k in ("ticker", "ticker symbol") if k in cols), None)
        if key is None or len(table) < 90 or len(table) > 110:
            continue
        symbols = sorted(set(str(x).strip().replace(".", "-") for x in table[key].dropna()))
        if 90 <= len(symbols) <= 110 and all(s.replace("-", "").isalnum() for s in symbols):
            return symbols
    raise RuntimeError("Cannot validate Nasdaq-100 constituents; no scan performed")


def score_one(symbol, frame):
    if frame.empty or len(frame) < 55:
        raise ValueError("insufficient bars")
    close = pd.to_numeric(frame["Close"], errors="coerce").dropna()
    volume = pd.to_numeric(frame["Volume"], errors="coerce").reindex(close.index)
    if len(close) < 55:
        raise ValueError("insufficient valid closes")
    bar = pd.Timestamp(close.index[-1])
    if bar.tzinfo is None:
        raise ValueError("missing timezone")
    age = datetime.now(timezone.utc) - bar.to_pydatetime().astimezone(timezone.utc)
    if not timedelta(minutes=-1) <= age <= timedelta(minutes=MAX_QUOTE_AGE_MINUTES):
        raise ValueError("stale quote")
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    g, l = float(gain.iloc[-1]), float(loss.iloc[-1])
    rsi = 100.0 if l == 0 and g > 0 else (50.0 if l == 0 else 100 - 100 / (1 + g / l))
    price = float(close.iloc[-1])
    prev = float(close.iloc[-2])
    if price <= 0 or prev <= 0 or abs(price / prev - 1) > .25:
        raise ValueError("invalid price or jump")
    vol_avg = float(volume.iloc[-21:-1].mean())
    vol_ratio = float(volume.iloc[-1]) / vol_avg if vol_avg > 0 else 0
    bearish = price < float(ema20.iloc[-1]) < float(ema50.iloc[-1]) and rsi < 45 and vol_ratio > 1
    strength = max(0, 45 - rsi) + max(0, (float(ema20.iloc[-1]) / price - 1) * 100) + min(vol_ratio, 5)
    return {"symbol": symbol, "price": round(price, 4), "rsi": round(rsi, 2),
            "volume_ratio": round(vol_ratio, 2), "score": round(strength, 3),
            "signal": "SHORT" if bearish else "WAIT", "bar_time": bar.isoformat()}


def scan():
    symbols = universe()
    results, errors = [], {}
    for offset in range(0, len(symbols), BATCH_SIZE):
        batch = symbols[offset:offset + BATCH_SIZE]
        try:
            data = yf.download(batch, period="5d", interval="5m", auto_adjust=True,
                               progress=False, threads=False, group_by="ticker", timeout=30)
            for symbol in batch:
                try:
                    frame = data[symbol] if isinstance(data.columns, pd.MultiIndex) else data
                    results.append(score_one(symbol, frame))
                except Exception as exc:
                    errors[symbol] = str(exc)
        except Exception as exc:
            errors.update({s: str(exc) for s in batch})
        if offset + BATCH_SIZE < len(symbols):
            time.sleep(2)
    ranked = sorted((r for r in results if r["signal"] == "SHORT"),
                    key=lambda r: r["score"], reverse=True)
    report = {"timestamp": datetime.now(timezone.utc).isoformat(),
              "mode": "V4_SCANNER_ONLY_NO_ORDERS", "universe_count": len(symbols),
              "valid_quotes": len(results), "error_count": len(errors),
              "top_short_candidates": ranked[:10], "results": results, "errors": errors}
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp = OUTPUT.with_suffix(".tmp")
    temp.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    temp.replace(OUTPUT)
    LOG.info("Scan complete: %s/%s valid, %s candidates, %s errors",
             len(results), len(symbols), len(ranked), len(errors))
    return report


if __name__ == "__main__":
    scan()
