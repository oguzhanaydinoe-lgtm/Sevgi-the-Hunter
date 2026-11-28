import unittest
from datetime import datetime
from market_calendar import is_market_open

class CalendarTests(unittest.TestCase):
    def test_sessions(self):
        for stamp, expected in [
            ("2026-11-26T18:00:00+00:00", False),
            ("2026-11-27T14:29:59+00:00", False),
            ("2026-11-27T14:30:00+00:00", True),
            ("2026-11-27T17:59:59+00:00", True),
            ("2026-11-27T18:00:00+00:00", False),
            ("2026-11-28T16:00:00+00:00", False),
            ("2026-11-30T20:59:59+00:00", True),
            ("2026-11-30T21:00:00+00:00", False),
            ("2026-07-06T13:30:00+00:00", True),
            ("2026-07-06T20:00:00+00:00", False),
        ]:
            with self.subTest(stamp=stamp):
                self.assertEqual(is_market_open(datetime.fromisoformat(stamp)), expected)
    def test_naive_rejected(self):
        with self.assertRaises(ValueError):
            is_market_open(datetime(2026,11,27,10))
if __name__ == "__main__":
    unittest.main()
