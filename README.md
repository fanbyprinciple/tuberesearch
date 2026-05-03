# tuberesearch

YouTube research agent. Give it a task. It searches YouTube (no API key — scrapes the public results page), fetches video transcripts, ranks the best ones, surfaces tools/sites mentioned across videos.

Three CLIs in one repo, pick by need:

| Binary | Uses LLM? | API key | Best for |
|---|---|---|---|
| `tuberesearch` | yes (Claude Haiku + Sonnet) | needs `ANTHROPIC_API_KEY` | best ranking quality |
| `tuberesearch-pure` | no — heuristic ranker | none | offline, free, deterministic |
| `tuberesearch-telegram` | no | needs `TELEGRAM_BOT_TOKEN` | research from your phone |

Plus an `--raw` mode on the main CLI that emits JSON for downstream tools / Claude Code skill use.

## How it works

```
query --> public YouTube results page scrape (no key, no browser)
       --> youtube-transcript-api (no auth, free)
       --> Claude Haiku 4.5 summarize each video (~$0.001/video)
       --> Claude Sonnet 4.6 rank winners + dedupe tools (~$0.01/run)
       --> rich terminal output
```

No browser automation. No Google API key. No OAuth. **Only an Anthropic API key is required.**

## Setup

```bash
cd ~/codeplay/tuberesearch
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
# edit .env, paste your ANTHROPIC_API_KEY, then:
set -a && source .env && set +a
```

### Get an Anthropic key

https://console.anthropic.com → API Keys → Create → paste into `.env` as `ANTHROPIC_API_KEY`.

That's the only key you need.

## Usage — main CLI (with Anthropic key)

```bash
tuberesearch "best react three fiber tutorials 2025"
tuberesearch "claude code workflow tips" --max 8
tuberesearch "noise cancelling earbuds long flight" --recent 90
tuberesearch "EPUB reader Android Kotlin tutorial" --max 5
tuberesearch "your topic" --raw                 # JSON only, no LLM, no key
```

## Usage — pure-Python heuristic CLI (no key, no LLM)

```bash
tuberesearch-pure "free seedance 2.0 access"
tuberesearch-pure "ableton live workflow" --top 5 --recent 365
tuberesearch-pure "any topic" --format json     # structured output
tuberesearch-pure "any topic" --format rich     # pretty terminal panels
```

Default output is WhatsApp / Telegram / AI-agent friendly markdown:

```
*Top picks for:* _free seedance 2.0 access_

*1. How to use Seedance 2.0 (No VPN Needed)?*
_Geek Savvy_ · 2.4k views · 1m 56s · 2026-04-03
https://www.youtube.com/watch?v=lMEASTL7Iew
Why: with transcript · surfaces 9 tools  (score 0.78)
Tools: superbase.co, Seedance, VPN, Cedance, ...

*2. ...*

---
*Tools surfaced* (most-mentioned first):
• Seedance — 5 videos
• VPN — 3 videos
...
```

Paste straight into WhatsApp, Slack, ChatGPT, or any other LLM as a research brief.

## Usage — Telegram bot (no key beyond Telegram, no LLM)

```bash
# 1. Get a bot token from @BotFather on Telegram
# 2. Add to .env:
echo "TELEGRAM_BOT_TOKEN=123456:ABC..." >> .env
# 3. Start the polling bot:
tuberesearch-telegram
```

Then in Telegram, message your bot:

```
free seedance 2.0 access sites
/research best vegan wraps under 30 minutes
claude code workflow tips --max 8
ableton workflow --recent 90
```

Bot replies with the same markdown format, split into <4000-char chunks if long.

## Cost (rough)

- Search: free (HTTP scrape, no API quota).
- Transcripts: free (`youtube-transcript-api`, no auth).
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

## How the search step works (no API key)

1. Issues a plain `GET https://www.youtube.com/results?search_query=<query>` with a normal Chrome User-Agent.
2. Regex-extracts the `ytInitialData` JSON blob YouTube embeds in the page.
3. Walks the renderer tree for `videoRenderer` entries, pulls title / channel / view count / duration / publish-relative-time.
4. Returns the same `VideoHit` shape the rest of the pipeline already consumed.

This is the same trick `yt-dlp` uses. Stable for years. If YouTube changes the HTML and the scrape breaks, swap in browser automation (`browser-use` + Playwright) — see `tuberesearch/search.py` for the contract to satisfy.

## Design notes

- `youtube-transcript-api` falls back gracefully when captions are missing — those videos still get ranked, just on title + description.
- Transcripts truncated to 28k chars before summary (keeps Haiku prompts cheap).
- Per-video summarize runs in parallel (`--workers`, default 4).
- Final ranking is one batched Sonnet call across all summaries.

## When you might want browser automation later

If you ever want personalized YouTube results (Watch History bias, subscribed channels, age-gated content), swap `search.py` for a `browser-use`-driven Playwright session. Logged-in Chrome → same `VideoHit` output. Slower (~1-2 min/run) but personalized.

For research-the-topic queries, the scrape path is faster, cheaper, and gets you the same relevance.
