# Quickstart

Five-minute setup, both for standalone CLI use and as a Claude Code skill.

## 1. Install the CLI

```bash
git clone https://github.com/fanbyprinciple/tuberesearch.git ~/codeplay/tuberesearch
cd ~/codeplay/tuberesearch
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Verify:

```bash
tuberesearch --help
```

## 2. Get keys

### YouTube Data API v3 (free)

1. https://console.cloud.google.com → New Project (or pick existing)
2. APIs & Services → Library → "YouTube Data API v3" → Enable
3. APIs & Services → Credentials → Create Credentials → API key
4. (Optional but recommended) Restrict to YouTube Data API v3
5. Copy the key

### Anthropic API key

1. https://console.anthropic.com → API Keys → Create
2. Copy the key

## 3. Configure `.env`

```bash
cd ~/codeplay/tuberesearch
cp .env.example .env
# edit .env, fill in:
#   YOUTUBE_API_KEY=AIza...
#   ANTHROPIC_API_KEY=sk-ant-...
```

## 4. Run it (standalone)

```bash
cd ~/codeplay/tuberesearch
source .venv/bin/activate
set -a && source .env && set +a
tuberesearch "best react three fiber tutorials"
```

Common flags:

| Flag | Effect |
|---|---|
| `--max N` | how many videos to fetch (default 10, max 25) |
| `--recent N` | only consider videos from last N days |
| `--workers N` | parallel transcript+summary workers (default 4) |

Examples:

```bash
tuberesearch "claude code workflow tips" --max 8
tuberesearch "noise cancelling earbuds long flight" --recent 90
tuberesearch "ableton live workflow" --max 5 --recent 365
```

## 5. Run it from Claude Code (as a skill)

The skill at `~/.claude-work/skills/tuberesearch/SKILL.md` wraps this CLI. After `.env` is filled, just type any of these inside Claude Code:

```
research best react three fiber tutorials on youtube
tuberesearch claude code workflow tips
find me good talks on systems design from the last year
summarize what youtube is saying about claude agent sdk
```

Claude will pick up the `tuberesearch` skill automatically and run the same CLI under the hood. The skill is a wrapper — there is one source of truth (this repo). Update the Python here, the skill reflects it next run.

## 6. Cost + quota

| Service | What it costs |
|---|---|
| YouTube Data API | Free 10k units/day; one run = ~110 units → **~90 runs/day free** |
| Claude Haiku 4.5 (per-video summary) | ~$0.001 each |
| Claude Sonnet 4.6 (final rank) | ~$0.01 per run |
| **10-video run total** | **~$0.02** |

## 7. What the output looks like

For each winner:

- Rank + title (panel)
- Why it won (one line from Claude Sonnet)
- Channel · views · duration · published date
- Direct video URL
- Per-video brief (gist, key points, tools mentioned, practical value)

Then a "Tools surfaced" table that dedupes tool mentions across videos.

Then a dim "Skip" list of clickbait / off-task / ad-heavy videos.

## 8. Troubleshooting

- **`error: YOUTUBE_API_KEY not set`** — `.env` not sourced. Run `set -a && source .env && set +a` first.
- **HTTP 403 quota exceeded** — you've hit 10k units/day. Wait until midnight Pacific or upgrade billing.
- **No transcripts on most videos** — niche topic with no captions; still ranks on title + description, but quality drops. Try a broader query.
- **Stale results** — add `--recent 90` to bias to recent uploads.
