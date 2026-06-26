"""
Maarten's Grant Seeker — find flood-related funding opportunities.

Merges opportunities from two kinds of sources:
  1. Structured APIs (Grants.gov) — deterministic, no LLM needed.
  2. Open web search via Claude — for everything else, including NEW sources.

It also keeps a growing memory of which sources have been fruitful
(data/sources_db.json), seeded from sources.yaml, and feeds that back into the
search prompt so it checks known places first while still hunting for new ones.

Run:
    python scout.py
    python scout.py --json out.json     # also write the merged result to a file
    python scout.py --debug             # print the raw search notes
    python scout.py --no-api            # skip the Grants.gov pull (web search only)
"""

import argparse
import hashlib
import html
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date
from urllib.parse import urlparse

import anthropic
import yaml

# Models tried in order. Sonnet first: it's much faster and cheaper than Opus
# and plenty capable for find-and-summarize. Opus is the overload fallback.
MODELS = ["claude-sonnet-4-6", "claude-opus-4-8"]

# Max web searches per run — the main lever on how long the run takes.
MAX_SEARCHES = 6

# What we're hunting for. Edit freely — this is the whole topic definition.
TOPICS = [
    "coastal flooding and coastal flood risk",
    "inland / riverine / pluvial (stormwater) flooding",
    "flood resilience and flood adaptation",
    "flood risk mapping and flood modelling",
    "nature-based solutions for flood mitigation",
]

SOURCES_FILE = "sources.yaml"                      # seed list of known places
SOURCES_DB = os.path.join("data", "sources_db.json")  # learned-sources memory
DB_PATH = os.path.join("data", "opportunities_db.json")  # accumulated leads (for verdict lookup)

# Shared verdicts store (Cloudflare Worker URL). When set, the agent learns from
# what the team marked promising / rejected and steers future searches.
VERDICTS_API = os.environ.get("VERDICTS_API", "")
PROCESSED_FILE = os.path.join("data", "processed_comments.json")  # @claude directives already handled
GUIDANCE_FILE = os.path.join("data", "guidance.json")  # standing search-guidance rules from @claude
MAX_GUIDANCE = 30  # keep the most recent N rules so the prompt doesn't grow forever

# Safe action set for "@claude ..." comment directives.
DIRECTIVE_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["hide", "unhide", "guidance", "research", "ignore"]},
        "guidance": {"type": "string", "description": "search instruction if action=guidance, else ''"},
        "reply": {"type": "string", "description": "one short sentence to post back"},
    },
    "required": ["action", "guidance", "reply"],
    "additionalProperties": False,
}

# Who we are — used to assess eligibility in "@claude, tell me more". Edit for accuracy.
FLOODADAPT_CONTEXT = (
    "FloodAdapt is a free, open-source flood-adaptation decision-support tool "
    "developed by Deltares (an independent Dutch research institute). It lets "
    "local and regional governments, water authorities, and coastal communities "
    "rapidly assess flood risk and compare adaptation strategies. 'FloodAdapt "
    "subscribers' are the organisations and practitioners who use it — typically "
    "US and international municipalities, counties, and water/coastal agencies. "
    "When judging eligibility, consider both Deltares (a research institute, often "
    "a partner or sub-awardee) and these end-user organisations."
)

# Keywords for the structured Grants.gov query.
GRANTS_GOV_KEYWORDS = ["flood", "flood resilience", "coastal resilience", "stormwater"]

# Only keep Grants.gov hits whose TITLE actually looks flood/water-related — the
# keyword API returns many loosely-matched, unrelated federal grants otherwise.
GRANTS_GOV_TITLE_TERMS = (
    "flood", "coast", "storm", "water", "resilien", "watershed", "mitigation",
    "hazard", "shorel", "levee", "drain", "wetland", "sea level", "erosion",
    "river", "restor", "nature-based",
)

