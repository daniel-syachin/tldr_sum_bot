# Changelog

All notable changes to this project are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

_Add new entries above this line as you make changes — see the "How to use this changelog" note at the bottom._

## 2026-10-17 — CI checks before every deploy, state locking, pinned actions

### Added
- **`ci.yml`: checks on every pull request** — `ruff`, the new offline
  `pytest` suite (`tests/`, every external call mocked), and
  `terraform fmt -check` / `validate`. Previously a push to `main` went
  straight to `terraform apply -auto-approve` with nothing in between.
- **`terraform plan` in the PR job summary**, via a *separate read-only*
  role (`AWS_PLAN_ROLE_ARN` repository variable). A PR branch can edit the
  workflow, so it must never be able to assume the deploy role. Skipped,
  not failed, until that variable exists.
- `.github/dependabot.yml` for monthly, grouped GitHub Actions bumps.

### Changed
- **`deploy.yml` runs the same checks first** (`workflow_call` into
  `ci.yml`), so a direct push to `main` can't skip what a PR would catch.
- **Deploys are serialized** with a `concurrency` group (queued, not
  cancelled), and **the Terraform state is locked** with S3-native locking
  (`use_lockfile = true`). Two quick pushes used to be able to run two
  applies against the same unlocked state.
- **Terraform 1.9.8 → 1.16.6** in CI (`required_version >= 1.11`, the
  release where S3-native locking became GA).
- **Every action is pinned to a commit SHA** (checkout v7.0.1, setup-python
  v7.0.0, configure-aws-credentials v6.3.0, setup-terraform v4.0.1) instead
  of a movable major tag — the deploy job runs with AWS credentials, so a
  hijacked tag would run attacker code with them. The deploy also sets
  `allowed-account-ids`, refusing credentials for any other account.
- `.terraform.lock.hcl` gained the Linux `h1:` hashes for the existing
  provider versions (it only had the hashes of the platform it was created on).

## 2026-10-17 — Robust summaries: stop reasons, long articles, links, errors

### Added
- **Links are found anywhere in a message.** The bot used to require the
  whole message to be a bare URL, so "глянь https://…", a link hidden behind
  text, or a forwarded post (text in `caption`) were all rejected. It now
  reads Telegram's own `url` / `text_link` entities (offsets are UTF-16, so
  the text is re-encoded before slicing), including scheme-less links.
- **`CLAUDE_MODEL` env var** (default `claude-sonnet-5`), so a newer model
  can be tried on real articles from the Lambda console without a deploy —
  and rolled back the same way. The prompt was tuned for `claude-sonnet-5`,
  so a model switch deserves a side-by-side comparison, not a blind bump.

### Changed
- **Long articles are capped at 150k characters** before going to Claude, so
  a runaway page (docs site, giant comment thread) can't silently cost many
  times a normal summary. Truncation is never silent: the model sees a
  "text truncated" marker and the user gets a note under the summary.
- **Long summaries are split at paragraph/line boundaries** instead of at
  character 4000, which could cut through an HTML tag and make Telegram
  reject the chunk (degrading it to the plain-text fallback).
- **Users no longer see raw exception text.** Errors meant for users are now
  a `UserFacingError` with a Russian message; Anthropic SDK errors are mapped
  to readable ones (overloaded, rate-limited, unreachable, timed out). Any
  other exception is logged with its full traceback and the user gets a
  generic message instead of English SDK/httpx internals.
- URL validation accepts TLDs longer than 6 characters (`.technology`,
  `.photography`) and punycode TLDs.

### Fixed
- **Truncated or refused answers were sent as if complete.** `summarize()`
  ignored `stop_reason`: a reply cut off by `max_tokens` went out without
  any hint, and a `refusal` surfaced as a confusing "no text block" error.
  Both are now handled explicitly, as is a search loop still paused after
  all continuations.
