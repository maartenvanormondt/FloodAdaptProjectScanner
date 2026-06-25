"""
Grant Seeker — proof of concept.

Runs Claude with the built-in web-search tool to find current funding
opportunities (grants / RFPs / RFIs) in flood-related fields, and extracts a
fixed set of fields per opportunity as validated JSON.

This POC just prints what it finds. The database, dedupe, daily digest,
email, and website come later — this step exists to validate the core
(can Claude reliably find real opportunities and fill in our fields?).

Run:
    export ANTHROPIC_API_KEY=sk-ant-...        # PowerShell: $env:ANTHROPIC_API_KEY="sk-ant-..."
    python scout.py
    python scout.py --json results.json        # also dump raw JSON to a file
"""

import argparse
import json
import os
import sys

import anthropic

# Models tried in order. Sonnet first: it's much faster and cheaper than Opus
# and plenty capable for find-and-summarize, so the daily run finishes quickly.
# Opus is only the fallback if Sonnet is overloaded (HTTP 529).
MODELS = ["claude-sonnet-4-6", "claude-opus-4-8"]

# Max web searches per run. Each search is a sequential round-trip, so this is
# the main lever on how long "find opportunities" takes. Raise for more
# coverage, lower for speed.
MAX_SEARCHES = 6

# What we're hunting for. Edit freely — this is the whole topic definition.
TOPICS = [
    "coastal flooding and coastal flood risk",
    "inland / riverine / pluvial (stormwater) flooding",
    "flood resilience and flood adaptation",
    "flood risk mapping and flood modelling",
    "nature-based solutions for flood mitigation",
]

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
                    "one_liner": {
                        "type": "string",
                        "description": "One short sentence (<= ~20 words).",
                    },
                    "summary_paragraph": {
                        "type": "string",
                        "description": "One paragraph (3-5 sentences) on scope and goals.",
                    },
                    "due_date": {
                        "type": "string",
                        "description": "ISO date (YYYY-MM-DD) if known, else 'rolling' or 'unknown'.",
                    },
                    "eligibility": {
                        "type": "string",
                        "description": "Who can apply (countries, org types). 'unknown' if unclear.",
                    },
                    "budget": {
                        "type": "string",
                        "description": "Award size / total funding. 'unknown' if unstated.",
                    },
                    "topics": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Which of our focus areas this matches.",
                    },
                    "source_url": {
                        "type": "string",
                        "description": "Direct URL to the opportunity page.",
                    },
                },
                "required": [
                    "title",
                    "funder",
                    "one_liner",
                    "summary_paragraph",
                    "due_date",
                    "eligibility",
                    "budget",
                    "topics",
                    "source_url",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["opportunities"],
    "additionalProperties": False,
}

PROMPT = f"""You are a research assistant that finds OPEN funding opportunities \
(grants, RFPs, RFIs, calls for proposals) in these areas:

{chr(10).join(f"  - {t}" for t in TOPICS)}

Use the web_search tool to find currently-open opportunities. Search several \
angles: government programs (e.g. Grants.gov, FEMA, EU Funding & Tenders, \
national agencies), foundations, NGOs, and research funders. Prefer \
opportunities whose deadline is in the future or that accept rolling \
submissions.

Return 8-15 distinct, REAL opportunities. For each, fill in every field. \
Rules:
  - Only include opportunities you actually found and can cite with a real \
    source_url. Do NOT invent or guess opportunities.
  - If a field (budget, due_date, eligibility) is not stated in the source, \
    use "unknown" rather than guessing.
  - one_liner: one short sentence. summary_paragraph: 3-5 sentences.
  - topics: list which of the focus areas above each opportunity matches.
"""


def _search(client: anthropic.Anthropic, model: str) -> str:
    """Phase 1: free-form web search to gather opportunities.

    No forced JSON schema here. When structured output is combined with the
    web-search tool in one call, the model tends to skip searching and emit an
    empty result — so we let it search and write notes, then structure them in
    phase 2.
    """
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": MAX_SEARCHES}]
    messages = [{"role": "user", "content": PROMPT}]

    # Server-side web search loops on Anthropic's side; stop_reason="pause_turn"
    # means it hit the per-turn cap and we re-send to resume.
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
        # output_config via extra_body so this works on older SDK versions too.
        extra_body={
            "output_config": {
                "format": {"type": "json_schema", "schema": OPPORTUNITY_SCHEMA}
            }
        },
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


def fetch_opportunities(client: anthropic.Anthropic):
    """Search, then extract — trying each model in MODELS; fall back on overload.

    Returns (data, model_used, notes).
    """
    for i, model in enumerate(MODELS):
        try:
            notes = _search(client, model)
            data = _extract(client, model, notes)
        except anthropic.APIStatusError as e:
            transient = e.status_code == 429 or e.status_code >= 500
            if transient and i < len(MODELS) - 1:
                print(
                    f"  {model} unavailable ({e.status_code}); "
                    f"falling back to {MODELS[i + 1]}...",
                    file=sys.stderr,
                )
                continue
            if transient:
                raise SystemExit(
                    f"All models busy ({e.status_code}). Try again in a few minutes."
                )
            raise
        return data, model, notes

    raise SystemExit("No models configured.")


def print_report(data: dict) -> None:
    opps = data.get("opportunities", [])
    print(f"\n=== Found {len(opps)} opportunities ===\n")
    for i, o in enumerate(opps, 1):
        print(f"{i}. {o['title']}  —  {o['funder']}")
        print(f"   {o['one_liner']}")
        print(f"   Due: {o['due_date']}   |   Budget: {o['budget']}")
        print(f"   Eligibility: {o['eligibility']}")
        print(f"   Topics: {', '.join(o.get('topics', []))}")
        print(f"   {o['source_url']}")
        print(f"   {o['summary_paragraph']}\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", metavar="PATH", help="also write raw JSON here")
    ap.add_argument(
        "--debug", action="store_true", help="print the raw phase-1 search notes"
    )
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY first (see the header of this file).")

    # max_retries: the SDK auto-retries 429/5xx/529 with exponential backoff,
    # which smooths over transient "overloaded" blips before we fall back.
    client = anthropic.Anthropic(max_retries=6)
    data, model_used, notes = fetch_opportunities(client)
    print(f"(answered by {model_used})")
    if args.debug:
        print("\n--- search notes ---\n" + notes + "\n--- end notes ---\n", file=sys.stderr)
    print_report(data)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"Wrote {args.json}")


if __name__ == "__main__":
    main()
