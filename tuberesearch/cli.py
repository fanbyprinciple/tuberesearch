"""tuberesearch CLI."""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text

from .search import VideoHit, search_videos
from .transcript import TranscriptResult, fetch_transcript

console = Console()


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


def _gather_transcripts(
    hits: list[VideoHit],
    *,
    max_workers: int = 4,
    quiet: bool = False,
) -> dict[str, TranscriptResult]:
    transcripts: dict[str, TranscriptResult] = {}
    if quiet:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(fetch_transcript, h.video_id): h for h in hits}
            for fut in as_completed(futures):
                tr = fut.result()
                transcripts[tr.video_id] = tr
        return transcripts

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        transient=True,
    ) as progress:
        t_task = progress.add_task("fetching transcripts", total=len(hits))
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(fetch_transcript, h.video_id): h for h in hits}
            for fut in as_completed(futures):
                tr = fut.result()
                transcripts[tr.video_id] = tr
                progress.advance(t_task)
    return transcripts


def _emit_raw_json(
    task: str,
    hits: list[VideoHit],
    transcripts: dict[str, TranscriptResult],
) -> None:
    """Print structured JSON: search hits + transcripts. No LLM."""
    payload = {
        "task": task,
        "videos": [
            {
                **asdict(h),
                "url": h.url,
                "transcript": _serialize_transcript(transcripts.get(h.video_id)),
            }
            for h in hits
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _serialize_transcript(tr: TranscriptResult | None) -> dict | None:
    if tr is None:
        return None
    return {
        "language": tr.language,
        "auto_generated": tr.auto_generated,
        "error": tr.error,
        "text": tr.text,
    }


def _gather_briefs(
    hits: list[VideoHit],
    *,
    max_workers: int = 4,
):
    from .summarize import VideoBrief, summarize_video  # deferred so --raw mode has no anthropic dep

    transcripts: dict[str, TranscriptResult] = {}
    briefs: list[VideoBrief] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        transient=True,
    ) as progress:
        t_task = progress.add_task("fetching transcripts", total=len(hits))
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(fetch_transcript, h.video_id): h for h in hits}
            for fut in as_completed(futures):
                tr = fut.result()
                transcripts[tr.video_id] = tr
                progress.advance(t_task)

        s_task = progress.add_task("summarizing", total=len(hits))
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {
                ex.submit(summarize_video, h, transcripts[h.video_id]): h for h in hits
            }
            for fut in as_completed(futures):
                briefs.append(fut.result())
                progress.advance(s_task)

    return briefs


def _print_results(task: str, hits: list[VideoHit], briefs, rank: dict) -> None:
    by_id = {h.video_id: h for h in hits}
    brief_by_id = {b.video_id: b for b in briefs}

    console.print()
    console.rule(f"[bold]Top picks for: [italic]{task}[/italic]")
    winners = rank.get("winners") or []
    if not winners:
        console.print("[yellow]No winners ranked. Raw output:[/yellow]")
        console.print(rank.get("_raw", rank))
        return

    for w in winners:
        vid = w.get("video_id")
        h = by_id.get(vid)
        if not h:
            continue
        b = brief_by_id.get(vid)
        title = Text(f"#{w.get('rank', '?')}  {h.title}", style="bold")
        meta = (
            f"{h.channel}  ·  {_fmt_views(h.view_count)} views  ·  "
            f"{_fmt_duration(h.duration_iso)}  ·  {h.published_at[:10]}"
        )
        body = (
            f"[bold]Why:[/bold] {w.get('why', '')}\n"
            f"[dim]{meta}[/dim]\n"
            f"[blue]{h.url}[/blue]"
        )
        if b and b.text:
            body += f"\n\n[dim]{b.text}[/dim]"
        console.print(Panel(body, title=title, border_style="green", expand=True))

    tools = rank.get("tools_recommended") or []
    if tools:
        console.rule("[bold]Tools surfaced")
        table = Table(show_header=True, header_style="bold")
        table.add_column("Tool", style="cyan")
        table.add_column("Why", overflow="fold")
        table.add_column("Source")
        for t in tools:
            vid = t.get("video_id")
            link = by_id.get(vid).url if by_id.get(vid) else ""
            table.add_row(t.get("name", ""), t.get("why", ""), link)
        console.print(table)

    skips = rank.get("skip_list") or []
    if skips:
        console.rule("[dim]Skip[/dim]")
        for s in skips:
            vid = s.get("video_id")
            h = by_id.get(vid)
            if h:
                console.print(f"  [dim]· {h.title} — {s.get('why', '')}[/dim]")


def main(argv: list[str] | None = None) -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(
        prog="tuberesearch",
        description="Search YouTube, fetch transcripts, summarize, rank.",
    )
    parser.add_argument("query", nargs="+", help="research topic / task description")
    parser.add_argument("--max", type=int, default=10, help="max videos to fetch (default 10)")
    parser.add_argument("--recent", type=int, default=None, help="only consider videos from last N days")
    parser.add_argument("--workers", type=int, default=4, help="parallel transcript+summary workers")
    parser.add_argument(
        "--raw",
        action="store_true",
        help="emit JSON of search hits + transcripts on stdout. Skips LLM summary/rank. No ANTHROPIC_API_KEY required.",
    )
    parser.add_argument(
        "--transcript-chars",
        type=int,
        default=12_000,
        help="max transcript chars per video in --raw output (default 12000)",
    )
    args = parser.parse_args(argv)

    task = " ".join(args.query)

    # --raw needs no key
    if not args.raw and not os.environ.get("ANTHROPIC_API_KEY"):
        console.print(
            "[red]error:[/red] ANTHROPIC_API_KEY not set. Add it to .env, "
            "or run with --raw for JSON output (no LLM summary)."
        )
        return 2

    if not args.raw:
        console.print(f"[bold]task:[/bold] {task}")
        console.print(f"[dim]searching YouTube (max={args.max}, recent={args.recent or 'all'})...[/dim]")

    hits = search_videos(task, max_results=args.max, recent_days=args.recent)
    if not hits:
        if args.raw:
            print(json.dumps({"task": task, "videos": []}, ensure_ascii=False, indent=2))
        else:
            console.print("[yellow]No videos found.[/yellow]")
        return 1

    if args.raw:
        transcripts = _gather_transcripts(hits, max_workers=args.workers, quiet=True)
        # truncate transcripts for chat-friendly payloads
        for tr in transcripts.values():
            if tr.text and len(tr.text) > args.transcript_chars:
                tr.text = tr.text[: args.transcript_chars] + "…"
        _emit_raw_json(task, hits, transcripts)
        return 0

    console.print(f"[green]{len(hits)}[/green] videos found")
    from .summarize import rank_videos  # deferred import for --raw users
    briefs = _gather_briefs(hits, max_workers=args.workers)
    rank = rank_videos(task, hits, briefs)
    _print_results(task, hits, briefs, rank)
    return 0


if __name__ == "__main__":
    sys.exit(main())
