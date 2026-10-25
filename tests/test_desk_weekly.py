"""The weekly report reads the ledger and SPY, and says who was ahead."""

from desk_data import Bar
from desk_ledger import connect, record_equity
from desk_weekly import build, render


def test_the_week_is_measured_against_spy(tmp_path):
    conn = connect(tmp_path / "desk.db")
    for ts, equity in (("2026-10-16T20:00:00+00:00", 100.0), ("2026-10-23T20:00:00+00:00", 101.0)):
        conn.execute("INSERT INTO equity (ts, mode, equity, cash) VALUES (?, 'mixed', ?, 0)",
                     (ts, equity))
    record_equity(conn, 50.0, 0.0, mode="mixed-sleeve")  # never the headline series
    spy = [Bar(ts=d, open=0, high=0, low=0, close=c)
           for d, c in (("2026-10-16", 600.0), ("2026-10-19", 606.0), ("2026-10-23", 612.0))]
    r = build(conn, "2026-10-18", spy)
    assert r["mode"] == "mixed"
    assert abs(r["return"] - 0.01) < 1e-9 and abs(r["spy_return"] - 0.02) < 1e-9
    assert "Behind SPY." in render(r)
    assert "Too little" in render(build(conn, "2026-10-10", spy))  # history starts inside
