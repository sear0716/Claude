import pandas as pd

from gap_bot.prefilter import load_universe, scan_gaps, yahoo_symbol


def test_universe_has_sp500():
    u = load_universe()
    assert 495 <= len(u) <= 510 and "AAPL" in u and "BRK.B" in u


def test_yahoo_symbol():
    assert yahoo_symbol("BRK.B") == "BRK-B" and yahoo_symbol("BRK B") == "BRK-B"


def test_scan_gaps(rules):
    def d(prev_close, open_, close):
        return pd.DataFrame({"open": [prev_close, open_], "close": [prev_close, close]})
    data = {"UP5": d(100, 105, 106), "UP3": d(10, 10.31, 10.4), "FLAT": d(100, 101, 102),
            "CHEAP": d(2, 2.2, 2.3), "DOWN": d(100, 90, 91)}
    rows = scan_gaps(data, rules)
    assert [r["symbol"] for r in rows] == ["UP5", "UP3"]