- **Text written before a `pause_turn` was dropped.** A continuation only
  returns the blocks generated after the pause, but the summary was built
  from the last response alone (and only that response was re-sent as the
  assistant turn). Blocks from every round are now accumulated.

## 2026-10-17 — Private bot, deadline-aware Claude calls, fail-closed auth

### Added
- **Chat allowlist (`ALLOWED_CHAT_IDS`).** The webhook secret proves a request
  came from Telegram, not *who* is messaging the bot — anyone who found the bot
  could run Claude + web search on the author's bill. Only listed chat IDs are
  now served; anyone else gets a refusal containing their own `chat_id` (so the
  owner can add themselves) before any paid API call. Unset means nobody is
  allowed.

### Changed
- **Webhook secret check is now fail-closed.** An unset
  `TELEGRAM_WEBHOOK_SECRET` used to skip authentication with only a warning —
  one botched env-var edit would have silently opened the endpoint. It now
  rejects every webhook request (`500`). The comparison uses
  `hmac.compare_digest` instead of `!=`.
- **Webhook logging no longer dumps the raw event.** It logged the first 300
  characters of the API Gateway event, which includes request headers (client
  IP, and potentially the webhook secret itself). Now it logs only `update_id`
  and `chat_id`.

### Fixed
- **Slow Claude responses could outlive the Lambda and be re-run.** The SDK
  defaults (10 min timeout, 2 retries) were far longer than the 240s Lambda
  timeout, so a slow request ended in a hard Lambda timeout: the user's status
  message stayed stuck, and Lambda's async retry policy could re-run the whole
  summarization (and re-bill it) up to twice more. The async invocation now
  derives a deadline from `context.get_remaining_time_in_millis()` (minus a
  15s margin for reporting the error) and every Claude call is bounded by it,
  without SDK retries. Paired with a one-off `MaximumRetryAttempts=0` on the
  function's async invoke config (set via CLI — see CLAUDE.md), since
  Terraform deliberately doesn't manage it.

## 2026-07-16 — Web search for fact-checking

### Added
- `summarize()` now gives Claude a server-side `web_search_20260209` tool,
  capped at `max_uses: 3` per article — used to spot-check a claim or pull
  current context, not to research the whole topic. Anthropic runs the
  search and hands results back to the model directly; no new dependency.
- Handles `stop_reason: "pause_turn"` (the server-side search loop's own
  10-round ceiling) by re-sending the conversation so the summary doesn't
  come back silently truncated.

### Changed
- `SUMMARIZE_PROMPT` no longer tells the model it has no internet access —
  it now explains the search budget, when to spend it (fact-checking, not
  blanket research), and to cite sources inline rather than as a separate
  link list.
- `summarize()` now concatenates every `text` content block instead of
  returning the first one — with web search, Claude's answer can be split
  across multiple text segments interleaved with search calls.

## 2026-07-16 — Prompt rewrite for Claude Sonnet 5

### Changed
- `SUMMARIZE_PROMPT` rewritten around how `claude-sonnet-5` actually behaves
  (very literal instruction following): removed the "research the topic
  yourself" instruction (the API call has no web access — it only invited
  fabricated facts), made the critical-analysis sub-points conditional
  instead of mandatory (a forced "conflict of interest" bullet gets invented
  when there isn't one), added a graceful path for non-article input (error
  pages, paywalls), restricted formatting to the HTML subset Telegram
  actually parses, and banned preamble. The article now goes *first* in the
  prompt, wrapped in `<article>` tags, per Anthropic long-context guidance.

### Fixed
- `summarize()` crashed on any article containing literal `{` or `}` (code
  snippets, JSON) because the article was injected via `str.format()`. Now
  concatenated instead.
- `max_tokens` raised 2000 → 8000: Sonnet 5 runs adaptive thinking by
  default and thinking tokens count against `max_tokens`, so 2000 could be
  consumed by reasoning alone, truncating the reply or returning no text
  block at all.

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
