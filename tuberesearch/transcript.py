"""Transcript fetcher with graceful fallback."""
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


def fetch_transcript(video_id: str, *, max_chars: int = 28_000) -> TranscriptResult:
    """Fetch English transcript if available. Truncate to max_chars to keep prompts cheap."""
    try:
        listing = YouTubeTranscriptApi.list_transcripts(video_id)
        transcript = None
        try:
            transcript = listing.find_manually_created_transcript(["en", "en-US", "en-GB"])
            auto = False
        except NoTranscriptFound:
            try:
                transcript = listing.find_generated_transcript(["en", "en-US", "en-GB"])
                auto = True
            except NoTranscriptFound:
                # last resort: take any transcript and translate to English
                first = next(iter(listing), None)
                if first is None:
                    return TranscriptResult(video_id, None, None, False, error="no transcripts")
                if first.is_translatable:
                    transcript = first.translate("en")
                    auto = True
                else:
                    transcript = first
                    auto = first.is_generated

        chunks = transcript.fetch()
        text = " ".join(c["text"].replace("\n", " ").strip() for c in chunks if c.get("text"))
        if len(text) > max_chars:
            text = text[:max_chars] + "…"
        return TranscriptResult(
            video_id=video_id,
            text=text,
            language=transcript.language_code,
            auto_generated=auto,
        )
    except (TranscriptsDisabled, VideoUnavailable) as e:
        return TranscriptResult(video_id, None, None, False, error=type(e).__name__)
    except Exception as e:
        return TranscriptResult(video_id, None, None, False, error=str(e)[:160])
