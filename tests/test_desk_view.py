from desk_ledger import Decision, connect, record_decision, record_equity
import desk_view as V


def _cand(conn, cid, symbol, rule=""):
    conn.execute("INSERT INTO candidates (id, ts, symbol, venue, source, status, rule_id)"
                 " VALUES (?, 't', ?, 'stocks', 'yahoo', 'scanned', ?)", (cid, symbol, rule))


def test_watchlist_shows_where_each_symbol_stands(tmp_path):
    conn = connect(tmp_path / "d.db")
    _cand(conn, 1, "MSFT", "reversion")
    _cand(conn, 2, "SPY")
    record_decision(conn, Decision(symbol="MSFT", action="skip", candidate_id=1, brain="gate",
                                   vetoed_by="signal", skipped_reason="no reversion entry signal today"))
    record_decision(conn, Decision(symbol="SPY", action="skip", candidate_id=2, brain="gate",
                                   vetoed_by="walk_forward",
                                   skipped_reason="every rule benched (stopped at 'holdout')"))
    watch = {w["symbol"]: w for w in V.overview(conn)["watchlist"]}
    assert watch["MSFT"]["status"] == "waiting for reversion entry signal" and watch["MSFT"]["validated"]
    assert watch["SPY"]["status"] == "benched — (stopped at 'holdout')"
    assert [w["symbol"] for w in V.overview(conn)["watchlist"]] == ["MSFT", "SPY"]  # validated first


def test_judge_panel_lists_only_real_model_calls_with_readable_names(tmp_path):
    conn = connect(tmp_path / "d.db")
    record_decision(conn, Decision(symbol="A", action="skip", brain="gate"))
    record_decision(conn, Decision(symbol="B", action="buy", confidence=0.6, brain="openrouter",
                                   model="nvidia/nemotron-3-ultra-550b-a55b:free"))
    record_decision(conn, Decision(symbol="B", action="buy", brain="cache"))
    calls = V.overview(conn)["judge_calls"]
    assert [(c["symbol"], c["model"]) for c in calls] == [("B", "Nemotron 3 Ultra 550B")]


def test_equity_comes_from_the_ledger_before_any_cycle(tmp_path):
    conn = connect(tmp_path / "d.db")
    assert V.overview(conn)["equity"] is None
    record_equity(conn, 10_000, 10_000)
    record_equity(conn, 10_250, 9_000, positions_value=1_250)
    view = V.overview(conn)
    assert view["equity"] == 10_250 and view["start_equity"] == 10_000
    assert view["series"] == [10_000, 10_250]


def test_pnl_is_measured_against_starting_capital_not_the_first_row(tmp_path):
    conn = connect(tmp_path / "d.db")
    record_equity(conn, 10_000, 10_000)        # an early one-venue book
    record_equity(conn, 19_995, 19_995)        # both venues
    view = V.overview(conn, capital=20_000)
    assert view["equity"] - view["start_equity"] == -5
    assert view["series"] == [19_995]          # the one-venue row is left out
