#!/usr/bin/env python3
"""
Telegram-бот для суммаризации статей с помощью Claude AI.
Отправь ссылку — получи краткую выжимку.
"""

import os
import re
import logging
from dotenv import load_dotenv
import httpx
import anthropic
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Загрузка переменных окружения
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Инициализация клиента Anthropic
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# Промпт для суммаризации (можешь настроить под себя)
SUMMARIZE_PROMPT = """Ты — эксперт по анализу текстов. Создай краткую, информативную выжимку статьи.

Правила:
- Будь конкретен, избегай воды
- Включай главные инсайты и ключевые факты, особенно цифры, даты, имена, статистику
- Цифры и данные обязательно оставлять, без интерпретации, если они есть в тексте
- Отделяй мнения автора отдельно
- НЕ используй Markdown-ссылки вида [текст](url)
- Для выделения используй только эмодзи, НЕ используй ** или __ для выделения

Формат ответа:

📌 Главное:
[Суть статьи в двух предложениях]


🔹 Ключевые тезисы:
• [Тезис 1]

• [Тезис 2]

• [Тезис 3]

• [Тезис 4 — если есть]

• [Тезис 5 — если есть]

• [Тезис 6 — если есть]

• [Тезис 7 — если есть]

💡 Вывод:
[Что можно вынести из статьи, практическая польза]


🎲 Противоречия:
[Выдели из статьи противоречия внутри статьи или противоречия достоверным фактам]


---
Статья для анализа:

{article_text}
"""

def sanitize_markdown(text: str) -> str:
    """Исправляет проблемные Markdown-символы для Telegram."""
    # Убираем незакрытые или проблемные конструкции
    
    # Заменяем ** на просто текст если они не парные
    # Подсчитываем количество **
    if text.count("**") % 2 != 0:
        text = text.replace("**", "")
    
    # То же для одинарных *
    # Считаем * которые не часть **
    temp = text.replace("**", "")
    if temp.count("*") % 2 != 0:
        text = text.replace("*", "")
    
    # Проблемные символы в Markdown
    # Экранируем [ и ] если они не часть ссылки
    # Проще всего — убрать одиночные скобки
    if text.count("[") != text.count("]"):
        text = text.replace("[", "").replace("]", "")
    
    # Убираем ` если непарные
    if text.count("`") % 2 != 0:
        text = text.replace("`", "'")
    
    return text


# Регулярка для проверки URL
URL_PATTERN = re.compile(
    r'^https?://'
    r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'
    r'localhost|'
    r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'
    r'(?::\d+)?'
    r'(?:/?|[/?]\S+)$', re.IGNORECASE
)


async def fetch_article(url: str) -> str:
    """Извлекает текст статьи через Jina Reader."""
    jina_url = f"https://r.jina.ai/{url}"
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                jina_url,
                timeout=60.0,
                headers={"Accept": "text/plain"}
            )
            response.raise_for_status()
            return response.text
        except httpx.TimeoutException:
            raise Exception("Таймаут при загрузке статьи. Попробуй ещё раз.")
        except httpx.HTTPStatusError as e:
            raise Exception(f"Ошибка загрузки: {e.response.status_code}")


def summarize_text(text: str) -> str:
    """Создаёт выжимку текста через Claude API."""
    
    # Ограничиваем длину текста (примерно 12000 токенов ~ 48000 символов)
    max_chars = 48000
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n[Текст обрезан из-за большого размера]"
    
    prompt = SUMMARIZE_PROMPT.format(article_text=text)
    
    response = claude.messages.create(
        model="claude-haiku-4-5-20251001",  # Быстрая и дешёвая модель
        max_tokens=2500,
        messages=[
            {"role": "user", "content": prompt}
        ]
    )
    
    return response.content[0].text


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start."""
    welcome_message = """👋 Привет! Я бот для суммаризации статей.

Как использовать:
Просто отправь мне ссылку на статью — я прочитаю её и сделаю краткую выжимку.

Поддерживаю:
• Новостные сайты
• Блоги (Habr, Medium, Dev.to и др.)
• Документацию
• Большинство веб-страниц

Команды:
/start — это сообщение
/help — помощь

Отправь ссылку, чтобы начать! 🚀"""
    
    await update.message.reply_text(welcome_message)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /help."""
    help_message = """📖 Помощь

Как работает бот:
1. Ты отправляешь ссылку на статью
2. Я извлекаю текст со страницы
3. AI анализирует содержимое
4. Ты получаешь краткую выжимку

Советы:
• Лучше работает с текстовыми статьями
• Может не сработать на сайтах с paywall
• Очень длинные статьи будут обрезаны

Проблемы?
Некоторые сайты блокируют автоматическое чтение. 
Если не получается — попробуй другой источник."""
    
    await update.message.reply_text(help_message)


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик сообщений с URL."""
    message_text = update.message.text.strip()
    
    # Проверяем, что это URL
    if not URL_PATTERN.match(message_text):
        await update.message.reply_text(
            "🤔 Это не похоже на ссылку. Отправь URL статьи, которую нужно суммаризировать."
        )
        return
    
    url = message_text
    user = update.effective_user
    logger.info(f"Запрос от {user.username or user.id}: {url}")
    
    # Отправляем индикатор загрузки
    processing_msg = await update.message.reply_text("⏳ Читаю статью...")
    
    try:
        # Шаг 1: Извлекаем текст
        await processing_msg.edit_text("⏳ Извлекаю текст со страницы...")
        article_text = await fetch_article(url)
        
        if len(article_text.strip()) < 100:
            await processing_msg.edit_text(
                "❌ Не удалось извлечь текст. Возможно, сайт блокирует автоматическое чтение."
            )
            return
        
        # Шаг 2: Суммаризируем
        await processing_msg.edit_text("🤖 Анализирую содержимое...")
        summary = summarize_text(article_text)
        
        # Шаг 3: Отправляем результат (с fallback если Markdown сломан)
        try:
            clean_summary = sanitize_markdown(summary)
            await processing_msg.edit_text(clean_summary, parse_mode="Markdown")
        except Exception as parse_error:
            logger.warning(f"Markdown parse error, отправляю без форматирования: {parse_error}")
            # Убираем все Markdown-символы
            plain_summary = summary.replace("**", "").replace("*", "").replace("`", "").replace("_", "")
            await processing_msg.edit_text(plain_summary)
        
        logger.info(f"Успешно обработан URL: {url}")
        
    except Exception as e:
        error_message = f"❌ Ошибка: {str(e)}"
        logger.error(f"Ошибка при обработке {url}: {e}")
        await processing_msg.edit_text(error_message)


async def handle_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик неизвестных сообщений."""
    await update.message.reply_text(
        "Отправь мне ссылку на статью, и я сделаю выжимку.\n"
        "Например: https://habr.com/ru/articles/..."
    )


def main() -> None:
    """Запуск бота."""
    
    # Проверка наличия ключей
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN не найден в .env")
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY не найден в .env")
    
    # Создание приложения
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Регистрация обработчиков
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    
    # Запуск
    logger.info("Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()