"""Telegram bot wrapper for tuberesearch-pure.

Usage:
    export TELEGRAM_BOT_TOKEN=123456:ABC...
    tuberesearch-telegram

The bot:
  - listens for /research <query> or plain text messages
  - runs the pure-Python heuristic pipeline (no LLM, no API key)
  - replies with the markdown summary

Setup:
  1. Talk to @BotFather on Telegram → /newbot → choose name → get token
  2. Put token in TELEGRAM_BOT_TOKEN env var (or .env)
  3. Run `tuberesearch-telegram` (this script). Polls forever.
  4. Open a chat with your bot. Send: research best vegan wraps under 30 minutes
"""
from __future__ import annotations

import os
import sys
import time
import traceback

import requests
from dotenv import load_dotenv

from .pure import render_markdown, run_pure


API_BASE = "https://api.telegram.org/bot{token}"
POLL_TIMEOUT_S = 25
MESSAGE_HARD_LIMIT = 4000  # Telegram caps at ~4096 chars per message; keep margin


def _post(token: str, method: str, **payload):
    url = f"{API_BASE.format(token=token)}/{method}"
    r = requests.post(url, json=payload, timeout=POLL_TIMEOUT_S + 5)
    r.raise_for_status()
    return r.json()


def _get(token: str, method: str, **params):
    url = f"{API_BASE.format(token=token)}/{method}"
    r = requests.get(url, params=params, timeout=POLL_TIMEOUT_S + 5)
    r.raise_for_status()
    return r.json()


def send_message(token: str, chat_id: int, text: str, *, parse_mode: str | None = "Markdown") -> None:
    """Send text. Splits long messages into <4000-char chunks at paragraph boundaries."""
    chunks = _chunk(text, MESSAGE_HARD_LIMIT)
    for i, chunk in enumerate(chunks):
        try:
            _post(token, "sendMessage", chat_id=chat_id, text=chunk, parse_mode=parse_mode,
                  disable_web_page_preview=False)
        except requests.HTTPError:
            # Telegram parse_mode is strict — retry plain text if markdown fails
            _post(token, "sendMessage", chat_id=chat_id, text=chunk)
        if i + 1 < len(chunks):
            time.sleep(0.4)  # gentle


def _chunk(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    out: list[str] = []
    paragraphs = text.split("\n\n")
    buf = ""
    for p in paragraphs:
        candidate = f"{buf}\n\n{p}".strip() if buf else p
        if len(candidate) <= limit:
            buf = candidate
        else:
            if buf:
                out.append(buf)
            if len(p) <= limit:
                buf = p
            else:
                # paragraph itself is too long — hard split at limit
                while len(p) > limit:
                    out.append(p[:limit])
                    p = p[limit:]
                buf = p
    if buf:
        out.append(buf)
    return out


def parse_query(text: str) -> str | None:
    """Strip /research, /start, /help prefixes. Empty result = ignore."""
    if not text:
        return None
    s = text.strip()
    lowered = s.lower()
    for prefix in ("/research", "/r", "/tube", "/tuberesearch"):
        if lowered.startswith(prefix):
            s = s[len(prefix):].strip()
            return s or None
    if lowered in ("/start", "/help"):
        return ""  # marker for help reply
    if s.startswith("/"):
        return None  # unknown command
    return s


HELP_TEXT = (
    "*tuberesearch bot*\n\n"
    "Send a research topic, I'll scan YouTube, fetch transcripts, rank top videos, "
    "and surface tools/sites mentioned across them. Pure heuristic — no LLM, no API tokens.\n\n"
    "Examples:\n"
    "• best vegan wraps under 30 minutes\n"
    "• /research claude code workflow tips\n"
    "• free seedance 2.0 access sites\n\n"
    "Flags inside text: append `--max 8` or `--recent 30` to control fetch."
)


def handle_message(token: str, message: dict) -> None:
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "")
    if not chat_id:
        return

    query = parse_query(text)
    if query is None:
        return  # silent on unknown command
    if query == "":
        send_message(token, chat_id, HELP_TEXT)
        return

    # parse simple flags inline
    max_results = 10
    recent_days: int | None = None
    cleaned = []
    for token_word in query.split():
        if token_word.startswith("--max="):
            try:
                max_results = max(1, min(15, int(token_word.split("=", 1)[1])))
            except ValueError:
                pass
        elif token_word.startswith("--recent="):
            try:
                recent_days = max(1, int(token_word.split("=", 1)[1]))
            except ValueError:
                pass
        else:
            cleaned.append(token_word)
    final_task = " ".join(cleaned).strip() or query

    send_message(token, chat_id, f"_searching YouTube for:_ {final_task}")

    try:
        ranked, tools = run_pure(
            final_task,
            max_results=max_results,
            recent_days=recent_days,
            workers=4,
            top_n=5,
            transcript_chars=8000,
        )
    except Exception:
        send_message(token, chat_id, f"failed: {traceback.format_exc(limit=2)}")
        return

    if not ranked:
        send_message(token, chat_id, f"No videos found for: {final_task}")
        return

    out = render_markdown(final_task, ranked, tools)
    send_message(token, chat_id, out)


def run_bot(token: str) -> int:
    print(f"[tuberesearch-telegram] polling Telegram (token={token[:6]}...). Ctrl-C to stop.")
    last_update_id = 0
    while True:
        try:
            resp = _get(
                token,
                "getUpdates",
                offset=last_update_id + 1 if last_update_id else None,
                timeout=POLL_TIMEOUT_S,
            )
        except requests.RequestException as e:
            print(f"[warn] poll failed: {e}; retrying in 5s")
            time.sleep(5)
            continue

        for update in resp.get("result", []):
            last_update_id = update.get("update_id", last_update_id)
            msg = update.get("message") or update.get("channel_post")
            if not msg:
                continue
            try:
                handle_message(token, msg)
            except Exception:
                traceback.print_exc()


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print(
            "error: TELEGRAM_BOT_TOKEN not set.\n"
            "Get one from @BotFather on Telegram, then add to .env or env vars.",
            file=sys.stderr,
        )
        return 2
    return run_bot(token)


if __name__ == "__main__":
    sys.exit(main())
