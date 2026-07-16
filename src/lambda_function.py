"""
Telegram bot for article summarization (Lambda with async processing)

Solving the webhook timeout/retry problem:
1. Webhook receives the message → returns 200 immediately
2. Lambda asynchronously invokes itself to do the actual processing
3. Telegram doesn't retry, because it already got a 200
"""

import json
import os
import re
import logging
import boto3
import httpx
import anthropic

# ═══════════════════════════════════════════════════════════════════════════
# SETUP
# ═══════════════════════════════════════════════════════════════════════════

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# httpx logs the full request URL at INFO level, and the bot token is
# embedded directly in that URL (api.telegram.org/bot<TOKEN>/...) — without
# this, it was leaking in plaintext into CloudWatch Logs on every message.
logging.getLogger("httpx").setLevel(logging.WARNING)

ANTHROPIC_CLIENT = anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY', ''))
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# Lambda client used for async self-invocation
LAMBDA_CLIENT = boto3.client('lambda')

HTTP_CLIENT = httpx.Client(
    timeout=httpx.Timeout(90.0, connect=10.0),  # generous timeout for slow article pages
    limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
    follow_redirects=True,
    headers={
        "User-Agent": "Mozilla/5.0 (compatible; SummarizerBot/1.0)",
        "Accept": "text/plain,text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
    }
)

# ═══════════════════════════════════════════════════════════════════════════
# PROMPT
# ═══════════════════════════════════════════════════════════════════════════

# The bot serves Russian-speaking users, so the prompt (and every user-facing
# string in this file) is intentionally in Russian — that's a product choice,
# not something to translate.
SUMMARIZE_PROMPT = """
Ты — критически мыслящий аналитик.
Твоя задача — сделать краткую, но глубокую выжимку статьи с фокусом на критический анализ.
Узнай актуальную дату и поищи самостоятельно информацию о теме статьи.
Дата статьи может тебе казаться из будущего, это не так. Твои знания даты не актуальны.

ФОРМАТ ОТВЕТА (используй HTML-теги для форматирования):

<b>📌 Суть</b>
[2-3 предложения: о чём статья и главный тезис автора]

<b>🔹 Ключевое</b>
• [тезис 1]
• [тезис 2]
• [тезис 3]
• [тезис 4]
• [тезис 5]
[максимум 10 пунктов, каждый — 1-2 предложение]

<b>🔬 Критический анализ</b>
[Этот раздел ОБЯЗАТЕЛЕН. Проанализируй:]
• <b>Противоречия внутри статьи</b> — если автор сам себе противоречит
• <b>Спорные утверждения</b> — что не подкреплено данными или источниками
• <b>Научная обоснованность</b> — если claims не доказаны или основаны на слабых исследованиях, укажи это
• <b>Что упущено</b> — важный контекст, который автор игнорирует
• <b>Конфликт интересов</b> — если автор может быть предвзят

<b>⚠️ Красные флаги</b>
[Если есть что-то из этого — укажи:]
• Псевдонаука или недоказанные утверждения
• Манипулятивная риторика
• Отсутствие источников на ключевые claims
• Корреляция выдаётся за причинность
[Если красных флагов нет — пропусти этот раздел]

ПРАВИЛА:
- Всегда отвечай на русском языке, даже если статья на другом языке
- Будь краток — вся выжимка должна уместиться в ~2000 символов
- Используй HTML-теги: <b>жирный</b>, <i>курсив</i>, <code>код</code>
- Не используй Markdown (звёздочки, подчёркивания)
- Будь честен и критичен
- Всегда отвечай на русском языке

СТАТЬЯ:

{article_text}"""


# ═══════════════════════════════════════════════════════════════════════════
# ARTICLE FETCHING
# ═══════════════════════════════════════════════════════════════════════════

def fetch_article(url: str) -> str:
    """Fetch article text via Jina Reader."""
    jina_url = f"https://r.jina.ai/{url}"

    try:
        response = HTTP_CLIENT.get(
            jina_url,
            headers={"Accept": "text/plain"},
            timeout=60.0
        )
        response.raise_for_status()
        return response.text

    except httpx.TimeoutException:
        raise Exception("Таймаут при загрузке статьи")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise Exception("Страница не найдена (404)")
        elif e.response.status_code == 403:
            raise Exception("Доступ запрещён (403)")
        else:
            raise Exception(f"Ошибка HTTP: {e.response.status_code}")
    except Exception as e:
        raise Exception(f"Не удалось загрузить: {str(e)}")


