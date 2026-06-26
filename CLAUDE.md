# Maarten's Grant Seeker — project guide

An autonomous agent that scours the web (and structured APIs) for **flood-related
funding opportunities** — grants, RFPs, RFIs, loans, and investment — in coastal &
inland flooding, flood resilience, and flood risk mapping. Each lead is tagged with
a **track**: *Application* (project funding you apply for) or *Business Development*
(financing for the company/product). It accumulates them in a database, emails a
daily digest of **newly found** leads, and publishes a **searchable, filterable
website** where the team can like/dislike, comment, and steer the agent.

Everything runs **free** on GitHub Actions (daily cron) + GitHub Pages + a tiny
Cloudflare Worker. There is no server to maintain.

---

## What it does, end to end

```
                    ┌─────────────────── GitHub Actions (daily, 07:23 UTC) ───────────────────┐
                    │                                                                          │
  Cloudflare Worker │  scout.py                                                                │
  (shared state) ◄──┼─ 1. process @claude comment directives (hide/unhide, add guidance)       │
   verdicts +       │  2. learn from verdicts (promising/rejected) + standing guidance rules    │
   comments         │  3. search: Claude web-search  +  Grants.gov API  → dedupe → out JSON     │
                    │  build_site.py                                                            │
                    │  4. merge new leads into the database (dedupe by URL, stamp first_seen)    │
                    │  5. write data/new.json (NEW only, minus rejected/hidden)                 │
                    │  6. regenerate docs/opportunities.js (visible leads, +region +id)         │
                    │  email_digest.py                                                          │
                    │  7. email the digest of NEW leads (HTML, via Gmail SMTP or Resend)         │
                    │  8. commit the database + generated files back to the repo                │
                    └──────────────────────────────────────────────────────────────────────────┘
                                   │                                  │
                                   ▼                                  ▼
                     data/opportunities_db.json            docs/  → GitHub Pages
                     (accumulated source of truth)          (searchable site + 👍/👎 + comments)
```

The website and the pipeline both read/write the **Cloudflare Worker**, so verdicts
and comments are **shared across all visitors** and the agent can act on them.

---

## Repository layout

| Path | What it is |
|---|---|
| `scout.py` | Finds opportunities (Claude web search + Grants.gov API), learns from verdicts, processes `@claude` directives, manages source memory. Writes the fresh run to `--json`. |
| `build_site.py` | Accumulating database + dedupe; writes `data/new.json` (new-only) and `docs/opportunities.js` (the site data); classifies region; assigns `opp_id`; backfills missing `track`→`Application`; drops hidden entries. |
| `email_digest.py` | Composes the HTML digest (joke → intro → site link → new leads) and sends via Resend or SMTP. |
| `docs/index.html` | The website: search + filters (status / **track** / region / topic / funder / sort), 👍/👎 verdicts, and comments. Pure static + vanilla JS; loads `opportunities.js` with a cache-buster so daily updates show without a hard refresh. |
| `floodadapt.md` | Public, editable **"who we are"** context (what FloodAdapt is, its users, eligibility). Loaded by `load_floodadapt()` into the search + research prompts. Keep sensitive bits out — put those in the `FLOODADAPT_CONTEXT_EXTRA` secret. |
| `docs/opportunities.js` | **Generated** site data (`window.OPPORTUNITIES = [...]`). Do not hand-edit. |
| `worker/verdicts-worker.js` | Cloudflare Worker: shared store for verdicts (`/`) and comments (`/comments`). |
| `worker/wrangler.toml`, `worker/README.md` | Worker config + deploy notes. |
| `sources.yaml` | Seed list of known funding sources, fed into the search prompt. Editable. |
| `recipients.yaml.example` | Template for the local recipient list (`recipients.yaml` is git-ignored). |
| `secret.bat.example` | Template for local secrets (`secret.bat` is git-ignored). |
| `run.bat` / `email.bat` / `site.bat` | Windows launchers (resolve Python, load `secret.bat`, run the scripts). |
| `requirements.txt` | `anthropic`, `pyyaml`. |
| `.github/workflows/daily-digest.yml` | The daily cron pipeline (also runnable manually). |
| `.github/workflows/deploy-worker.yml` | One-time / on-demand Worker deploy (so no local Node needed). |
| `.gitignore`, `.gitattributes` | Ignore transient files; auto-resolve generated files on merge (see Gotchas). |

### Data files (`data/`)

