"""Does the judge earn its seat? Score its calls against what prices did next.

Every candidate that cleared the gate reached the judge, and the ledger keeps
its price, time and the verdict. Comparing forward returns of the calls it
*bought* with the ones it *passed on* is the rules-only vs rules+judge test,
run on the desk's own history with no second book:

    python -m desk_scorecard            # 5 and 20 trading days ahead

If the passed-on names did as well as the bought ones, the judge adds latency
and nothing else, and ``DESK_BRAIN=stub`` (always buy what the gate clears) is
the honest setting. Costs are charged on both legs, as everywhere else.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from typing import Iterable

from config import DESK_DB, desk_costs


def forward_return(entry: float, closes_after: list[float], horizon: int,
                   costs: tuple[float, float]) -> float | None:
    """Return after ``horizon`` closes, both legs costed; None if not that far yet."""
    if entry <= 0 or len(closes_after) < horizon:
        return None
    cost = sum(costs) / 10_000.0
    return closes_after[horizon - 1] * (1 - cost) / (entry * (1 + cost)) - 1


def summarise(calls: Iterable[tuple[str, float | None]]) -> dict[str, dict]:
    """``(action, forward_return)`` pairs -> count, mean and hit rate per action."""
    groups: dict[str, list[float]] = defaultdict(list)
    for action, ret in calls:
        if ret is not None:
            groups[action].append(ret)
            groups["all (rules only)"].append(ret)
    return {k: {"n": len(v), "mean_pct": 100 * sum(v) / len(v),
                "hit_pct": 100 * sum(r > 0 for r in v) / len(v)} for k, v in groups.items()}


def judged_calls(conn) -> list[tuple]:
    """(ts, symbol, venue, price, action) for every call the judge itself made."""
    return conn.execute(
        "SELECT d.ts, d.symbol, c.venue, c.price, d.action FROM decisions d "
        "JOIN candidates c ON c.id = d.candidate_id "
        "WHERE d.vetoed_by IN ('', 'judge') AND d.brain NOT IN ('', 'stub', 'cache')"
        " AND c.price > 0"
    ).fetchall()


def main(horizons: tuple[int, ...] = (5, 20)) -> None:
    import sqlite3

    from desk_data import YahooSource

    conn = sqlite3.connect(DESK_DB)
    calls = judged_calls(conn)
    if not calls:
        print("No judged calls in the ledger yet — run the desk first.")
        return
    src, cache = YahooSource(), {}
    for h in horizons:
        rows = []
        for ts, symbol, venue, price, action in calls:
            if symbol not in cache:
                try:
                    cache[symbol] = src.bars(symbol, start=ts[:10])
                except Exception:  # noqa: BLE001 — one bad symbol must not end the report
                    cache[symbol] = []
            after = [b.close for b in cache[symbol] if b.ts > ts[:10]]
            rows.append((action, forward_return(price, after, h, desk_costs(venue))))
        print(f"\n{h} trading days ahead ({len(calls)} judged calls):")
        if not any(ret is not None for _, ret in rows):
            print("  none old enough to score yet")
        for action, s in sorted(summarise(rows).items()):
            print(f"  {action:18} n={s['n']:4d}  mean {s['mean_pct']:+6.2f}%  up {s['hit_pct']:5.1f}%")
    print("\nThe judge helps only if 'buy' beats 'all (rules only)' and 'skip'/'hold' trail it.")


if __name__ == "__main__":
    main(tuple(int(a) for a in sys.argv[1:]) or (5, 20))
