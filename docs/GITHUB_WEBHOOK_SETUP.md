# GitHub Webhook Auto-Update

This repo now supports GitHub push webhooks at:

- `POST /api/webhooks/github`
- `GET /api/webhooks/github/health`

The webhook endpoint verifies `X-Hub-Signature-256` with `HMAC-SHA256`.

## 1) Configure Environment

Set these in `.env` (or your service environment):

```bash
AXIOM_GITHUB_WEBHOOK_SECRET=<long-random-secret>
AXIOM_GITHUB_WEBHOOK_BRANCH=feature/quant-factory-upgrade
AXIOM_GITHUB_WEBHOOK_REMOTE=origin
AXIOM_GITHUB_WEBHOOK_REPO=/home/trestor/axiom
AXIOM_GITHUB_WEBHOOK_POST_PULL_CMD=
```

Optional post-pull command example:

```bash
AXIOM_GITHUB_WEBHOOK_POST_PULL_CMD=systemctl --user restart axiom-stack.service
```

Restart backend after setting env vars.

## 2) Configure GitHub Webhook

In GitHub repo settings:

1. Go to `Settings -> Webhooks -> Add webhook`
2. Payload URL: `https://<your-domain>/api/webhooks/github`
3. Content type: `application/json`
4. Secret: same value as `AXIOM_GITHUB_WEBHOOK_SECRET`
5. Events: `Just the push event`
6. Active: enabled

## 3) Verify

Check endpoint config:

```bash
curl -s http://127.0.0.1:8003/api/webhooks/github/health
```

In GitHub webhook UI, use `Recent Deliveries` to inspect response payloads.

## Notes

- Only push events for `AXIOM_GITHUB_WEBHOOK_BRANCH` trigger pull.
- Updates run with:
  - `git -C <repo> fetch <remote>`
  - `git -C <repo> pull --ff-only <remote> <branch>`
- If the working tree is dirty, pull may fail.
