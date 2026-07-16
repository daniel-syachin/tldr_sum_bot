#!/usr/bin/env python3
"""
Registers the Telegram webhook. Usually NOT needed — the webhook is already
set to https://wczogedzph.execute-api.eu-central-1.amazonaws.com/webhook
and doesn't change between deploys (Terraform doesn't touch the API Gateway URL).

Only use this if you recreate API Gateway (URL changes) or rotate the webhook
secret token:
    python scripts/set_webhook.py <webhook_url> [secret_token]

If given, secret_token must match the Lambda's TELEGRAM_WEBHOOK_SECRET
environment variable — Telegram echoes it back on every webhook call via the
X-Telegram-Bot-Api-Secret-Token header, which lambda_function.py verifies.
"""

import os
import sys
import httpx
from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    if len(sys.argv) not in (2, 3):
        print(__doc__)
        sys.exit(1)

    webhook_url = sys.argv[1]
    secret_token = sys.argv[2] if len(sys.argv) == 3 else None

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN not found in .env")

    payload = {"url": webhook_url}
    if secret_token:
        payload["secret_token"] = secret_token

    response = httpx.post(
        f"https://api.telegram.org/bot{token}/setWebhook",
        json=payload,
        timeout=30.0,
    )
    response.raise_for_status()
    print(response.json())


if __name__ == "__main__":
    main()
