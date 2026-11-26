"""Paper research only; never writes portfolio state or submits orders."""
import sqlite3
import json
from pathlib import Path
from datetime import datetime, timezone

def record_scan(data_dir, quotes, errors, universe, observed_at):
    db = Path(data_dir) / "research_v4.sqlite3"
    with sqlite3.connect(db, timeout=15) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS scans (id INTEGER PRIMARY KEY, observed_at TEXT NOT NULL, universe TEXT NOT NULL, errors TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS observations (scan_id INTEGER NOT NULL, symbol TEXT NOT NULL, bar_date TEXT NOT NULL, price REAL NOT NULL, signal TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(scan_id,symbol))")
        conn.execute("CREATE TABLE IF NOT EXISTS hypotheses (symbol TEXT, side TEXT, bar_date TEXT, entry_at TEXT, entry_price REAL, exit_at TEXT, exit_price REAL, return_pct REAL, reason TEXT, PRIMARY KEY(symbol,side,bar_date))")
        scan_id = conn.execute("INSERT INTO scans(observed_at,universe,errors) VALUES(?,?,?)", (observed_at,json.dumps(universe),json.dumps(errors))).lastrowid
        today = datetime.now(timezone.utc).astimezone(__import__("zoneinfo").ZoneInfo("America/New_York")).date().isoformat()
        for symbol,q in quotes.items():
            price = q["price"]
            conn.execute("INSERT INTO observations VALUES(?,?,?,?,?,?)",(scan_id,symbol,q["bar_date"],price,q["signal"],json.dumps(q,allow_nan=False)))
            if q["bar_date"] != today:
                continue
            for direction,bar,entry in conn.execute("SELECT side,bar_date,entry_price FROM hypotheses WHERE symbol=? AND exit_at IS NULL",(symbol,)).fetchall():
                ret = (price/entry-1)*(1 if direction=="LONG" else -1)
                if ret <= -.05 or ret >= .10:
                    conn.execute("UPDATE hypotheses SET exit_at=?,exit_price=?,return_pct=?,reason=? WHERE symbol=? AND side=? AND bar_date=?",(observed_at,price,100*ret,"STOP" if ret<=-.05 else "TAKE",symbol,direction,bar))
            if q["signal"] in ("LONG","SHORT"):
                conn.execute("INSERT OR IGNORE INTO hypotheses(symbol,side,bar_date,entry_at,entry_price) VALUES(?,?,?,?,?)",(symbol,q["signal"],q["bar_date"],observed_at,price))
        count = conn.execute("SELECT COUNT(*) FROM hypotheses WHERE exit_at IS NULL").fetchone()[0]
    print(json.dumps({"event":"research_scan_saved","scan_id":scan_id,"observations":len(quotes),"open_hypotheses":count,"timestamp":observed_at,"price_basis":"daily_bar_snapshot_not_execution"}),flush=True)