# ═══════════════════════════════════════════════════════════════════════════
# SUMMARIZATION
# ═══════════════════════════════════════════════════════════════════════════

def summarize(text: str) -> str:
    """Generate a summary via Claude."""
    prompt = SUMMARIZE_PROMPT.format(article_text=text)

    response = ANTHROPIC_CLIENT.messages.create(
        model="claude-sonnet-5",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )

    return response.content[0].text


# ═══════════════════════════════════════════════════════════════════════════
# TELEGRAM API
# ═══════════════════════════════════════════════════════════════════════════

def _strip_html(text: str) -> str:
    """Crudely strip HTML tags — fallback for when Telegram can't parse the markup."""
    return re.sub(r"</?[a-zA-Z][^>]*>", "", text)


def send_message(chat_id: int, text: str, parse_mode: str = None) -> dict:
    """Send a Telegram message. Retries without markup if parsing fails."""
    payload = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode

    try:
        response = HTTP_CLIENT.post(
            f"{TELEGRAM_API}/sendMessage",
            json=payload,
            timeout=15.0
        )
        result = response.json()
        if not result.get("ok"):
            logger.error(f"Telegram sendMessage failed: {result}")
            if parse_mode:
                payload = {"chat_id": chat_id, "text": _strip_html(text)}
                response = HTTP_CLIENT.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=15.0)
                result = response.json()
                if not result.get("ok"):
                    logger.error(f"Telegram sendMessage retry (no parse_mode) also failed: {result}")
        return result
    except Exception as e:
        logger.error(f"Telegram send error: {e}")
        return {"ok": False}


def edit_message(chat_id: int, message_id: int, text: str, parse_mode: str = None) -> dict:
    """Edit a Telegram message. Retries without markup if parsing fails."""
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

    try:
        response = HTTP_CLIENT.post(
            f"{TELEGRAM_API}/editMessageText",
            json=payload,
            timeout=15.0
        )
        result = response.json()
        if not result.get("ok"):
            logger.error(f"Telegram editMessageText failed: {result}")
            if parse_mode:
                payload = {"chat_id": chat_id, "message_id": message_id, "text": _strip_html(text)}
                response = HTTP_CLIENT.post(f"{TELEGRAM_API}/editMessageText", json=payload, timeout=15.0)
                result = response.json()
                if not result.get("ok"):
                    logger.error(f"Telegram editMessageText retry (no parse_mode) also failed: {result}")
        return result
    except Exception as e:
        logger.error(f"Telegram edit error: {e}")
        return {"ok": False}


# ═══════════════════════════════════════════════════════════════════════════
# MESSAGE HANDLING
# ═══════════════════════════════════════════════════════════════════════════

URL_PATTERN = re.compile(
    r'^https?://'
    r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'
    r'localhost|'
    r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'
    r'(?::\d+)?'
    r'(?:/?|[/?]\S+)$', re.IGNORECASE
)

WELCOME_MESSAGE = """👋 Привет! Я создаю критические выжимки из статей.

<b>Как использовать:</b>
Отправь ссылку → получи анализ с проверкой фактов.

<b>Что я делаю:</b>
• Извлекаю суть и ключевые тезисы
• Нахожу противоречия и спорные утверждения
• Оцениваю научную обоснованность
• Выявляю красные флаги

Отправь ссылку! 🔍"""


