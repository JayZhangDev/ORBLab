"""Unit tests for the ORB rules. Run: python -m unittest discover -s tests"""
import unittest
from datetime import date

import pandas as pd

from orblab.strategy import ET, Params, evaluate_day

P = Params(slip_ticks=0, commission_rt=0.0)     # zero costs so the arithmetic is easy to check
DAY = date(2025, 3, 14)


def make_day(post: dict[str, tuple], or_range=(100.0, 110.0), end="10:30") -> pd.DataFrame:
    """Flat 1-min bars for 09:30-10:00 spanning or_range, then `post` overrides
    {"HH:MM": (open, high, low, close)}; every other minute until `end` sits mid-range."""
    lo, hi = or_range
    idx = pd.date_range(f"{DAY} 09:30", f"{DAY} {end}", freq="1min", tz=ET)
    mid = (lo + hi) / 2
    rows = []
    for t in idx:
        key = t.strftime("%H:%M")
        if key in post:
            rows.append(post[key])
        elif t.hour == 9 or (t.hour == 10 and False):
            rows.append((mid, hi if key == "09:45" else mid, lo if key == "09:50" else mid, mid))
        else:
            rows.append((mid, mid, mid, mid))
    return pd.DataFrame(rows, index=idx, columns=["open", "high", "low", "close"])


class ORBRules(unittest.TestCase):
    def test_range_is_09_30_to_10_00(self):
        r = evaluate_day(make_day({}), P)
        self.assertEqual((r.or_low, r.or_high), (100.0, 110.0))
        self.assertEqual(r.status, "NO_SIGNAL")

    def test_long_hits_target(self):
        # 10:00-10:04 bar closes at 112 (> 110) -> signal; entry = open of 10:05 bar.
        post = {"10:04": (110, 112, 110, 112), "10:05": (112, 113, 111, 112),
                "10:20": (115, 140, 115, 139)}
        r = evaluate_day(make_day(post), P)
        self.assertEqual((r.direction, r.status, r.exit_reason), ("long", "CLOSED", "target"))
        self.assertEqual(r.entry, 112)                 # next bar's OPEN, not the signal close
        self.assertEqual(r.stop, 100)                  # opposite side of the range
        self.assertEqual(r.target, 112 + 2 * 12)       # 2R
        self.assertAlmostEqual(r.r, 2.0)

    def test_short_hits_stop(self):
        post = {"10:04": (100, 100, 98, 98), "10:05": (98, 99, 97, 98), "10:10": (98, 111, 98, 110)}
        r = evaluate_day(make_day(post), P)
        self.assertEqual((r.direction, r.exit_reason, r.stop), ("short", "stop", 110))
        self.assertAlmostEqual(r.r, -1.0)

    def test_stop_wins_when_bar_touches_both(self):
        post = {"10:04": (110, 112, 110, 112), "10:05": (112, 130, 99, 112)}
        r = evaluate_day(make_day(post), P)
        self.assertEqual(r.exit_reason, "stop")

    def test_signal_needs_a_close_outside_range(self):
        # Wick above the range, but every 5-min close back inside -> no trade.
        post = {"10:02": (108, 115, 108, 109)}
        self.assertEqual(evaluate_day(make_day(post), P).status, "NO_SIGNAL")

    def test_signal_waits_for_the_bar_to_complete(self):
        # Live: data stops at 10:03, so the 10:00-10:04 bar isn't complete -> no signal yet.
        post = {"10:03": (110, 113, 110, 113)}
        r = evaluate_day(make_day(post, end="10:03"), P, final=False)
        self.assertEqual(r.status, "NO_SIGNAL")

    def test_open_trade_reported_open_when_live(self):
        post = {"10:04": (110, 112, 110, 112), "10:05": (112, 113, 111, 112)}
        r = evaluate_day(make_day(post, end="10:30"), P, final=False)
        self.assertEqual(r.status, "OPEN")

    def test_eod_exit_when_nothing_hits(self):
        post = {"10:04": (110, 112, 110, 112), "10:05": (112, 113, 111, 112)}
        r = evaluate_day(make_day(post, end="10:30"), P, final=True)
        self.assertEqual((r.status, r.exit_reason), ("CLOSED", "eod"))

    def test_costs_reduce_r(self):
        post = {"10:04": (110, 112, 110, 112), "10:05": (112, 113, 111, 112), "10:20": (115, 140, 115, 139)}
        free = evaluate_day(make_day(post), P)
        costly = evaluate_day(make_day(post), Params())
        self.assertLess(costly.r, free.r)
        self.assertLess(costly.pnl_usd, free.pnl_usd)

    def test_incomplete_range_gives_no_trade(self):
        r = evaluate_day(make_day({}, end="09:45"), P, final=False)
        self.assertEqual(r.status, "NO_RANGE")


if __name__ == "__main__":
    unittest.main()
