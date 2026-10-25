"""Sector momentum rotation, research only (not wired into the desk).

Each rebalance, hold the TOP sector ETFs with the best LOOKBACK-day return,
equal weight; cash when none is positive (an absolute-momentum guard).
Parameters are fixed in advance at the textbook values rather than tuned, so
the result is not fitted to the windows it is judged on.

    python -m desk_rotation

Judged against SPY on the gate's holdout (2022-2024) and the forward window
(2025 on). It only earns a place in the desk if it beats SPY, outright or per
unit of drawdown (desk_backtest.beats_benchmark), on *both*.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from desk_backtest import beats_benchmark, hold_drawdown

SECTORS = ["XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLU", "XLB"]
LOOKBACK, TOP, REBALANCE = 126, 3, 21  # ~6 months, top 3, ~monthly


def rotate(data: dict[str, list], start: str, end: str = "9999", *,
           lookback: int = LOOKBACK, top: int = TOP, every: int = REBALANCE,
           cost_bps: float = 6.0) -> dict[str, Any]:
    """Equity curve of the rotation from ``start`` to ``end``. Pure, no network.

    Ranks on closes up to day i and trades at day i+1's open (no look-ahead);
    cost_bps is charged on every pound turned over.
    """
    closes = {s: {b.ts: b.close for b in bars} for s, bars in data.items()}
    opens = {s: {b.ts: b.open for b in bars} for s, bars in data.items()}
    calendar = sorted(set.intersection(*(set(c) for c in closes.values())))
    days = [d for d in calendar if start <= d <= end]
    first = calendar.index(days[0])
    if first < lookback:
        raise ValueError(f"need {lookback} bars of history before {days[0]}")
    equity, weights, curve, turnover = 1.0, {}, [], 0.0
    for k, day in enumerate(days[:-1]):
        i = first + k
        nxt = calendar[i + 1]
        if k % every == 0:  # rank on today's close, trade tomorrow's open
            past = calendar[i - lookback]
            momentum = {s: closes[s][day] / closes[s][past] - 1 for s in data}
            picks = [s for s in sorted(momentum, key=momentum.get, reverse=True)[:top]
                     if momentum[s] > 0]
            target = {s: 1 / len(picks) for s in picks}
            churn = sum(abs(target.get(s, 0) - weights.get(s, 0)) for s in data)
            equity *= 1 - churn * cost_bps / 10_000
            turnover += churn
            weights = target
        # hold from tomorrow's open to the next open
        after = calendar[i + 2] if i + 2 < len(calendar) else None
        if after and weights:
            equity *= sum(w * opens[s][after] / opens[s][nxt] for s, w in weights.items()) \
                + (1 - sum(weights.values()))
        curve.append((nxt, equity))
    peak, dd = 0.0, 0.0
    for _, e in curve:
        peak = max(peak, e)
        dd = max(dd, (peak - e) / peak)
    return {"curve": curve, "return": curve[-1][1] - 1 if curve else 0.0,
            "drawdown": dd, "turnover": turnover}


def judge(rotation: dict, spy: list, start: str, end: str = "9999",
          cost_bps: float = 6.0) -> dict[str, Any]:
    window = [b for b in spy if start <= b.ts <= end]
    cost = cost_bps / 2 / 10_000
    hold = window[-1].close * (1 - cost) / (window[0].open * (1 + cost)) - 1
    hold_dd = hold_drawdown(window)
    beat, basis = beats_benchmark(rotation["return"], rotation["drawdown"], hold, hold_dd)
    return {"return": rotation["return"], "drawdown": rotation["drawdown"],
            "spy": hold, "spy_drawdown": hold_dd, "beat": beat, "basis": basis}


def main() -> None:
    from desk_data import YahooSource

    from desk_forward import FORWARD_START, RESEARCH_DIR

    src = YahooSource()
    data = {s: src.bars(s, start="2015-01-01") for s in SECTORS}
    spy = src.bars("SPY", start="2015-01-01")
    report = {}
    for label, start, end in (("holdout 2022-24", "2022-01-01", "2024-12-31"),
                              (f"forward {FORWARD_START[:4]}+", FORWARD_START, "9999")):
        result = judge(rotate(data, start, end), spy, start, end)
        report[label] = result
        print(f"{label:16} rotation {result['return']:+7.1%} (dd {result['drawdown']:5.1%})   "
              f"SPY {result['spy']:+7.1%} (dd {result['spy_drawdown']:5.1%})   "
              f"{'PASS' if result['beat'] else 'fail'}: {result['basis']}")
    verdict = all(r["beat"] for r in report.values())
    print("\nverdict:", "earns a place — beats SPY on both windows" if verdict
          else "stays research-only — does not beat SPY on both windows")
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    (RESEARCH_DIR / "rotation.json").write_text(
        json.dumps({"windows": report, "passed": verdict,
                    "params": {"lookback": LOOKBACK, "top": TOP, "every": REBALANCE}},
                   indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
