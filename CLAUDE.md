# CLAUDE.md

Guidance for Claude Code (or any future assistant) working in this repository.
Read this before making changes — it captures context that isn't obvious from
the code alone.

## What this is

A Telegram bot that summarizes articles by link, using Claude for the actual
summarization/critique. Runs on AWS Lambda, deployed automatically from GitHub
via Terraform and GitHub Actions (OIDC, no long-lived AWS keys).

Full architecture explanation and diagrams: see [README.md](README.md).
History of changes: see [CHANGELOG.md](CHANGELOG.md).

## Real AWS resources (already exist — do not recreate)

| Resource | Identifier |
|---|---|
| AWS account | `456824039329` |
| Lambda function | `telegram-summarizer-bot` (region `eu-central-1`) |
| Lambda execution role | `telegram-summarizer-bot-role-hgo9fvd7` (path `/service-role/`) |
| Lambda Layer (deps) | `telegram-summarizer-bot-layer` |
| API Gateway (HTTP API) | `wczogedzph`, route `POST /webhook`, stage `$default` |
| CI IAM role | `github-actions-tgbot` (assumed via OIDC from this GitHub repo) |
| Terraform state bucket | `tfstate-tgbot-456824039329-eu-north-1-an` (region `eu-north-1` — note: different region than the Lambda itself) |

These were set up manually before this repo had a CI/CD pipeline, and
Terraform deliberately only manages the Lambda function's code/config — not
the role, the layer, or API Gateway. See "Why Terraform's scope is narrow"
below before changing that.

## File map

```
src/lambda_function.py   # THE bot — this exact file runs in production Lambda
src/local_dev.py         # local long-polling runner, imports and reuses lambda_function.py
infra/                   # Terraform — manages ONLY the Lambda function resource
  backend.tf             # S3 backend config for Terraform state
  provider.tf             # AWS provider + region
  variables.tf            # function name, execution role name, layer ARN
  main.tf                 # the actual aws_lambda_function resource + data sources
  outputs.tf               # ARN / last_modified outputs
.github/workflows/deploy.yml  # CI: build zip, OIDC login, terraform apply
requirements.txt          # full deps for local dev (includes boto3, python-dotenv)
requirements-lambda.txt   # what's actually bundled in the Lambda Layer (httpx, anthropic only)
scripts/set_webhook.py    # one-off: re-register the Telegram webhook if the API Gateway URL ever changes
.env.example              # local dev env var template (TELEGRAM_BOT_TOKEN, ANTHROPIC_API_KEY)
```

## Conventions

- **Code comments and docstrings: English.** This is a public repo used as a
  portfolio piece.
- **User-facing bot strings: Russian.** `SUMMARIZE_PROMPT`, `WELCOME_MESSAGE`,
  and every string passed to `send_message`/`edit_message` in
  `lambda_function.py` are intentionally Russian — the bot serves
  Russian-speaking users. Do not translate these; only translate
  comments/docstrings around them.
- **Commit messages**: explain *why*, not just what — this repo is read by
  people evaluating the author's engineering judgment, not just running the
  code.

## How to make a change

1. Edit `src/lambda_function.py` (or `infra/*.tf` for infrastructure changes).
2. Optional: test locally first —
   ```
   pip install -r requirements.txt
   cp .env.example .env   # fill in real tokens
   cd src && python local_dev.py
   ```
   This runs via polling on the developer's machine; production Lambda is
   untouched.
3. Commit and push to `main`.
4. GitHub Actions deploys automatically — **but only if the push touches**
   `src/**`, `infra/**`, `requirements-lambda.txt`, or the workflow file
   itself (see the `paths:` filter in `deploy.yml`). Pushing only
   README/CHANGELOG changes will *not* trigger a deploy.
