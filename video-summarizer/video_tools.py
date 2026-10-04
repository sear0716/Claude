"""Free, local tools that let Claude "watch" a social media video.

Claude can read text and images but cannot open a video URL or listen to audio,
so these tools turn a video into things Claude can read:

* yt-dlp (free, open source)        -> metadata, captions, audio/video download
                                       for YouTube, TikTok, Instagram, X/Twitter,
                                       Facebook, Reddit, Vimeo and ~1800 other sites
* faster-whisper (free, local)      -> speech-to-text when the video has no captions
* ffmpeg (free, open source)        -> keyframe screenshots Claude can look at
"""

from __future__ import annotations

import base64
import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

MAX_TRANSCRIPT_CHARS = 60_000
MAX_FRAMES = 12


class ToolError(Exception):
    """Raised when a tool cannot do its job; reported back to Claude as is_error."""


@dataclass
class VideoSession:
    """Holds per-run state (work dir, downloaded files) so tools don't re-download."""

    source: str
    cookies_from_browser: str | None = None
    whisper_model: str = "base"
    workdir: str = field(default_factory=lambda: tempfile.mkdtemp(prefix="vidsum_"))
    _info: dict | None = None
    _video_path: str | None = None
    _audio_path: str | None = None

    @property
    def is_local(self) -> bool:
        return os.path.isfile(self.source)

    def cleanup(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)

    # ---- yt-dlp helpers -------------------------------------------------

    def _ydl_opts(self, **extra) -> dict:
        opts = {"quiet": True, "no_warnings": True, "noplaylist": True}
        if self.cookies_from_browser:
            # Needed for Instagram/Facebook/some TikToks that require a login.
            opts["cookiesfrombrowser"] = (self.cookies_from_browser,)
        opts.update(extra)
        return opts

    def info(self) -> dict:
        if self._info is None:
            if self.is_local:
                self._info = {"title": os.path.basename(self.source), "duration": _probe_duration(self.source)}
            else:
                yt_dlp = _import_yt_dlp()
                try:
                    with yt_dlp.YoutubeDL(self._ydl_opts(skip_download=True)) as ydl:
                        self._info = ydl.sanitize_info(ydl.extract_info(self.source, download=False))
                except Exception as e:  # yt-dlp raises many error types
                    raise ToolError(f"Could not read video info: {e}") from e
        return self._info

    def download(self, audio_only: bool) -> str:
        if self.is_local:
            return self.source
        if audio_only and self._audio_path:
            return self._audio_path
        if self._video_path:
            return self._video_path
        yt_dlp = _import_yt_dlp()
        kind = "audio" if audio_only else "video"
        fmt = "bestaudio/best" if audio_only else "best[height<=480]/bestvideo[height<=480]+bestaudio/best"
        outtmpl = os.path.join(self.workdir, f"{kind}.%(ext)s")
        try:
            with yt_dlp.YoutubeDL(self._ydl_opts(format=fmt, outtmpl=outtmpl)) as ydl:
                ydl.download([self.source])
        except Exception as e:
            raise ToolError(f"Could not download {kind}: {e}") from e
        files = glob.glob(os.path.join(self.workdir, f"{kind}.*"))
        if not files:
            raise ToolError(f"Download finished but no {kind} file was produced.")
        if audio_only:
            self._audio_path = files[0]
        else:
            self._video_path = files[0]
        return files[0]


# ---- tool implementations ------------------------------------------------


def get_video_info(session: VideoSession) -> str:
    info = session.info()
    keys = [
        "title", "uploader", "channel", "upload_date", "duration", "view_count",
        "like_count", "comment_count", "extractor_key", "webpage_url", "tags",
    ]
    out = {k: info.get(k) for k in keys if info.get(k) not in (None, "", [])}
    if info.get("description"):
        out["description"] = info["description"][:4000]
    out["has_captions"] = bool(info.get("subtitles") or info.get("automatic_captions"))
    return json.dumps(out, indent=2, ensure_ascii=False)


def get_transcript(session: VideoSession, language: str = "en") -> str:
    """Return the spoken words: platform captions first, local Whisper as fallback."""
    if not session.is_local:
        text = _captions_from_platform(session, language)
        if text:
            return _truncate(f"[source: platform captions]\n{text}")
    return _truncate(f"[source: local Whisper transcription]\n{_whisper(session, language)}")


def get_keyframes(session: VideoSession, count: int = 6) -> list[dict]:
    """Return evenly spaced screenshots as Claude image blocks, each labelled with its timestamp."""
    if shutil.which("ffmpeg") is None:
        raise ToolError("ffmpeg is not installed, so frames cannot be extracted.")
    count = max(1, min(int(count), MAX_FRAMES))
    path = session.download(audio_only=False)
    duration = session.info().get("duration") or _probe_duration(path) or 0
    stamps = [duration * (i + 0.5) / count for i in range(count)] if duration else [0]
    blocks: list[dict] = []
    for i, t in enumerate(stamps):
        out = os.path.join(session.workdir, f"frame_{i:02d}.jpg")
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", path,
             "-frames:v", "1", "-vf", "scale=768:-2", "-q:v", "4", out],
            check=False,
        )
        if not os.path.exists(out):
            continue
        with open(out, "rb") as f:
            data = base64.standard_b64encode(f.read()).decode()
        blocks.append({"type": "text", "text": f"Frame at {_fmt_time(t)}"})
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}})
    if not blocks:
        raise ToolError("ffmpeg could not extract any frames from the video.")
    return blocks


