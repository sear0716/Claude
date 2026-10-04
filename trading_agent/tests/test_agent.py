import json

from trading_agent.agent import TradingAgent
from trading_agent.config import Config
from trading_agent.report import to_csv, to_markdown
from trading_agent.sources.fred import MacroSnapshot
from trading_agent.sources.gdelt import NewsSignal
from trading_agent.sources.ibkr import Holding
from trading_agent.sources.offline import DemoSource
from trading_agent.sources.sec_edgar import Fundamentals

from .conftest import make_ohlc, wavy_trend


class Prices:
    name = "fake"

    def __init__(self, frames):
        self.frames = frames

    def history(self, symbol, days):
        if symbol not in self.frames:
            raise LookupError("no data")
        return self.frames[symbol].tail(days)


class Broker:
    def account_summary(self):
        return {"NetLiquidation": 50_000.0}

    def positions(self):
        return [Holding("DOWN", 20, 170.0, "U1"), Holding("OUTSIDE", 5, 100.0, "U1")]


class Sec:
    def fundamentals(self, symbol, include_filings=True):
        shares = {"UP1": 1e9, "UP2": 5e9, "UP3": 2e9}.get(symbol)
        return Fundamentals(symbol, name=f"{symbol} Corp", shares_outstanding=shares, revenue_growth=0.2, net_income=1.0)


class Fred:
    def snapshot(self):
        return MacroSnapshot(score=0, regime="neutral", notes=[])


class Gdelt:
    def __init__(self):
        self.queries = []

    def geopolitical(self):
        return NewsSignal("geo", avg_tone=-1.0)

    def company_news(self, name):
        self.queries.append(name)
        return NewsSignal(name, avg_tone=2.5, headlines=[{"title": "good", "url": "http://x", "domain": "d", "seen": "s"}])


def _frames():
    up = wavy_trend(100, 180, n=330, amp=0.0)
    return {
        "UP1": make_ohlc(up),
        "UP2": make_ohlc(up * 1.1),
        "UP3": make_ohlc(up * 0.9),
        "DOWN": make_ohlc(wavy_trend(180, 100)),
        "OUTSIDE": make_ohlc(up),
    }


def test_agent_full_pipeline(tmp_path):
    cfg = Config()
    cfg.per_sector = 2
    cfg.top_n = 3
    gdelt = Gdelt()
    agent = TradingAgent(
        cfg,
        Prices(_frames()),
        broker=Broker(),
        sec=Sec(),
        fred=Fred(),
        gdelt=gdelt,
        universe={"Tech": ["UP1", "UP2", "UP3"], "Energy": ["DOWN", "MISSING"]},
    )
    report = agent.run().to_dict()

    assert report["equity"] == 50_000.0
    # Market-cap ranking keeps the two largest: UP2 (5e9 shares) and UP3 (2e9 x 0.9 price) over UP1.
    tech = [r["symbol"] for r in report["universe"] if r["sector"] == "Tech"]
    assert sorted(tech) == ["UP2", "UP3"]
    assert any("MISSING" in e for e in report["errors"])

    held = {r["symbol"]: r for r in report["holdings"]}
    assert held["DOWN"]["action"] == "SELL"
    assert "OUTSIDE" in held and held["OUTSIDE"]["sector"].startswith("Holdings")

    for pick in report["top_picks"]:
        assert pick["action"] in ("BUY", "BUY_PULLBACK")
        assert pick["news"]["avg_tone"] == 2.5  # shortlisted names were enriched
    assert gdelt.queries  # company names come from SEC entity names
    assert all(q.endswith("Corp") for q in gdelt.queries)

    md = to_markdown(report)
    assert "Macro regime" in md and "Your IBKR positions" in md
    to_csv(report, tmp_path / "s.csv")
    assert (tmp_path / "s.csv").read_text().startswith("symbol,")
    json.dumps(report, default=str)


def test_cli_demo_mode(tmp_path, capsys):
    from trading_agent.cli import main

    assert main(["--demo", "--symbols", "AAPL,MSFT,NVDA", "--out", str(tmp_path)]) == 0
    assert (tmp_path / "report.json").exists()
    assert "DEMO MODE" in (tmp_path / "report.md").read_text()


def test_demo_universe_runs_every_sector():
    cfg = Config()
    report = TradingAgent(cfg, DemoSource()).run().to_dict()
    assert len(report["sector_leaders"]) == 11
    assert len(report["top_picks"]) <= cfg.top_n
    sectors = [p["sector"] for p in report["top_picks"]]
    assert max(sectors.count(s) for s in set(sectors)) <= cfg.max_per_sector_in_top