5. Verify: check the [Actions tab](https://github.com/daniel-syachin/tldr_sum_bot/actions),
   then confirm in AWS if needed:
   ```
   aws lambda get-function --function-name telegram-summarizer-bot --region eu-central-1 \
     --query "{LastModified:Configuration.LastModified,CodeSha256:Configuration.CodeSha256}"
   ```
6. **Always update [CHANGELOG.md](CHANGELOG.md)** for any change a future
   reader would care about (new feature, prompt rewrite, bug fix, infra
   change). Add a new dated section above the previous one, following the
   existing `### Added` / `### Changed` / `### Fixed` structure. Skip it only
   for trivial things (typo fixes, comment tweaks).

## Adding a new dependency

Dependencies are **not** in the deploy zip — they live in a separate Lambda
Layer, built once and reused. Adding an `import` for something not already in
`requirements-lambda.txt` requires rebuilding that layer manually (the exact
commands are in a comment at the top of `requirements-lambda.txt`), then
updating `lambda_layer_arn` in `infra/variables.tf` to the new layer version's
ARN. A plain code change does not need any of this — only new third-party
imports do.

## Why Terraform's scope is narrow

Terraform in this repo manages **only** the `aws_lambda_function` resource
(code + config, with `environment` explicitly ignored). It does **not** manage
the execution role, the Lambda Layer, or API Gateway, even though Terraform
*could* import and manage all of them. This was a deliberate choice: those
pieces already worked, and giving the CI role broader IAM permissions
(`apigateway:*`, full `iam:*Role*` actions) to manage them would increase blast
radius without adding value. Don't widen this scope casually — if you do need
Terraform to own more of the stack, treat it as a real decision (import
carefully, verify `terraform plan` shows a clean/expected diff before
applying, and narrow the CI IAM policy to match exactly what's now needed).

## Known gotchas (already solved once — don't rediscover them)

- **GitHub OIDC trust policy and immutable subject claims.** If
  `sts:AssumeRoleWithWebIdentity` starts failing with "Not authorized" after
  touching the CI role or its trust policy, check
  `GET /repos/{owner}/{repo}/actions/oidc/customization/sub` — if
  `use_immutable_subject` is `true`, the real token subject is
  `repo:owner@id/repo@id:*`, not the classic `repo:owner/repo:*`. The trust
  policy condition must match the *actual* subject format.
- **httpx logs full request URLs at INFO level**, and the Telegram API token
  is embedded in that URL. `logging.getLogger("httpx").setLevel(logging.WARNING)`
  in `lambda_function.py` prevents this from leaking into CloudWatch — don't
  remove it, and think twice before adding any other httpx-based client
  without the same guard.
- **Telegram API responses aren't automatically checked for `"ok"`.**
  `send_message`/`edit_message` log and retry (stripping HTML) when Telegram
  rejects a request — if you add a new Telegram API call, follow the same
  pattern, or a failure will silently do nothing while the code logs success.
- **CI only triggers on `src/**`, `infra/**`, `requirements-lambda.txt`, or
  the workflow file** (see `paths:` in `deploy.yml`). If a change to
  production behavior doesn't seem to deploy, check whether the changed path
  is actually covered by that filter.
- **`local_dev.py` needs its own bot token.** Telegram refuses `getUpdates`
  polling on a bot that already has a webhook registered (`409 Conflict`).
  Local development must use a separate bot (see the note in `.env.example`)
  — never point it at the production `TELEGRAM_BOT_TOKEN`.
- **Windows Git Bash + `tr -d '\n'` leaves a stray `\r`.** Generating a secret
  and piping it through `tr -d '\n'` on this platform strips the LF but not
  the CR, silently adding one invisible extra character. This caused a real
  webhook-secret mismatch once (Lambda had 65 chars, Telegram had 64). Prefer
  Python's `.strip()` (or `tr -d '\r\n'`) when handling secrets in shell
  pipelines here, and verify by comparing lengths, never by eyeballing output.
- **`response.content[0]` is not always the answer.** The Anthropic SDK
  response is a list of typed blocks — `claude-sonnet-5` can put a
  `ThinkingBlock` (its reasoning) ahead of the `TextBlock`, and future models
  could add other block types (citations, tool use, etc.). `summarize()`
  picks the block by `block.type == "text"` rather than by position — follow
  the same pattern for any new Anthropic API call, and don't assume a fixed
  response shape survives a model bump.

## Secrets

`TELEGRAM_BOT_TOKEN`, `ANTHROPIC_API_KEY`, `TELEGRAM_WEBHOOK_SECRET`, and
`ALLOWED_CHAT_IDS` live
as plain environment variables on the Lambda function itself, set by hand
(or via `aws lambda update-function-configuration`) — never in this repo,
never in GitHub Secrets. `.env` is gitignored and only used for local
development. If a token is ever exposed (logs, screenshots, chat
transcripts), rotate it via BotFather (`/revoke`) or the Anthropic console,
then update the Lambda environment variable — Terraform will not overwrite
or interfere with that (`environment` is in `lifecycle.ignore_changes`).

`TELEGRAM_WEBHOOK_SECRET` is checked against the
`X-Telegram-Bot-Api-Secret-Token` header on every webhook call (see
`lambda_handler` in `lambda_function.py`), so a request that isn't actually
from Telegram gets a `401` instead of being processed. The check is
fail-closed: if the variable is unset, every webhook request gets a `500`.

`ALLOWED_CHAT_IDS` is a comma-separated list of chat IDs the bot will serve
(e.g. `123456789,987654321`). Unset means nobody — the bot replies to every
chat with "private bot, your chat_id is N", which is also the easiest way to
find your own ID. A malformed (non-integer) entry makes the module fail at
import, i.e. the whole function errors until it's fixed — check CloudWatch
right after editing it. `local_dev.py` doesn't apply the allowlist (it's a
private dev bot).

**Editing env vars from the CLI replaces the whole set.**
`aws lambda update-function-configuration --environment "Variables={...}"`
does not merge — any variable you leave out (e.g. the tokens) is deleted.
Use the console, or read the current set first and pass all of it back.

**Async retries are disabled by hand, not by Terraform.** The webhook
self-invokes with `InvocationType='Event'`, and Lambda retries a failed async
invocation up to twice by default — for this bot that means re-running (and
re-billing) a whole summarization after a timeout. It's turned off once via:
```
aws lambda put-function-event-invoke-config --function-name telegram-summarizer-bot \
  --region eu-central-1 --maximum-retry-attempts 0
```
It isn't in Terraform because managing it would need extra CI IAM permissions
(see "Why Terraform's scope is narrow"). If the function is ever recreated,
re-run this. If this secret is ever
rotated, the webhook must be re-registered with the new value:
`python scripts/set_webhook.py <webhook_url> <new_secret>` — otherwise
Telegram keeps sending the old (now-mismatched) header and every real message
gets rejected.

## Debugging in production

- Logs: `aws logs filter-log-events --log-group-name "/aws/lambda/telegram-summarizer-bot" --region eu-central-1 --start-time <epoch_ms>`
  (on Windows Git Bash, prefix with `MSYS_NO_PATHCONV=1` or the leading `/` in
  the log group name gets mangled into a Windows path).
- Webhook status: `curl "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"`
  — an empty `"url"` means the webhook got cleared (e.g. after rotating the
  bot token) and needs re-registering with `scripts/set_webhook.py`.
- Direct test invoke (safe — use a fake `chat_id`, it just fails at the
  Telegram API level without affecting real users). Since the webhook secret
  check landed, a payload without the matching header now correctly gets
  `401` — include the header (read the real value from the Lambda console,
  never print it in a shared terminal/log) to actually exercise the handler.
  The fake `chat_id` below isn't in `ALLOWED_CHAT_IDS`, so this exercises
  auth + the allowlist refusal path (look for `Rejected message from chat_id=1`
  in the logs):
  ```
  aws lambda invoke --function-name telegram-summarizer-bot --region eu-central-1 \
    --cli-binary-format raw-in-base64-out \
    --payload '{"headers": {"x-telegram-bot-api-secret-token": "<value>"}, "body": "{\"message\": {\"chat\": {\"id\": 1}, \"text\": \"/start\"}}"}' \
    response.json && cat response.json
  ```
  Simpler alternative that skips the secret entirely: invoke with
  `async_process` directly to exercise `process_message`/`fetch_article`/
  `summarize` without going through the webhook-auth branch at all —
  ```
  aws lambda invoke --function-name telegram-summarizer-bot --region eu-central-1 \
    --cli-binary-format raw-in-base64-out \
    --payload '{"async_process": true, "chat_id": 1, "text": "<article url>"}' \
    response.json && cat response.json
  ```
  then check CloudWatch for `OK: <url>` (success) vs. an `[ERROR]` line — the
  Lambda's own HTTP response is always `{"statusCode": 200, "body": "processed"}`
  either way, since `process_message` swallows its own exceptions.
