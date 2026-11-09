import datetime as dt
import json
import math
from pathlib import Path

import pytest

from cheap_options import cli, demo, pricing, volatility as vol
from cheap_options.analysis import Config, analyze, implied_forward
from cheap_options.models import OptionQuote, Snapshot
from cheap_options.providers import schwab, yahoo
from cheap_options.store import IvStore

FIXTURES = Path(__file__).resolve().parents[2] / "schwab_mcp" / "tests" / "fixtures"


# --- pricing -----------------------------------------------------------------

def test_black76_matches_a_known_value_and_put_call_parity():
    # Hull-style check: F=K=100, vol 20%, 1y, no discounting -> 100*(2N(0.1)-1)
    assert pricing.black76(100, 100, 0.2, 1.0, 1.0, "C") == pytest.approx(7.9656, abs=1e-4)
    f, k, v, t, df = 103.0, 95.0, 0.31, 0.4, 0.985
    call, put = pricing.black76(f, k, v, t, df, "C"), pricing.black76(f, k, v, t, df, "P")
    assert call - put == pytest.approx(df * (f - k))


@pytest.mark.parametrize("right,strike,v", [("C", 110, 0.18), ("P", 90, 0.45), ("C", 100, 1.2), ("P", 60, 0.9)])
def test_implied_vol_round_trips(right, strike, v):
    price = pricing.black76(100, strike, v, 0.25, 0.99, right)
    assert pricing.implied_vol(price, 100, strike, 0.25, 0.99, right) == pytest.approx(v, abs=1e-5)


def test_implied_vol_rejects_prices_outside_arbitrage_bounds():
    assert pricing.implied_vol(0.0, 100, 100, 0.25, 1.0, "C") is None
    assert pricing.implied_vol(101.0, 100, 100, 0.25, 1.0, "C") is None  # above the forward
    assert pricing.implied_vol(9.0, 100, 90, 0.25, 1.0, "C") is None  # below intrinsic


# --- volatility --------------------------------------------------------------

def test_close_to_close_on_a_hand_computed_series():
    closes = [100 * math.exp(0.01 * s) for s in (0, 1, 0, 1, 0, 1, 0)]  # returns alternate +1%, -1%
    assert vol.close_to_close(closes, 6) == pytest.approx(math.sqrt(0.0001 * 6 / 5 * 252))


def test_estimators_recover_the_volatility_that_generated_the_data():
    snap = demo.make_snapshot("X", realised_vol=0.30, implied_vol=0.30, seed=5)
    closes = [b.close for b in snap.bars]
    assert vol.close_to_close(closes, 252) == pytest.approx(0.30, rel=0.12)
    assert vol.ewma(closes) == pytest.approx(0.30, rel=0.30)
    assert vol.yang_zhang(snap.bars, 252) == pytest.approx(0.30, rel=0.25)


