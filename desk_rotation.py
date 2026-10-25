"""Portfolio strategies, research only (not wired into the desk).

Three published strategies, each with its textbook parameters fixed in advance
(not tuned), run on one engine and judged against SPY on the gate's holdout
(2022-2024) and the forward window (2025 on):

  * sector rotation — the TOP sector ETFs by 6-month momentum, monthly; cash
    when none is positive
  * dual momentum   — SPY when its 12-month return beats T-bills (BIL), else
    bonds (AGG), monthly (Antonacci's absolute-momentum leg)
  * vol target      — SPY sized to 15% annualised volatility from the last 20
    days, rest in cash, weekly

A strategy only earns a place in the desk if it beats SPY, outright or per unit
of drawdown (desk_backtest.beats_benchmark), on *both* windows.

    python -m desk_rotation
"""

from __future__ import annotations

import json
from statistics import pstdev
from typing import Any, Callable

from desk_backtest import beats_benchmark, hold_drawdown

SECTORS = ["XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLU", "XLB"]
LOOKBACK, TOP, REBALANCE = 126, 3, 21  # ~6 months, top 3, ~monthly

Weigh = Callable[[dict, list, int], dict]


def run_weights(data: dict[str, list], start: str, end: str, weigh: Weigh, *,
                every: int, cost_bps: float = 6.0, need: int = LOOKBACK) -> dict[str, Any]:
    """Equity curve of a weights-per-rebalance strategy. Pure, no network.

    ``weigh(closes, calendar, i)`` sees closes up to day i only and returns
    {symbol: weight}; the trade happens at day i+1's open. ``cost_bps`` is
    charged on every unit of weight turned over; unweighted capital is cash.
    """
    closes = {s: {b.ts: b.close for b in bars} for s, bars in data.items()}
    opens = {s: {b.ts: b.open for b in bars} for s, bars in data.items()}
    calendar = sorted(set.intersection(*(set(c) for c in closes.values())))
    days = [d for d in calendar if start <= d <= end]
    first = calendar.index(days[0])
    if first < need:
        raise ValueError(f"need {need} bars of history before {days[0]}")
    equity, weights, curve = 1.0, {}, []
    for k, day in enumerate(days[:-1]):
        i = first + k
        if k % every == 0:
            target = weigh(closes, calendar, i)
            churn = sum(abs(target.get(s, 0) - weights.get(s, 0)) for s in data)
            equity *= 1 - churn * cost_bps / 10_000
            weights = target
        nxt, after = calendar[i + 1], calendar[i + 2] if i + 2 < len(calendar) else None
        if after and weights:
            equity *= sum(w * opens[s][after] / opens[s][nxt] for s, w in weights.items()) \
                + (1 - sum(weights.values()))
        curve.append((nxt, equity))
    peak, dd = 0.0, 0.0
    for _, e in curve:
        peak = max(peak, e)
        dd = max(dd, (peak - e) / peak)
    return {"curve": curve, "return": curve[-1][1] - 1 if curve else 0.0, "drawdown": dd}


def momentum_top(lookback: int = LOOKBACK, top: int = TOP) -> Weigh:
    def weigh(closes, calendar, i):
        day, past = calendar[i], calendar[i - lookback]
        mom = {s: c[day] / c[past] - 1 for s, c in closes.items()}
        picks = [s for s in sorted(mom, key=mom.get, reverse=True)[:top] if mom[s] > 0]
        return {s: 1 / len(picks) for s in picks}
    return weigh


def dual_momentum(lookback: int = 252) -> Weigh:
    def weigh(closes, calendar, i):
        day, past = calendar[i], calendar[i - lookback]
        ret = {s: closes[s][day] / closes[s][past] - 1 for s in ("SPY", "BIL")}
        return {"SPY": 1.0} if ret["SPY"] > ret["BIL"] else {"AGG": 1.0}
    return weigh


def vol_target(target: float = 0.15, window: int = 20) -> Weigh:
    def weigh(closes, calendar, i):
        spy = [closes["SPY"][d] for d in calendar[i - window:i + 1]]
        rets = [b / a - 1 for a, b in zip(spy, spy[1:])]
        vol = pstdev(rets) * 252 ** 0.5
        return {"SPY": min(1.0, target / vol) if vol > 0 else 1.0}
    return weigh


def rotate(data: dict[str, list], start: str, end: str = "9999", *,
           lookback: int = LOOKBACK, top: int = TOP, every: int = REBALANCE,
           cost_bps: float = 6.0) -> dict[str, Any]:
    """Sector rotation (kept as a named entry point for tests and callers)."""
    return run_weights(data, start, end, momentum_top(lookback, top), every=every,
                       cost_bps=cost_bps, need=lookback)


STRATEGIES = {
    # name: (symbols it trades, weigh, rebalance every N days, history needed)
    "sector rotation": (SECTORS, momentum_top(), REBALANCE, LOOKBACK),
    "dual momentum": (["SPY", "AGG", "BIL"], dual_momentum(), 21, 252),
    "vol-targeted SPY": (["SPY"], vol_target(), 5, 21),
}


def judge(result: dict, spy: list, start: str, end: str = "9999",
          cost_bps: float = 6.0) -> dict[str, Any]:
    window = [b for b in spy if start <= b.ts <= end]
    cost = cost_bps / 2 / 10_000
    hold = window[-1].close * (1 - cost) / (window[0].open * (1 + cost)) - 1
    hold_dd = hold_drawdown(window)
    beat, basis = beats_benchmark(result["return"], result["drawdown"], hold, hold_dd)
    return {"return": result["return"], "drawdown": result["drawdown"],
            "spy": hold, "spy_drawdown": hold_dd, "beat": beat, "basis": basis}


def main() -> None:
    from desk_data import YahooSource

    from desk_forward import FORWARD_START, RESEARCH_DIR

    src = YahooSource()
    symbols = sorted({s for syms, *_ in STRATEGIES.values() for s in syms} | {"SPY"})
    bars = {s: src.bars(s, start="2015-01-01") for s in symbols}
    windows = (("holdout 2022-24", "2022-01-01", "2024-12-31"),
               (f"forward {FORWARD_START[:4]}+", FORWARD_START, "9999"))
    report = {}
    for name, (syms, weigh, every, need) in STRATEGIES.items():
        data = {s: bars[s] for s in syms}
        result = {label: judge(run_weights(data, start, end, weigh, every=every, need=need),
                               bars["SPY"], start, end)
                  for label, start, end in windows}
        report[name] = {"windows": result, "passed": all(r["beat"] for r in result.values())}
        print(f"\n{name}")
        for label, r in result.items():
            print(f"  {label:16} {r['return']:+7.1%} (dd {r['drawdown']:5.1%})   SPY {r['spy']:+7.1%} "
                  f"(dd {r['spy_drawdown']:5.1%})   {'PASS' if r['beat'] else 'fail'}: {r['basis']}")
        print("  verdict:", "earns a place" if report[name]["passed"] else "stays research-only")
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    (RESEARCH_DIR / "strategies.json").write_text(json.dumps(report, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
