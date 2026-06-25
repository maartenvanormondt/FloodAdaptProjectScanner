# Shared verdicts backend (Cloudflare Worker)

Stores everyone's 👍/👎 in one shared place so the website and the daily
pipeline both see the same likes/dislikes. Free tier is far more than enough.

## One-time deploy

1. Install wrangler and log in to Cloudflare (free account):
   ```
   npm install -g wrangler
   wrangler login
   ```
2. Create the KV namespace and copy the `id` it prints:
   ```
   wrangler kv namespace create VERDICTS
   ```
3. Paste that id into `wrangler.toml` (replace `REPLACE_WITH_YOUR_KV_NAMESPACE_ID`).
4. Deploy (run from this `worker/` folder):
   ```
   wrangler deploy
   ```
   It prints your Worker URL, e.g. `https://grant-seeker-verdicts.<you>.workers.dev`.

## Wire it up (two places, same URL)

- **Website:** set `VERDICTS_API` to that URL at the top of `../docs/index.html`.
- **Pipeline:** add a GitHub repo secret named `VERDICTS_API` with the same URL
  (Settings → Secrets and variables → Actions). The daily run then drops
  rejected opportunities from the email.

Until both are set, likes/dislikes fall back to being stored per-browser
(localStorage) — nothing breaks.

## Test it

```
curl https://grant-seeker-verdicts.<you>.workers.dev          # -> {}
curl -X POST https://grant-seeker-verdicts.<you>.workers.dev \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.org/1","verdict":"like"}'        # -> {"ok":true}
curl https://grant-seeker-verdicts.<you>.workers.dev          # -> {"https://example.org/1":"like"}
```

Note: it's open (no key), so anyone with the URL can vote — fine for a small
internal team. Ask if you later want a shared key added.
