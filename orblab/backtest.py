"""ORB backtest with an in-sample / out-of-sample split.

    python -m orblab.backtest [--csv data/mes_1min_2022_2026.csv]

Nothing is optimized: the rules are fixed before the data is touched, so the
split guards against us fooling ourselves rather than against curve-fitting.
Writes results/backtest.json (read by the dashboard) and results/backtest_trades.csv.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from orblab import stats
from orblab.strategy import ET, Params, evaluate_day

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = ROOT / "data" / "mes_1min_2022_2026.csv"
RESULTS = ROOT / "results"
SPLIT = pd.Timestamp("2023-01-01").date()      # pre-2023 in-sample, 2023+ out-of-sample


def load_1m(csv: Path) -> pd.DataFrame:
    """1-minute bars in US/Eastern, regular hours only (09:25-16:00)."""
    df = pd.read_csv(csv, usecols=["ts_event", "open", "high", "low", "close", "instrument_id"])
    df["ts_event"] = pd.to_datetime(df["ts_event"], utc=True)
    df = df.set_index("ts_event").tz_convert(ET).sort_index()
    df = df[~df.index.duplicated(keep="first")]
    t = df.index.time
    return df[(t >= pd.Timestamp("09:25").time()) & (t <= pd.Timestamp("16:00").time())]


def run(df: pd.DataFrame, p: Params) -> tuple[pd.DataFrame, dict]:
    """Evaluate every session. Returns (trades, day counts)."""
    rows, counts = [], {"sessions": 0, "roll_days_skipped": 0, "no_range": 0, "no_signal": 0, "skipped": 0}
    for day, bars in df.groupby(df.index.date):
        if day.weekday() >= 5:
            continue
        counts["sessions"] += 1
        if bars["instrument_id"].nunique() > 1:      # contract roll inside the session
            counts["roll_days_skipped"] += 1
            continue
        res = evaluate_day(bars[["open", "high", "low", "close"]], p, final=True)
        if res.status == "CLOSED":
            rows.append(res.as_dict())
        else:
            key = {"NO_RANGE": "no_range", "NO_SIGNAL": "no_signal"}.get(res.status, "skipped")
            counts[key] += 1
    trades = pd.DataFrame(rows)
    return trades, counts


def window(trades: pd.DataFrame, sessions: pd.Series, lo, hi) -> tuple[pd.DataFrame, int]:
    m = (trades["trade_date"] >= lo) & (trades["trade_date"] < hi)
    n_days = int(((sessions >= lo) & (sessions < hi)).sum())
    return trades[m], n_days


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    args = ap.parse_args()

    print(f"Loading {args.csv} ...")
    df = load_1m(args.csv)
    sessions = pd.Series(sorted({d for d in df.index.date if d.weekday() < 5}))
    far = pd.Timestamp("2100-01-01").date()
    first, last = sessions.iloc[0], sessions.iloc[-1]

    base = Params()
    trades, counts = run(df, base)
    print(f"{counts['sessions']} sessions, {len(trades)} trades")

    periods = {"ALL": (first, far), "IN_SAMPLE": (first, SPLIT), "OUT_OF_SAMPLE": (SPLIT, far)}
    metrics = {}
    for name, (lo, hi) in periods.items():
        t, n_days = window(trades, sessions, lo, hi)
        metrics[name] = stats.compute(t, n_days)

    by_year = {}
    for y in sorted({d.year for d in trades["trade_date"]}):
        lo, hi = pd.Timestamp(f"{y}-01-01").date(), pd.Timestamp(f"{y + 1}-01-01").date()
        t, n_days = window(trades, sessions, lo, hi)
        by_year[str(y)] = stats.compute(t, n_days)

    # Robustness grid. NOT used to pick parameters; shown so you can see whether the
    # result depends on one lucky setting. Out-of-sample expectancy in R per trade.
    sens = []
    for or_min in (15, 30, 60):
        for tgt in (1.0, 1.5, 2.0, 3.0):
            pp = Params(or_minutes=or_min, target_r=tgt)
            tt, _ = run(df, pp)
            t_oos, n_days = window(tt, sessions, SPLIT, far)
            m = stats.compute(t_oos, n_days)
            sens.append({"or_minutes": or_min, "target_r": tgt, "trades": m.get("trades", 0),
                         "expectancy_r": m.get("expectancy_r"), "win_rate": m.get("win_rate")})

    curve = trades.sort_values("trade_date")
    curve = [{"date": str(d), "usd": float(u), "r": float(r), "oos": d >= SPLIT}
             for d, u, r in zip(curve["trade_date"], curve["pnl_usd"].cumsum(), curve["r"].cumsum())]

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data": {"file": args.csv.name, "first": str(first), "last": str(last), "split": str(SPLIT), **counts},
        "params": {k: (str(v) if not isinstance(v, (int, float)) else v) for k, v in asdict(base).items()},
        "verdict": stats.verdict(metrics["OUT_OF_SAMPLE"], metrics["IN_SAMPLE"]),
        "metrics": metrics, "by_year": by_year, "sensitivity": sens, "equity_curve": curve,
        "direction": {d: stats.compute(g, None) for d, g in trades.groupby("direction")},
        "exit_reasons": trades["exit_reason"].value_counts().to_dict(),
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "backtest.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    trades.to_csv(RESULTS / "backtest_trades.csv", index=False)

    for name in periods:
        m = metrics[name]
        print(f"\n{name}: {m['trades']} trades | win {m['win_rate']:.1%} | avg RR {m['avg_rr']:.2f} | "
              f"expectancy {m['expectancy_r']:+.3f}R | maxDD ${m['max_dd_usd']:,.0f} | "
              f"Sharpe {m['sharpe']:.2f} | total ${m['total_usd']:,.0f}")
    print(f"\nVERDICT: {out['verdict']['label']}\n  {out['verdict']['text']}")


if __name__ == "__main__":
    main()
