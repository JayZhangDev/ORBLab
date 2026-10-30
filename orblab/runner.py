"""Paper trade runner: finds the ORB setup each trading day and logs the outcome.

    python -m orblab.runner                  # live: MES=F via yfinance, run daily from ~09:25 ET
    python -m orblab.runner --symbol SPY     # SPY proxy (point value defaults to 100 shares)
    python -m orblab.runner --replay 2025-03-14 --db replay.db    # re-run a past day from the CSV

It starts any time before 09:30 ET, waits for the open, then polls until the trade
resolves (stop, target) or 15:55 ET. Every poll re-runs `strategy.evaluate_day` on the
bars so far, so the journal row is created at entry and updated to WIN/LOSS on exit.

Data caveat: Yahoo's free 1-minute futures bars are delayed (typically ~10-15 minutes)
and can have gaps. That delay doesn't change results (decisions use bar timestamps) but
real fills would be worse than paper fills. This is research, not execution.
"""
from __future__ import annotations

import argparse
import time as _time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from orblab import journal
from orblab.strategy import ET, Params, evaluate_day

POLL_SECONDS = 30
SYMBOLS = {"MES": ("MES=F", 5.0), "SPY": ("SPY", 100.0)}   # (yfinance ticker, $/point per lot)


def fetch_today(ticker: str) -> pd.DataFrame:
    import yfinance as yf
    df = yf.Ticker(ticker).history(period="2d", interval="1m", prepost=False, auto_adjust=False)
    if df.empty:
        return df
    df = df.rename(columns=str.lower)[["open", "high", "low", "close"]]
    df.index = df.index.tz_convert(ET)
    today = datetime.now(df.index.tz).date()
    return df[[d == today for d in df.index.date]]


def replay_bars(csv: Path, day: str) -> pd.DataFrame:
    from orblab.backtest import load_1m
    df = load_1m(csv)
    d = pd.Timestamp(day).date()
    return df[[x == d for x in df.index.date]][["open", "high", "low", "close"]]


def log_result(res, symbol: str, p: Params, source: str, db: Path | None) -> None:
    rid = journal.upsert_orb(res, symbol, p, source=source, db=db)
    if rid:
        tail = f"{res.exit_reason} @ {res.exit:.2f} ({res.r:+.2f}R)" if res.status == "CLOSED" else "open"
        print(f"  journal #{rid}: {res.direction} entry {res.entry:.2f} stop {res.stop:.2f} "
              f"target {res.target:.2f} -> {tail}")


def run_live(symbol: str, p: Params, db: Path | None) -> None:
    ticker, _ = SYMBOLS[symbol]
    now = datetime.now(pd.Timestamp.now(tz=ET).tz)
    close = now.replace(hour=16, minute=5, second=0, microsecond=0)
    if now.weekday() >= 5:
        print("Weekend, nothing to do.")
        return
    open_t = now.replace(hour=9, minute=30, second=0, microsecond=0)
    if now < open_t:
        wait = (open_t - now).total_seconds()
        print(f"Waiting {wait / 60:.0f} min for the 09:30 ET open ...")
        _time.sleep(wait)
    last_status = None
    while datetime.now(now.tzinfo) < close:
        try:
            bars = fetch_today(ticker)
        except Exception as e:                              # network blips shouldn't kill the day
            print(f"  fetch failed ({e}); retrying")
            _time.sleep(POLL_SECONDS)
            continue
        if bars.empty:
            if datetime.now(now.tzinfo) > open_t + timedelta(minutes=45):
                print("No bars 45 min after the open; market holiday? Exiting.")
                return
            _time.sleep(POLL_SECONDS)
            continue
        res = evaluate_day(bars, p, final=False)
        if res.status != last_status:
            print(f"{datetime.now():%H:%M:%S} status={res.status} {res.note}")
            last_status = res.status
        log_result(res, symbol, p, "paper", db)
        if res.status in ("CLOSED", "SKIPPED"):
            return
        if res.status == "NO_SIGNAL" and bars.index[-1].time() >= p.flat_time:
            print("No breakout today. No trade.")
            return
        _time.sleep(POLL_SECONDS)
    # Past the close with the trade still open: finalize at the last bar.
    bars = fetch_today(ticker)
    if not bars.empty:
        log_result(evaluate_day(bars, p, final=True), symbol, p, "paper", db)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--symbol", choices=list(SYMBOLS), default="MES")
    ap.add_argument("--replay", metavar="YYYY-MM-DD", help="re-run a past day from the local CSV")
    ap.add_argument("--csv", type=Path, default=Path(__file__).resolve().parent.parent / "data" / "mes_1min_2022_2026.csv")
    ap.add_argument("--db", type=Path, help="journal database (default: journal.db)")
    args = ap.parse_args()

    p = Params(point_value=SYMBOLS[args.symbol][1])
    if args.replay:
        bars = replay_bars(args.csv, args.replay)
        res = evaluate_day(bars, p, final=True)
        print(f"{args.replay}: {res.status} {res.note}")
        log_result(res, args.symbol, p, "replay", args.db)
    else:
        run_live(args.symbol, p, args.db)


if __name__ == "__main__":
    main()
