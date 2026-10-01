from types import SimpleNamespace

import anthropic

from trading_agent import briefing


class FakeClient:
    def __init__(self, stop_reason="end_turn"):
        self.kwargs = None
        self.stop_reason = stop_reason
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=" Brief. ")],
        )


REPORT = {"top_picks": [], "universe": [{"symbol": "X"}], "sector_leaders": {"Tech": {"symbol": "X", "score": 1.0}}}


def test_briefing_sends_compact_report(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(anthropic, "Anthropic", lambda: fake)
    assert briefing.write_briefing(REPORT) == "Brief."
    assert fake.kwargs["model"] == "claude-opus-5-5"
    prompt = fake.kwargs["messages"][0]["content"]
    assert '"universe"' not in prompt and '"sector_leaders"' in prompt


def test_briefing_handles_refusal(monkeypatch):
    monkeypatch.setattr(anthropic, "Anthropic", lambda: FakeClient("refusal"))
    assert "unavailable" in briefing.write_briefing(REPORT)