def get_top_comments(session: VideoSession, limit: int = 20) -> str:
    if session.is_local:
        raise ToolError("Comments are not available for local files.")
    yt_dlp = _import_yt_dlp()
    limit = max(1, min(int(limit), 50))
    opts = session._ydl_opts(
        skip_download=True,
        getcomments=True,
        extractor_args={"youtube": {"max_comments": [str(limit)], "comment_sort": ["top"]}},
    )
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(session.source, download=False)
    except Exception as e:
        raise ToolError(f"Could not fetch comments: {e}") from e
    comments = sorted(info.get("comments") or [], key=lambda c: c.get("like_count") or 0, reverse=True)
    if not comments:
        raise ToolError("This platform did not return any comments.")
    lines = [f"- ({c.get('like_count') or 0} likes) {c.get('text', '').strip()[:400]}" for c in comments[:limit]]
    return "\n".join(lines)


# ---- internals -----------------------------------------------------------


def _captions_from_platform(session: VideoSession, language: str) -> str | None:
    info = session.info()
    for track_set in (info.get("subtitles") or {}, info.get("automatic_captions") or {}):
        lang = _pick_language(track_set, language)
        if not lang:
            continue
        for track in track_set[lang]:
            if track.get("ext") in ("vtt", "srv1", "json3", "ttml", "srt") and track.get("url"):
                raw = _fetch(session, track["url"])
                text = parse_captions(raw, track["ext"])
                if text:
                    return text
    return None


def _pick_language(tracks: dict, language: str) -> str | None:
    if language in tracks:
        return language
    for key in tracks:
        if key.split("-")[0] == language:
            return key
    # Fall back to any original-language track so non-English videos still work.
    for key in tracks:
        if key.endswith("-orig"):
            return key
    return next(iter(tracks), None)


def _fetch(session: VideoSession, url: str) -> str:
    yt_dlp = _import_yt_dlp()
    with yt_dlp.YoutubeDL(session._ydl_opts()) as ydl:
        return ydl.urlopen(url).read().decode("utf-8", errors="replace")


def parse_captions(raw: str, ext: str) -> str:
    """Turn a caption file into plain text with [mm:ss] markers, dropping duplicate lines."""
    lines: list[str] = []
    if ext == "json3":
        data = json.loads(raw)
        for ev in data.get("events", []):
            text = "".join(s.get("utf8", "") for s in ev.get("segs", []) or []).strip()
            if text:
                lines.append(f"[{_fmt_time(ev.get('tStartMs', 0) / 1000)}] {text}")
    else:
        stamp = None
        for line in raw.splitlines():
            line = line.strip()
            m = re.match(r"(\d+:)?(\d{1,2}):(\d{2})[.,]\d+\s*-->", line)
            if m:
                h = int(m.group(1)[:-1]) if m.group(1) else 0
                stamp = h * 3600 + int(m.group(2)) * 60 + int(m.group(3))
                continue
            if not line or line.isdigit() or line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")):
                continue
            text = re.sub(r"<[^>]+>", "", line).strip()
            if text:
                lines.append(f"[{_fmt_time(stamp or 0)}] {text}")
    deduped: list[str] = []
    seen_text: str | None = None
    for line in lines:
        text = line.split("] ", 1)[-1]
        if text != seen_text:  # auto-captions repeat each line as it scrolls
            deduped.append(line)
        seen_text = text
    return "\n".join(deduped)


def _whisper(session: VideoSession, language: str) -> str:
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise ToolError(
            "No captions were available and faster-whisper is not installed. "
            "Install it with `pip install faster-whisper` to transcribe audio locally for free."
        ) from e
    path = session.download(audio_only=True)
    model = WhisperModel(session.whisper_model, device="auto", compute_type="int8")
    segments, _ = model.transcribe(path, language=None if language == "auto" else language, vad_filter=True)
    text = "\n".join(f"[{_fmt_time(s.start)}] {s.text.strip()}" for s in segments)
    if not text:
        raise ToolError("Whisper found no speech in the audio (the video may be music-only).")
    return text


def _probe_duration(path: str) -> float | None:
    if shutil.which("ffprobe") is None:
        return None
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return None


def _import_yt_dlp():
    try:
        import yt_dlp
    except ImportError as e:
        raise ToolError("yt-dlp is not installed. Run `pip install yt-dlp`.") from e
    return yt_dlp


def _fmt_time(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _truncate(text: str) -> str:
    if len(text) <= MAX_TRANSCRIPT_CHARS:
        return text
    return text[:MAX_TRANSCRIPT_CHARS] + f"\n... [transcript truncated; {len(text) - MAX_TRANSCRIPT_CHARS} more characters]"
