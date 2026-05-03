"""Pure-Python heuristic ranking. No LLM, no API key, no network beyond the existing search + transcript steps.

Strategy:
  1. Reuse search.py + transcript.py.
  2. Extract tools/links/products from each transcript with regex + a curated keyword list.
  3. Score each video deterministically:
       0.30 * log10(views) normalised
     + 0.20 * recency factor
     + 0.20 * transcript length factor
     + 0.30 * task-keyword density in title+description+transcript
  4. Rank top N. Dedupe tools across videos by normalised name.
  5. Print rich panels — same shape as the LLM version, just without "Why" reasoning.

Quality is lower than the Claude-Sonnet ranker, but deterministic and free.
"""
from __future__ import annotations

import argparse
import math
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text

from .search import VideoHit, search_videos
from .transcript import TranscriptResult, fetch_transcript

console = Console()


# --- regex + dictionaries ---

URL_RE = re.compile(r"https?://[\w.-]+(?:/[\w./%-]*)?", re.IGNORECASE)
DOMAIN_RE = re.compile(
    r"\b([a-z0-9-]+\.(?:com|ai|io|app|dev|co|net|org|tech|page|so|ml|cloud))\b",
    re.IGNORECASE,
)
PRODUCT_RE = re.compile(
    r"\b([A-Z][a-zA-Z0-9]{2,}(?:\s+[A-Z][a-zA-Z0-9]+)?)\b",
)

# Tokens that survive PRODUCT_RE but aren't products. Keep this list short — too aggressive and we lose real tools.
PRODUCT_STOPLIST = {
    "Today", "Tomorrow", "Yesterday", "Hello", "Welcome", "Subscribe",
    "Like", "Comment", "Share", "Click", "Tap", "Open", "Close", "First",
    "Second", "Third", "Last", "Next", "Previous", "Here", "There",
    "This", "That", "These", "Those", "What", "When", "Where", "Why",
    "How", "Who", "Which", "Just", "Now", "Then", "Also", "Still",
    "Even", "Already", "Something", "Anything", "Nothing", "Everything",
    "Everyone", "Anyone", "Someone", "Nobody", "Everybody",
    "And", "But", "Or", "If", "Because", "Although", "Since",
    "Hi", "Hey", "Yes", "No", "Okay", "Ok", "Sure", "Maybe",
    "Indian", "American", "British", "Chinese", "Japanese", "European",
    "Hindi", "English", "Spanish", "French", "German",
    "Tutorial", "Video", "Watch", "Read", "Use", "Get", "Make", "See",
    "Look", "Find", "Try", "Know", "Want", "Need", "Take", "Give",
    "Show", "Tell", "Ask", "Help", "Work", "Play", "Run", "Move",
    "Best", "Better", "Good", "Great", "New", "Old", "Free", "Paid",
    "Premium", "Pro", "Plus", "Basic", "Full", "Complete", "Final",
    "Real", "Fake", "True", "False", "Right", "Wrong",
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "AI", "API", "URL", "JSON", "HTTP", "HTTPS", "USA", "UK", "EU",
}

# Common AI/tool product names — boost their score if seen
PRODUCT_HINTS = {
    "chatgpt", "gpt", "claude", "gemini", "grok", "perplexity", "midjourney",
    "stable diffusion", "dall-e", "dalle", "openai", "anthropic", "cursor",
    "copilot", "github", "gitlab", "vscode", "notion", "obsidian", "raycast",
    "linear", "figma", "framer", "webflow", "supabase", "firebase", "vercel",
    "netlify", "render", "fly", "railway", "huggingface", "replicate",
    "runwayml", "runway", "kling", "pika", "veo", "sora", "luma",
    "seedance", "dreamina", "doubao", "wavespeed", "capcut", "pipit",
    "openart", "martiniart", "creative fabrica", "robo neo", "elevenlabs",
    "synthesia", "heygen", "discord", "slack", "telegram", "whatsapp",
    "instagram", "tiktok", "youtube", "twitter", "linkedin", "reddit",
    "ollama", "lmstudio", "llama", "mistral", "deepseek", "qwen",
    "n8n", "zapier", "make.com", "browser-use", "playwright", "selenium",
}


@dataclass
class ToolMention:
    name: str
    sources: list[str]  # video_ids that mentioned it
    raw_examples: list[str]  # raw strings as they appeared (for surfacing the URL form etc.)

    @property
    def count(self) -> int:
        return len(self.sources)