# JSON Schema for structured outputs — guarantees every record has our fields.
OPPORTUNITY_SCHEMA = {
    "type": "object",
    "properties": {
        "opportunities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "funder": {"type": "string"},
                    "one_liner": {"type": "string", "description": "One short sentence."},
                    "summary_paragraph": {"type": "string", "description": "3-5 sentences."},
                    "due_date": {"type": "string", "description": "YYYY-MM-DD, 'rolling', or 'unknown'."},
                    "eligibility": {"type": "string", "description": "Who can apply; 'unknown' if unclear."},
                    "budget": {"type": "string", "description": "Award size; 'unknown' if unstated."},
                    "topics": {"type": "array", "items": {"type": "string"}},
                    "source_url": {"type": "string", "description": "Direct URL to the opportunity."},
                },
                "required": [
                    "title", "funder", "one_liner", "summary_paragraph",
                    "due_date", "eligibility", "budget", "topics", "source_url",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["opportunities"],
    "additionalProperties": False,
}


# --------------------------------------------------------------- source memory

def load_known_sources(demote: set | None = None):
    """Return (display list for the prompt, learned-sources db dict).

    Domains in `demote` (mostly-rejected) are not promoted as fruitful.
    """
    demote = demote or set()
    seeds = []
    if os.path.exists(SOURCES_FILE):
        with open(SOURCES_FILE, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        for s in data.get("sources", []):
            name, url = s.get("name", ""), s.get("url", "")
            seeds.append(f"{name} ({url})".strip())

    learned = {"domains": {}}
    if os.path.exists(SOURCES_DB):
        with open(SOURCES_DB, encoding="utf-8") as f:
            learned = json.load(f)

    # Add the most-fruitful learned domains that aren't already seeded or demoted.
    seed_text = " ".join(seeds).lower()
    ranked = sorted(
        learned.get("domains", {}).items(),
        key=lambda kv: kv[1].get("count", 0),
        reverse=True,
    )
    learned_display = [
        f"{domain} (found {info.get('count', 0)}x before)"
        for domain, info in ranked[:15]
        if domain not in seed_text and domain not in demote
    ]
    return seeds + learned_display, learned


def _domain(url: str) -> str:
    netloc = urlparse(url or "").netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


def opp_id(o: dict) -> str:
    """Stable per-opportunity id (title+funder) — must match build_site.opp_id."""
    base = (o.get("title", "") + "|" + o.get("funder", "")).strip().lower()
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]


def learn_sources(opportunities: list, learned: dict) -> None:
    """Record the domains of found opportunities so future runs check them first."""
    domains = learned.setdefault("domains", {})
    today = date.today().isoformat()
    for o in opportunities:
        netloc = _domain(o.get("source_url", ""))
        if not netloc:
            continue
        entry = domains.setdefault(netloc, {"count": 0, "first_seen": today})
        entry["count"] += 1
        entry["last_seen"] = today
    os.makedirs(os.path.dirname(SOURCES_DB), exist_ok=True)
    with open(SOURCES_DB, "w", encoding="utf-8") as f:
        json.dump(learned, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------- learning from verdicts

def fetch_verdicts() -> dict:
    """Fetch the shared {url: like/dislike} map from the Worker, if configured."""
    if not VERDICTS_API:
        return {}
    try:
        req = urllib.request.Request(VERDICTS_API, headers={"User-Agent": "GrantSeeker/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except Exception as e:  # noqa: BLE001 - degrade gracefully
        print(f"  verdicts fetch failed: {e}", file=sys.stderr)
        return {}


def load_db() -> dict:
    if os.path.exists(DB_PATH):
        with open(DB_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"opportunities": []}


def save_db(db: dict) -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)


def load_db_opps() -> list:
    return load_db().get("opportunities", [])


# ------------------------------------------- standing search-guidance "rules"

def load_guidance() -> list:
    """Return the list of standing search-guidance rule texts."""
    if not os.path.exists(GUIDANCE_FILE):
        return []
    with open(GUIDANCE_FILE, encoding="utf-8") as f:
        return [r.get("text", "") for r in json.load(f).get("rules", []) if r.get("text")]


def add_guidance(text: str, source: str) -> None:
    """Append a standing guidance rule (kept to the most recent MAX_GUIDANCE)."""
    rules = []
    if os.path.exists(GUIDANCE_FILE):
        with open(GUIDANCE_FILE, encoding="utf-8") as f:
            rules = json.load(f).get("rules", [])
    rules.append({"text": text, "added": date.today().isoformat(), "from": source})
    rules = rules[-MAX_GUIDANCE:]
    os.makedirs(os.path.dirname(GUIDANCE_FILE), exist_ok=True)
    with open(GUIDANCE_FILE, "w", encoding="utf-8") as f:
        json.dump({"rules": rules}, f, indent=2, ensure_ascii=False)


# -------------------------------------------------- @claude comment directives

def fetch_comments() -> dict:
    """Fetch the shared {oppId: [comment, ...]} map from the Worker."""
    if not VERDICTS_API:
        return {}
    try:
        req = urllib.request.Request(
            VERDICTS_API + "/comments", headers={"User-Agent": "GrantSeeker/1.0"}
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except Exception as e:  # noqa: BLE001
        print(f"  comments fetch failed: {e}", file=sys.stderr)
        return {}


def post_reply(opp_key: str, text: str) -> None:
    """Post a confirmation reply comment as Claude."""
    if not VERDICTS_API or not text:
        return
    try:
        body = json.dumps({"oppId": opp_key, "name": "Claude 🤖", "text": text}).encode("utf-8")
        req = urllib.request.Request(
            VERDICTS_API + "/comments", data=body, method="POST",
            headers={"Content-Type": "application/json", "User-Agent": "GrantSeeker/1.0"},
        )
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as e:  # noqa: BLE001
        print(f"  reply post failed: {e}", file=sys.stderr)


def interpret_directive(client: anthropic.Anthropic, instruction: str, opp: dict | None) -> dict:
    """Map one '@claude ...' instruction to a safe structured action."""
    title = (opp or {}).get("title", "(unknown opportunity)")
    funder = (opp or {}).get("funder", "")
    prompt = (
        "You are the agent behind a flood-funding scanner. A user left this "
        f"instruction addressed to you on the opportunity \"{title}\" ({funder}). "
        "Choose exactly ONE action:\n"
        "- hide: remove/hide THIS entry from the website.\n"
        "- unhide: show a previously hidden entry again.\n"
        "- guidance: steer FUTURE searches. Put a concise, self-contained search "
        "instruction in `guidance` (fold in useful context from this opportunity, "
        "e.g. its topic or region).\n"
        "- research: the user wants a deeper briefing on THIS opportunity — more "
        "detail, what's been funded before, and whether we're eligible (e.g. "
        "'tell me more', 'who else applied', 'are we eligible?').\n"
        "- ignore: not an actionable instruction.\n"
        "Write a short friendly `reply` (one sentence) confirming what you did. "
        "Leave `guidance` empty unless the action is guidance.\n\n"
        f"Instruction: {instruction}"
    )
    for i, model in enumerate(MODELS):
        try:
            resp = client.messages.create(
                model=model, max_tokens=500,
                extra_body={"output_config": {"format": {"type": "json_schema", "schema": DIRECTIVE_SCHEMA}}},
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.APIStatusError as e:
            if (e.status_code == 429 or e.status_code >= 500) and i < len(MODELS) - 1:
                continue
            raise
        txt = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            return json.loads(txt)
        except json.JSONDecodeError:
            return {"action": "ignore", "guidance": "", "reply": ""}
    return {"action": "ignore", "guidance": "", "reply": ""}


def research_opportunity(client: anthropic.Anthropic, opp: dict, instruction: str) -> str:
    """Deep-dive one opportunity via web search; return a plain-text briefing."""
    title = opp.get("title", "?")
    funder = opp.get("funder", "?")
    url = opp.get("source_url", "")
    extra = ""
    if instruction and instruction.lower() not in ("tell me more", "more", "details", "detail"):
        extra = f"\nAlso specifically address: {instruction}\n"
    prompt = (
        "Research this flood-related funding opportunity in depth using web "
        "search, then write a concise briefing as plain text suitable for a "
        "comment (short labelled lines, no markdown headings).\n\n"
        f"Opportunity: {title}\nFunder: {funder}\nURL: {url}\n\n"
        f"Who we are:\n{FLOODADAPT_CONTEXT}\n\n"
        "Cover, as far as you can verify:\n"
        "1. Detail — scope, goals, funding amount, timeline, key dates.\n"
        "2. Track record — examples of previously funded projects or typical awardees.\n"
        "3. Eligibility for us — could Deltares / FloodAdapt and FloodAdapt "
        "subscribers be eligible? How to position or partner, and any blockers "
        "(e.g. nationality/geography limits)?"
        f"{extra}\n"
        "Be accurate; if something can't be verified, say so. Start with a "
        "one-line summary. Keep it under ~400 words."
    )
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}]
    for i, model in enumerate(MODELS):
        messages = [{"role": "user", "content": prompt}]
        try:
            for _ in range(6):
                resp = client.messages.create(
                    model=model, max_tokens=2000, tools=tools, messages=messages
                )
                if resp.stop_reason == "pause_turn":
                    messages.append({"role": "assistant", "content": resp.content})
                    continue
                break
        except anthropic.APIStatusError as e:
            if (e.status_code == 429 or e.status_code >= 500) and i < len(MODELS) - 1:
                continue
            raise
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        return text or "I couldn't find additional detail on this one."
    return "I couldn't research this right now — try again later."


def process_directives(client: anthropic.Anthropic) -> None:
    """Read new '@claude ...' comments, act on them, and reply. Updates the DB
    (hide/unhide) and the standing guidance file; marks each directive done."""
    comments = fetch_comments()
    if not comments:
        return
    processed = set()
    if os.path.exists(PROCESSED_FILE):
        with open(PROCESSED_FILE, encoding="utf-8") as f:
            processed = set(json.load(f))

    db = load_db()
    by_id = {opp_id(o): o for o in db["opportunities"]}
    db_changed = False
    newly_done = []

    for opp_key, clist in comments.items():
        for c in clist:
            cid, text = c.get("id"), (c.get("text") or "")
            stripped = text.lstrip()
            if not cid or cid in processed or stripped[:7].lower() != "@claude":
                continue
            instruction = stripped[7:].lstrip(" ,:") or stripped
            opp = by_id.get(opp_key)
            action = interpret_directive(client, instruction, opp)
            act = action.get("action", "ignore")
            reply = action.get("reply", "")
            if act == "hide" and opp is not None:
                opp["hidden"] = True
                db_changed = True
            elif act == "unhide" and opp is not None:
                opp["hidden"] = False
                db_changed = True
            elif act == "guidance" and action.get("guidance"):
                add_guidance(action["guidance"], f"comment {cid}")
            elif act == "research" and opp is not None:
                reply = research_opportunity(client, opp, instruction)  # the briefing IS the reply
            post_reply(opp_key, reply)
            newly_done.append(cid)
            print(f"  @claude: {act} — {instruction[:60]}", file=sys.stderr)

    if db_changed:
        save_db(db)
    if newly_done:
        processed |= set(newly_done)
        os.makedirs(os.path.dirname(PROCESSED_FILE), exist_ok=True)
        with open(PROCESSED_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(processed), f, indent=2)


def build_preferences(verdicts: dict, db_opps: list, limit: int = 15):
    """Turn shared verdicts (keyed by opp_id) into (liked, rejected) label lists."""
    by_id = {opp_id(o): o for o in db_opps}
    liked, rejected = [], []
    for key, v in verdicts.items():
        o = by_id.get(key)
        if not o:
            continue
        label = f"{o.get('title', '?')} — {o.get('funder', '?')}"
        if v == "like":
            liked.append(label)
        elif v == "dislike":
            rejected.append(label)
    return liked[:limit], rejected[:limit]


def rejected_domains(verdicts: dict, db_opps: list) -> set:
    """Domains that mostly produce rejected leads, to stop promoting as 'fruitful'."""
    by_id = {opp_id(o): o for o in db_opps}
    likes, dislikes = {}, {}
    for key, v in verdicts.items():
        o = by_id.get(key)
        if not o:
            continue
        d = _domain(o.get("source_url", ""))
        if not d:
            continue
        if v == "like":
            likes[d] = likes.get(d, 0) + 1
        elif v == "dislike":
            dislikes[d] = dislikes.get(d, 0) + 1
    return {d for d, n in dislikes.items() if n >= 2 and n > likes.get(d, 0)}


# --------------------------------------------------------------- prompt

# Geographic spread to actively pursue, so results aren't dominated by US
# federal grants. Edit freely.
GEOGRAPHIES = [
    "US STATE, regional, and city/local programs — especially Florida (e.g. the "
    "Resilient Florida Program), and other coastal states (Louisiana, Texas, the "
    "Carolinas, California, New York) and their cities",
    "Canadian federal, provincial, and municipal programs (e.g. Infrastructure "
    "Canada's Disaster Mitigation and Adaptation Fund, the FCM Green Municipal Fund)",
    "International development funders and city-resilience programs: World Bank / "
    "GFDRR, the Asian Development Bank (ADB), Green Climate Fund, Adaptation Fund, "
    "and climate-resilience funding for Asian cities",
]


def build_prompt(known_sources: list, liked=None, rejected=None, demote=None) -> str:
    topics = "\n".join(f"  - {t}" for t in TOPICS)
    geo = "\n".join(f"  - {g}" for g in GEOGRAPHIES)

    # Preference feedback learned from the team's likes/dislikes + @claude rules.
    pref = ""
    rules = load_guidance()
    if rules:
        pref += (
            "STANDING INSTRUCTIONS from the team (follow these every run):\n"
            + "\n".join(f"  * {r}" for r in rules) + "\n\n"
        )
    if liked:
        pref += (
            "The team marked these PROMISING — prioritise opportunities similar in "
            "topic, funder, or geography:\n"
            + "\n".join(f"  + {x}" for x in liked) + "\n\n"
        )
    if rejected:
        pref += (
            "The team REJECTED these — avoid surfacing near-duplicates or very "
            "similar ones (a clearly different opportunity from the same funder is "
            "still fine):\n"
            + "\n".join(f"  - {x}" for x in rejected) + "\n\n"
        )
    if demote:
        pref += (
            "Be more selective with these sources — their results are often "
            f"rejected: {', '.join(sorted(demote))}.\n\n"
        )

    sources_block = ""
    if known_sources:
        src = "\n".join(f"  - {s}" for s in known_sources)
        sources_block = (
            "Start by checking these known, often-fruitful sources, then ALSO "
            "search the open web for NEW opportunities and NEW sources that are "
            f"not in this list:\n{src}\n\n"
        )
    return (
        "You are a research assistant that finds OPEN funding opportunities "
        "(grants, RFPs, RFIs, calls for proposals) in these areas:\n\n"
        f"{topics}\n\n"
        "Cast a WIDE geographic net — do NOT focus mainly on US federal grants "
        "(US federal opportunities are already covered by a separate source, so "
        "spend your searches on everything else). Actively look for:\n"
        f"{geo}\n\n"
        f"{pref}"
        f"{sources_block}"
        "Use the web_search tool to find currently-open opportunities across "
        "national, state/provincial, and local government programs, international "
        "development banks, foundations, NGOs, and research funders. Prefer "
        "opportunities whose deadline is in the future or that accept rolling "
        "submissions.\n\n"
        "Return 10-20 distinct, REAL opportunities with a good MIX of geographies "
        "(not all from one country or level of government). For each, fill in "
        "every field. Rules:\n"
        "  - Only include opportunities you actually found and can cite with a "
        "real source_url. Do NOT invent opportunities.\n"
        "  - source_url must link to the SPECIFIC opportunity / call page, not a "
        "generic homepage or grants-listing page.\n"
        "  - If a field (budget, due_date, eligibility) is not stated, use "
        '"unknown" rather than guessing.\n'
        "  - one_liner: one short sentence. summary_paragraph: 3-5 sentences.\n"
        "  - topics: which of the focus areas above each opportunity matches."
    )


# --------------------------------------------------------------- web search (Claude)

def _search(client: anthropic.Anthropic, model: str, prompt: str) -> str:
    """Phase 1: free-form web search to gather opportunities (no forced schema)."""
    # Basic web search — no code-execution/dynamic-filtering, so no container_id
    # round-trip is needed on pause_turn continuations (which 400s in CI).
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": MAX_SEARCHES}]
    messages = [{"role": "user", "content": prompt}]
    for _ in range(6):
        resp = client.messages.create(
            model=model, max_tokens=16000, tools=tools, messages=messages
        )
        if resp.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": resp.content})
            continue
        break
    if resp.stop_reason == "refusal":
        raise SystemExit(f"Model refused: {getattr(resp, 'stop_details', None)}")
    return "".join(b.text for b in resp.content if b.type == "text").strip()


def _extract(client: anthropic.Anthropic, model: str, notes: str) -> dict:
    """Phase 2: turn the research notes into structured JSON (no tools)."""
    prompt = (
        "From the research notes below, extract every funding opportunity into "
        "the required JSON schema. Include only opportunities that have a real "
        'source_url. Use "unknown" for any field the notes do not state.\n\n'
        f"RESEARCH NOTES:\n{notes}"
    )
    resp = client.messages.create(
        model=model,
        max_tokens=16000,
        extra_body={"output_config": {"format": {"type": "json_schema", "schema": OPPORTUNITY_SCHEMA}}},
        messages=[{"role": "user", "content": prompt}],
    )
    text = next((b.text for b in resp.content if b.type == "text"), None)
    if text is None:
        raise SystemExit("No text block in extraction response.")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        print("Could not parse JSON. Raw model output:\n", text, file=sys.stderr)
        raise


def fetch_opportunities(client: anthropic.Anthropic, prompt: str):
    """Search, then extract — trying each model; fall back on overload.

    Returns (data, model_used, notes).
    """
    for i, model in enumerate(MODELS):
        try:
            notes = _search(client, model, prompt)
            data = _extract(client, model, notes)
        except anthropic.APIStatusError as e:
            transient = e.status_code == 429 or e.status_code >= 500
            if transient and i < len(MODELS) - 1:
                print(f"  {model} unavailable ({e.status_code}); falling back to {MODELS[i + 1]}...", file=sys.stderr)
                continue
            if transient:
                raise SystemExit(f"All models busy ({e.status_code}). Try again in a few minutes.")
            raise
        return data, model, notes
    raise SystemExit("No models configured.")


# --------------------------------------------------------------- Grants.gov API

def fetch_grants_gov() -> list:
    """Query the Grants.gov search2 API directly (structured, no LLM).

    Best-effort: on any error it logs and returns [] so the run still succeeds
    with the web-search results.
    """
    found, seen = [], set()
    for keyword in GRANTS_GOV_KEYWORDS:
        try:
            body = json.dumps(
                {"keyword": keyword, "oppStatuses": "forecasted|posted", "rows": 50}
            ).encode("utf-8")
            req = urllib.request.Request(
                "https://api.grants.gov/v1/api/search2",
                data=body,
                method="POST",
                headers={"Content-Type": "application/json", "User-Agent": "GrantSeeker/1.0"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.load(resp)
        except Exception as e:  # noqa: BLE001 - degrade gracefully, keep web results
            print(f"  Grants.gov '{keyword}' failed: {e}", file=sys.stderr)
            continue

        for hit in payload.get("data", {}).get("oppHits", []):
            oid = str(hit.get("id", ""))
            if not oid or oid in seen:
                continue
            title = html.unescape(hit.get("title", ""))
            if not any(term in title.lower() for term in GRANTS_GOV_TITLE_TERMS):
                continue  # skip federal grants whose title isn't flood/water-related
            seen.add(oid)
            agency = html.unescape(
                hit.get("agencyName") or hit.get("agencyCode") or "U.S. federal agency"
            )
            found.append({
                "title": title or "Untitled",
                "funder": agency,
                "one_liner": f"Grants.gov opportunity from {agency}.",
                "summary_paragraph": (
                    f"Posted on Grants.gov by {agency} (opportunity "
                    f"{hit.get('number', oid)}). See the source page for full scope, "
                    "eligibility, funding, and application details."
                ),
                "due_date": hit.get("closeDate") or "unknown",
                "eligibility": "See source (US federal grant)",
                "budget": "unknown",
                "topics": [f"matched '{keyword}'"],
                "source_url": f"https://www.grants.gov/search-results-detail/{oid}",
            })
    if found:
        print(f"  Grants.gov: {len(found)} opportunities.", file=sys.stderr)
    return found


# --------------------------------------------------------------- helpers

def _norm(url: str) -> str:
    return (url or "").strip().rstrip("/").lower()


def dedupe(opps: list) -> list:
    out, seen = [], set()
    for o in opps:
        key = _norm(o.get("source_url"))
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(o)
    return out


def print_report(data: dict) -> None:
    opps = data.get("opportunities", [])
    print(f"\n=== Found {len(opps)} opportunities ===\n")
    for i, o in enumerate(opps, 1):
        print(f"{i}. {o.get('title', '?')}  —  {o.get('funder', '?')}")
        print(f"   {o.get('one_liner', '')}")
        print(f"   Due: {o.get('due_date', '?')}   |   Budget: {o.get('budget', '?')}")
        print(f"   {o.get('source_url', '')}\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", metavar="PATH", help="also write the merged JSON here")
    ap.add_argument("--debug", action="store_true", help="print the raw search notes")
    ap.add_argument("--no-api", action="store_true", help="skip the Grants.gov pull")
    ap.add_argument(
        "--directives-only", action="store_true",
        help="only act on @claude comments, then exit (no search/email)",
    )
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY first (see the header of this file).")

    client = anthropic.Anthropic(max_retries=6)

    # Act on any new "@claude ..." comment directives first: hide/unhide entries,
    # append standing search-guidance rules, and post research briefings.
    process_directives(client)
    if args.directives_only:
        return  # quick mode for the frequent "answer @claude" workflow

    # Learn from the team's shared verdicts: steer toward promising, away from
    # rejected, and stop promoting sources that mostly get rejected.
    verdicts = fetch_verdicts()
    db_opps = load_db_opps()
    liked, rejected = build_preferences(verdicts, db_opps)
    demote = rejected_domains(verdicts, db_opps)
    if liked or rejected:
        print(f"(learning from {len(liked)} liked / {len(rejected)} rejected)", file=sys.stderr)

    known_sources, learned = load_known_sources(demote=demote)
    prompt = build_prompt(known_sources, liked=liked, rejected=rejected, demote=demote)

    web_data, model_used, notes = fetch_opportunities(client, prompt)
    print(f"(answered by {model_used})")
    if args.debug:
        print("\n--- search notes ---\n" + notes + "\n--- end notes ---\n", file=sys.stderr)

    opps = list(web_data.get("opportunities", []))
    if not args.no_api:
        opps += fetch_grants_gov()
    opps = dedupe(opps)

    learn_sources(opps, learned)          # grow the source memory
    data = {"opportunities": opps}
    print_report(data)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"Wrote {args.json}")


if __name__ == "__main__":
    main()
