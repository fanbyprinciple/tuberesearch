"""YouTube Data API v3 search wrapper."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from googleapiclient.discovery import build


@dataclass
class VideoHit:
    video_id: str
    title: str
    channel: str
    channel_id: str
    description: str
    published_at: str
    duration_iso: str | None = None
    view_count: int | None = None
    like_count: int | None = None

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


def _client(api_key: str | None = None):
    key = api_key or os.environ["YOUTUBE_API_KEY"]
    return build("youtube", "v3", developerKey=key, cache_discovery=False)


def search_videos(
    query: str,
    *,
    max_results: int = 12,
    recent_days: int | None = None,
    api_key: str | None = None,
) -> list[VideoHit]:
    """Search YouTube and return enriched video metadata.

    Two API calls: search.list -> video IDs; videos.list -> stats + duration.
    """
    yt = _client(api_key)

    search_kwargs = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "maxResults": min(max_results, 25),
        "relevanceLanguage": "en",
        "safeSearch": "moderate",
        "order": "relevance",
    }
    if recent_days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=recent_days)
        search_kwargs["publishedAfter"] = cutoff.replace(microsecond=0).isoformat()

    search_resp = yt.search().list(**search_kwargs).execute()
    items = search_resp.get("items", [])
    if not items:
        return []

    ids = [it["id"]["videoId"] for it in items if it.get("id", {}).get("videoId")]
    if not ids:
        return []

    details_resp = yt.videos().list(
        part="contentDetails,statistics",
        id=",".join(ids),
    ).execute()
    detail_map = {
        it["id"]: it for it in details_resp.get("items", [])
    }

    hits: list[VideoHit] = []
    for it in items:
        vid = it.get("id", {}).get("videoId")
        if not vid:
            continue
        snip = it.get("snippet", {})
        det = detail_map.get(vid, {})
        stats = det.get("statistics", {})
        cd = det.get("contentDetails", {})
        hits.append(
            VideoHit(
                video_id=vid,
                title=snip.get("title", ""),
                channel=snip.get("channelTitle", ""),
                channel_id=snip.get("channelId", ""),
                description=snip.get("description", ""),
                published_at=snip.get("publishedAt", ""),
                duration_iso=cd.get("duration"),
                view_count=int(stats["viewCount"]) if "viewCount" in stats else None,
                like_count=int(stats["likeCount"]) if "likeCount" in stats else None,
            )
        )
    return hits
