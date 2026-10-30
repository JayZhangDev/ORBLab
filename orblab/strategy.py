"""Opening Range Breakout (ORB) on MES: the single source of truth for the rules.

The backtest and the live paper runner both call `evaluate_day`, so a paper
trade is decided by exactly the code that produced the backtest numbers.

Rules (all times US/Eastern, parameters fixed up front, never fitted):
  1. Opening range = high/low of 1-minute bars from 09:30 until 09:30 + or_minutes.
  2. After the range closes, build 5-minute bars. The FIRST 5-minute bar that
     CLOSES above the range high (long) or below the range low (short) is the signal.
  3. Enter at the OPEN of the first 1-minute bar after the signal bar completes
     (the first price you could actually trade), plus slippage.
  4. Stop = opposite side of the range. Target = target_r x risk from entry.
  5. One trade per day. If neither level is hit, exit at the last bar <= 15:55.
  6. If one 1-minute bar touches both stop and target, the stop is assumed to hit
     first (pessimistic by design).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

ET = "America/New_York"


@dataclass(frozen=True)
class Params:
    or_minutes: int = 30
    bar_minutes: int = 5
    target_r: float = 2.0
    tick: float = 0.25
    slip_ticks: float = 1.0          # per side, applied to entry and exit
    commission_rt: float = 1.40      # $ per contract, round trip
    point_value: float = 5.0         # MES = $5 per index point
    session_start: time = time(9, 30)
    flat_time: time = time(15, 55)

    @property
    def slip(self) -> float:
        return self.slip_ticks * self.tick


@dataclass
class DayResult:
    trade_date: date
    status: str                      # NO_RANGE | NO_SIGNAL | PENDING_ENTRY | SKIPPED | OPEN | CLOSED
    or_high: float | None = None
    or_low: float | None = None
    direction: str | None = None     # long | short
    signal_time: datetime | None = None
    entry_time: datetime | None = None
    entry: float | None = None
    stop: float | None = None
    target: float | None = None
    exit_time: datetime | None = None
    exit: float | None = None
    exit_reason: str | None = None   # target | stop | eod
    points: float | None = None      # net of slippage, before commission
    pnl_usd: float | None = None     # 1 contract, net of slippage and commission
    r: float | None = None           # pnl_usd / (risk in $)
    note: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _ts(day: date, t: time, tz) -> pd.Timestamp:
    return pd.Timestamp(datetime.combine(day, t)).tz_localize(tz)


def evaluate_day(bars: pd.DataFrame, p: Params = Params(), final: bool = True) -> DayResult:
    """Evaluate one session.

    bars:  1-minute OHLC for a single ET trading day, tz-aware ET index, sorted.
    final: True when the day's data is complete (backtests). False for live use:
           the trade is then reported OPEN until the stop, target or flat time hits.
    """
    if bars.empty:
        return DayResult(date.today(), "NO_RANGE", note="no bars")
    tz = bars.index.tz
    day = bars.index[0].date()
    t0 = _ts(day, p.session_start, tz)
    or_end = t0 + pd.Timedelta(minutes=p.or_minutes)
    flat = _ts(day, p.flat_time, tz)
    res = DayResult(day, "NO_RANGE")

    or_bars = bars[(bars.index >= t0) & (bars.index < or_end)]
    last_seen = bars.index[-1]
    if len(or_bars) < 0.8 * p.or_minutes or last_seen < or_end - pd.Timedelta(minutes=1):
        res.note = "opening range incomplete"
        return res
    res.or_high, res.or_low = float(or_bars["high"].max()), float(or_bars["low"].min())

    post = bars[(bars.index >= or_end) & (bars.index <= flat)]
    if post.empty:
        res.status = "NO_SIGNAL"
        return res

    # --- signal: first completed bar_minutes bar that closes outside the range ---
    step = pd.Timedelta(minutes=p.bar_minutes)
    bucket = ((post.index - or_end) // step).astype(int)
    sig_dir, sig_end = None, None
    for k, grp in post.groupby(bucket):
        bar_start = or_end + k * step
        complete = last_seen >= bar_start + step - pd.Timedelta(minutes=1)
        if not complete:
            break
        close = float(grp["close"].iloc[-1])
        if close > res.or_high:
            sig_dir = "long"
        elif close < res.or_low:
            sig_dir = "short"
        if sig_dir:
            sig_end = bar_start + step
            break
    if sig_dir is None:
        res.status = "NO_SIGNAL"
        return res
    res.direction, res.signal_time = sig_dir, sig_end - pd.Timedelta(minutes=1)

    # --- entry: open of the first 1-minute bar at/after the signal bar's end ---
    fill = post[post.index >= sig_end]
    if fill.empty:
        res.status = "PENDING_ENTRY"
        return res
    d = 1 if sig_dir == "long" else -1
    entry_bar_time = fill.index[0]
    entry = float(fill["open"].iloc[0]) + d * p.slip
    stop = res.or_low if d == 1 else res.or_high
    risk = (entry - stop) * d
    if risk <= 0:
        res.status, res.note = "SKIPPED", "entry gapped through the stop level"
        return res
    target = entry + d * risk * p.target_r
    res.entry_time, res.entry, res.stop, res.target = entry_bar_time, entry, stop, target

    # --- exit: scan 1-minute bars from the entry bar; stop wins ties ---
    path = fill
    lows, highs = path["low"].to_numpy(), path["high"].to_numpy()
    stop_hit = lows <= stop if d == 1 else highs >= stop
    tgt_hit = highs >= target if d == 1 else lows <= target
    hit = stop_hit | tgt_hit
    if hit.any():
        i = int(np.argmax(hit))
        reason = "stop" if stop_hit[i] else "target"
        raw_exit = stop if reason == "stop" else target
        exit_time = path.index[i]
    elif final or last_seen >= flat:
        i = len(path) - 1
        reason, raw_exit, exit_time = "eod", float(path["close"].iloc[-1]), path.index[-1]
    else:
        res.status = "OPEN"
        res.note = f"last price {float(path['close'].iloc[-1]):.2f}"
        return res

    exit_price = raw_exit - d * p.slip
    points = (exit_price - entry) * d
    pnl = points * p.point_value - p.commission_rt
    res.status, res.exit_time, res.exit, res.exit_reason = "CLOSED", exit_time, exit_price, reason
    res.points, res.pnl_usd = points, pnl
    res.r = pnl / (risk * p.point_value)
    return res
