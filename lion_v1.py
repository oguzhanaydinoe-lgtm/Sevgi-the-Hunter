"""Independent read-only audit prototype. No veto or broker access."""
import json
import math
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

def audit(data_dir):
    root = Path(data_dir)
    findings = []
    try:
        state = json.loads((root / "paper_state_v4.json").read_text())
        report = json.loads((root / "report_v4.json").read_text())
        if state["positions"] != report["positions"]:
            findings.append("POSITION_MISMATCH")
        realized = sum(float(t["realized_pnl_usd"]) for t in state["trades"])
        if not math.isclose(realized,state["realized_pnl_usd"],abs_tol=.0001):
            findings.append("REALIZED_MISMATCH")
        marks = {q["symbol"]:q for q in report["results"]}
        if not report.get("unrealized_pnl_partial"):
            unrealized = sum((marks[p["symbol"]]["price"]-p["entry_price"])*p["quantity"]*(1 if p.get("side","SHORT")=="LONG" else -1) for p in state["positions"])
            if not math.isclose(unrealized,report["unrealized_pnl_usd"],abs_tol=.0001):
                findings.append("UNREALIZED_MISMATCH")
        else:
            findings.append("PARTIAL_MARKS")
        if report["analyzed_count"] != report["universe_count"]:
            findings.append("INCOMPLETE_UNIVERSE")
        with sqlite3.connect((root/"research_v4.sqlite3").resolve().as_uri()+"?mode=ro",uri=True) as conn:
            scan = conn.execute("SELECT id,observed_at FROM scans ORDER BY id DESC LIMIT 1").fetchone()
            if not scan or scan[1] != report["timestamp"]:
                findings.append("RESEARCH_TIMESTAMP_MISMATCH")
    except Exception as exc:
        findings.append(type(exc).__name__+": "+str(exc))
    result = {"event":"lion_shadow_audit","timestamp":datetime.now(timezone.utc).isoformat(),"status":"REVIEW" if findings else "PASS_LIMITED","findings":findings,"scope":"shared-source consistency only; no independent market feed"}
    print(json.dumps(result),flush=True)
    return result

if __name__ == "__main__":
    import os
    audit(os.environ["DATA_DIR"])
