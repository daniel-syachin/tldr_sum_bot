#!/usr/bin/env python3
"""
Local bot runner via long polling — for development without deploying to Lambda.

Reuses the same functions (fetch_article, summarize, process_message) as the
production lambda_function.py — fix the logic once, it works in both places.
Instead of Lambda's self-invoke (async_process), messages are processed right
away in a separate thread, so they don't block the polling loop.
"""

import logging
import threading
from dotenv import load_dotenv

load_dotenv()

from lambda_function import process_message, TELEGRAM_API, HTTP_CLIENT  # noqa: E402

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


def get_updates(offset: int) -> list:
    response = HTTP_CLIENT.get(
        f"{TELEGRAM_API}/getUpdates",
        params={"offset": offset, "timeout": 30},
        timeout=40.0,
    )
    response.raise_for_status()
    return response.json().get("result", [])


def main() -> None:
    offset = 0
    logger.info("Local bot started (polling)")

    while True:
        for update in get_updates(offset):
            offset = update["update_id"] + 1

            message = update.get("message", {})
            chat_id = message.get("chat", {}).get("id")
            text = (message.get("text") or "").strip()

            if chat_id and text:
                threading.Thread(
                    target=process_message, args=(chat_id, text), daemon=True
                ).start()


if __name__ == "__main__":
    main()
