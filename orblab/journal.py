"""SQLite trade journal for paper (and manual) trades.

    python -m orblab.journal list
    python -m orblab.journal add --date 2026-11-02 --dir long --entry 6850.25 --stop 6841 \
        --target 6868.75 --setup manual --notes "faded the open drive"
    python -m orblab.journal close 12 --exit 6868.75 --notes "hit target"

`result` is OPEN until closed, then WIN or LOSS by net P&L (after slippage and
commission for automatic ORB trades; raw price difference minus commission for
manual ones).
"""
from __future__ import annotations

import argparse
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from orblab import stats
from orblab.strategy import DayResult, Params

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("ORBLAB_DB", ROOT / "journal.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT NOT NULL,
    symbol TEXT NOT NULL DEFAULT 'MES',
    setup TEXT NOT NULL DEFAULT 'ORB',       -- ORB, manual, ...
    direction TEXT NOT NULL,                  -- long | short
    entry_time TEXT,
    entry REAL NOT NULL,
    stop REAL NOT NULL,
    target REAL NOT NULL,
    exit_time TEXT,
    exit REAL,
    result TEXT NOT NULL DEFAULT 'OPEN',      -- OPEN | WIN | LOSS
    exit_reason TEXT,                         -- target | stop | eod | manual
    points REAL,
    pnl_usd REAL,                             -- per contract
    r_multiple REAL,
    point_value REAL NOT NULL DEFAULT 5.0,
    source TEXT NOT NULL DEFAULT 'paper',     -- paper | replay | manual
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    c = sqlite3.connect(path or DB_PATH)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


@contextmanager
def session(path: Path | None = None):
    """Open, commit and always close (sqlite's own `with` doesn't close on Windows)."""
    c = connect(path)
    try:
        yield c
        c.commit()
    finally:
        c.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _iso(t) -> str | None:
    return t.isoformat() if t is not None else None


def upsert_orb(res: DayResult, symbol: str, p: Params, source: str = "paper",
               db: Path | None = None) -> int | None:
    """Create or update the single automatic ORB row for (date, symbol). Returns its id."""
    if res.status not in ("OPEN", "CLOSED"):
        return None
    result = "OPEN"
    if res.status == "CLOSED":
        result = "WIN" if (res.r or 0) > 0 else "LOSS"
    note = f"OR {res.or_low:.2f}-{res.or_high:.2f}; signal bar closed outside range"
    with session(db) as c:
        row = c.execute("SELECT id FROM trades WHERE trade_date=? AND symbol=? AND setup='ORB'",
                        (str(res.trade_date), symbol)).fetchone()
        vals = dict(direction=res.direction, entry_time=_iso(res.entry_time), entry=res.entry,
                    stop=res.stop, target=res.target, exit_time=_iso(res.exit_time), exit=res.exit,
                    result=result, exit_reason=res.exit_reason, points=res.points,
                    pnl_usd=res.pnl_usd, r_multiple=res.r)
        if row:
            sets = ", ".join(f"{k}=?" for k in vals)
            c.execute(f"UPDATE trades SET {sets} WHERE id=?", (*vals.values(), row["id"]))
            return row["id"]
        cur = c.execute(
            "INSERT INTO trades (trade_date, symbol, setup, point_value, source, notes, created_at, "
            + ", ".join(vals) + ") VALUES (?,?,?,?,?,?,?," + ",".join("?" * len(vals)) + ")",
            (str(res.trade_date), symbol, "ORB", p.point_value, source, note, _now(), *vals.values()))
        return cur.lastrowid


def add_manual(date: str, direction: str, entry: float, stop: float, target: float,
               setup: str = "manual", symbol: str = "MES", point_value: float = 5.0,
               notes: str = "", db: Path | None = None) -> int:
    with session(db) as c:
        cur = c.execute(
            "INSERT INTO trades (trade_date, symbol, setup, direction, entry, stop, target, "
            "point_value, source, notes, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (date, symbol, setup, direction, entry, stop, target, point_value, "manual", notes, _now()))
        return cur.lastrowid


def close_manual(trade_id: int, exit_price: float, notes: str = "", commission_rt: float = 1.40,
                 db: Path | None = None) -> None:
    with session(db) as c:
        t = c.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
        if t is None:
            raise SystemExit(f"no trade with id {trade_id}")
        d = 1 if t["direction"] == "long" else -1
        points = (exit_price - t["entry"]) * d
        pnl = points * t["point_value"] - commission_rt
        r = pnl / (abs(t["entry"] - t["stop"]) * t["point_value"])
        reason = "target" if exit_price == t["target"] else "stop" if exit_price == t["stop"] else "manual"
        c.execute("UPDATE trades SET exit=?, exit_time=?, result=?, exit_reason=?, points=?, pnl_usd=?, "
                  "r_multiple=?, notes=? WHERE id=?",
                  (exit_price, _now(), "WIN" if pnl > 0 else "LOSS", reason, points, pnl, r,
                   (t["notes"] + (" | " + notes if notes else "")), trade_id))


def snapshot(db: Path | None = None) -> dict:
    """Everything the dashboard needs: entries, live stats, equity curve."""
    with session(db) as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM trades ORDER BY trade_date DESC, id DESC")]
    closed = [r for r in rows if r["result"] != "OPEN"]
    import pandas as pd
    closed_df = pd.DataFrame(closed)
    metrics = {"trades": 0}
    curve = []
    if not closed_df.empty:
        closed_df = closed_df.rename(columns={"r_multiple": "r"})
        metrics = stats.compute(closed_df, None)
        asc = closed_df.sort_values(["trade_date", "id"])
        curve = [{"date": d, "usd": float(u), "r": float(r)}
                 for d, u, r in zip(asc["trade_date"], asc["pnl_usd"].cumsum(), asc["r"].cumsum())]
    by_setup = {}
    for s in sorted({r["setup"] for r in closed}):
        sub = closed_df[closed_df["setup"] == s]
        by_setup[s] = stats.compute(sub, None)
    return {"entries": rows, "open": sum(r["result"] == "OPEN" for r in rows),
            "metrics": metrics, "equity_curve": curve, "by_setup": by_setup,
            "min_trades_for_verdict": stats.MIN_TRADES}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    a = sub.add_parser("add")
    a.add_argument("--date", required=True)
    a.add_argument("--dir", dest="direction", choices=["long", "short"], required=True)
    a.add_argument("--entry", type=float, required=True)
    a.add_argument("--stop", type=float, required=True)
    a.add_argument("--target", type=float, required=True)
    a.add_argument("--setup", default="manual")
    a.add_argument("--symbol", default="MES")
    a.add_argument("--point-value", type=float, default=5.0)
    a.add_argument("--notes", default="")
    c = sub.add_parser("close")
    c.add_argument("id", type=int)
    c.add_argument("--exit", dest="exit_price", type=float, required=True)
    c.add_argument("--notes", default="")
    args = ap.parse_args()

    if args.cmd == "list":
        for r in snapshot()["entries"]:
            pnl = f"{r['pnl_usd']:+.2f}" if r["pnl_usd"] is not None else "-"
            print(f"#{r['id']:<4}{r['trade_date']}  {r['symbol']:<4}{r['setup']:<7}{r['direction']:<6}"
                  f"in {r['entry']:<9.2f}stop {r['stop']:<9.2f}tgt {r['target']:<9.2f}{r['result']:<5}${pnl}")
    elif args.cmd == "add":
        print("Logged trade #%d" % add_manual(args.date, args.direction, args.entry, args.stop, args.target,
                                              args.setup, args.symbol, args.point_value, args.notes))
    else:
        close_manual(args.id, args.exit_price, args.notes)
        print(f"Closed trade #{args.id}")


if __name__ == "__main__":
    main()
