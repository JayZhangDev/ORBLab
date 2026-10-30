"""Performance statistics and the honest verdict.

Everything is per ONE contract. `r` is each trade's net P&L divided by its
initial risk, so results stay comparable as the index level and range width change.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

MIN_TRADES = 30        # below this, no verdict stronger than "inconclusive"


def _max_drawdown(equity: np.ndarray) -> float:
    if len(equity) == 0:
        return 0.0
    peak = np.maximum.accumulate(np.concatenate([[0.0], equity]))[1:]
    return float((equity - peak).min())


def bootstrap_ci(x: np.ndarray, n: int = 10_000, seed: int = 7) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    means = rng.choice(x, size=(n, len(x)), replace=True).mean(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(lo), float(hi)


def compute(trades: pd.DataFrame, n_days: int | None = None) -> dict:
    """trades needs columns: r, pnl_usd. n_days = trading days in the window
    (days with no trade count as zero P&L for the Sharpe ratio)."""
    n = len(trades)
    if n == 0:
        return {"trades": 0}
    r = trades["r"].to_numpy(float)
    usd = trades["pnl_usd"].to_numpy(float)
    wins, losses = r[r > 0], r[r <= 0]
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(losses.mean()) if len(losses) else 0.0
    out = {
        "trades": n,
        "win_rate": len(wins) / n,
        "avg_win_r": avg_win,
        "avg_loss_r": avg_loss,
        "avg_rr": (avg_win / abs(avg_loss)) if avg_loss else None,   # realized reward:risk
        "expectancy_r": float(r.mean()),
        "profit_factor": (float(wins.sum() / abs(losses.sum())) if losses.sum() else None),
        "total_r": float(r.sum()),
        "total_usd": float(usd.sum()),
        "max_dd_usd": _max_drawdown(np.cumsum(usd)),
        "max_dd_r": _max_drawdown(np.cumsum(r)),
    }
    out["breakeven_win_rate"] = (1 / (1 + out["avg_rr"])) if out["avg_rr"] else None
    # Sharpe on daily R, counting no-trade days as zero.
    days = max(n_days or n, n)
    daily = np.concatenate([r, np.zeros(days - n)])
    sd = daily.std(ddof=1) if days > 1 else 0.0
    out["sharpe"] = float(daily.mean() / sd * math.sqrt(252)) if sd > 0 else None
    # Is the mean R distinguishable from zero?
    if n >= 2 and r.std(ddof=1) > 0:
        t = r.mean() / (r.std(ddof=1) / math.sqrt(n))
        out["t_stat"] = float(t)
        out["p_value"] = float(math.erfc(abs(t) / math.sqrt(2)))     # two-sided, normal approx
        out["ci95_r"] = list(bootstrap_ci(r))
    return out


def verdict(oos: dict, is_: dict) -> dict:
    """Plain-language call on whether ORB shows edge, judged on OUT-OF-SAMPLE data only."""
    n = oos.get("trades", 0)
    if n < MIN_TRADES:
        return {"label": "INCONCLUSIVE", "tone": "warn",
                "text": f"Only {n} out-of-sample trades; need at least {MIN_TRADES} to say anything."}
    e, ci = oos["expectancy_r"], oos["ci95_r"]
    p = oos["p_value"]
    ise = is_.get("expectancy_r", 0.0)
    if ci[0] > 0 and ise > 0 and p < 0.05:
        return {"label": "EDGE (PRELIMINARY)", "tone": "good",
                "text": (f"Out-of-sample expectancy is {e:+.3f}R per trade (95% CI {ci[0]:+.3f} to "
                         f"{ci[1]:+.3f}, p={p:.3f}) and in-sample agrees. Still one instrument and "
                         "one regime. Paper trade before risking money.")}
    if e <= 0:
        return {"label": "NO EDGE", "tone": "bad",
                "text": (f"Out-of-sample expectancy is {e:+.3f}R per trade after costs "
                         f"(95% CI {ci[0]:+.3f} to {ci[1]:+.3f}). This ORB variant loses money on "
                         "unseen data. Do not trade it live.")}
    return {"label": "NO PROVEN EDGE", "tone": "warn",
            "text": (f"Out-of-sample expectancy is {e:+.3f}R per trade, but the 95% CI "
                     f"({ci[0]:+.3f} to {ci[1]:+.3f}, p={p:.3f}) includes zero. The result is "
                     "consistent with luck. It is not evidence of edge.")}
