"""
Maarten's Grant Seeker — daily digest email composer / sender.

Reads recipients (RECIPIENTS env var, else recipients.yaml), opens each email
with that person's greeting and a fresh joke about flood modelling, a short
intro, a link to the web viewer, and the list of opportunities.

Emails are sent as HTML (so links show as clickable titles, not giant raw URLs)
with a plain-text fallback. Use --dry-run to print instead of sending.

Run:
    python email_digest.py --opps out.json --dry-run     # preview (no sending)
    python email_digest.py --opps out.json               # send

`out.json` is produced by:  python scout.py --json out.json
"""

import argparse
import html as html_lib
import json
import os
import smtplib
import sys
import urllib.error
import urllib.request
from email.message import EmailMessage

import anthropic
import yaml

# Same fallback chain as scout.py.
MODELS = ["claude-sonnet-4-6", "claude-opus-4-8"]

# Flavor text for the email intro (just the displayed name).
ASSISTANT_NAME = "Claude Sonnet"

# Background blurb, paragraphs separated by blank lines.
INTRO = (
    f"Your colleague Maarten asked his AI buddy {ASSISTANT_NAME} to scour the "
    "web for interesting funding leads for FloodAdapt. This is now done "
    "automatically every day through GitHub Actions.\n"
    "\n"
    "How it works: every morning a scheduled job turns Claude loose on the web "
    "to hunt for grants, RFPs, and RFIs across coastal & inland flooding, flood "
    "resilience, and flood risk mapping. It pulls out the bits that matter — "
    "deadline, eligibility, budget, and a one-paragraph summary — and adds any "
    "new ones to a growing, searchable list. No spreadsheets were harmed (and "
    "barely any humans were involved) in the making of this digest.\n"
    "\n"
    "You cannot opt out of this email chain — unless you ask Maarten nicely."
)

# Public web viewer link. Override with the SITE_URL env var.
SITE_URL = os.environ.get(
    "SITE_URL", "https://maartenvanormondt.github.io/FloodAdaptProjectScanner/"
)

esc = html_lib.escape


def load_recipients(path: str) -> list:
    # Prefer the RECIPIENTS env var (e.g. a GitHub Actions secret) so addresses
    # never live in the (public) repo. One recipient per line: "email | greeting".
    env = os.environ.get("RECIPIENTS")
    if env and env.strip():
        recipients = []
        for line in env.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            email, _, greeting = line.partition("|")
            recipients.append(
                {"email": email.strip(), "greeting": greeting.strip() or "Hello,"}
            )
        if recipients:
            return recipients

    if not os.path.exists(path):
        raise SystemExit(
            f"No recipients. Set the RECIPIENTS env var, or create {path} "
            "(copy recipients.yaml.example)."
        )
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    recipients = data.get("recipients", [])
    if not recipients:
        raise SystemExit(f"No recipients found in {path}.")
    for r in recipients:
        if "email" not in r or "greeting" not in r:
            raise SystemExit(f"Each recipient needs 'email' and 'greeting': {r}")
    return recipients