| File | Committed? | Owner | Purpose |
|---|---|---|---|
| `opportunities_db.json` | ✅ | CI | **The database.** Accumulated, deduped leads. Each has a `track` (Application / Business Development). `hidden: true` marks removed-from-site entries (kept so they don't re-appear). |
| `sources_db.json` | ✅ | CI | Learned fruitful **domains** (counts), fed into the prompt. |
| `guidance.json` | ✅ | CI / you | **Standing search-guidance rules** from `@claude` (`{text, added, from}`, newest 30). Hand-editable. |
| `processed_comments.json` | ✅ | CI | IDs of `@claude` directives already acted on (so each fires once). |
| `opportunities.json` | ❌ git-ignored | transient | A single scout run's raw output (merged into the DB, then discarded). |
| `new.json` | ❌ git-ignored | transient | The new-this-run leads the email sends. |

---

## Components in detail

### `scout.py`
- **Models:** `claude-sonnet-4-6` primary (fast/cheap, plenty capable), `claude-opus-4-8` as the overload fallback. `MODELS` at the top.
- **Search is two-phase** (`_search` → `_extract`): a free-form web search (basic `web_search_20250305` tool) gathers notes, then a second call with structured outputs (`output_config.format`) turns the notes into the fixed schema. *Do not* combine web search + structured output in one call — the model skips searching and returns nothing, and the dynamic-filtering tool (`web_search_20260209`) 400s in CI on multi-turn (`container_id` required).
- **Grants.gov API** (`fetch_grants_gov`): deterministic US-federal pull, filtered to flood/water-relevant titles (`GRANTS_GOV_TITLE_TERMS`) so it doesn't flood the list with unrelated federal grants. Degrades gracefully (returns `[]`) on error.
- **Prompt** (`build_prompt`): topics + the funding types to hunt (grants/calls/tenders **and** loans/equity/impact investment) + a geographic mandate (`GEOGRAPHIES`: state/local incl. Florida, Canada, World Bank/ADB/Asian cities — *not* mostly US federal) + standing guidance rules + verdict preferences + known sources.
- **Tracks:** every lead is classified into `track` = **Application** (project funding you apply for) or **Business Development** (financing for the company/product). The schema *requires* `track`; Grants.gov pulls are `Application`. `build_site.py` backfills any legacy lead missing one.
- **Who we are:** `load_floodadapt()` merges `floodadapt.md` (public) with the `FLOODADAPT_CONTEXT_EXTRA` secret (sensitive bits) and injects it into both the search prompt (relevance) and `@claude` research (eligibility).
- **`opp_id(o)`** = `sha1(title|funder)[:12]` — the **stable per-opportunity key** used for verdicts and comments (NOT the URL, which is sometimes a generic landing page). `build_site.opp_id` must stay identical.

### `build_site.py`
- `merge()` adds leads not already in the DB (dedupe by normalized `source_url`), stamps `first_seen`, returns the new ones.
- Writes `data/new.json` = new leads minus rejected (verdicts) minus hidden.
- `write_site()` writes `docs/opportunities.js` from the **visible** DB (drops `hidden`), newest-first, each tagged with `region` (`classify_region`, a heuristic from URL/funder) and `id` (`opp_id`).
- **Track backfill:** any opportunity without a `track` is set to `Application` (legacy grants/RFPs predate the field) and persisted to the DB — runs on every build, including no-`--merge` rebuilds.

### `email_digest.py`
- Recipients from the `RECIPIENTS` env var (CI secret, `email | greeting` per line) or local `recipients.yaml`.
- Per recipient: a fresh flood-modelling **joke** (Claude), the **intro** blurb, the **website link**, then the **new** opportunities. Sent as **HTML** (titles are the clickable links — avoids giant Outlook safelink URLs) with a plain-text fallback.
- Sender: **Resend** if `RESEND_API_KEY` is set, else **SMTP**. `--smtp` forces SMTP. Per-recipient failures are reported but don't stop the others.

### `docs/index.html` (website)
- Static, vanilla JS. Loads `docs/opportunities.js` **dynamically with a `?t=` cache-buster** (skipped for local `file://`, where query strings break the path) so daily updates appear on a normal refresh. Filters: status (Active hides rejected / Promising / Undecided / Rejected / All), **track** (Application / Business Development), region, topic, funder, sort; full-text search.
- **Verdicts** (👍 Promising / 👎 Reject) and **comments** read/write the Worker (`VERDICTS_API` constant near the bottom of the file). Falls back to `localStorage` for verdicts if the Worker isn't configured.
- Comment deletes require a confirm prompt. Comments addressed `@claude …` are acted on by the next pipeline run.

### `worker/verdicts-worker.js` (Cloudflare Worker)
- KV-backed shared store. **Verdicts:** `GET /` (map of `oppId → like/dislike`), `POST /` (`{url: oppId, verdict}`). **Comments:** `GET /comments`, `POST /comments` (`{oppId, name?, text}`), `POST /comments/delete` (`{oppId, commentId}`). Open access (no auth); CORS enabled.
- Deployed via the `deploy-worker.yml` Action (needs `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID` secrets). KV data survives redeploys.
- Current URL: `https://grant-seeker-verdicts.maarten-vanormondt.workers.dev`.

---

## The `@claude` command channel

Comments that start with `@claude` are instructions the agent acts on during its
next run (`process_directives` in `scout.py`). Claude interprets each into ONE
**safe** action and posts a confirmation reply:

- `@claude, remove this entry` → sets `hidden: true` on that opportunity. Gone from
  the site forever, but kept in the DB so dedup stops it re-appearing in searches/email.
- `@claude, unhide this` → reverses a hide.
- `@claude, find more like this but particularly in Asia` → appends a **standing
  rule** to `data/guidance.json`, which steers every future search.
- `@claude, tell me more` (also "who else applied", "are we eligible?") →
  **researches** that specific opportunity via web search (`research_opportunity`)
  and posts a briefing back as a comment: scope/funding/dates, previously funded
  projects/awardees, and an eligibility read for Deltares / FloodAdapt and
  FloodAdapt subscribers (context from `floodadapt.md` + the
  `FLOODADAPT_CONTEXT_EXTRA` secret, via `load_floodadapt()`).

It is a **public, open channel** (anyone can comment), so actions are deliberately
non-destructive — it never hard-deletes from the DB or does arbitrary things. Each
directive is processed once (`processed_comments.json`).

---

## Learning loops

1. **Source memory** — domains that yield leads accrue in `sources_db.json`; the top ones are promoted in the prompt as "check these first."
2. **Verdicts** — liked/rejected opportunities become "find more like / avoid similar" examples in the prompt; domains that are mostly rejected get demoted.
3. **Standing guidance** — `@claude` guidance rules in `guidance.json` are injected into every search.

---

## GitHub Actions

- **`daily-digest.yml`** — cron `23 7 * * *` (07:23 UTC; off-the-hour to dodge GitHub's queue congestion) + manual `workflow_dispatch`. Steps: find → update DB/site → send digest → commit data back. Needs `permissions: contents: write` **and** repo Settings → Actions → Workflow permissions = "Read and write."
- **`answer-claude.yml`** — runs `scout.py --directives-only` (acts on `@claude`
  comments, no full search/email). Fired by **`repository_dispatch`** the moment
  an `@claude` comment is posted (the Worker calls the GitHub API), so research
  briefings land in ~30-60s. A `*/30` cron is a backup.
- **`deploy-worker.yml`** — manual; deploys the Worker (creates the KV namespace if needed). No local Node required.

**Worker secrets** (Cloudflare dashboard → Worker → Settings → Variables and Secrets):
`GH_DISPATCH_TOKEN` (a GitHub token that can trigger dispatches — classic `repo`
scope, or fine-grained Contents: read/write) so the Worker can kick off
`answer-claude.yml`; optionally `GH_REPO` (defaults to `maartenvanormondt/FloodAdaptProjectScanner`).

> Scheduled runs are **best-effort** — GitHub can delay them by minutes or skip on-the-hour slots. To test, use **Run workflow** (manual); don't chase the clock.

---

## Secrets & configuration

**GitHub repo secrets** (Settings → Secrets and variables → Actions):

| Secret | Used by | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | scout, email | A real **API key** (`sk-ant-api03-…`), with API **billing/credit**. NOT a Claude Code OAuth token (`sk-ant-oat01-…`) and separate from any Claude subscription. |
| `VERDICTS_API` | site (baked in), scout, build_site | The Worker URL. Powers shared verdicts, comments, `@claude`, and email rejection-filtering. |
| Email — **Gmail SMTP** (reaches anyone) | email | `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USER`, `SMTP_PASS` (Gmail **app password**), `EMAIL_FROM`. **Do not also set `RESEND_API_KEY`** or Resend wins. |
| Email — **Resend** (your own address only) | email | `RESEND_API_KEY`, `EMAIL_FROM=onboarding@resend.dev`. Only reaches your Resend account address until a domain is verified. |
| `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` | deploy-worker | "Edit Cloudflare Workers" token + account id. |
| `FLOODADAPT_CONTEXT_EXTRA` | scout | *Optional.* Sensitive "who we are" details (fees, strategy) merged into the `floodadapt.md` context at runtime, kept out of the public repo. |

**Local** (`secret.bat`, git-ignored — copy from `secret.bat.example`): `set "ANTHROPIC_API_KEY=…"`, optional `VERDICTS_API`, optional email vars, optional `PYTHON_EXE`.

---

## Local development (Windows)

`run.bat` / `email.bat` / `site.bat` resolve the miniforge `delftdashboard_dev`
Python, load `secret.bat`, and run from the repo folder. Typical loop:

```bat
run --json out.json        :: search + save
site --merge out.json      :: accumulate into the DB + rebuild docs/opportunities.js
email --opps data/new.json :: email only the new ones (Resend locally; SMTP is blocked on the corp laptop)
```

Then open `docs/index.html` directly (data is embedded as a script — no server needed).

---

## Conventions & gotchas (hard-won)

- **SMTP is blocked** on the Deltares network *and* on the corporate laptop (TLS handshake killed) *and* on GitHub cloud runners for Microsoft. → Locally use **Resend** (HTTPS/443). For the daily job emailing colleagues, use **Gmail app-password SMTP** (works from Actions, reaches anyone).
- **Resend testing mode** only delivers to your own account address until you verify a domain. To email the team, use Gmail SMTP.
- **Generated/data files cause merge conflicts** (CI and you both commit them). Mitigations already in place: transient files are git-ignored; `data/*_db.json`, `guidance.json`, `processed_comments.json`, and `docs/opportunities.js` use a `merge=theirs` driver (`.gitattributes`). **Register the driver once per clone:** `git config merge.theirs.driver "cp -f %B %A"` and `git config merge.theirs.name "take incoming"`. Treat the DB as **CI-owned** — don't hand-edit and expect it to survive a pull.
- **Cloudflare blocks the default Python-urllib User-Agent (403).** Always set `User-Agent` on requests to the Worker (the code does).
- **Verdict/comment key is `opp_id` (title+funder hash), not the URL** — because some leads share a generic landing-page URL.
- **Web search tool:** use basic `web_search_20250305`, not `_20260209` (the latter runs code execution and 400s in CI on multi-turn).
- **Model IDs are current** (`claude-sonnet-4-6`, `claude-opus-4-8`); thinking is adaptive-only, no `budget_tokens`, no sampling params.
- **GitHub Pages** serves from `main` / `/docs`. The repo is public (no secrets live in it; keys are in Actions secrets + git-ignored `secret.bat`).
- **Rebuilding the site locally: `git pull` first.** The DB is CI-owned and your local copy is usually behind. Running `site` on a stale DB regenerates `docs/opportunities.js` from old data — pull to sync, *then* rebuild + push.
- **Site cache-busting:** `index.html` loads `opportunities.js` with `?t=<timestamp>` so data updates show on a normal refresh (local `file://` skips the query). When `index.html` *itself* changes, one hard-refresh is needed to pick up the new page; after that it's automatic.

---

## Editing the agent's focus

- **Who we are (for eligibility + relevance):** `floodadapt.md` (public, committed) — read by `load_floodadapt()` into the search prompt and the `@claude` research. Sensitive bits (fees, strategy) go in the `FLOODADAPT_CONTEXT_EXTRA` secret instead (merged in at runtime, never committed).
- **Topics:** `TOPICS` in `scout.py`.
- **Funding types & tracks:** the funding-type list and the Application / Business Development definitions live in `build_prompt` (`scout.py`); the `track` enum is in `OPPORTUNITY_SCHEMA`.
- **Geographic mandate:** `GEOGRAPHIES` in `scout.py`.
- **Seed sources:** `sources.yaml`.
- **Search breadth/speed:** `MAX_SEARCHES` in `scout.py`.
- **Standing rules:** `data/guidance.json` (or just comment `@claude …` on the site).
- **Recipients:** the `RECIPIENTS` secret (CI) or `recipients.yaml` (local).
