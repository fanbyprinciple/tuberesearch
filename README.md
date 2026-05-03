# tuberesearch

YouTube research agent. Give it a task. It searches, fetches transcripts, summarizes each video with Claude Haiku, and ranks the best ones with Claude Sonnet. Returns winners + tools surfaced + skip list.

## How it works

```
query --> YouTube Data API search (free, 10k units/day quota)
       --> youtube-transcript-api (no auth, free)
       --> Claude Haiku 4.5 summarize each video (~$0.001/video)
       --> Claude Sonnet 4.6 rank winners + dedupe tools (~$0.01/run)
       --> rich terminal output
```

No browser automation. No OAuth (yet). One CLI command.

## Setup

```bash
cd ~/codeplay/tuberesearch
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
# fill keys, then:
set -a && source .env && set +a
```

### Get a YouTube API key

1. https://console.cloud.google.com → New project
2. APIs & Services → Library → enable "YouTube Data API v3"
3. APIs & Services → Credentials → Create Credentials → API key
4. Restrict to YouTube Data API v3 (recommended)
5. Paste into `.env` as `YOUTUBE_API_KEY`

### Get an Anthropic key

https://console.anthropic.com → paste into `.env` as `ANTHROPIC_API_KEY`.

## Usage

```bash
tuberesearch "best react three fiber tutorials 2025"
tuberesearch "claude code workflow tips" --max 8
tuberesearch "noise cancelling earbuds long flight" --recent 90
tuberesearch "EPUB reader Android Kotlin tutorial" --max 5
```

## Cost (rough)

- YouTube API: free up to 10k units/day. One run uses ~110 units (search.list + videos.list + N video fetches). ~90 runs/day on free tier.
- Claude Haiku per-video summary: ~$0.001
- Claude Sonnet final rank: ~$0.01
- 10-video run total: **≈ $0.02**

## Use it from Claude Code (skill)

A wrapper skill is registered at `~/.claude-work/skills/tuberesearch/SKILL.md`. Inside Claude Code:

```
research best react three fiber tutorials on youtube
tuberesearch claude code workflow tips
find me good talks on systems design from the last year
```

The skill calls this Python CLI as a Bash hook — it does not re-implement the logic. So one source of truth: this repo. Update here, the skill picks up changes immediately.

## When to add OAuth (later)

Skip OAuth for v1. Add it when you want:
- Personalized recs (your own watch history bias)
- Filtering against your liked / saved videos
- Avoiding videos from channels you've already dismissed

For pure search + transcripts, public API key is enough.

## Design notes

- `youtube-transcript-api` falls back gracefully when captions are missing — those videos still get ranked, just on title + description.
- Transcripts truncated to 28k chars before summary (keeps Haiku prompts cheap).
- Per-video summarize runs in parallel (`--workers`, default 4).
- Final ranking is one batched Sonnet call across all summaries.
