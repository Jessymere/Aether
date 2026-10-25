"""Replay the stock desk day by day over the forward window, rules only.

The desk's own pieces, in its own order: the gate picks a rule (and its
train-tuned params) per symbol from 2015-2024 history, then from
FORWARD_START a signal on day i's close fills at day i+1's open, sized by
chief_budget / size_position under the SPY dial and the sector cap, and exits
on the stop, target, trail or the rule's own exit, at stock costs. The judge is
left out (every signal is a buy): replaying it would mean thousands of model
calls, and desk_scorecard measures it from the live record instead.

    python -m desk_replay

Writes desk/research/replay.json (desk and SPY equity curves) for the Research tab.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import desk_backtest as B
from config import (DESK_HOLDOUT, DESK_MAX_PER_SECTOR, DESK_SECTORS, DESK_START_CAPITAL,
                    DESK_UNIVERSE, desk_costs)
from desk_forward import FORWARD_START, RESEARCH_DIR

#: Position sizes the sweep tries, in % of equity per position.
SIZES = (10, 20, 35, 50, 100)
from desk_ledger import Position
from desk_seats import (atr_pct, chief_budget, compute_dial, should_exit, size_position,
                        stop_and_take, trail_stop)


def simulate(rules: dict[str, tuple[B.Rule, dict]], data: dict[str, list], *,
             start: str = FORWARD_START, capital: float = DESK_START_CAPITAL,
             costs: tuple[float, float] = (3.0, 3.0), benchmark: str = "SPY",
             confidence: float = 0.6, max_position_pct: float | None = None) -> dict[str, Any]:
    """The day loop. ``data`` must include ``benchmark`` (it sets the dial and
    the calendar). Pure: no network, so tests drive it with synthetic bars."""
    cost = sum(costs) / 10_000
    index = {s: {b.ts: i for i, b in enumerate(bars)} for s, bars in data.items()}
    days = [b.ts for b in data[benchmark] if b.ts >= start]
    cash, held, trades, curve, pending = capital, {}, [], [], []

    def mark(day: str) -> float:
        return cash + sum(p.qty * data[s][index[s][day]].close
                          for s, p in held.items() if day in index[s])

    for day in days:
        for s in pending:  # yesterday's signals fill at today's open
            i = index[s].get(day)
            if i is None or s in held:
                continue
            sector = DESK_SECTORS.get(s, s)
            if sum(DESK_SECTORS.get(x, x) == sector for x in held) >= DESK_MAX_PER_SECTOR:
                continue
            dial = compute_dial(data[benchmark][:index[benchmark][day]])
            budget = chief_budget(mark(day), cash, dial, len(held), max_pct=max_position_pct)
            rng = atr_pct(data[s][:i])
            price = data[s][i].open * (1 + cost)
            qty = size_position(budget.per_position, price, confidence, rng)
            if budget.slots <= 0 or qty <= 0 or qty * price > cash:
                continue
            stop, take = stop_and_take(price, rng)
            cash -= qty * price
            held[s] = Position(symbol=s, qty=qty, avg_price=price, stop=stop, take=take,
                               atr_pct=rng, rule_id=rules[s][0].rule_id, params=rules[s][1])
        pending = []
        for s in list(held):  # at the close: stops, trail, the rule's exit
            i = index[s].get(day)
            if i is None:
                continue
            p, close = held[s], data[s][i].close
            p.stop = max(p.stop or 0.0, trail_stop(p, close, p.atr_pct))
            rule, params = rules[s]
            if should_exit(p, close) or rule.exit([b.close for b in data[s][:i + 1]], params):
                proceeds = p.qty * close * (1 - cost)
                trades.append({"symbol": s, "return": proceeds / (p.qty * p.avg_price) - 1,
                               "exit": day})
                cash += proceeds
                del held[s]
        for s, (rule, params) in rules.items():
            i = index[s].get(day)
            if i is not None and s not in held and rule.entry([b.close for b in data[s][:i + 1]], params):
                pending.append(s)
        curve.append((day, mark(day)))

    bench = data[benchmark]
    first = index[benchmark][days[0]]
    hold = [(b.ts, capital * b.close * (1 - cost) / (bench[first].open * (1 + cost)))
            for b in bench[first:]]
    peak, dd = 0.0, 0.0
    for _, e in curve:
        peak = max(peak, e)
        dd = max(dd, (peak - e) / peak)
    return {"curve": curve, "benchmark": hold, "trades": trades, "drawdown": dd,
            "hold_drawdown": B.hold_drawdown(bench[first:]),
            "return": curve[-1][1] / capital - 1 if curve else 0.0,
            "hold_return": hold[-1][1] / capital - 1 if hold else 0.0,
            "rules": {s: r.rule_id for s, (r, _) in rules.items()}, "start": start}


def run(symbols: list[str]) -> dict[str, Any]:
    from desk_data import YahooSource

    costs = desk_costs("stocks")
    src, rules, data = YahooSource(), {}, {}
    for s in dict.fromkeys(["SPY", *symbols]):
        if s.endswith("-USD"):
            continue  # stock desk only: one calendar, one cost model
        history = src.bars(s, start="2015-01-01", end=DESK_HOLDOUT[1])
        for rule in B.RULES.values():
            ok, verdicts = B.evaluate(s, history, rule, costs=costs)
            if ok:
                rules[s] = (rule, next(v.metrics["params"] for v in verdicts if v.verdict == "train"))
                break
        data[s] = src.bars(s, start="2023-06-01")
    report = simulate(rules, data, costs=costs)
    # The same data at other position sizes: the desk is mostly cash at 10%.
    report["sizing"] = [
        {"pct": pct, **{k: r[k] for k in ("return", "drawdown", "hold_return", "hold_drawdown")},
         "trades": len(r["trades"])}
        for pct, r in ((pct, simulate(rules, data, costs=costs, max_position_pct=pct))
                       for pct in SIZES)]
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    (RESEARCH_DIR / "replay.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    return report


def main(argv: list[str]) -> None:
    r = run(argv or DESK_UNIVERSE)
    wins = sum(t["return"] > 0 for t in r["trades"])
    print(f"validated rules: {r['rules'] or 'none'}")
    print(f"window {r['curve'][0][0]} .. {r['curve'][-1][0]}  ({len(r['curve'])} days), judge excluded")
    print(f"desk : {r['return']:+.1%}  max drawdown {r['drawdown']:.1%}  trades {len(r['trades'])}"
          + (f"  win rate {wins / len(r['trades']):.0%}" if r["trades"] else ""))
    print(f"SPY  : {r['hold_return']:+.1%}  max drawdown {r['hold_drawdown']:.1%}")
    print("\nposition size sweep (% of equity per position, before dial and range cuts):")
    for s in r["sizing"]:
        ratio = s["return"] / max(s["drawdown"], 0.02)
        print(f"  {s['pct']:5.0f}%  desk {s['return']:+7.1%}  dd {s['drawdown']:5.1%}  "
              f"return/dd {ratio:5.2f}   (SPY {s['hold_return'] / max(s['hold_drawdown'], 0.02):.2f})")


if __name__ == "__main__":
    main(sys.argv[1:])
