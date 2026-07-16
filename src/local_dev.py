#!/usr/bin/env python3
"""
Локальный запуск бота через long polling — для разработки без деплоя на Lambda.

Использует те же функции (fetch_article, summarize, process_message), что и
продовый lambda_function.py — правишь логику один раз, она работает и тут, и там.
Вместо Lambda self-invoke (async_process) сообщение обрабатывается сразу,
в отдельном потоке, чтобы не блокировать цикл polling.
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
    logger.info("Локальный бот запущен (polling)")

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
