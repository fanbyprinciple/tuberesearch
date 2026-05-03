"""Per-video summary + final ranking via Claude."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

from anthropic import Anthropic

from .search import VideoHit
from .transcript import TranscriptResult

HAIKU_MODEL = "claude-haiku-4-5-20251001"
SONNET_MODEL = "claude-sonnet-4-6"

SUMMARY_SYSTEM = """You are a careful research assistant.

You will receive a video's metadata and (when available) its transcript. Produce a
short, factual brief in this exact format:

GIST: <one sentence describing what the video actually covers>
KEY_POINTS:
- <crisp bullet>
- <crisp bullet>
- <crisp bullet, 3-6 total>
TOOLS_MENTIONED: <comma-separated list, or "none">
PRACTICAL_VALUE: <one sentence on whether this video helps someone do the user's task>
SIGNAL_NOTES: <fluff, ad-read minutes, sponsor sections, padding — anything off-task>

Rules:
- Quote nothing. Paraphrase.
- If transcript is missing, say so in SIGNAL_NOTES and base the brief only on title + description.
- No emojis. No filler. No markdown headers."""


RANK_SYSTEM = """You are an expert curator who picks the best videos for a user's specific task.

You receive:
- the user's task
- N candidate videos with: title, channel, view count, duration, brief

Output JSON ONLY, no preamble, exactly in this shape:
{
  "winners": [
    {"video_id": "...", "rank": 1, "why": "<concrete reason this beats others, ≤30 words>"},
    ... up to 5 entries, fewer if pool is shallow ...
  ],
  "tools_recommended": [
    {"name": "<tool name>", "why": "<one line>", "video_id": "<vid that surfaced it>"}
  ],
  "skip_list": [
    {"video_id": "...", "why": "<one line>"}
  ]
}

Rank rules:
- Reward: practical depth, recency for fast-moving topics, clear demos, signal density.
- Penalize: clickbait without payoff, padding, ad-heavy, surface-level overviews when user wants depth.
- "tools_recommended" should de-duplicate tools across videos and pick the best mention of each.
- "skip_list" is for videos that are misleading, off-task, or pure ads.
- Be strict. Quality over quantity."""


@dataclass
class VideoBrief:
    video_id: str
    text: str
    used_transcript: bool


def _client(api_key: str | None = None) -> Anthropic:
    return Anthropic(api_key=api_key or os.environ["ANTHROPIC_API_KEY"])


def summarize_video(
    hit: VideoHit,
    transcript: TranscriptResult,
    *,
    client: Anthropic | None = None,
) -> VideoBrief:
    client = client or _client()
    used_transcript = transcript.has_text
    transcript_block = transcript.text or "(no transcript available)"
    user_msg = (
        f"VIDEO METADATA\n"
        f"Title: {hit.title}\n"
        f"Channel: {hit.channel}\n"
        f"Published: {hit.published_at}\n"
        f"Views: {hit.view_count}\n"
        f"Duration (ISO): {hit.duration_iso}\n"
        f"Description: {hit.description[:1000]}\n\n"
        f"TRANSCRIPT (truncated):\n{transcript_block}\n"
    )
    msg = client.messages.create(
        model=HAIKU_MODEL,
        max_tokens=600,
        system=[
            {"type": "text", "text": SUMMARY_SYSTEM, "cache_control": {"type": "ephemeral"}},
        ],
        messages=[{"role": "user", "content": user_msg}],
    )
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    return VideoBrief(video_id=hit.video_id, text=text, used_transcript=used_transcript)


def rank_videos(
    task: str,
    hits: list[VideoHit],
    briefs: list[VideoBrief],
    *,
    client: Anthropic | None = None,
) -> dict:
    client = client or _client()
    by_id = {h.video_id: h for h in hits}
    blocks = []
    for brief in briefs:
        h = by_id.get(brief.video_id)
        if not h:
            continue
        blocks.append(
            f"--- VIDEO {brief.video_id} ---\n"
            f"Title: {h.title}\n"
            f"Channel: {h.channel}\n"
            f"Views: {h.view_count}\n"
            f"Duration: {h.duration_iso}\n"
            f"Used transcript: {brief.used_transcript}\n"
            f"Brief:\n{brief.text}\n"
        )
    user_msg = f"USER TASK:\n{task}\n\nCANDIDATES:\n\n" + "\n".join(blocks)

    msg = client.messages.create(
        model=SONNET_MODEL,
        max_tokens=2000,
        system=[
            {"type": "text", "text": RANK_SYSTEM, "cache_control": {"type": "ephemeral"}},
        ],
        messages=[{"role": "user", "content": user_msg}],
    )
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"winners": [], "tools_recommended": [], "skip_list": [], "_raw": text}