def test_forecast_reverts_from_current_towards_long_run_vol():
    calm = [100 * math.exp(0.005 * (-1) ** i) for i in range(400)]
    closes = calm + [calm[-1] * math.exp(0.04 * (-1) ** i * (i // 2 + 1) / 5) for i in range(1, 11)]  # recent turbulence
    current, long_run = vol.ewma(closes), vol.close_to_close(closes, 252)
    short, long = vol.forecast(closes, 5), vol.forecast(closes, 120)
    assert current > long_run
    assert long_run < long < short < current


def test_percentile_and_insufficient_history():
    assert vol.percentile_of(0.25, [0.1, 0.2, 0.3, 0.4]) == 0.5
    assert vol.percentile_of(0.25, []) is None
    assert vol.close_to_close([100, 101], 20) is None
    assert vol.forecast([100, 101, 102], 21) is None


# --- analysis ----------------------------------------------------------------

def test_forward_and_atm_vol_are_recovered_from_the_chain():
    snap = demo.make_snapshot("X", realised_vol=0.25, implied_vol=0.32, seed=3, spread=0.01)
    rep = analyze(snap)
    for e in rep.expiries:
        assert e.forward == pytest.approx(100 * math.exp(0.04 * e.t), rel=2e-3)
        assert e.atm_iv == pytest.approx(0.32, abs=0.004)
    assert rep.iv30 == pytest.approx(0.32, abs=0.004)
    assert rep.skew > 0  # the demo chain has put skew


def test_implied_forward_falls_back_to_spot_without_a_call_put_pair():
    quotes = [OptionQuote(dt.date(2026, 2, 1), 100, "C", 1.0, 1.1)]
    assert implied_forward(quotes, 100.0, 0.99) == pytest.approx(100 / 0.99)


def test_cheap_underlying_scores_high_and_rich_one_scores_low():
    quiet, fair, pricey = (analyze(s) for s in demo.demo_snapshots())
    assert quiet.verdict == "CHEAP" and pricey.verdict == "rich"
    assert min(c.score for c in quiet.contracts) > 65
    assert max(c.score for c in pricey.contracts) < 35
    assert all(c.iv_vs_forecast < 0 and c.hist_edge > 0 for c in quiet.contracts)
    assert all(c.iv_vs_forecast > 0 and c.hist_edge < 0 for c in pricey.contracts)


def test_a_strike_below_its_smile_is_found():
    rep = analyze(demo.demo_snapshots()[1])
    best = rep.contracts[0]
    assert (best.dte, best.quote.strike, best.quote.right) == (46, 105.0, "C")
    assert best.iv - best.smile_iv == pytest.approx(-0.04, abs=0.006)
    assert best.components["smile"] == 1.0
    others = [c for c in rep.contracts if c is not best]
    assert all(abs(c.components["smile"]) < 0.3 for c in others if "smile" in c.components)


def test_a_cheap_expiry_is_flagged_against_the_term_structure():
    base = demo.make_snapshot("X", 0.25, 0.30, seed=7)
    cheap_mid = demo.make_snapshot("X", 0.25, 0.26, seed=7, dtes=(46,))
    base.quotes = [q for q in base.quotes if (q.expiry - base.asof).days != 46] + cheap_mid.quotes
    rep = analyze(base)
    mid = next(e for e in rep.expiries if e.dte == 46)
    assert mid.term_fair_iv == pytest.approx(0.30, abs=0.005)
    assert all(c.components["term"] == 1.0 for c in rep.contracts if c.dte == 46)
    assert all("term" not in c.components for c in rep.contracts if c.dte != 46)  # no curve to compare ends with


def test_liquidity_and_delta_filters():
    snap = demo.make_snapshot("X", 0.25, 0.30, seed=2)
    everything = analyze(snap, Config(min_abs_delta=0.0, max_abs_delta=1.0))
    default = analyze(snap)
    assert len(default.contracts) < len(everything.contracts)
    assert all(0.10 <= abs(c.delta) <= 0.60 for c in default.contracts)
    assert analyze(snap, Config(min_open_interest=5000)).contracts == []
    assert analyze(snap, Config(max_spread_pct=0.01)).contracts == []
    assert analyze(snap, Config(min_dte=30, max_dte=60)).expiries[0].dte == 46


def test_wide_spreads_lower_the_score():
    tight = analyze(demo.make_snapshot("X", 0.25, 0.30, seed=2, spread=0.02))
    wide = analyze(demo.make_snapshot("X", 0.25, 0.30, seed=2, spread=0.20))
    assert max(c.score for c in wide.contracts) < max(c.score for c in tight.contracts)


def test_only_out_of_the_money_contracts_are_ranked():
    rep = analyze(demo.make_snapshot("X", 0.25, 0.30, seed=2))
    fwd = {e.expiry: e.forward for e in rep.expiries}
    for c in rep.contracts:
        q = c.quote
        assert q.strike >= fwd[q.expiry] if q.right == "C" else q.strike < fwd[q.expiry]


def test_earnings_inside_an_expiry_is_flagged():
    snap = demo.make_snapshot("X", 0.25, 0.30, seed=2)
    snap.earnings_date = snap.asof + dt.timedelta(days=30)
    rep = analyze(snap)
    assert [e.spans_earnings for e in rep.expiries] == [False, True, True]
    assert all(c.spans_earnings == (c.dte > 30) for c in rep.contracts)


def test_iv_level_uses_own_history_when_there_is_enough():
    snap = demo.make_snapshot("X", 0.25, 0.30, seed=2)
    proxy = analyze(snap)
    assert proxy.level_basis == "realised-vol proxy"
    assert analyze(snap, iv_history=[0.5] * 10).level_basis == "realised-vol proxy"  # too short to trust
    high = analyze(snap, iv_history=[0.40 + 0.001 * i for i in range(100)])
    assert high.level_basis == "own IV history (100 days)"
    assert high.iv_percentile == 0.0 and high.iv_rank == 0.0
    low = analyze(snap, iv_history=[0.10 + 0.001 * i for i in range(100)])
    assert low.iv_percentile == 1.0 and low.iv_rank == 1.0
    assert high.contracts[0].score > low.contracts[0].score


def test_closed_market_quotes_produce_a_note_not_a_crash():
    snap = demo.make_snapshot("X", 0.25, 0.30, seed=2)
    snap.quotes = [OptionQuote(q.expiry, q.strike, q.right, 0.0, 0.0, 0, q.open_interest) for q in snap.quotes]
    rep = analyze(snap)
    assert rep.contracts == [] and rep.expiries == [] and rep.verdict == "n/a"
    assert "market is probably closed" in rep.notes[0]


def test_short_price_history_drops_the_history_based_measures():
    snap = demo.make_snapshot("X", 0.25, 0.30, seed=2)
    snap.bars = snap.bars[-15:]
    rep = analyze(snap)
    assert rep.forecast30 is None and rep.contracts
    assert all(set(c.components) <= {"smile", "spread", "term"} for c in rep.contracts)


# --- providers, storage, CLI -------------------------------------------------

def test_schwab_payloads_are_parsed():
    chain = json.loads((FIXTURES / "option_chain.json").read_text())
    history = json.loads((FIXTURES / "price_history.json").read_text())
    snap = schwab.parse("AAPL", dt.date(2025, 1, 3), chain, history)
    assert snap.spot == chain["underlyingPrice"]
    assert len(snap.bars) == len(history["candles"]) and snap.bars[0].close == 179.5
    call = next(q for q in snap.quotes if q.right == "C" and q.strike == 185.0)
    assert (call.expiry, call.bid, call.ask, call.volume, call.open_interest) == (dt.date(2025, 1, 10), 2.0, 2.1, 100, 1000)
    assert {q.right for q in snap.quotes} == {"C", "P"}
    with pytest.raises(RuntimeError):
        schwab.parse("ZZZZ", dt.date(2025, 1, 3), {"status": "FAILED"}, {})


def test_yahoo_frames_are_converted_and_missing_values_become_zero():
    pd = pytest.importorskip("pandas")
    frame = pd.DataFrame({"contractSymbol": ["A", "B"], "strike": [100.0, 105.0], "bid": [1.0, float("nan")],
                          "ask": [1.2, 0.0], "volume": [float("nan"), 7], "openInterest": [50, float("nan")]})
    a, b = yahoo.quotes_from_frame(frame, dt.date(2026, 2, 20), "C")
    assert (a.bid, a.ask, a.volume, a.open_interest, a.symbol) == (1.0, 1.2, 0, 50, "A")
    assert (b.bid, b.volume, b.open_interest) == (0.0, 7, 0)
    hist = pd.DataFrame({"Open": [10.0, float("nan")], "High": [11.0, 12.0], "Low": [9.0, 10.0], "Close": [10.5, 11.0]},
                        index=pd.to_datetime(["2026-01-02", "2026-01-05"]))
    bars = yahoo.bars_from_frame(hist)
    assert len(bars) == 1 and bars[0].date == dt.date(2026, 1, 2) and bars[0].close == 10.5


def test_iv_store_keeps_one_value_per_day_and_excludes_today(tmp_path):
    store = IvStore(tmp_path / "sub" / "iv.sqlite")
    store.record("aapl", dt.date(2026, 1, 2), 0.20)
    store.record("AAPL", dt.date(2026, 1, 5), 0.30)
    store.record("AAPL", dt.date(2026, 1, 5), 0.25)
    store.record("MSFT", dt.date(2026, 1, 5), 0.99)
    assert store.history("AAPL", dt.date(2026, 1, 6)) == [0.20, 0.25]
    assert store.history("AAPL", dt.date(2026, 1, 5)) == [0.20]
    store.close()


def test_snapshot_round_trips_through_json():
    snap = demo.make_snapshot("X", 0.25, 0.30, seed=2)
    snap.earnings_date = dt.date(2026, 2, 1)
    again = Snapshot.from_dict(json.loads(json.dumps(snap.to_dict())))
    assert (again.bars, again.quotes, again.earnings_date, again.spot) == (snap.bars, snap.quotes, snap.earnings_date, snap.spot)


def test_cli_demo_writes_reports_and_leaves_the_iv_database_alone(tmp_path, capsys):
    db = tmp_path / "iv.sqlite"
    code = cli.main(["--demo", "--top", "5", "--csv", str(tmp_path / "o.csv"), "--json", str(tmp_path / "o.json"),
                     "--iv-db", str(db)])
    out = capsys.readouterr().out
    assert code == 0 and "QUIET" in out and "CHEAPEST CONTRACTS" in out
    assert not db.exists()
    rows = (tmp_path / "o.csv").read_text().splitlines()
    assert len(rows) == 6 and rows[0].startswith("symbol,expiry,dte,strike,right")
    payload = json.loads((tmp_path / "o.json").read_text())
    assert [u["verdict"] for u in payload["underlyings"]] == ["CHEAP", "fair", "rich"]
    assert len(payload["contracts"]) == 5 and payload["contracts"][0]["symbol"] == "QUIET"


def test_cli_snapshot_files_record_iv_history_and_filters_apply(tmp_path, capsys):
    snap = demo.demo_snapshots()[1]
    path = tmp_path / "FAIR.json"
    path.write_text(json.dumps(snap.to_dict()))
    db = tmp_path / "iv.sqlite"
    assert cli.main(["--from-snapshot", str(path), "--iv-db", str(db), "--right", "P", "--sort", "smile"]) == 0
    out = capsys.readouterr().out
    body = out.split("CHEAPEST CONTRACTS")[1]
    assert "  P  " in body and "  C  " not in body
    store = IvStore(db)
    assert store.history("FAIR", snap.asof + dt.timedelta(days=1)) == [pytest.approx(0.27, abs=0.005)]
    store.close()
    assert cli.main(["--from-snapshot", str(path), "--no-store", "--sort", "smile", "--top", "1"]) == 0
    assert "105" in capsys.readouterr().out.split("CHEAPEST CONTRACTS")[1].splitlines()[3]


def test_cli_requires_a_ticker():
    with pytest.raises(SystemExit):
        cli.main(["--no-store"])
