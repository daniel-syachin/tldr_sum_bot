# Changelog

All notable changes to this project are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

_Add new entries above this line as you make changes — see the "How to use this changelog" note at the bottom._

## 2026-07-16 — Fix crash on Claude Sonnet 5 responses

### Fixed
- **Every summary was failing** (`'ThinkingBlock' object has no attribute
  'text'`) after the `claude-sonnet-5` model bump. `summarize()` assumed
  `response.content[0]` was always the answer text, but Sonnet 5 can place a
  `ThinkingBlock` (its reasoning) before the actual `TextBlock`. Now picks the
  block by `type == "text"` instead of by fixed position.

## 2026-07-16 — Add MIT license

### Added
- `LICENSE` (MIT) — permissive license fitting a public portfolio project;
  linked from the README.

## 2026-07-16 — Webhook authentication

### Added
- The webhook now verifies every request against a secret token
  (`X-Telegram-Bot-Api-Secret-Token`), rejecting anything that doesn't match
  with a `401`. The API Gateway URL was never a credential, but without this
  check, anyone who found it could POST fake "messages" straight to the
  Lambda and run up the Anthropic bill on someone else's behalf.
- `scripts/set_webhook.py` now accepts an optional `secret_token` argument
  for re-registering the webhook after rotating the secret.

## 2026-07-16 — English docs and CLAUDE.md

### Changed
- Translated all code comments and docstrings to English across
  `src/`, `infra/`, and `scripts/` (public repo, portfolio piece). The bot's
  actual Russian-language content — the prompt, the welcome message, every
  string sent to Telegram users — is untouched; that's a product decision,
  not documentation.

### Added
- `CLAUDE.md`, so a fresh assistant session (or a new contributor) has the
  real AWS resource IDs, the file map, the reasoning behind Terraform's
  narrow scope, and the gotchas already solved, without rediscovering any of
  it from scratch.

## 2026-07-16 — CI trigger scope and model bump

### Changed
- The deploy workflow now only triggers on changes to `src/**`, `infra/**`,
  `requirements-lambda.txt`, or itself — pushing README/CHANGELOG updates no
  longer causes a no-op Lambda redeploy.
- Summarization model bumped from `claude-sonnet-4-6` to `claude-sonnet-5`.

## 2026-07-16 — CI/CD pipeline and production hardening

### Added
- GitHub Actions workflow (`.github/workflows/deploy.yml`) that deploys to AWS Lambda on every push to `main`, authenticating via OIDC (no long-lived AWS access keys stored in GitHub).
- Terraform configuration (`infra/`) that manages the Lambda function's code and config. Scoped deliberately narrow: API Gateway, the Lambda execution role, and the dependency Layer are left as read-only references, since they already worked and didn't need to be brought under new tooling.
- `src/local_dev.py` — a local long-polling entry point that reuses the exact same functions as production (`src/lambda_function.py`), so prompt/logic changes can be tested on a laptop before deploying.
- `requirements-lambda.txt` documenting exactly what's bundled in the Lambda Layer, with the rebuild command in a comment.

### Fixed
- **CI/CD couldn't authenticate to AWS.** The IAM role's trust policy expected the classic OIDC subject format (`repo:owner/repo:*`), but this repository has GitHub's immutable subject claims enabled, so the real token subject is `repo:owner@id/repo@id:*`. Updated the trust policy condition to match.
- **Bot token was leaking into CloudWatch Logs.** `httpx` logs the full request URL at `INFO` level, and the Telegram bot token is embedded directly in that URL (`api.telegram.org/bot<TOKEN>/...`). Silenced httpx's own logger to `WARNING`. The exposed token was rotated via BotFather as a precaution.
- **Bot could get stuck on "Анализирую..." forever.** `send_message`/`edit_message` returned Telegram's response without checking `"ok"`, so a rejected request (e.g. malformed HTML entities in a Claude-generated summary) failed silently — the code logged success while the user's message never actually updated. Now failures are logged, and a failed HTML-formatted send retries once as plain text.

### Changed
- Narrowed the CI IAM policy to the exact resources it needs: it previously referenced a role ARN (`tg-bot-lambda-exec`) that didn't exist, and granted broad `iam:CreateRole`/`DeleteRole`/etc. actions that are unnecessary now that Terraform only *reads* the existing execution role (for `PassRole`) instead of owning it.

---

### How to use this changelog

Add a new dated section above whenever you ship a meaningful change (new feature, prompt rewrite, bug fix, infra change). Doesn't need to be fancy — a couple of bullets under `### Added` / `### Changed` / `### Fixed` is enough. Future-you (and anyone reviewing the repo) will thank you.
