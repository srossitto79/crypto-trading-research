# External Research Sources — Operator Guide

The hypothesis-first research pipeline can pull evidence from five source types: **YouTube** (shipped earlier), **Reddit**, **blog/RSS feeds**, **GitHub**, and **forums**. Each is opt-in and configured via `axiom_settings`.

This guide covers: enabling each source, adding API credentials when useful, tuning registries, and troubleshooting.

---

## How gating works

Every external research tool checks three gates before making any HTTP call:

1. **Research contract** — the task must be in the `benchmarking` lane with `external_sources_allowed: true` and the source type in `allowed_external_source_types`.
2. **Source registry** — `research_settings.research_sources.<type>.enabled` must be `true`.
3. **Rate limiter** — per-domain budget not yet exhausted for the current minute.

Failing the gate returns a structured error envelope to the agent (`{"ok": false, "error": "..."}`) rather than consuming a network call.

---

## Settings shape

All source configuration lives under `axiom_settings.research_settings.research_sources`. The DB key is `axiom:settings`. Defaults are:

```json
{
  "research_settings": {
    "research_sources": {
      "reddit":  { "enabled": false, "subs":  ["algotrading", "quant", "options", "thetagang", "systematictrading"], "client_id": null, "client_secret": null, "rate_limit_per_min": 30 },
      "blog":    { "enabled": false, "feeds": ["https://www.quantstart.com/articles/rss/", "https://quantocracy.com/feed/", "https://blog.quantinsti.com/feed/"], "rate_limit_per_min": 30 },
      "github":  { "enabled": false, "orgs":  ["quantopian", "hudson-and-thames", "stefan-jansen"], "personal_access_token": null, "rate_limit_per_min": 60 },
      "forum":   { "enabled": false, "sites": ["elitetrader.com", "quantconnect.com", "quantnet.com"], "rate_limit_per_min": 20 }
    }
  }
}
```

All sources default to `enabled: false` — you must opt in explicitly.

---

## Reddit

**Tools exposed:** `discover_reddit_posts(query, subs?, limit?)`, `inspect_reddit_thread(permalink)`

### Enable

Set `research_sources.reddit.enabled = true`. Tune `subs` to the subreddits you want agents to search.

### Credentials (optional)

Anonymous `www.reddit.com/*.json` endpoints work out of the box with ~60 req/min. For higher throughput or to avoid intermittent 429s, register an OAuth app at https://www.reddit.com/prefs/apps and fill in `client_id` / `client_secret`. OAuth upgrade path is wired but not activated automatically — see Future Work.

### Rate limit

Default `rate_limit_per_min: 30`. Raise to 60 if anonymous and running tight benchmarking loops; keep below 100 to avoid IP-level throttling.

### Troubleshooting

- `error_code: "rate_limited"` → lower the limit or add OAuth credentials.
- `error_code: "parse"` with `"unexpected reddit response shape"` → Reddit returned an error dict instead of the normal listing. Usually means the subreddit is private/quarantined or the permalink was malformed.
- Deleted or removed posts return `ok: true` with `selftext: "[deleted]"` — the artifact is still attached so agents can reason about the fact that evidence was withdrawn.

---

## Blog / RSS

**Tools exposed:** `discover_blog_articles(query, feeds?, limit?)`, `inspect_blog_article(url)`

### Enable

Set `research_sources.blog.enabled = true`. Curate `feeds` to high-signal RSS/Atom sources. Both RSS 2.0 and Atom are supported.

### Default seeds

Three starter feeds are included: QuantStart, Quantocracy, QuantInsti. Swap for your preferred sources — add anything that exposes a feed.

### Article extraction