@dataclass
class ScoredVideo:
    hit: VideoHit
    transcript: TranscriptResult
    score: float
    tools: list[str]
    why: str  # short heuristic reason

    @property
    def video_id(self) -> str:
        return self.hit.video_id


# --- scoring ---

def _views_score(views: int | None) -> float:
    if not views or views <= 0:
        return 0.0
    # log10(1e6 views) ~= 6 → normalize against 7 (10M as ceiling)
    return min(math.log10(views) / 7.0, 1.0)


def _recency_score(published_at: str) -> float:
    if not published_at:
        return 0.5
    try:
        dt = datetime.fromisoformat(published_at)
    except ValueError:
        return 0.5
    age_days = (datetime.now(timezone.utc) - dt).days
    if age_days <= 30:
        return 1.0
    if age_days <= 90:
        return 0.7
    if age_days <= 365:
        return 0.4
    return 0.15


def _transcript_score(text: str | None) -> float:
    if not text:
        return 0.0
    n = len(text)
    if n >= 3000:
        return 1.0
    if n >= 1000:
        return 0.7
    if n >= 200:
        return 0.4
    return 0.1


def _keyword_score(task: str, hit: VideoHit, transcript_text: str | None) -> float:
    task_tokens = [t for t in re.findall(r"\w+", task.lower()) if len(t) > 2]
    if not task_tokens:
        return 0.0
    blob = " ".join(filter(None, [
        hit.title.lower(),
        hit.description.lower(),
        (transcript_text or "").lower(),
    ]))
    if not blob:
        return 0.0
    hits = sum(blob.count(t) for t in task_tokens)
    # rough density: hits per 1000 chars, capped
    density = hits / max(len(blob) / 1000.0, 1.0)
    return min(density / 8.0, 1.0)


def score_video(task: str, hit: VideoHit, transcript: TranscriptResult) -> float:
    return (
        0.30 * _views_score(hit.view_count)
        + 0.20 * _recency_score(hit.published_at)
        + 0.20 * _transcript_score(transcript.text)
        + 0.30 * _keyword_score(task, hit, transcript.text)
    )


def _why(hit: VideoHit, transcript: TranscriptResult, tools: list[str]) -> str:
    bits = []
    if hit.view_count and hit.view_count > 5000:
        bits.append(f"{_fmt_views(hit.view_count)} views")
    if transcript.has_text:
        bits.append("with transcript")
    else:
        bits.append("title-only signal")
    if tools:
        bits.append(f"surfaces {len(tools)} tools")
    return " · ".join(bits) if bits else "default rank"


# --- tool extraction ---

def extract_tools(text: str | None) -> list[str]:
    """Return a normalized, ordered list of tool/site names mentioned in text."""
    if not text:
        return []
    found: list[str] = []

    for url in URL_RE.findall(text):
        # strip tracking + path
        domain = re.sub(r"^https?://", "", url, flags=re.IGNORECASE).split("/", 1)[0]
        domain = domain.lower().lstrip("www.")
        found.append(domain)

    for dom in DOMAIN_RE.findall(text):
        found.append(dom.lower().lstrip("www."))

    # capitalized product names (filtered)
    for cand in PRODUCT_RE.findall(text):
        token = cand.strip()
        if token in PRODUCT_STOPLIST:
            continue
        if len(token) < 3:
            continue
        # require either it's in the hint list (case-insensitive) or it appears at least 2x in the text
        if token.lower() in PRODUCT_HINTS or text.count(token) >= 2:
            found.append(token)

    # also look for hint terms case-insensitively even without capitalization
    lower_text = text.lower()
    for hint in PRODUCT_HINTS:
        if hint in lower_text:
            found.append(hint)

    # dedupe order-preserving
    seen: set[str] = set()
    out: list[str] = []
    for t in found:
        key = _normalize_tool(t)
        if key and key not in seen:
            seen.add(key)
            out.append(t)
    return out


def _normalize_tool(name: str) -> str:
    n = name.lower().strip()
    n = re.sub(r"[^\w.-]", "", n)
    if n.startswith("www."):
        n = n[4:]
    if n.endswith("."):
        n = n[:-1]
    return n


# --- pipeline ---

