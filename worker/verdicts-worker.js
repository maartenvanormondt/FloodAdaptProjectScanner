// Cloudflare Worker — shared like/dislike store for Maarten's Grant Seeker.
//
// Stores a single JSON map { "<source_url>": "like" | "dislike" } in KV under
// one key, so the website and the GitHub Actions pipeline can both read it and
// everyone sees the same verdicts.
//
//   GET  /   -> returns the full verdicts map as JSON
//   POST /   -> body { "url": "...", "verdict": "like"|"dislike"|null }
//               sets (like/dislike) or clears (null) the verdict for that url
//
// Open access (no key) by design. KV binding name: VERDICTS.

const KEY = "all";
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { ...CORS, "Content-Type": "application/json" },
  });
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") return new Response(null, { headers: CORS });

    if (request.method === "GET") {
      const data = (await env.VERDICTS.get(KEY)) || "{}";
      return new Response(data, { headers: { ...CORS, "Content-Type": "application/json" } });
    }

    if (request.method === "POST") {
      let body;
      try {
        body = await request.json();
      } catch {
        return json({ error: "invalid JSON" }, 400);
      }
      const url = (body.url || "").trim();
      if (!url) return json({ error: "url required" }, 400);

      const map = JSON.parse((await env.VERDICTS.get(KEY)) || "{}");
      if (body.verdict === "like" || body.verdict === "dislike") map[url] = body.verdict;
      else delete map[url]; // null / anything else clears it
      await env.VERDICTS.put(KEY, JSON.stringify(map));
      return json({ ok: true });
    }

    return json({ error: "method not allowed" }, 405);
  },
};
