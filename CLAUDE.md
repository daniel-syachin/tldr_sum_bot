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
.github/workflows/ci.yml      # PR checks: ruff, pytest, terraform fmt/validate (+ plan if a plan role exists)
.github/workflows/deploy.yml  # on push to main: runs ci.yml, then OIDC login + terraform apply
.github/dependabot.yml        # monthly PRs bumping the SHA-pinned actions
tests/                    # pytest unit tests, every external call mocked (conftest.py sets env vars)
pyproject.toml            # ruff + pytest config only (not a package)
requirements.txt          # full deps for local dev (includes boto3, python-dotenv)
requirements-dev.txt      # requirements.txt + pinned pytest/ruff (what CI installs)
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
2. Run the same checks CI runs (required — CI blocks the deploy otherwise):
   ```
   pip install -r requirements-dev.txt
   ruff check . && pytest
   ```
   New behavior gets a test in `tests/` — mock every external call (Telegram,
   Jina, Claude, Lambda), the suite must stay offline and free.
   Optional: try it against a real (dev) bot —
   ```
   cp .env.example .env   # fill in real tokens
   cd src && python local_dev.py
   ```
   This runs via polling on the developer's machine; production Lambda is
   untouched.
3. Open a pull request: `ci.yml` runs the checks (and a `terraform plan`
   in the job summary, once the plan role exists — see "CI/CD" below).
4. Merging to `main` deploys automatically: `deploy.yml` re-runs the same
   checks via `workflow_call`, then applies — **but only if the push touches**
   `src/**`, `infra/**`, `requirements-lambda.txt`, or either workflow file
   (see the `paths:` filter in `deploy.yml`). Pushing only
   README/CHANGELOG/tests changes will *not* trigger a deploy.
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

## CI/CD

- **Actions are pinned to commit SHAs** with the version in a comment
  (`uses: actions/checkout@<sha> # v7.0.1`). Don't replace a pin with a tag;
  bump through Dependabot's PRs or resolve the new SHA with
  `git ls-remote https://github.com/<owner>/<action> refs/tags/<tag>`.
- **Deploys are serialized** (`concurrency: deploy-production`, not
  cancelled) and **state is locked** with S3-native locking
  (`use_lockfile = true`, Terraform >= 1.11). The lock is an object next to
  the state: `tg-bot/terraform.tfstate.tflock`. The deploy role
  (`github-actions-tgbot`) needs `s3:GetObject`, `s3:PutObject` and
  `s3:DeleteObject` on that key — if its policy only lists the state key
  itself, `terraform init/apply` fails with a lock error. A crashed run can
  leave a stale lock; `terraform force-unlock <lock-id>` (the ID is in the
  error) clears it — only once you're sure no apply is running.
- **`terraform plan` on PRs uses a separate read-only role**, never the
  deploy role: a PR branch can edit the workflow, so any role it can assume
  must be harmless. The plan step runs only when the repository variable
  `AWS_PLAN_ROLE_ARN` is set (Settings → Secrets and variables → Actions →
  Variables). To create the role:
  - Trust: the same GitHub OIDC provider as `github-actions-tgbot`, with the
    `sub` condition copied from that role's trust policy but ending in
    `:pull_request` instead of `:*` (keep whichever subject format — classic
    or immutable — the deploy role uses; see the OIDC gotcha below).
  - Permissions: AWS managed `AWSLambda_ReadOnlyAccess`, plus an inline
    policy with `s3:ListBucket` on the state bucket, `s3:GetObject` on
    `tg-bot/terraform.tfstate`, and `iam:GetRole` on the execution role.
    The plan runs with `-lock=false`, so no S3 write is needed. If a plan
    fails with `AccessDenied`, the error names the missing action — add
    exactly that, nothing broader.
- **Branch protection on `main`** (requiring the `CI` checks) is what makes
  the PR flow binding; without it, a direct push still runs the checks (the
  deploy job `needs` them) but nothing forces a PR.

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
- **Windows only: Git Bash + `tr -d '\n'` leaves a stray `\r`.** (The project
  is now developed on macOS, where this doesn't happen; kept because the
  lesson applies to any secret handling.) Generating a secret
  and piping it through `tr -d '\n'` on that platform strips the LF but not
  the CR, silently adding one invisible extra character. This caused a real
  webhook-secret mismatch once (Lambda had 65 chars, Telegram had 64). Prefer
  Python's `.strip()` (or `tr -d '\r\n'`) when handling secrets in shell
  pipelines here, and verify by comparing lengths, never by eyeballing output.
- **`response.content[0]` is not always the answer.** The Anthropic SDK
  response is a list of typed blocks — `claude-sonnet-5` and `claude-sonnet-5-5` can put a
  `ThinkingBlock` (its reasoning) ahead of the `TextBlock`, and future models
  could add other block types (citations, tool use, etc.). `summarize()`
  picks the block by `block.type == "text"` rather than by position — follow
  the same pattern for any new Anthropic API call, and don't assume a fixed
  response shape survives a model bump.
- **Only `UserFacingError` messages reach the user.** `process_message`
  shows the text of a `UserFacingError` (Russian, written for end users) and
  replaces any other exception with a generic message, logging the full
  traceback. When adding a failure the user should understand, raise
  `UserFacingError("…")`; a plain `Exception("…")` will show up as
  "Что-то пошло не так".
- **Telegram entity offsets are UTF-16 code units, not Python characters.**
  An emoji before a link shifts a naive `text[offset:offset+length]` slice.
  `extract_url()` re-encodes to UTF-16 before slicing — do the same for any
  other entity-based parsing.

## Configuration (non-secret env vars)

`CLAUDE_MODEL` (optional, default `claude-sonnet-5-5`) selects the model
`summarize()` uses. It lives on the Lambda environment like the secrets
below, so switching or rolling back a model is a console edit, not a deploy.
The TLDR prompt was written for `claude-sonnet-5-5`; before moving to a newer
model permanently, compare a handful of real summaries side by side, and
check that model's migration notes (thinking/effort parameters change
between generations).

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
  (Windows Git Bash only: prefix with `MSYS_NO_PATHCONV=1`, or the leading `/`
  in the log group name gets mangled into a Windows path. Not needed on macOS/Linux.)
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
