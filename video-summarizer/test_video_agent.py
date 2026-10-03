import shutil
import subprocess
from types import SimpleNamespace

import pytest

import video_agent
import video_tools as vt

VTT = """WEBVTT
Kind: captions

00:00:01.000 --> 00:00:03.000
Hello <c>everyone</c>

00:00:03.000 --> 00:00:05.000
Hello everyone

00:01:05.500 --> 00:01:07.000
Today we bake bread
"""


def test_parse_vtt_strips_tags_and_duplicates():
    assert vt.parse_captions(VTT, "vtt") == "[00:01] Hello everyone\n[01:05] Today we bake bread"


def test_parse_json3():
    raw = '{"events": [{"tStartMs": 2000, "segs": [{"utf8": "Hi "}, {"utf8": "there"}]}, {"tStartMs": 3000}]}'
    assert vt.parse_captions(raw, "json3") == "[00:02] Hi there"


def test_pick_language_falls_back():
    assert vt._pick_language({"en-US": []}, "en") == "en-US"
    assert vt._pick_language({"de": [], "fr-orig": []}, "en") == "fr-orig"
    assert vt._pick_language({}, "en") is None


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_keyframes_from_local_file(tmp_path):
    clip = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=4:size=320x240:rate=10",
                    str(clip)], check=True)
    session = vt.VideoSession(str(clip))
    try:
        blocks = vt.get_keyframes(session, count=3)
    finally:
        session.cleanup()
    images = [b for b in blocks if b["type"] == "image"]
    assert len(images) == 3
    assert blocks[0]["text"].startswith("Frame at 00:0")


class FakeClient:
    """Plays back scripted responses and records what the agent sent."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def _block(**kw):
    return SimpleNamespace(**kw)


def test_agent_loop_runs_tools_and_returns_summary(monkeypatch):
    monkeypatch.setattr(vt, "get_video_info", lambda s: '{"title": "Bread"}')

    def boom(s, language="en"):
        raise vt.ToolError("no captions")

    monkeypatch.setattr(vt, "get_transcript", boom)
    client = FakeClient([
        _block(stop_reason="tool_use", content=[
            _block(type="tool_use", id="t1", name="get_video_info", input={}),
            _block(type="tool_use", id="t2", name="get_transcript", input={"language": "en"}),
        ]),
        _block(stop_reason="end_turn", content=[_block(type="text", text="## TL;DR\nBread.")]),
    ])
    out = video_agent.summarize(vt.VideoSession("https://example.com/v"), client=client, verbose=False)
    assert out == "## TL;DR\nBread."
    results = client.calls[1]["messages"][2]["content"]  # user turn carrying tool results
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"]
    assert results[1]["is_error"] is True
    assert client.calls[0]["model"] == video_agent.MODEL