def run_pure(
    task: str,
    *,
    max_results: int = 10,
    recent_days: int | None = None,
    workers: int = 4,
    top_n: int = 5,
    transcript_chars: int = 12_000,
) -> tuple[list[ScoredVideo], dict[str, ToolMention]]:
    """End-to-end pure-Python pipeline. Returns ranked videos + tool dict keyed by normalized name."""
    hits = search_videos(task, max_results=max_results, recent_days=recent_days)
    if not hits:
        return [], {}

    transcripts: dict[str, TranscriptResult] = {}
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        transient=True,
    ) as progress:
        t_task = progress.add_task("fetching transcripts", total=len(hits))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(fetch_transcript, h.video_id, max_chars=transcript_chars): h for h in hits}
            for fut in as_completed(futures):
                tr = fut.result()
                transcripts[tr.video_id] = tr
                progress.advance(t_task)

    scored: list[ScoredVideo] = []
    tool_index: dict[str, ToolMention] = {}

    for h in hits:
        tr = transcripts.get(h.video_id) or TranscriptResult(h.video_id, None, None, False, error="missing")
        blob = " ".join(filter(None, [h.title, h.description, tr.text or ""]))
        tools = extract_tools(blob)
        for raw in tools:
            key = _normalize_tool(raw)
            if not key:
                continue
            existing = tool_index.get(key)
            if existing is None:
                tool_index[key] = ToolMention(name=raw, sources=[h.video_id], raw_examples=[raw])
            else:
                if h.video_id not in existing.sources:
                    existing.sources.append(h.video_id)
                if raw not in existing.raw_examples:
                    existing.raw_examples.append(raw)

        s = score_video(task, h, tr)
        scored.append(ScoredVideo(hit=h, transcript=tr, score=s, tools=tools, why=_why(h, tr, tools)))

    scored.sort(key=lambda v: v.score, reverse=True)
    return scored[:top_n], tool_index


# --- CLI rendering ---

def _fmt_views(n: int | None) -> str:
    if n is None:
        return "—"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _fmt_duration(iso: str | None) -> str:
    if not iso or not iso.startswith("PT"):
        return "—"
    body = iso[2:]
    out = ""
    num = ""
    for ch in body:
        if ch.isdigit():
            num += ch
        elif ch in "HMS":
            out += num + ch.lower() + " "
            num = ""
    return out.strip() or "—"


def _print_rich(task: str, ranked: list[ScoredVideo], tool_index: dict[str, ToolMention]) -> None:
    console.print()
    console.rule(f"[bold]Top picks for: [italic]{task}[/italic]")
    for idx, sv in enumerate(ranked, 1):
        h = sv.hit
        title = Text(f"#{idx}  {h.title}", style="bold")
        meta = (
            f"{h.channel}  ·  {_fmt_views(h.view_count)} views  ·  "
            f"{_fmt_duration(h.duration_iso)}  ·  {h.published_at[:10] if h.published_at else '—'}"
        )
        body = (
            f"[bold]Score:[/bold] {sv.score:.2f}  ·  [bold]Why:[/bold] {sv.why}\n"
            f"[dim]{meta}[/dim]\n"
            f"[blue]{h.url}[/blue]"
        )
        if sv.tools:
            preview = ", ".join(sv.tools[:8])
            body += f"\n\n[dim]Tools/sites mentioned: {preview}{' …' if len(sv.tools) > 8 else ''}[/dim]"
        if sv.transcript.error:
            body += f"\n[yellow](no transcript: {sv.transcript.error})[/yellow]"
        console.print(Panel(body, title=title, border_style="green", expand=True))

    if not tool_index:
        return

    console.rule("[bold]Tools surfaced (deduped across all candidates)")
    table = Table(show_header=True, header_style="bold")
    table.add_column("Tool", style="cyan")
    table.add_column("Mentioned in", justify="right")
    table.add_column("Examples", overflow="fold")
    sorted_tools = sorted(tool_index.values(), key=lambda t: t.count, reverse=True)
    for t in sorted_tools[:25]:
        examples = ", ".join(sorted(set(t.raw_examples))[:3])
        table.add_row(t.name, str(t.count), examples)
    console.print(table)


