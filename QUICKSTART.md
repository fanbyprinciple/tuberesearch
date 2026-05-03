# Quickstart

Two-minute setup. One key required (Anthropic). YouTube needs no key.

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

## 2. Get an Anthropic API key

1. https://console.anthropic.com → API Keys → Create
2. Copy the key

That's it. No YouTube key, no Google account, no OAuth.

## 3. Configure `.env`

```bash
cd ~/codeplay/tuberesearch
cp .env.example .env
# edit .env, fill in:
#   ANTHROPIC_API_KEY=sk-ant-...
```

## 4. Run it (standalone)

```bash
cd ~/codeplay/tuberesearch
source .venv/bin/activate
set -a && source .env && set +a
tuberesearch "claude code workflow tips"
```

Common flags:

| Flag | Effect |
|---|---|
| `--max N` | how many videos to fetch (default 10) |
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

## 6. Cost + limits

| Service | What it costs |
|---|---|
| YouTube search | Free (no API, scrapes public results page) |
| Transcripts | Free (`youtube-transcript-api`, no auth) |
| Claude Haiku 4.5 (per-video summary) | ~$0.001 each |
| Claude Sonnet 4.6 (final rank) | ~$0.01 per run |
| **10-video run total** | **~$0.02** |

No daily quota beyond Anthropic's account-level rate limit. Run it as often as you like.

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

- **`error: ANTHROPIC_API_KEY not set`** — `.env` not sourced. Run `set -a && source .env && set +a` first.
- **`Could not locate ytInitialData`** — YouTube changed their HTML. Open an issue. Workaround: temporarily switch to browser automation (`browser-use` + Playwright); the rest of the pipeline doesn't change.
- **No transcripts on most videos** — niche topic with no captions; still ranks on title + description, but quality drops. Try a broader query.
- **Stale results** — add `--recent 90` to bias to recent uploads.
- **Anthropic rate-limit** — your account-level cap; wait a minute and retry.