def process_message(chat_id: int, text: str, status_msg_id: int = None) -> None:
    """
    Process a message (called asynchronously).

    Args:
        chat_id: chat ID
        text: message text (a URL)
        status_msg_id: ID of the already-sent status message, if any
    """

    # Commands
    if text == "/start" or text == "/help":
        send_message(chat_id, WELCOME_MESSAGE, parse_mode="HTML")
        return

    # Must be a URL
    if not URL_PATTERN.match(text):
        send_message(chat_id, "🔗 Отправь ссылку на статью для анализа")
        return

    url = text
    logger.info(f"Processing: {url}")

    # Send a status message if one hasn't been sent yet
    if not status_msg_id:
        status = send_message(chat_id, "📖 Загружаю...")
        status_msg_id = status.get("result", {}).get("message_id")

    try:
        # Fetch the article
        article_text = fetch_article(url)

        if len(article_text.strip()) < 200:
            if status_msg_id:
                edit_message(chat_id, status_msg_id,
                    "❌ Не удалось извлечь текст. Сайт может блокировать автоматическое чтение.")
            return

        # Update status
        if status_msg_id:
            edit_message(chat_id, status_msg_id, "🔬 Анализирую...")

        # Summarize
        summary = summarize(article_text)

        # Send the result
        if len(summary) > 4000:
            if status_msg_id:
                edit_message(chat_id, status_msg_id, summary[:4000], parse_mode="HTML")
            send_message(chat_id, summary[4000:], parse_mode="HTML")
        else:
            if status_msg_id:
                edit_message(chat_id, status_msg_id, summary, parse_mode="HTML")
            else:
                send_message(chat_id, summary, parse_mode="HTML")

        logger.info(f"OK: {url[:50]}...")

    except Exception as e:
        logger.error(f"Error: {url} - {e}")
        if status_msg_id:
            edit_message(chat_id, status_msg_id, f"❌ {str(e)}")
        else:
            send_message(chat_id, f"❌ {str(e)}")


# ═══════════════════════════════════════════════════════════════════════════
# LAMBDA HANDLER
# ═══════════════════════════════════════════════════════════════════════════

def lambda_handler(event, context):
    """
    Lambda entry point with async self-invocation.

    How it works:
    1. Telegram sends a webhook → Lambda receives the event
    2. Lambda immediately returns 200 (Telegram is happy, won't retry)
    3. Lambda asynchronously invokes itself with async_process=True
    4. The second invocation does the actual work (no deadline from Telegram)
    """

    # ─────────────────────────────────────────────────────────────────────
    # Warming ping from EventBridge
    # ─────────────────────────────────────────────────────────────────────
    if event.get("source") == "aws.events" or event.get("warming"):
        logger.info("Warming ping")
        return {"statusCode": 200, "body": "warm"}

    # ─────────────────────────────────────────────────────────────────────
    # Async processing (second invocation)
    # ─────────────────────────────────────────────────────────────────────
    if event.get("async_process"):
        logger.info("Async processing started")

        chat_id = event.get("chat_id")
        text = event.get("text")
        status_msg_id = event.get("status_msg_id")

        if chat_id and text:
            process_message(chat_id, text, status_msg_id)

        return {"statusCode": 200, "body": "processed"}

    # ─────────────────────────────────────────────────────────────────────
    # Webhook from Telegram (first invocation)
    # ─────────────────────────────────────────────────────────────────────
    logger.info(f"Webhook received: {json.dumps(event)[:300]}")

    try:
        body = event.get("body", "{}")
        if isinstance(body, str):
            body = json.loads(body)

        message = body.get("message", {})
        chat_id = message.get("chat", {}).get("id")
        text = message.get("text", "").strip()

        if not chat_id or not text:
            return {"statusCode": 200, "body": "no message"}

        # Fast commands — handle immediately
        if text in ("/start", "/help"):
            send_message(chat_id, WELCOME_MESSAGE, parse_mode="HTML")
            return {"statusCode": 200, "body": "ok"}

        # Must be a URL
        if not URL_PATTERN.match(text):
            send_message(chat_id, "🔗 Отправь ссылку на статью для анализа")
            return {"statusCode": 200, "body": "ok"}

        # Send status right away
        status = send_message(chat_id, "📖 Загружаю...")
        status_msg_id = status.get("result", {}).get("message_id")

        # ─────────────────────────────────────────────────────────────────
        # THE KEY BIT: invoke ourselves asynchronously
        # ─────────────────────────────────────────────────────────────────
        LAMBDA_CLIENT.invoke(
            FunctionName=context.function_name,
            InvocationType='Event',  # 'Event' = asynchronous invocation
            Payload=json.dumps({
                "async_process": True,
                "chat_id": chat_id,
                "text": text,
                "status_msg_id": status_msg_id
            })
        )

        logger.info(f"Async invocation triggered for: {text[:50]}")

        # Return 200 RIGHT AWAY — Telegram won't retry
        return {"statusCode": 200, "body": json.dumps({"ok": True})}

    except Exception as e:
        logger.error(f"Handler error: {e}")
        return {"statusCode": 200, "body": json.dumps({"ok": False})}
