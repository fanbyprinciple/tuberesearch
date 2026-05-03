"""Transcript fetcher with stealth posture (jitter, IP-block detection)."""
from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
)


@dataclass
class TranscriptResult:
    video_id: str
    text: str | None
    language: str | None
    auto_generated: bool
    error: str | None = None
    ip_blocked: bool = False

    @property
    def has_text(self) -> bool:
        return self.text is not None and len(self.text.strip()) > 0


_ENGLISH_CODES = ["en", "en-US", "en-GB", "en-IN", "en-AU"]


def _proxies_from_env() -> dict | None:
    user = os.environ.get("WEBSHARE_USERNAME")
    pw = os.environ.get("WEBSHARE_PASSWORD")
    if user and pw:
        proxy_url = f"http://{user}:{pw}@p.webshare.io:80"
        return {"http": proxy_url, "https": proxy_url}
    http = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
    https = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if http or https:
        return {"http": http or https, "https": https or http}
    return None


def _is_ip_block(err: Exception) -> bool:
    s = (str(err) + " " + type(err).__name__).lower()
    return any(
        sig in s
        for sig in (
            "ipblocked",
            "request_blocked",
            "blocking requests from your ip",
            "youtuberequestfailed",
            "too many requests",
        )
    )


def fetch_transcript(
    video_id: str,
    *,
    max_chars: int = 28_000,
    jitter_seconds: tuple[float, float] = (2.0, 5.0),
) -> TranscriptResult:
    """Fetch English transcript with stealth pacing. Sleeps random 2-5s before every call."""
    time.sleep(random.uniform(*jitter_seconds))

    try:
        api = YouTubeTranscriptApi(proxies=_proxies_from_env())
    except TypeError:
        api = YouTubeTranscriptApi()

    try:
        transcript_list = api.list(video_id)
        chosen = None
        auto = False

        try:
            chosen = transcript_list.find_manually_created_transcript(_ENGLISH_CODES)
            auto = False
        except NoTranscriptFound:
            pass

        if chosen is None:
            try:
                chosen = transcript_list.find_generated_transcript(_ENGLISH_CODES)
                auto = True
            except NoTranscriptFound:
                pass

        if chosen is None:
            first = next(iter(transcript_list), None)
            if first is None:
                return TranscriptResult(video_id, None, None, False, error="no_transcripts")
            if first.is_translatable:
                chosen = first.translate("en")
                auto = True
            else:
                chosen = first
                auto = first.is_generated

        fetched = chosen.fetch()
        snippets = list(fetched)
        text = " ".join(
            (getattr(s, "text", "") or "").replace("\n", " ").strip()
            for s in snippets
        ).strip()

        if not text:
            return TranscriptResult(video_id, None, chosen.language_code, auto, error="empty")

        if len(text) > max_chars:
            text = text[:max_chars] + "…"

        return TranscriptResult(
            video_id=video_id,
            text=text,
            language=chosen.language_code,
            auto_generated=auto,
        )
    except (TranscriptsDisabled, VideoUnavailable) as e:
        return TranscriptResult(video_id, None, None, False, error=type(e).__name__)
    except Exception as e:
        if _is_ip_block(e):
            return TranscriptResult(video_id, None, None, False, error="ip_blocked", ip_blocked=True)
        return TranscriptResult(video_id, None, None, False, error=str(e)[:200])
