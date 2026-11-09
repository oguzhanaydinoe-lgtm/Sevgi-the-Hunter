"""Continuous, single-process Nasdaq-100 paper-only signal scanner."""
import json
import os
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from trade_bot import main

INTERVAL_SECONDS = max(300, int(os.getenv("SCAN_INTERVAL_SECONDS", "300")))

def log(event, **fields):
    print(json.dumps({"event": event, "timestamp": datetime.now(timezone.utc).isoformat(), **fields}), flush=True)

if __name__ == "__main__":
    log("worker_started", interval_seconds=INTERVAL_SECONDS, mode=os.getenv("SCAN_ONLY", "1"))
    while True:
        started = time.monotonic()
        try:
            ny = datetime.now(ZoneInfo("America/New_York"))
            # Scan on US trading days; retain process and heartbeat outside hours.
            if ny.weekday() < 5 and (9, 30) <= (ny.hour, ny.minute) < (16, 0):
                main()
            else:
                log("market_closed", local_time=ny.isoformat())
        except Exception as exc:
            log("scan_failed", error_type=type(exc).__name__, error=str(exc))
        time.sleep(max(1, INTERVAL_SECONDS - (time.monotonic() - started)))
