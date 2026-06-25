"""
Grant Seeker — accumulate opportunities and build the website data.

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
import json
import os
from datetime import date

DB_PATH = os.path.join("data", "opportunities_db.json")
SITE_DATA = os.path.join("docs", "opportunities.js")


def _norm(url: str) -> str:
    return (url or "").strip().rstrip("/").lower()


def load_db() -> dict:
    if os.path.exists(DB_PATH):
        with open(DB_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"opportunities": []}


def save_db(db: dict) -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)


def merge(db: dict, fresh_path: str) -> int:
    """Add opportunities from fresh_path that aren't already in the DB."""
    with open(fresh_path, encoding="utf-8") as f:
        fresh = json.load(f)
    seen = {_norm(o.get("source_url")) for o in db["opportunities"]}
    today = date.today().isoformat()
    added = 0
    for o in fresh.get("opportunities", []):
        key = _norm(o.get("source_url"))
        if not key or key in seen:
            continue
        record = dict(o)
        record["first_seen"] = today
        db["opportunities"].append(record)
        seen.add(key)
        added += 1
    return added


def write_site(db: dict) -> None:
    os.makedirs(os.path.dirname(SITE_DATA), exist_ok=True)
    # Newest-first by the date we first saw each lead.
    opps = sorted(
        db["opportunities"], key=lambda o: o.get("first_seen", ""), reverse=True
    )
    # Embedded as a JS global so the page works from file:// without a server.
    with open(SITE_DATA, "w", encoding="utf-8") as f:
        f.write("window.OPPORTUNITIES = ")
        json.dump(opps, f, ensure_ascii=False)
        f.write(";\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--merge", metavar="PATH", help="merge a fresh scout JSON into the DB")
    args = ap.parse_args()

    db = load_db()
    if args.merge:
        added = merge(db, args.merge)
        save_db(db)
        print(f"Merged {args.merge}: {added} new, {len(db['opportunities'])} total.")
    write_site(db)
    print(f"Wrote {SITE_DATA} ({len(db['opportunities'])} opportunities).")


if __name__ == "__main__":
    main()
