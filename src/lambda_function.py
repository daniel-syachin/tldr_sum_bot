"""
Telegram bot for article summarization (Lambda with async processing)

Solving the webhook timeout/retry problem:
1. Webhook receives the message → returns 200 immediately
2. Lambda asynchronously invokes itself to do the actual processing
3. Telegram doesn't retry, because it already got a 200
"""

import hmac
import json
import os
import re
import time
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

# Verifies that webhook requests actually came from Telegram (via the
# X-Telegram-Bot-Api-Secret-Token header) and not from someone who just found
# the public API Gateway URL. Fail-closed: if unset, every webhook request is
# rejected — a misconfigured env var must not silently open the endpoint.
TELEGRAM_WEBHOOK_SECRET = os.environ.get('TELEGRAM_WEBHOOK_SECRET', '')
if not TELEGRAM_WEBHOOK_SECRET:
    logger.error("TELEGRAM_WEBHOOK_SECRET is not set — all webhook requests will be rejected")

# Comma-separated chat IDs allowed to use the bot. Every summary costs real
# money (Claude + web search), so the bot is private by default: if this is
# unset, nobody is allowed, and strangers are told their chat_id so the owner
# can add themselves.
ALLOWED_CHAT_IDS = {
    int(chat_id) for chat_id in os.environ.get('ALLOWED_CHAT_IDS', '').split(',')
    if chat_id.strip()
}
if not ALLOWED_CHAT_IDS:
    logger.warning("ALLOWED_CHAT_IDS is not set — the bot will refuse every chat")

# Time reserved at the end of the async invocation for telling the user that
# something failed — without it, a Lambda timeout kills the process mid-call
# and the status message stays stuck on "Анализирую..." forever.
DEADLINE_MARGIN_SECONDS = 15

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
#
# Prompt structure follows Anthropic long-context guidance: the article goes
# FIRST (wrapped in <article> tags, prepended in summarize()), instructions
# after. The model has a capped web_search tool (see summarize()) — the
# instructions below tell it when to spend those searches (fact-checking,
# not blanket research) and to cite what it found inline rather than
# fabricating or dumping a separate link list.
SUMMARIZE_PROMPT = """Выше — текст статьи, извлечённый автоматически.

Ты — критически мыслящий аналитик. Сделай краткую, но глубокую выжимку этой статьи с фокусом на критический анализ.

КОНТЕКСТ И ОГРАНИЧЕНИЯ:
- У тебя есть инструмент веб-поиска, но с жёстким лимитом на статью. Используй его точечно — чтобы проверить ключевое фактическое утверждение, устаревшую или спорную цифру, или уточнить актуальный контекст по теме. Не ищи по каждому пункту и не трать поиски на то, что и так очевидно из текста статьи.
- Если для какого-то вывода в анализе ты опирался на результат поиска — кратко укажи источник прямо в тексте (например: «по данным Reuters» или домен сайта), не отдельным списком ссылок в конце.
- Если поиск ничего не дал или лимит уже исчерпан — прямо скажи, что утверждение не удалось проверить, вместо того чтобы полагаться на свои (потенциально устаревшие) знания как на факт.
- Дата статьи может быть позже даты твоих знаний — это нормально, полагайся на поиск, а не на собственные устаревшие представления о том, что «сейчас».
- Если текст не похож на статью (страница ошибки, пейволл, навигация сайта, капча) — не анализируй его и не ищи по нему. Вместо выжимки коротко ответь: «Не удалось извлечь текст статьи» и одним предложением поясни, что пришло вместо неё.

ФОРМАТ ОТВЕТА — начни сразу с первой секции, без вступлений вроде «Вот выжимка»:

<b>📌 Суть</b>
[2-3 предложения: о чём статья и главный тезис автора]

<b>🔹 Ключевое</b>
• [тезис, 1-2 предложения]
[3-7 пунктов — столько, сколько в статье реально значимых тезисов]

<b>🔬 Критический анализ</b>
[Обязательная секция. Разбери ТОЛЬКО те пункты из списка ниже, которые реально применимы к статье, — не заполняй пункт ради галочки и не выдумывай проблему там, где её нет:]
• <b>Противоречия внутри статьи</b> — автор противоречит сам себе
• <b>Спорные утверждения</b> — не подкреплено данными или источниками
• <b>Научная обоснованность</b> — утверждения не доказаны или опираются на слабые исследования
• <b>Что упущено</b> — важный контекст, который автор игнорирует
• <b>Конфликт интересов</b> — автор может быть предвзят (и видно почему)
[Если статья выдерживает критику по всем пунктам — так и напиши в 1-2 предложениях, это тоже результат анализа]

<b>⚠️ Красные флаги</b>
[Только если они есть: псевдонаука, манипулятивная риторика, отсутствие источников на ключевые утверждения, корреляция выдаётся за причинность. Если флагов нет — полностью пропусти секцию, включая заголовок]

ПРАВИЛА:
- Отвечай только на русском языке, даже если статья на другом
- Уложись примерно в 2000 символов
- Форматирование — только HTML-теги, которые понимает Telegram: <b>жирный</b>, <i>курсив</i>, <code>код</code>. Никаких других тегов (<ul>, <li>, <p>, <h1> и т.п.) и никакого Markdown (звёздочки, решётки, подчёркивания)
- Не используй символы < и > вне HTML-тегов"""


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

