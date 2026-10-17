"""
Test setup. lambda_function reads its configuration from the environment at
import time, so the values have to be in place before any test imports it.
"""

import os

os.environ.update(
    TELEGRAM_BOT_TOKEN="test-token",
    ANTHROPIC_API_KEY="test-key",
    TELEGRAM_WEBHOOK_SECRET="test-secret",
    ALLOWED_CHAT_IDS="42, 7",
    # boto3 needs a region to build the Lambda client; nothing calls AWS
    AWS_DEFAULT_REGION="eu-central-1",
)
