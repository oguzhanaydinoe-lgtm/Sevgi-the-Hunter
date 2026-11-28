"""Shared Nasdaq regular-session calendar; errors propagate, never assume open."""
from datetime import datetime, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo
import pandas_market_calendars as mcal

NY = ZoneInfo("America/New_York")

@lru_cache(maxsize=32)
def session_bounds(day):
    schedule = mcal.get_calendar("NASDAQ").schedule(start_date=day, end_date=day)
    if schedule.empty:
        return None
    row = schedule.iloc[0]
    return row["market_open"].to_pydatetime(), row["market_close"].to_pydatetime()

def is_market_open(now=None):
    now = now if now is not None else datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Timezone-aware timestamp required")
    bounds = session_bounds(now.astimezone(NY).date())
    return bounds is not None and bounds[0] <= now < bounds[1]
