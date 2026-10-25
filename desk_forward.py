"""Forward test: does a gated rule still work on data no verdict ever saw?

The gate tunes on 2015-2019 and judges on 2020-2024. Everything after the
holdout is untouched, so this replays each rule, frozen at its train params,
from FORWARD_START to today and compares it with holding — the honest check
for rules picked out of many tries.

    python -m desk_forward                 # the desk universe
    python -m desk_forward XLK XLE SPY     # any symbols

Results are also written to desk/research/forward.json for the Research tab.
"""

from __future__ import annotations

import json
import sys
from statistics import mean
from typing import Any

import desk_backtest as B
from config import DESK_DIR, DESK_HOLDOUT, DESK_UNIVERSE, desk_costs

FORWARD_START = "2025-01-01"
#: Bars before FORWARD_START that indicators may read but trades may not use.
WARMUP_BARS = 260
RESEARCH_DIR = DESK_DIR / "research"


def forward_rule(rule: B.Rule, first: int) -> B.Rule:
    """``rule`` that may only enter once the window reaches bar ``first``.

    replay() hands entry() ``closes[:i + 1]``, so its length is the bar index:
    the warm-up bars feed the averages but can never open a trade.
    """
    return B.Rule(rule_id=rule.rule_id, exit=rule.exit, grid=rule.grid,
                  entry=lambda c, p: len(c) > first and rule.entry(c, p))


def forward_rows(symbol: str, history: list, recent: list,
                 costs: tuple[float, float]) -> list[dict[str, Any]]:
    """One row per rule: gate verdict on ``history``, forward result on ``recent``."""
    first = next((i for i, b in enumerate(recent) if b.ts >= FORWARD_START), None)
    if first is None or first < WARMUP_BARS or first >= len(recent) - 2:
        raise ValueError(f"{symbol}: need {WARMUP_BARS} warm-up bars and some forward bars")
    fees, slippage = costs
    rows = []
    for rule in B.RULES.values():
        passed, verdicts = B.evaluate(symbol, history, rule, costs=costs)
        params = next((v.metrics.get("params") for v in verdicts if v.verdict == "train"),
                      None) or rule.grid[0]
        m = B.replay(recent, forward_rule(rule, first), params,
                     fees_bps=fees, slippage_bps=slippage)
        forward = recent[first:]
        hold, hold_dd = B.buy_and_hold(forward, fees_bps=fees, slippage_bps=slippage), \
            B.hold_drawdown(forward)
        ret = m.ending_equity / m.starting_equity - 1
        beat, basis = B.beats_benchmark(ret, m.mtm_dd, hold, hold_dd)
        rows.append({"symbol": symbol, "rule": rule.rule_id, "gate": passed,
                     "params": params, "trades": m.n_trades, "return": ret,
                     "drawdown": m.mtm_dd, "hold": hold, "hold_drawdown": hold_dd,
                     "beat": beat, "basis": basis,
                     "window": f"{forward[0].ts}..{forward[-1].ts}"})
    return rows


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Gate-passed vs rejected: if rejects do as well, the gate selects nothing."""
    out = {}
    for label, group in (("passed", [r for r in rows if r["gate"]]),
                         ("rejected", [r for r in rows if not r["gate"]])):
        out[label] = {"n": len(group), "beat": sum(r["beat"] for r in group),
                      "mean_return": mean(r["return"] for r in group) if group else 0.0,
                      "mean_hold": mean(r["hold"] for r in group) if group else 0.0}
    return out


def run(symbols: list[str]) -> dict[str, Any]:
    from desk_data import YahooSource

    src, rows, skipped = YahooSource(), [], []
    holdout_end = DESK_HOLDOUT[1]
    for symbol in symbols:
        costs = desk_costs("crypto" if symbol.endswith("-USD") else "stocks")
        try:
            history = src.bars(symbol, start="2015-01-01", end=holdout_end)
            recent = src.bars(symbol, start="2023-06-01")
            rows += forward_rows(symbol, history, recent, costs)
        except Exception as exc:  # noqa: BLE001 — one bad symbol must not end the run
            skipped.append(f"{symbol}: {exc}")
    report = {"rows": rows, "summary": summarise(rows), "skipped": skipped,
              "start": FORWARD_START}
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    (RESEARCH_DIR / "forward.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    return report


def main(argv: list[str]) -> None:
    report = run(argv or DESK_UNIVERSE)
    print(f"forward window from {FORWARD_START} (tuned on 2015-19, gated on 2020-24)\n")
    print(f"{'symbol':8} {'rule':10} {'trades':>6} {'rule %':>8} {'hold %':>8} {'dd %':>6}  verdict")
    for r in report["rows"]:
        if r["gate"]:
            print(f"{r['symbol']:8} {r['rule']:10} {r['trades']:6d} {r['return']*100:8.1f} "
                  f"{r['hold']*100:8.1f} {r['drawdown']*100:6.1f}  {r['basis']}")
    for label, s in report["summary"].items():
        print(f"\n{label}: {s['n']} combos, beat holding {s['beat']}/{s['n']}, "
              f"mean {s['mean_return']*100:+.1f}% vs hold {s['mean_hold']*100:+.1f}%")
    for line in report["skipped"]:
        print("skipped", line)


if __name__ == "__main__":
    main(sys.argv[1:])
