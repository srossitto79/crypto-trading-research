# Backups & Rollback (Git-first)

Axiom's source and project state live in a local git repo on this machine. Trading data and secrets are **not** committed.

## What is covered
- Source code and project state tracked in git.
- Secrets excluded via `.gitignore` (`.env`, keys, certs, `*.db`, the `.axiom_home/` workspace).

## Daily flow
```powershell
git add -A
git commit -m "checkpoint: <what changed>"
```

## Restore point before risky changes
```powershell
git tag -a stable-YYYYMMDD-HHMM -m "Known good restore point"
```

## Roll back
```powershell
git checkout <tag-or-commit>
```

## Optional: publish to a private remote
The repo is local-only by default. To back it up off-machine, create a **private** empty repo and push:
```powershell
git remote add origin <YOUR_PRIVATE_REPO_URL>
git branch -M main
git push -u origin main --tags
```

## Safety checks
- Never commit `.env`, `*.db`, auth tokens, or anything under `.axiom_home/`.
- Rotate keys immediately if a secret is ever committed.
- Keep any remote private.
