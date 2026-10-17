"""
Unit tests for lambda_function.py. Every external call (Telegram, Jina,
Claude, Lambda self-invoke) is mocked — these run offline and cost nothing.
"""

import json
import time
from unittest import mock

import anthropic
import httpx
import pytest

import lambda_function as lf

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def url_entity(text: str, url: str) -> dict:
    """A Telegram `url` entity for `url` inside `text` (offsets in UTF-16 units)."""
    offset = len(text[: text.index(url)].encode("utf-16-le")) // 2
    return {"type": "url", "offset": offset, "length": len(url.encode("utf-16-le")) // 2}


def webhook_event(chat_id: int, text: str, secret: str | None = "test-secret", **message) -> dict:
    headers = {"X-Telegram-Bot-Api-Secret-Token": secret} if secret is not None else {}
    body = {"update_id": 1, "message": {"chat": {"id": chat_id}, "text": text, **message}}
    return {"headers": headers, "body": json.dumps(body)}


def claude_response(stop_reason: str, *texts: str) -> mock.Mock:
    blocks = [mock.Mock(type="text", text=t) for t in texts]
    return mock.Mock(stop_reason=stop_reason, content=blocks, stop_details=None)


@pytest.fixture
def context():
    ctx = mock.Mock(function_name="telegram-summarizer-bot")
    ctx.get_remaining_time_in_millis.return_value = 240_000
    return ctx


@pytest.fixture
def telegram():
    """Mocked send_message (returns a status message id) and Lambda client."""
    with mock.patch.object(lf, "send_message", return_value={"result": {"message_id": 5}}) as send, \
         mock.patch.object(lf, "LAMBDA_CLIENT") as lambda_client:
        yield send, lambda_client


@pytest.fixture
def claude():
    with mock.patch.object(lf, "ANTHROPIC_CLIENT") as client:
        # with_options() is used under a deadline; route it to the same mock
        client.with_options.return_value = client
        yield client


# ─────────────────────────────────────────────────────────────────────────────
# Webhook auth and allowlist
# ─────────────────────────────────────────────────────────────────────────────


def test_allowlist_parses_comma_separated_ids():
    assert lf.ALLOWED_CHAT_IDS == {42, 7}


@pytest.mark.parametrize("secret", [None, "wrong", "ünïcode"])
def test_webhook_rejects_bad_secret(secret, context, telegram):
    _, lambda_client = telegram
    response = lf.lambda_handler(webhook_event(42, "https://example.com", secret=secret), context)
    assert response["statusCode"] == 401
    assert not lambda_client.invoke.called


def test_webhook_fails_closed_without_configured_secret(context, telegram):
    with mock.patch.object(lf, "TELEGRAM_WEBHOOK_SECRET", ""):
        response = lf.lambda_handler(webhook_event(42, "https://example.com"), context)
    assert response["statusCode"] == 500


def test_unknown_chat_is_refused_with_its_chat_id(context, telegram):
    send, lambda_client = telegram
    response = lf.lambda_handler(webhook_event(999, "https://example.com"), context)
    assert response["body"] == "forbidden"
    assert "999" in send.call_args.args[1]
    assert not lambda_client.invoke.called


def test_link_inside_text_is_sent_to_async_invocation(context, telegram):
    _, lambda_client = telegram
    text = "глянь 👉 https://example.com/post"
    event = webhook_event(42, text, entities=[url_entity(text, "https://example.com/post")])
    lf.lambda_handler(event, context)
    payload = json.loads(lambda_client.invoke.call_args.kwargs["Payload"])
    assert payload["text"] == "https://example.com/post"


def test_message_without_link_gets_a_hint(context, telegram):
    send, lambda_client = telegram
    lf.lambda_handler(webhook_event(42, "привет"), context)
    assert "ссылку" in send.call_args.args[1]
    assert not lambda_client.invoke.called


def test_async_invocation_passes_deadline_inside_lambda_timeout(context):
    with mock.patch.object(lf, "process_message") as process:
        lf.lambda_handler({"async_process": True, "chat_id": 1, "text": "https://example.com"}, context)
    deadline = process.call_args.args[3]
    expected = 240 - lf.DEADLINE_MARGIN_SECONDS
    assert expected - 5 < deadline - time.monotonic() <= expected


# ─────────────────────────────────────────────────────────────────────────────
# Link extraction
# ─────────────────────────────────────────────────────────────────────────────


def test_extract_url_handles_utf16_offsets_after_emoji():
    text = "🔥🔥 глянь https://example.com/a?b=1 круто"
    message = {"text": text, "entities": [url_entity(text, "https://example.com/a?b=1")]}
    assert lf.extract_url(message) == "https://example.com/a?b=1"


def test_extract_url_reads_text_link():
    message = {"text": "статья тут", "entities": [
        {"type": "text_link", "offset": 7, "length": 3, "url": "https://x.org/p"},
    ]}
    assert lf.extract_url(message) == "https://x.org/p"


def test_extract_url_reads_forwarded_caption_and_adds_scheme():
    text = "📰 site.photography/post"
    message = {"caption": text, "caption_entities": [url_entity(text, "site.photography/post")]}
    assert lf.extract_url(message) == "https://site.photography/post"


@pytest.mark.parametrize("text", [
    "https://news.technology/x",
    "https://xn--80aswg.xn--p1ai/a",
])
def test_extract_url_accepts_bare_url_with_long_or_punycode_tld(text):
    assert lf.extract_url({"text": text}) == text


@pytest.mark.parametrize("message", [
    {"text": "привет"},
    {"text": "hi", "entities": [{"type": "text_link", "offset": 0, "length": 2, "url": "tg://user?id=1"}]},
])
def test_extract_url_returns_none_without_web_link(message):
    assert lf.extract_url(message) is None


# ─────────────────────────────────────────────────────────────────────────────
# Message splitting
# ─────────────────────────────────────────────────────────────────────────────


def test_split_message_keeps_short_text_whole():
    assert lf.split_message("short") == ["short"]


def test_split_message_never_cuts_through_html_tags():
    text = "\n\n".join(f"<b>Секция {i}</b>\n• " + "слово " * 120 for i in range(20))
    chunks = lf.split_message(text)
    assert len(chunks) > 1
    assert all(len(chunk) <= lf.TELEGRAM_CHUNK_LIMIT for chunk in chunks)
    assert all(chunk.count("<b>") == chunk.count("</b>") for chunk in chunks)
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")


def test_split_message_hard_cuts_a_single_oversized_line():
    assert [len(c) for c in lf.split_message("x" * 9000)] == [4000, 4000, 1000]


# ─────────────────────────────────────────────────────────────────────────────
# Summarization
# ─────────────────────────────────────────────────────────────────────────────


def test_summarize_keeps_text_from_before_pause_turn(claude):
    claude.messages.create.side_effect = [claude_response("pause_turn", "A"), claude_response("end_turn", "B")]
    assert lf.summarize("article") == "AB"
    continuation = claude.messages.create.call_args_list[1].kwargs["messages"]
    assert continuation[1]["role"] == "assistant"
    assert [block.text for block in continuation[1]["content"]] == ["A"]


def test_summarize_flags_max_tokens_truncation(claude):
    claude.messages.create.side_effect = [claude_response("max_tokens", "cut")]
    assert "обрезан по лимиту" in lf.summarize("article")


def test_summarize_turns_refusal_into_user_facing_error(claude):
    claude.messages.create.side_effect = [claude_response("refusal")]
    with pytest.raises(lf.UserFacingError, match="отказался"):
        lf.summarize("article")


def test_summarize_caps_long_articles_and_says_so(claude):
    claude.messages.create.side_effect = [claude_response("end_turn", "S")]
    summary = lf.summarize("z" * (lf.MAX_ARTICLE_CHARS + 50_000))
    prompt = claude.messages.create.call_args.kwargs["messages"][0]["content"]
    assert len(prompt) < lf.MAX_ARTICLE_CHARS + 10_000
    assert "обрезан" in prompt
    assert "первая часть" in summary


def test_summarize_uses_deadline_without_sdk_retries(claude):
    claude.messages.create.side_effect = [claude_response("end_turn", "ok")]
    lf.summarize("article", time.monotonic() + 100)
    options = claude.with_options.call_args.kwargs
    assert options["max_retries"] == 0
    assert 95 < options["timeout"] <= 100


def test_summarize_refuses_to_start_past_deadline(claude):
    with pytest.raises(lf.UserFacingError, match="Не успел"):
        lf.summarize("article", time.monotonic() + 1)
    assert not claude.messages.create.called


REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


@pytest.mark.parametrize(("error", "message"), [
    (anthropic.APITimeoutError(request=REQUEST), "Не успел"),
    (anthropic.APIConnectionError(request=REQUEST), "связаться"),
    (anthropic.RateLimitError("rl", response=httpx.Response(429, request=REQUEST), body=None), "Слишком много"),
    (anthropic.APIStatusError("overloaded", response=httpx.Response(529, request=REQUEST), body=None), "перегружен"),
])
def test_summarize_maps_sdk_errors_to_readable_messages(claude, error, message):
    claude.messages.create.side_effect = [error]
    with pytest.raises(lf.UserFacingError, match=message):
        lf.summarize("article")


def test_summarize_does_not_hide_client_errors(claude):
    error = anthropic.BadRequestError("bad", response=httpx.Response(400, request=REQUEST), body=None)
    claude.messages.create.side_effect = [error]
    with pytest.raises(anthropic.BadRequestError):
        lf.summarize("article")


# ─────────────────────────────────────────────────────────────────────────────
# process_message
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def pipeline():
    with mock.patch.object(lf, "fetch_article", return_value="t" * 500), \
         mock.patch.object(lf, "edit_message") as edit, \
         mock.patch.object(lf, "send_message") as send:
        yield edit, send


def test_unexpected_error_shows_generic_message(pipeline):
    edit, _ = pipeline
    with mock.patch.object(lf, "summarize", side_effect=KeyError("internal detail")):
        lf.process_message(1, "https://example.com/a", status_msg_id=9)
    shown = edit.call_args.args[2]
    assert shown.startswith("❌ Что-то пошло не так")
    assert "internal detail" not in shown


def test_user_facing_error_is_shown_as_is(pipeline):
    edit, _ = pipeline
    with mock.patch.object(lf, "summarize", side_effect=lf.UserFacingError("Понятная ошибка")):
        lf.process_message(1, "https://example.com/a", status_msg_id=9)
    assert edit.call_args.args[2] == "❌ Понятная ошибка"


def test_long_summary_edits_status_then_sends_rest(pipeline):
    edit, send = pipeline
    summary = "\n\n".join("абзац " * 100 for _ in range(20))
    chunks = lf.split_message(summary)
    with mock.patch.object(lf, "summarize", return_value=summary):
        lf.process_message(1, "https://example.com/a", status_msg_id=9)
    assert edit.call_args.args[2] == chunks[0]
    assert send.call_count == len(chunks) - 1
