# Social Media Video Summarizer (Claude agent)

An agent that summarizes videos from YouTube/Shorts, TikTok, Instagram Reels, X/Twitter,
Facebook, Reddit, Vimeo and [~1800 other sites](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md),
or a local video file.

Claude can't play a video or hear audio on its own, so the agent gives it free, local tools
that turn a video into text and images. Claude decides which tools to call, then writes the summary.

| Tool | What it gives Claude | Free resource behind it |
|---|---|---|
| `get_video_info` | title, creator, duration, description, stats | [yt-dlp](https://github.com/yt-dlp/yt-dlp) |
| `get_transcript` | spoken words with timestamps | platform captions via yt-dlp, else local [faster-whisper](https://github.com/SYSTRAN/faster-whisper) |
| `get_keyframes` | timestamped screenshots (Claude reads images) | [ffmpeg](https://ffmpeg.org) |
| `get_top_comments` | audience reaction | yt-dlp |

The only paid part is the Claude API call itself. Everything else runs on your machine.

## Setup

```bash
cd video-summarizer
pip install -r requirements.txt          # faster-whisper is optional (only for videos without captions)
# install ffmpeg: macOS `brew install ffmpeg`, Ubuntu `sudo apt install ffmpeg`, Windows `winget install ffmpeg`
export ANTHROPIC_API_KEY=sk-ant-...
```

## Usage

```bash
python video_agent.py "https://www.youtube.com/watch?v=VIDEO_ID"
python video_agent.py "https://www.tiktok.com/@user/video/123" --style bullets
python video_agent.py "https://www.instagram.com/reel/XYZ/" --cookies-from-browser chrome
python video_agent.py ./clip.mp4 --focus "product claims" -o summary.md
```

Options: `--style standard|brief|bullets|detailed`, `--focus "<topic>"`,
`--whisper-model tiny|base|small|medium|large-v3`, `-o FILE`, `-q` (hide tool calls).
Set `VIDEO_AGENT_MODEL` to use a different Claude model (default `claude-opus-5-5`).

Output sections: TL;DR, Key points (with timestamps), Visuals, Tone & audience, Caveats.

## Notes

- Instagram, Facebook and some TikToks require you to be logged in: pass `--cookies-from-browser`.
- Keep yt-dlp current (`pip install -U yt-dlp`). Sites change often and old versions break.
- Only summarize content you are allowed to access, and follow each platform's terms.

## Tests

```bash
python -m pytest -q
```