def make_joke(client: anthropic.Anthropic) -> str:
    """Generate one short, fresh joke about flood modelling."""
    prompt = (
        "Write ONE short, genuinely funny joke about flood modelling / "
        "hydrodynamic flood simulation. Fair game: SFINCS, bathymetry, "
        "boundary conditions, calibration, Manning's n, grid resolution, "
        "wet/dry cells, 'it's just water'. One or two lines, clever, "
        "office-appropriate. Return ONLY the joke text, no preamble."
    )
    for i, model in enumerate(MODELS):
        try:
            resp = client.messages.create(
                model=model, max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.APIStatusError as e:
            if (e.status_code == 429 or e.status_code >= 500) and i < len(MODELS) - 1:
                continue
            raise
        return "".join(b.text for b in resp.content if b.type == "text").strip()
    return ""


def load_opps(path: str | None) -> list:
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("opportunities", [])


# --------------------------------------------------------------- digest bodies

def _digest_html(opps: list) -> str:
    if not opps:
        return "<p>No funding opportunities to report today.</p>"
    items = []
    for o in opps:
        url = o.get("source_url", "")
        title = esc(o.get("title", "?"))
        link = f'<a href="{esc(url)}">{title}</a>' if url else title
        items.append(
            '<li style="margin-bottom:12px">'
            f"{link} — {esc(o.get('funder', '?'))}<br>"
            f"{esc(o.get('one_liner', ''))}<br>"
            '<span style="color:#5b6b7a">'
            f"<b>Due:</b> {esc(o.get('due_date', '?'))} &nbsp;|&nbsp; "
            f"<b>Budget:</b> {esc(o.get('budget', '?'))}<br>"
            f"<b>Eligibility:</b> {esc(o.get('eligibility', '?'))}</span></li>"
        )
    return "<ol>" + "".join(items) + "</ol>"


def _digest_text(opps: list) -> str:
    if not opps:
        return "No funding opportunities to report today."
    lines = []
    for i, o in enumerate(opps, 1):
        lines.append(f"{i}. {o.get('title', '?')} — {o.get('funder', '?')}")
        lines.append(f"   {o.get('one_liner', '')}")
        lines.append(f"   Due: {o.get('due_date', '?')}  |  Budget: {o.get('budget', '?')}")
        lines.append(f"   Eligibility: {o.get('eligibility', '?')}")
        lines.append(f"   {o.get('source_url', '')}")
        lines.append("")
    return "\n".join(lines)


def compose_html(greeting: str, joke: str, opps: list) -> str:
    intro_paras = "".join(f"<p>{esc(p)}</p>" for p in INTRO.split("\n\n"))
    site_p = (
        f'<p><a href="{esc(SITE_URL)}">You can view (and like / dislike) the full '
        "list of announcements here.</a></p>"
        if SITE_URL else ""
    )
    return (
        '<div style="font-family:system-ui,Segoe UI,Arial,sans-serif;'
        'font-size:15px;color:#1c2733;line-height:1.5">'
        f"<p>{esc(greeting)}</p>"
        f"<p><em>{esc(joke)}</em></p>"
        f"{intro_paras}"
        f"{site_p}"
        "<hr>"
        "<p><b>Latest flood-related funding opportunities:</b></p>"
        f"{_digest_html(opps)}"
        "<p>— Maarten's Grant Seeker</p>"
        "</div>"
    )


def compose_text(greeting: str, joke: str, opps: list) -> str:
    parts = [greeting, "", joke, "", INTRO, ""]
    if SITE_URL:
        parts += [f"View (and like/dislike) the full list here: {SITE_URL}", ""]
    parts += ["Latest flood-related funding opportunities:", "", _digest_text(opps), "— Maarten's Grant Seeker"]
    return "\n".join(parts)


# --------------------------------------------------------------- sending

def _send_via_resend(to_addr, subject, text, html, api_key, sender) -> None:
    """Send over HTTPS via the Resend API (works from corporate nets / CI)."""
    payload = json.dumps(
        {"from": sender, "to": [to_addr], "subject": subject, "text": text, "html": html}
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # Resend is behind Cloudflare, which 403s the default urllib UA.
            "User-Agent": "Mozilla/5.0 (compatible; GrantSeeker/1.0)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Resend error {e.code}: {e.read().decode('utf-8', 'replace')}")
    print(f"Sent to {to_addr} (via Resend)")


def send_email(to_addr, subject, text, html, *, dry_run, prefer_smtp=False) -> None:
    if dry_run:
        print("=" * 70)
        print(f"To: {to_addr}\nSubject: {subject}\n")
        print(text)
        print("=" * 70 + "\n")
        return

    resend_key = os.environ.get("RESEND_API_KEY")
    if resend_key and not prefer_smtp:
        sender = os.environ.get("EMAIL_FROM", "onboarding@resend.dev")
        _send_via_resend(to_addr, subject, text, html, resend_key, sender)
        return

    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASS")
    sender = os.environ.get("EMAIL_FROM", user)
    if not (host and user and password):
        raise SystemExit(
            "No sender configured. Set RESEND_API_KEY (recommended), or "
            "SMTP_HOST/SMTP_USER/SMTP_PASS — or use --dry-run to preview."
        )

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    with smtplib.SMTP(host, port) as server:
        server.starttls()
        server.login(user, password)
        server.send_message(msg)
    print(f"Sent to {to_addr}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--opps", help="path to opportunities JSON (from scout.py --json)")
    ap.add_argument("--recipients", default="recipients.yaml")
    ap.add_argument("--subject", default="Flood funding — daily digest")
    ap.add_argument("--dry-run", action="store_true", help="print emails instead of sending")
    ap.add_argument("--smtp", action="store_true", help="force SMTP even if RESEND_API_KEY is set")
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY first.")

    recipients = load_recipients(args.recipients)
    opps = load_opps(args.opps)
    client = anthropic.Anthropic(max_retries=6)

    failures = 0
    for r in recipients:
        joke = make_joke(client)
        text = compose_text(r["greeting"], joke, opps)
        html = compose_html(r["greeting"], joke, opps)
        try:
            send_email(r["email"], args.subject, text, html, dry_run=args.dry_run, prefer_smtp=args.smtp)
        except Exception as e:  # noqa: BLE001 - report and continue per recipient
            failures += 1
            print(f"Failed to send to {r['email']}: {e}", file=sys.stderr)
    if failures:
        raise SystemExit(f"{failures} of {len(recipients)} email(s) failed.")


if __name__ == "__main__":
    main()
