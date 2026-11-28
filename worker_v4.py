"""Continuous, single-process Nasdaq-100 paper-only signal scanner."""
import json
import os
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from trade_bot import main
from market_calendar import is_market_open

INTERVAL_SECONDS = max(300, int(os.getenv("SCAN_INTERVAL_SECONDS", "300")))

def log(event, **fields):
    print(json.dumps({"event": event, "timestamp": datetime.now(timezone.utc).isoformat(), **fields}), flush=True)

if __name__ == "__main__":
    log("worker_started", interval_seconds=INTERVAL_SECONDS, mode=os.getenv("SCAN_ONLY", "1"))
    while True:
        started = time.monotonic()
        try:
            ny = datetime.now(ZoneInfo("America/New_York"))
            # Use exchange calendar including holidays and early closes.
            if is_market_open(ny):
                main()
            else:
                log("market_closed", local_time=ny.isoformat())
        except Exception as exc:
            log("scan_failed", error_type=type(exc).__name__, error=str(exc))
        time.sleep(max(1, INTERVAL_SECONDS - (time.monotonic() - started)))
