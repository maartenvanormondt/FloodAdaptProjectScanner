"""
Grant Seeker — find flood-related funding opportunities.

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

# Keywords for the structured Grants.gov query.
GRANTS_GOV_KEYWORDS = ["flood", "flood resilience", "coastal resilience", "stormwater"]

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

def load_known_sources():
    """Return (display list for the prompt, learned-sources db dict)."""
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

    # Add the most-fruitful learned domains that aren't already seeded.
    seed_text = " ".join(seeds).lower()
    ranked = sorted(
        learned.get("domains", {}).items(),
        key=lambda kv: kv[1].get("count", 0),
        reverse=True,
    )
    learned_display = [
        f"{domain} (found {info.get('count', 0)}x before)"
        for domain, info in ranked[:15]
        if domain not in seed_text
    ]
    return seeds + learned_display, learned


def learn_sources(opportunities: list, learned: dict) -> None:
    """Record the domains of found opportunities so future runs check them first."""
    domains = learned.setdefault("domains", {})
    today = date.today().isoformat()
    for o in opportunities:
        netloc = urlparse(o.get("source_url", "")).netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        if not netloc:
            continue
        entry = domains.setdefault(netloc, {"count": 0, "first_seen": today})
        entry["count"] += 1
        entry["last_seen"] = today
    os.makedirs(os.path.dirname(SOURCES_DB), exist_ok=True)
    with open(SOURCES_DB, "w", encoding="utf-8") as f:
        json.dump(learned, f, indent=2, ensure_ascii=False)


# --------------------------------------------------------------- prompt

def build_prompt(known_sources: list) -> str:
    topics = "\n".join(f"  - {t}" for t in TOPICS)
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
        f"{sources_block}"
        "Use the web_search tool to find currently-open opportunities across "
        "government programs, foundations, NGOs, and research funders. Prefer "
        "opportunities whose deadline is in the future or that accept rolling "
        "submissions.\n\n"
        "Return 8-15 distinct, REAL opportunities. For each, fill in every "
        "field. Rules:\n"
        "  - Only include opportunities you actually found and can cite with a "
        "real source_url. Do NOT invent opportunities.\n"
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
            seen.add(oid)
            agency = hit.get("agencyName") or hit.get("agencyCode") or "U.S. federal agency"
            found.append({
                "title": hit.get("title", "Untitled"),
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
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY first (see the header of this file).")

    known_sources, learned = load_known_sources()
    prompt = build_prompt(known_sources)

    client = anthropic.Anthropic(max_retries=6)
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