`inspect_blog_article` uses [trafilatura](https://trafilatura.readthedocs.io/) to extract plaintext. Quality depends on the site's HTML structure. Articles that are heavily SPA-rendered or paywalled will return empty content — the artifact still attaches so the agent knows the URL was checked.

### Rate limit

Default `rate_limit_per_min: 30`. Most blogs are forgiving, but large feed pulls that each fan out to article fetches can exhaust a per-domain budget quickly. Prefer a low `limit` on `discover_*` (5–10) to keep the subsequent inspect fetches bounded.

### Dependencies

`feedparser>=6.0` and `trafilatura>=2.0` are declared in `pyproject.toml`. Install with `pip install -e .` or `uv pip install -e .`.

---

## GitHub

**Tools exposed:** `discover_github_repos(query, orgs?, limit?)`, `inspect_github_repo(full_name)`

### Enable

Set `research_sources.github.enabled = true`. Tune `orgs` to known quant-trading repositories (default: quantopian, hudson-and-thames, stefan-jansen). Leave `orgs` as `[]` to let agents search across all of GitHub.

### Credentials — strongly recommended

Unauthenticated, GitHub's search API is 60 req/hr — exhausted by a handful of benchmarking runs. Create a Personal Access Token (no scopes needed for public-repo reads) at https://github.com/settings/tokens and set `personal_access_token`. That raises the ceiling to 5000 req/hr.

The PAT is sent via `Authorization: token <pat>` header. It is never logged.

### Rate limit

Default `rate_limit_per_min: 60`. Mostly decorative — GitHub's own rate ceiling binds first.

### `inspect_github_repo` output

Returns README (raw), top 5 open+closed issues, and repo metadata. Issue bodies are truncated to 200 chars each to keep the `content` blob bounded.

### Troubleshooting

- `error_code: "http_4xx"` on a known-public repo → PAT may be malformed or expired; try unsetting it first.
- `error_code: "rate_limited"` without a PAT → set the PAT.
- Empty README → the repo has no README.md; try README.rst or README.txt (not supported today).

---

## Forum

**Tools exposed:** `discover_forum_threads(query, sites?, limit?)`, `inspect_forum_thread(url)`

### Enable

Set `research_sources.forum.enabled = true`. Default `sites` include Elite Trader, QuantConnect forum, QuantNet.

### Adapter model

Forums have no standard schema. Each supported site ships with a per-site adapter in `axiom/research_sources/forum.py::ADAPTERS` that declares:
- search URL template
- CSS selector for result list items
- CSS selector for result link (usually `a.thread-link`)
- CSS selector for thread post container
- CSS selector for post body

When a site redesigns, its adapter selectors will break. Fix by inspecting the new HTML and updating the single adapter entry — blast radius stays small.

Sites not in `ADAPTERS` are silently skipped. If you want to add a forum:

```python
ADAPTERS["newforum.com"] = ForumAdapter(
    site="newforum.com",
    search_url=lambda q: f"https://newforum.com/search?q={quote(q)}",
    result_selector="div.result",
    result_link_selector="a.title-link",
    thread_selector="article.post",
    post_selector=".post-body",
)
```

### Rate limit

Default `rate_limit_per_min: 20`. Forums tend to enforce aggressive bot-detection (Cloudflare, etc.) — keep this low. The per-domain rate limit is also global, so multiple agents sharing a process share the budget.

### Troubleshooting

- Results come back empty despite a known-good search → selectors are probably stale. Check the adapter against the site's current HTML.
- `error_code: "http_4xx"` with status `403` → Cloudflare challenge. No workaround in v1; consider disabling that specific site.

---

## Artifact content cache

Each `inspect_*` tool returns a `content` string. When the agent calls `attach_hypothesis_artifact(..., cached_content=content)`, that string is persisted to `hypothesis_artifacts.cached_content` with a sha256 hash and byte count.

- **Hard cap:** 500 KB per artifact. Larger content is truncated with a `...[truncated]` marker.
- **Dedupe:** the same content attached to multiple sources produces the same hash — lets agents identify evidence that appears in more than one place.
- **API exposure:** `GET /api/hypotheses/{id}` strips `cached_content` by default (hash/bytes/cached_at still visible). Pass `?include=content` to retrieve the blobs.

---

## Security notes

- Credentials (Reddit `client_secret`, GitHub `personal_access_token`) are read from `axiom_settings` and injected into request headers. They are never logged or serialized into API responses.
- Never commit real credentials to the repo; use the settings DB as the source of truth.
- HTTP User-Agent is `axiom-research-sources/1.0` with a project URL. If you need to identify the instance differently, edit `axiom/research_sources/_http.py::USER_AGENT`.

---

## Enabling via settings DB

All settings live in the SQLite key `axiom:settings`. A minimal enable-everything block for testing:

```python
from axiom.db import kv_get, kv_set

s = kv_get("axiom:settings", {})
s.setdefault("research_settings", {}).setdefault("research_sources", {})
for src in ("reddit", "blog", "github", "forum"):
    s["research_settings"]["research_sources"].setdefault(src, {})["enabled"] = True
kv_set("axiom:settings", s)
```

Restart any long-running agent process after a settings change — the research contract is resolved per-task but the registry is read on each tool call, so changes take effect on the next hypothesis research task.

---

## Future work

- OAuth flow wire-up for Reddit (credentials slot already exists).
- Per-source spawn limits (today spawn limits are global across lanes).
- LLM-driven summarization before attachment (content is passed to agents raw today).
- Settings UI for source toggles (currently JSON-only via `kv_set`).
- GitHub ETag-aware caching (reduce wasted quota on unchanged repos).
- Automatic adapter health-check for forums (detect stale selectors proactively).