# Server-side web search, capped at 3 queries per article — enough to
# fact-check a couple of key claims without risking a runaway search bill.
WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 3}


def summarize(text: str, deadline: float = None) -> str:
    """
    Generate a summary via Claude, with capped web search for fact-checking.

    Args:
        text: article text
        deadline: time.monotonic() value by which every Claude call must be
            finished. Without it, the SDK defaults (10 min timeout, 2 retries)
            would outlive the 240s Lambda timeout, and Lambda would kill the
            invocation instead of letting it report the failure.
    """
    # Article first, instructions after (long-context best practice).
    # Concatenation instead of str.format() — articles routinely contain
    # literal { } (code snippets, JSON), which would crash .format().
    prompt = f"<article>\n{text}\n</article>\n\n{SUMMARIZE_PROMPT}"
    messages = [{"role": "user", "content": prompt}]

    # The server-side search loop can pause after 10 rounds mid-turn
    # (stop_reason "pause_turn"); re-send the conversation to let it finish.
    # max_uses=3 makes hitting that ceiling unlikely, but this keeps a
    # paused turn from silently truncating the summary.
    for _ in range(3):
        client = ANTHROPIC_CLIENT
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining < 5:
                raise Exception("Не успел проанализировать статью — попробуй ещё раз")
            # No SDK retries under a deadline: a retry after a timed-out
            # attempt would by definition run past the deadline.
            client = ANTHROPIC_CLIENT.with_options(timeout=remaining, max_retries=0)

        try:
            response = client.messages.create(
                model="claude-sonnet-5",
                # claude-sonnet-5 runs adaptive thinking by default, and thinking
                # tokens count against max_tokens. 2000 was barely enough for the
                # answer alone — thinking (plus search results in context) could
                # eat the whole budget and truncate the reply.
                max_tokens=8000,
                tools=[WEB_SEARCH_TOOL],
                messages=messages,
            )
        except anthropic.APITimeoutError:
            raise Exception("Не успел проанализировать статью — попробуй ещё раз")
        if response.stop_reason != "pause_turn":
            break
        messages = [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": response.content},
        ]

    # With web search, a response is several blocks: thinking, server-side
    # search calls/results, and one or more text segments interleaved
    # between searches. Concatenate every text block in order rather than
    # assuming the answer is a single block at a fixed position.
    text_parts = [block.text for block in response.content if block.type == "text"]
    if not text_parts:
        raise Exception("Claude не вернул текстовый блок в ответе")
    return "".join(text_parts)


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


def process_message(chat_id: int, text: str, status_msg_id: int = None,
                    deadline: float = None) -> None:
    """
    Process a message (called asynchronously).

    Args:
        chat_id: chat ID
        text: message text (a URL)
        status_msg_id: ID of the already-sent status message, if any
        deadline: time.monotonic() value by which summarization must finish
            (see summarize); None means no limit (local development)
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
        summary = summarize(article_text, deadline)

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

        deadline = (time.monotonic()
                    + context.get_remaining_time_in_millis() / 1000
                    - DEADLINE_MARGIN_SECONDS)

        if chat_id and text:
            process_message(chat_id, text, status_msg_id, deadline)

        return {"statusCode": 200, "body": "processed"}

    # ─────────────────────────────────────────────────────────────────────
    # Webhook from Telegram (first invocation)
    # ─────────────────────────────────────────────────────────────────────
    if not TELEGRAM_WEBHOOK_SECRET:
        return {"statusCode": 500, "body": "webhook secret not configured"}

    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    received_secret = headers.get("x-telegram-bot-api-secret-token") or ""
    # Constant-time comparison, so response timing doesn't leak how much of
    # a guessed secret was correct.
    if not hmac.compare_digest(received_secret.encode(), TELEGRAM_WEBHOOK_SECRET.encode()):
        logger.warning("Rejected webhook request with a missing/invalid secret token")
        return {"statusCode": 401, "body": "unauthorized"}

    try:
        body = event.get("body", "{}")
        if isinstance(body, str):
            body = json.loads(body)

        message = body.get("message", {})
        chat_id = message.get("chat", {}).get("id")
        text = message.get("text", "").strip()

        # Only IDs, never the raw event: it carries request headers (including
        # the webhook secret and the client IP) and the user's message text.
        logger.info(f"Webhook received: update_id={body.get('update_id')} chat_id={chat_id}")

        if not chat_id or not text:
            return {"statusCode": 200, "body": "no message"}

        if chat_id not in ALLOWED_CHAT_IDS:
            logger.info(f"Rejected message from chat_id={chat_id} (not in ALLOWED_CHAT_IDS)")
            send_message(chat_id, f"🔒 Это приватный бот. Твой chat_id: {chat_id}")
            return {"statusCode": 200, "body": "forbidden"}

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
