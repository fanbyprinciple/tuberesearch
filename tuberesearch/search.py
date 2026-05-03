"""YouTube search via public results-page scrape (no API key)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests


@dataclass
class VideoHit:
    video_id: str
    title: str
    channel: str
    channel_id: str
    description: str
    published_at: str          # ISO date string when we can derive one, else "approximate" text
    duration_iso: str | None = None
    view_count: int | None = None
    like_count: int | None = None  # not available via scrape; kept for compat

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


_YT_DATA_RE = re.compile(r"var ytInitialData = (\{.*?\});</script>", re.DOTALL)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Cookie": "CONSENT=YES+1",  # bypass EU consent splash
}

_REL_TIME_RE = re.compile(
    r"(\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago",
    re.IGNORECASE,
)
_REL_TIME_DAYS = {
    "second": 1 / 86400,
    "minute": 1 / 1440,
    "hour": 1 / 24,
    "day": 1,
    "week": 7,
    "month": 30,
    "year": 365,
}


def search_videos(
    query: str,
    *,
    max_results: int = 12,
    recent_days: int | None = None,
    api_key: str | None = None,  # accepted for back-compat, ignored
) -> list[VideoHit]:
    html = _fetch_results_html(query)
    initial = _extract_initial_data(html)
    items = _walk_video_renderers(initial)

    hits: list[VideoHit] = []
    for r in items:
        try:
            hit = _renderer_to_hit(r)
        except Exception:
            continue
        if hit is None:
            continue
        if recent_days is not None and not _published_within(hit.published_at, recent_days):
            continue
        hits.append(hit)
        if len(hits) >= max_results:
            break
    return hits


def _fetch_results_html(query: str) -> str:
    resp = requests.get(
        "https://www.youtube.com/results",
        params={"search_query": query, "hl": "en", "gl": "US"},
        headers=_HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.text


def _extract_initial_data(html: str) -> dict:
    match = _YT_DATA_RE.search(html)
    if not match:
        # fallback: some pages emit `window["ytInitialData"] = {...};`
        alt = re.search(r'ytInitialData"?\]?\s*=\s*(\{.*?\});\s*</script>', html, re.DOTALL)
        if not alt:
            raise RuntimeError("Could not locate ytInitialData in YouTube response")
        return json.loads(alt.group(1))
    return json.loads(match.group(1))


def _walk_video_renderers(data: dict) -> list[dict]:
    """Pull every videoRenderer from search results, in order."""
    out: list[dict] = []
    primary = (
        data.get("contents", {})
        .get("twoColumnSearchResultsRenderer", {})
        .get("primaryContents", {})
    )
    sections = (
        primary.get("sectionListRenderer", {}).get("contents", [])
    )
    for sec in sections:
        item_section = sec.get("itemSectionRenderer")
        if not item_section:
            continue
        for c in item_section.get("contents", []):
            vr = c.get("videoRenderer")
            if vr:
                out.append(vr)
    return out


def _renderer_to_hit(r: dict) -> VideoHit | None:
    video_id = r.get("videoId")
    if not video_id:
        return None
    title = _runs_text(r.get("title"))
    channel = _runs_text(r.get("ownerText")) or _runs_text(r.get("longBylineText"))
    channel_id = _channel_id(r)
    description = _runs_text(r.get("descriptionSnippet")) or _runs_text(r.get("detailedMetadataSnippets", [{}])[0].get("snippetText") if r.get("detailedMetadataSnippets") else None)
    duration = _simple_text(r.get("lengthText"))
    duration_iso = _to_iso_duration(duration)
    view_count = _parse_view_count(_simple_text(r.get("viewCountText")) or _runs_text(r.get("viewCountText")))
    published_text = _simple_text(r.get("publishedTimeText")) or ""
    published_iso = _approx_iso_from_relative(published_text)
    return VideoHit(
        video_id=video_id,
        title=title,
        channel=channel,
        channel_id=channel_id,
        description=description or "",
        published_at=published_iso or published_text,
        duration_iso=duration_iso,
        view_count=view_count,
        like_count=None,
    )


def _runs_text(node) -> str:
    if not node:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        if "simpleText" in node:
            return node["simpleText"] or ""
        runs = node.get("runs")
        if runs:
            return "".join(r.get("text", "") for r in runs)
    return ""


def _simple_text(node) -> str:
    if not node:
        return ""
    if isinstance(node, dict):
        return node.get("simpleText", "") or _runs_text(node)
    return ""


def _channel_id(r: dict) -> str:
    runs = r.get("ownerText", {}).get("runs") or r.get("longBylineText", {}).get("runs") or []
    for run in runs:
        nav = run.get("navigationEndpoint", {}).get("browseEndpoint", {})
        bid = nav.get("browseId")
        if bid:
            return bid
    return ""


def _parse_view_count(text: str) -> int | None:
    if not text:
        return None
    s = text.lower().replace(",", "").strip()
    s = s.replace("views", "").strip()
    if not s:
        return None
    multipliers = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}
    if s[-1] in multipliers:
        try:
            return int(float(s[:-1]) * multipliers[s[-1]])
        except ValueError:
            return None
    try:
        return int(s)
    except ValueError:
        return None


def _to_iso_duration(text: str) -> str | None:
    """Convert '18:42' / '1:08:42' to 'PT18M42S' / 'PT1H8M42S'."""
    if not text:
        return None
    parts = text.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 2:
        m, s = nums
        return f"PT{m}M{s}S"
    if len(nums) == 3:
        h, m, s = nums
        return f"PT{h}H{m}M{s}S"
    return None


def _approx_iso_from_relative(text: str) -> str | None:
    """'3 weeks ago' -> ISO date approx of (today - 3 weeks)."""
    if not text:
        return None
    m = _REL_TIME_RE.search(text)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2).lower()
    days = _REL_TIME_DAYS.get(unit, 0) * n
    when = datetime.now(timezone.utc) - timedelta(days=days)
    return when.replace(microsecond=0).isoformat()


def _published_within(published_at: str, days: int) -> bool:
    if not published_at:
        return True
    try:
        dt = datetime.fromisoformat(published_at)
    except ValueError:
        return True
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return dt >= cutoff
