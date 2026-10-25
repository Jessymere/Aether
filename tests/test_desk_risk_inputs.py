from desk_seats import risk_inputs


def test_risk_reads_the_ledger_not_the_run():
    hist = [("2026-10-20T15:00", 100.0), ("2026-10-20T20:00", 99.0),
            ("2026-10-21T20:00", 97.0), ("2026-10-22T20:00", 95.0),
            ("2026-10-23T14:00", 96.0)]
    day_pnl, dd, losing = risk_inputs(hist, 90.0, "2026-10-23")
    assert round(day_pnl, 4) == round((90 / 96 - 1) * 100, 4)  # vs today's first row
    assert round(dd, 4) == 10.0                                 # vs the all-time peak, 100
    assert losing == 2                                          # 99 -> 97 -> 95


def test_a_flat_or_up_day_breaks_the_losing_streak():
    hist = [("2026-10-20T20:00", 100.0), ("2026-10-21T20:00", 101.0),
            ("2026-10-22T20:00", 99.0)]
    assert risk_inputs(hist, 99.0, "2026-10-23")[2] == 1


def test_an_empty_ledger_is_a_flat_day():
    assert risk_inputs([], 100.0, "2026-10-23") == (0.0, 0.0, 0)
