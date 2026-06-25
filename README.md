# Maarten's Grant Seeker

An agent that scours the web for funding opportunities (grants, RFPs, RFIs)
in coastal & inland flooding, flood resilience, flood risk mapping, and
related fields — maintains a database of what it finds, sends a daily digest
of new opportunities plus a top-10, and (later) shows everything on a website.

## Status: proof of concept

`scout.py` is step 1. It runs Claude with the built-in web-search tool, finds
current opportunities, and extracts a fixed set of fields per opportunity as
validated JSON, then prints them. No database / cron / email / website yet —
this validates the core before we build the rest.

### Run it

**Easiest (Windows):** set your key once, then run the batch file.

```bat
setx ANTHROPIC_API_KEY "sk-ant-..."   :: one time; then open a NEW terminal
run.bat                                :: print results
run.bat --json out.json                :: also save raw JSON
```

`run.bat` finds the miniforge `delftdashboard_dev` Python automatically (override
with `set PYTHON_EXE=...`), checks the key is set, and runs from its own folder
so you can also just double-click it in Explorer. Any arguments pass through to
`scout.py`.

**Manual:**

```bash
# 1. Set your Anthropic API key
#    PowerShell:  $env:ANTHROPIC_API_KEY = "sk-ant-..."
#    Git Bash:    export ANTHROPIC_API_KEY=sk-ant-...

# 2. (recommended) upgrade the SDK
python -m pip install -U anthropic

# 3. run
python scout.py                 # print results
python scout.py --json out.json # also save raw JSON
```

On this machine, `python` resolves via miniforge — e.g.
`C:\Users\ormondt\miniforge3\envs\delftdashboard_dev\python.exe scout.py`.

### Fields captured per opportunity

`title`, `funder`, `one_liner`, `summary_paragraph`, `due_date`,
`eligibility`, `budget`, `topics`, `source_url`.

Edit the `TOPICS` list at the top of `scout.py` to retune what it hunts for.

## Planned architecture (GitHub Actions)

- **Daily cron** workflow runs `scout.py`-style logic on GitHub's runners.
- **Database** = `data/opportunities.json` committed back to the repo
  (the runner is ephemeral; the repo is the persistence layer). Dedupe by URL.
- **Daily digest**: new-since-last-run + ranked top-10, delivered by **email**.
- **Website**: static page on **GitHub Pages** (note: Pages on a *private*
  repo needs a paid plan — alternatives: Netlify/Cloudflare Pages free tier,
  or publish only the built site).
- Reliability boost: pull structured sources (Grants.gov API, SAM.gov,
  EU Funding & Tenders, FEMA, RSS feeds) alongside open web search.

## Cost

Free on GitHub Actions/Pages at this scale. Anthropic API ~cents–low single
digits per daily run on Opus; less on Sonnet (`claude-sonnet-4-6`).
