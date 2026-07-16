#!/usr/bin/env python3
"""
Регистрация вебхука Telegram. Обычно НЕ нужен — вебхук уже настроен на
https://wczogedzph.execute-api.eu-central-1.amazonaws.com/webhook
и не меняется от деплоя к деплою (Terraform адрес API Gateway не трогает).

Используй только если пересоздаёшь API Gateway и адрес меняется:
    python scripts/set_webhook.py <webhook_url>
"""

import os
import sys
import httpx
from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)

    webhook_url = sys.argv[1]
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN не найден в .env")

    response = httpx.post(
        f"https://api.telegram.org/bot{token}/setWebhook",
        json={"url": webhook_url},
        timeout=30.0,
    )
    response.raise_for_status()
    print(response.json())


if __name__ == "__main__":
    main()