def render_markdown(task: str, ranked: list[ScoredVideo], tool_index: dict[str, ToolMention]) -> str:
    """WhatsApp / Telegram / AI-agent friendly text. Plain markdown — no tables, no panels.

    Renders as readable text on:
      - WhatsApp (preserves *bold* and line breaks)
      - Telegram (with parse_mode='Markdown')
      - terminals (just text)
      - AI agent prompts (structured but human-skimmable)
    """
    lines: list[str] = []
    lines.append(f"*Top picks for:* _{task}_")
    lines.append("")

    for idx, sv in enumerate(ranked, 1):
        h = sv.hit
        date = h.published_at[:10] if h.published_at else "—"
        lines.append(f"*{idx}. {h.title}*")
        lines.append(f"_{h.channel}_ · {_fmt_views(h.view_count)} views · {_fmt_duration(h.duration_iso)} · {date}")
        lines.append(h.url)
        lines.append(f"Why: {sv.why}  (score {sv.score:.2f})")
        if sv.tools:
            preview = ", ".join(sv.tools[:8])
            extra = " …" if len(sv.tools) > 8 else ""
            lines.append(f"Tools: {preview}{extra}")
        if sv.transcript.error:
            lines.append(f"_(no transcript: {sv.transcript.error})_")
        lines.append("")

    if tool_index:
        lines.append("---")
        lines.append("*Tools surfaced* (most-mentioned first):")
        sorted_tools = sorted(tool_index.values(), key=lambda t: t.count, reverse=True)
        for t in sorted_tools[:20]:
            videos_word = "video" if t.count == 1 else "videos"
            lines.append(f"• {t.name} — {t.count} {videos_word}")
        lines.append("")

    return "\n".join(lines).strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tuberesearch-pure",
        description="Pure-Python YouTube research: heuristic rank, no LLM, no API key.",
    )
    parser.add_argument("query", nargs="+", help="research topic / task description")
    parser.add_argument("--max", type=int, default=10, help="max videos to fetch (default 10)")
    parser.add_argument("--top", type=int, default=5, help="how many ranked videos to print (default 5)")
    parser.add_argument("--recent", type=int, default=None, help="only consider videos from last N days")
    parser.add_argument("--workers", type=int, default=4, help="parallel transcript workers")
    parser.add_argument(
        "--transcript-chars",
        type=int,
        default=12_000,
        help="max transcript chars per video (default 12000)",
    )
    parser.add_argument(
        "--format",
        choices=["md", "rich", "json"],
        default="md",
        help="output format. md = WhatsApp/Telegram/AI-agent friendly markdown (default). rich = terminal panels. json = structured.",
    )
    args = parser.parse_args(argv)

    task = " ".join(args.query)

    if args.format == "rich":
        console.print(f"[bold]task:[/bold] {task}")
        console.print(f"[dim]searching YouTube (max={args.max}, recent={args.recent or 'all'})...[/dim]")

    ranked, tool_index = run_pure(
        task,
        max_results=args.max,
        recent_days=args.recent,
        workers=args.workers,
        top_n=args.top,
        transcript_chars=args.transcript_chars,
    )

    if not ranked:
        if args.format == "json":
            import json as _json
            print(_json.dumps({"task": task, "ranked": [], "tools": []}, indent=2))
        elif args.format == "rich":
            console.print("[yellow]No videos found.[/yellow]")
        else:
            print(f"No videos found for: {task}")
        return 1

    if args.format == "json":
        import json as _json

        payload = {
            "task": task,
            "ranked": [
                {
                    "rank": i + 1,
                    "video_id": sv.hit.video_id,
                    "title": sv.hit.title,
                    "channel": sv.hit.channel,
                    "url": sv.hit.url,
                    "score": round(sv.score, 4),
                    "why": sv.why,
                    "views": sv.hit.view_count,
                    "duration_iso": sv.hit.duration_iso,
                    "published_at": sv.hit.published_at,
                    "tools": sv.tools,
                    "has_transcript": sv.transcript.has_text,
                }
                for i, sv in enumerate(ranked)
            ],
            "tools": sorted(
                [
                    {"name": t.name, "count": t.count, "sources": t.sources, "examples": t.raw_examples}
                    for t in tool_index.values()
                ],
                key=lambda r: r["count"],
                reverse=True,
            )[:50],
        }
        print(_json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.format == "rich":
        _print_rich(task, ranked, tool_index)
        return 0

    # default: markdown
    print(render_markdown(task, ranked, tool_index))
    return 0


if __name__ == "__main__":
    sys.exit(main())
