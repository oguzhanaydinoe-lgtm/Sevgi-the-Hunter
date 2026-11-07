"""Offline regression checks for V4 paper engine."""
import unittest
from datetime import datetime, timezone
from paper_engine_v4 import run, new_state

NOW = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)

def scan(short_count=3):
    timestamp = NOW.isoformat()
    rows = [{"symbol": f"S{i:03}", "price": 10.0, "bar_time": timestamp,
             "score": 100-i, "signal": "SHORT" if i < short_count else "WAIT"}
            for i in range(100)]
    return {"timestamp": timestamp, "mode": "V4_SCANNER_ONLY_NO_ORDERS",
            "universe_count": 100, "results": rows}

class EngineTests(unittest.TestCase):
    def test_exposure_and_risk_limits(self):
        state, report = run(scan(), new_state(), NOW)
        self.assertLessEqual(report["position_count"], 3)
        self.assertLessEqual(report["gross_notional"], 100.001)
        self.assertLessEqual(report["stop_risk_usd"], 10.001)
        self.assertTrue(all(e["action"] == "OPEN_SHORT" for e in report["events"]))
        self.assertEqual(state["last_scan_timestamp"], NOW.isoformat())

    def test_duplicate_scan_rejected(self):
        state, _ = run(scan(), new_state(), NOW)
        with self.assertRaisesRegex(ValueError, "already processed"):
            run(scan(), state, NOW)

    def test_low_coverage_rejected(self):
        sample = scan()
        sample["results"] = sample["results"][:30]
        with self.assertRaisesRegex(ValueError, "coverage"):
            run(sample, new_state(), NOW)

    def test_stale_scan_rejected(self):
        sample = scan()
        sample["timestamp"] = "2026-11-08T15:00:00+00:00"
        with self.assertRaisesRegex(ValueError, "stale"):
            run(sample, new_state(), NOW)

    def test_close_on_stop(self):
        state, _ = run(scan(1), new_state(), NOW)
        newer = NOW.replace(minute=5)
        follow = scan(0)
        follow["timestamp"] = newer.isoformat()
        for row in follow["results"]:
            row["bar_time"] = newer.isoformat()
            if row["symbol"] == "S000":
                row["price"] = 11.0
        updated, report = run(follow, state, newer)
        self.assertEqual(report["position_count"], 0)
        self.assertEqual(report["events"][0]["action"], "CLOSE_SHORT")
        self.assertLess(updated["realized_pnl"], 0)

if __name__ == "__main__":
    unittest.main()
