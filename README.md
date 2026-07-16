# TL;DR Summarizer Bot

Telegram-бот на Claude (Anthropic), делающий критическую выжимку статьи по ссылке.

## Архитектура (как есть в проде)

```
Telegram → API Gateway (HTTP API, POST /webhook) → Lambda telegram-summarizer-bot
```

- Lambda сама себя асинхронно вызывает (`boto3` self-invoke), чтобы уложиться в таймаут
  ответа Telegram на вебхук, а реальную обработку (fetch + Claude) делать без спешки.
- Токены (`TELEGRAM_BOT_TOKEN`, `ANTHROPIC_API_KEY`) заданы как переменные окружения
  прямо в конфиге Lambda — руками через консоль. CI/CD их не читает и не трогает.
- API Gateway и IAM-роль исполнения уже настроены и работают — Terraform ими не управляет
  (сознательно, чтобы CI-роли не нужны были лишние права и не было риска что-то сломать).
- Зависимости (`httpx`, `anthropic`) едут отдельным **Lambda Layer**
  (`telegram-summarizer-bot-layer`), а не внутри zip функции — поэтому в
  деплое из CI собирается zip из одного файла `lambda_function.py`, без pip install.
  Список пакетов в layer — справочно в `requirements-lambda.txt`.

## Структура

```
src/
  lambda_function.py  # прод-код — то, что реально крутится на Lambda
  local_dev.py         # локальный запуск через getUpdates-polling для разработки
infra/                 # Terraform: обновляет только код и конфиг самой Lambda
.github/workflows/     # CI/CD: сборка zip + terraform apply при пуше в main
requirements.txt        # полный набор для локальной разработки
requirements-lambda.txt # только то, что реально едет в zip на Lambda
```

## Локальная разработка

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env   # вписать реальные токены
python src/local_dev.py
```

Правь `src/lambda_function.py` — это и есть боевой код, никакого дублирования логики.

## Деплой (CI/CD)

`git push` в `main` → GitHub Actions:
1. Ставит `requirements-lambda.txt` под платформу Lambda (`manylinux2014_x86_64`, Python 3.14).
2. Кладёт `lambda_function.py` в собранный `build/`.
3. Логинится в AWS по OIDC (без ключей) под ролью `github-actions-tgbot`.
4. `terraform apply` — обновляет код и конфиг функции `telegram-summarizer-bot`.

Один пуш — функция обновлена. Первым делом после клонирования репозитория на новой
машине нужно один раз сделать `terraform import` (см. историю переписки/PR) — сама
Lambda, API Gateway и роль уже существуют в AWS и не создаются заново.
