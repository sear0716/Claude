#!/usr/bin/env python3
"""Social media video summarizer agent.

Claude drives the work: it decides which free local tools to call (metadata,
captions/transcription, keyframes, comments), then writes the summary.

    python video_agent.py "https://www.youtube.com/watch?v=..."
    python video_agent.py "https://www.tiktok.com/@user/video/..." --style bullets
    python video_agent.py ./my_clip.mp4 -o summary.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import anthropic

import video_tools as vt

MODEL = os.environ.get("VIDEO_AGENT_MODEL", "claude-opus-5-5")
MAX_TURNS = 12

SYSTEM_PROMPT = """You summarize social media videos (YouTube, Shorts, TikTok, Instagram Reels, X, Facebook, Reddit, etc.).

You cannot play the video yourself, so use the tools to gather evidence:
- get_video_info: title, creator, platform, duration, description. Start here.
- get_transcript: what is said. Most of a video's content usually lives here.
- get_keyframes: screenshots across the video. Use them for on-screen text, demos, visual-only
  or music-only videos, or when the transcript is missing or ambiguous.
- get_top_comments: optional, for audience reaction when it adds something.

Call independent tools in parallel. If a tool fails, work with what you have and say what was
unavailable. Never invent content you did not see in the tool results.

Write the final answer in Markdown with these sections:
## TL;DR  (1-2 sentences)
## Key points  (bullets, with [mm:ss] timestamps where known)
## Visuals  (only if frames were used: what is shown on screen)
## Tone & audience  (style, intended audience, any calls to action or sponsorships)
## Caveats  (missing data, claims that look unverified, or misleading framing)
"""

STYLE_HINTS = {
    "standard": "",
    "brief": "Keep the whole summary under 120 words.",
    "bullets": "Use bullets only, no prose paragraphs.",
    "detailed": "Be thorough: include a section-by-section breakdown with timestamps.",
}

TOOLS = [
    {
        "name": "get_video_info",
        "description": "Get the video's metadata: title, uploader, platform, upload date, duration, view/like counts, description, tags, and whether captions exist.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_transcript",
        "description": "Get the spoken words with [mm:ss] timestamps. Uses the platform's captions if available, otherwise transcribes the audio locally with Whisper (slower).",
        "input_schema": {
            "type": "object",
            "properties": {
                "language": {"type": "string", "description": "ISO 639-1 code like 'en' or 'es', or 'auto' to detect. Default 'en'."}
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_keyframes",
        "description": f"Get evenly spaced screenshots from the video, each labelled with its timestamp. Max {vt.MAX_FRAMES}.",
        "input_schema": {
            "type": "object",
            "properties": {"count": {"type": "integer", "description": "Number of frames, 1-12. Default 6."}},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_top_comments",
        "description": "Get the most-liked viewer comments. Not supported on every platform.",
        "input_schema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "description": "Number of comments, 1-50. Default 20."}},
            "additionalProperties": False,
        },
    },
]


def run_tool(session: vt.VideoSession, name: str, args: dict):
    if name == "get_video_info":
        return vt.get_video_info(session)
    if name == "get_transcript":
        return vt.get_transcript(session, args.get("language", "en"))
    if name == "get_keyframes":
        return vt.get_keyframes(session, args.get("count", 6))
    if name == "get_top_comments":
        return vt.get_top_comments(session, args.get("limit", 20))
    raise vt.ToolError(f"Unknown tool: {name}")


def summarize(session: vt.VideoSession, style: str = "standard", focus: str | None = None,
              client: anthropic.Anthropic | None = None, verbose: bool = True) -> str:
    client = client or anthropic.Anthropic()
    request = f"Summarize this video: {session.source}"
    if STYLE_HINTS.get(style):
        request += f"\n{STYLE_HINTS[style]}"
    if focus:
        request += f"\nThe reader especially wants to know about: {focus}"
    messages: list = [{"role": "user", "content": request}]

    for _ in range(MAX_TURNS):
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
            output_config={"effort": "high"},
            # If a safety classifier declines, the API retries on a fallback model automatically.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("Claude declined to summarize this video.")
        if response.stop_reason == "max_tokens":
            raise RuntimeError("Response hit max_tokens before finishing.")

        # Append the full content unchanged (keeps thinking blocks valid across turns).
        messages.append({"role": "assistant", "content": response.content})
        tool_calls = [b for b in response.content if b.type == "tool_use"]
        if response.stop_reason != "tool_use" or not tool_calls:
            return "\n".join(b.text for b in response.content if b.type == "text").strip()

        results = []
        for call in tool_calls:
            if verbose:
                print(f"  -> {call.name}({json.dumps(call.input)})", file=sys.stderr)
            try:
                content = run_tool(session, call.name, call.input or {})
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": content})
            except vt.ToolError as e:
                if verbose:
                    print(f"     ! {e}", file=sys.stderr)
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": str(e), "is_error": True})
        # All results go back in one user message so Claude keeps calling tools in parallel.
        messages.append({"role": "user", "content": results})

    raise RuntimeError(f"Agent did not finish within {MAX_TURNS} turns.")


def main() -> int:
    p = argparse.ArgumentParser(description="Summarize a social media video with Claude.")
    p.add_argument("source", help="Video URL (YouTube, TikTok, Instagram, X, ...) or a local video file")
    p.add_argument("--style", choices=STYLE_HINTS, default="standard")
    p.add_argument("--focus", help="Something specific you want the summary to cover")
    p.add_argument("--cookies-from-browser", metavar="BROWSER",
                   help="Use your browser's login cookies for sites that need sign-in (chrome, firefox, edge, safari)")
    p.add_argument("--whisper-model", default="base", help="faster-whisper model size: tiny, base, small, medium, large-v3")
    p.add_argument("-o", "--output", help="Write the summary to this file as well")
    p.add_argument("-q", "--quiet", action="store_true", help="Don't print tool calls")
    args = p.parse_args()

    session = vt.VideoSession(args.source, cookies_from_browser=args.cookies_from_browser,
                              whisper_model=args.whisper_model)
    try:
        summary = summarize(session, style=args.style, focus=args.focus, verbose=not args.quiet)
    except (anthropic.APIError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    finally:
        session.cleanup()

    print(summary)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(summary + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
