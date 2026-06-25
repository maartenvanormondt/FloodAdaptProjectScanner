"""
Maarten's Grant Seeker — accumulate opportunities and build the website data.

Keeps a deduped database at data/opportunities_db.json (the source of truth,
growing over time) and regenerates docs/opportunities.js, which the website
(docs/index.html) loads. Dedupe key is the source_url; the date a lead first
appeared is stored as `first_seen`.

Run:
    python build_site.py --merge out.json   # merge a fresh scout run, rebuild
    python build_site.py                      # just rebuild the site from the DB

Typical flow:
    python scout.py --json out.json
    python build_site.py --merge out.json
    # then open docs/index.html (locally) or commit docs/ for GitHub Pages
"""

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date

DB_PATH = os.path.join("data", "opportunities_db.json")
SITE_DATA = os.path.join("docs", "opportunities.js")
NEW_PATH = os.path.join("data", "new.json")  # only the leads added this run (for the email)

# Optional shared verdicts store (Cloudflare Worker URL). When set, rejected
# opportunities are dropped from the email's new list.
VERDICTS_API = os.environ.get("VERDICTS_API", "")


def _norm(url: str) -> str:
    return (url or "").strip().rstrip("/").lower()


def opp_id(o: dict) -> str:
    """Stable per-opportunity id (title+funder) — the verdict key, so a generic
    source_url shared by several leads doesn't make them share a verdict."""
    base = (o.get("title", "") + "|" + o.get("funder", "")).strip().lower()
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]


def classify_region(o: dict) -> str:
    """Best-effort country/region from the funder + URL (for the site filter)."""
    url = o.get("source_url", "").lower()
    text = (o.get("funder", "") + " " + o.get("eligibility", "") + " " + o.get("title", "")).lower()
    blob = url + " " + text

    def has(*subs):
        return any(s in blob for s in subs)

    if has("worldbank", "world bank", "gfdrr", "greenclimate", "green climate",
           "adaptation-fund", "adaptation fund"):
        return "International / Global"
    if has("adb.org", "asian development", "c40", "uccrtf") or "asia" in text:
        return "Asia / Pacific"
    if has(".gc.ca", "infrastructure.gc.ca", "greenmunicipalfund", "fcm.ca") \
            or "canada" in text or "canadian" in text:
        return "Canada"
    if has("europa.eu", "horizon europe", "interreg", "ukri", ".gov.uk", ".ac.uk",
           "epsrc", "rvo.nl", "europe"):
        return "Europe"
    if has(".gov", "grants.gov", "fema", "noaa", "epa", "nsf", "floridadep",
           "lawatershed", "stormrecovery.ny", "scc.ca.gov", "recovery.texas",
           "united states", "u.s.", "us federal"):
        return "United States"
    return "Other / Unspecified"


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


def merge(db: dict, fresh_path: str) -> list:
    """Add opportunities from fresh_path that aren't already in the DB.

    Returns the list of newly-added records (so the email can show only those).
    """
    with open(fresh_path, encoding="utf-8") as f:
        fresh = json.load(f)
    seen = {_norm(o.get("source_url")) for o in db["opportunities"]}
    today = date.today().isoformat()
    new_records = []
    for o in fresh.get("opportunities", []):
        key = _norm(o.get("source_url"))
        if not key or key in seen:
            continue
        record = dict(o)
        record["first_seen"] = today
        db["opportunities"].append(record)
        seen.add(key)
        new_records.append(record)
    return new_records


def write_site(db: dict) -> None:
    os.makedirs(os.path.dirname(SITE_DATA), exist_ok=True)
    # Newest-first by the date we first saw each lead; tag each with a region.
    opps = sorted(
        db["opportunities"], key=lambda o: o.get("first_seen", ""), reverse=True
    )
    enriched = [{**o, "region": classify_region(o), "id": opp_id(o)} for o in opps]
    # Embedded as a JS global so the page works from file:// without a server.
    with open(SITE_DATA, "w", encoding="utf-8") as f:
        f.write("window.OPPORTUNITIES = ")
        json.dump(enriched, f, ensure_ascii=False)
        f.write(";\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--merge", metavar="PATH", help="merge a fresh scout JSON into the DB")
    args = ap.parse_args()

    db = load_db()
    if args.merge:
        new_records = merge(db, args.merge)
        save_db(db)
        # Write only the new-this-run leads (minus any already rejected) for the
        # email digest to send.
        rejected = {k for k, v in fetch_verdicts().items() if v == "dislike"}
        emailable = [o for o in new_records if opp_id(o) not in rejected]
        os.makedirs(os.path.dirname(NEW_PATH), exist_ok=True)
        with open(NEW_PATH, "w", encoding="utf-8") as f:
            json.dump({"opportunities": emailable}, f, indent=2, ensure_ascii=False)
        print(
            f"Merged {args.merge}: {len(new_records)} new "
            f"({len(emailable)} after removing rejected), "
            f"{len(db['opportunities'])} total. Wrote {NEW_PATH}."
        )
    write_site(db)
    print(f"Wrote {SITE_DATA} ({len(db['opportunities'])} opportunities).")


if __name__ == "__main__":
    main()
