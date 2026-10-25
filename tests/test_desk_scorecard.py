import pytest

import desk_ledger
import desk_scorecard as S


def test_forward_return_charges_both_legs_and_waits_for_the_horizon():
    assert S.forward_return(100, [110, 120], 3, (0, 0)) is None
    assert S.forward_return(100, [110, 120], 2, (0, 0)) == pytest.approx(0.2)
    assert S.forward_return(100, [100], 1, (10, 20)) < 0


def test_summary_groups_by_action_and_keeps_a_rules_only_total():
    s = S.summarise([("buy", 0.10), ("buy", -0.02), ("skip", 0.04), ("skip", None)])
    assert s["buy"]["n"] == 2 and round(s["buy"]["mean_pct"], 6) == 4.0
    assert s["skip"]["n"] == 1  # a call too recent to score is left out, not zeroed
    assert s["all (rules only)"]["n"] == 3 and s["buy"]["hit_pct"] == 50.0


def test_only_the_judges_own_calls_are_scored(tmp_path):
    conn = desk_ledger.connect(tmp_path / "d.db")
    conn.execute("INSERT INTO candidates (id, ts, symbol, venue, source, status, price) VALUES (1, 't', 'MSFT', 'stocks', 'yahoo', 'gated', 500)")
    conn.execute("INSERT INTO candidates (id, ts, symbol, venue, source, status, price) VALUES (2, 't', 'SPY', 'stocks', 'yahoo', 'scanned', 600)")
    conn.execute("INSERT INTO decisions (ts, candidate_id, symbol, action, brain, vetoed_by) "
                 "VALUES ('t', 1, 'MSFT', 'skip', 'openrouter', 'judge')")
    conn.execute("INSERT INTO decisions (ts, candidate_id, symbol, action, brain, vetoed_by) "
                 "VALUES ('t', 2, 'SPY', 'skip', '', 'walk_forward')")  # the gate's skip, not the judge's
    assert [row[1] for row in S.judged_calls(conn)] == ["MSFT"]
