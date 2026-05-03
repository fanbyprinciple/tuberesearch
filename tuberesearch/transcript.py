"""Transcript fetcher with graceful fallback (youtube-transcript-api v1.x)."""
from __future__ import annotations

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

    @property
    def has_text(self) -> bool:
        return self.text is not None and len(self.text.strip()) > 0


_ENGLISH_CODES = ["en", "en-US", "en-GB", "en-IN", "en-AU"]


def fetch_transcript(video_id: str, *, max_chars: int = 28_000) -> TranscriptResult:
    """Fetch English transcript if available. Truncate to max_chars to keep prompts cheap."""
    api = YouTubeTranscriptApi()
    try:
        transcript_list = api.list(video_id)
        chosen = None
        auto = False

        # 1. Try manually-created English captions (best quality)
        try:
            chosen = transcript_list.find_manually_created_transcript(_ENGLISH_CODES)
            auto = False
        except NoTranscriptFound:
            pass

        # 2. Try auto-generated English captions
        if chosen is None:
            try:
                chosen = transcript_list.find_generated_transcript(_ENGLISH_CODES)
                auto = True
            except NoTranscriptFound:
                pass

        # 3. Fall back to first available, translate to English if possible
        if chosen is None:
            first = next(iter(transcript_list), None)
            if first is None:
                return TranscriptResult(video_id, None, None, False, error="no transcripts")
            if first.is_translatable:
                chosen = first.translate("en")
                auto = True
            else:
                chosen = first
                auto = first.is_generated

        fetched = chosen.fetch()  # FetchedTranscript with .snippets
        snippets = list(fetched)
        text = " ".join(
            (getattr(s, "text", "") or "").replace("\n", " ").strip()
            for s in snippets
        ).strip()

        if not text:
            return TranscriptResult(video_id, None, chosen.language_code, auto, error="empty transcript")

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
        return TranscriptResult(video_id, None, None, False, error=str(e)[:200])
