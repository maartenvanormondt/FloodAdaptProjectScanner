// Cloudflare Worker — shared store for Maarten's Grant Seeker.
//
// Backs two features for the static site, both keyed by per-opportunity id:
//   Verdicts (like/dislike):
//     GET  /            -> { "<oppId>": "like" | "dislike", ... }
//     POST /            -> { url: "<oppId>", verdict: "like"|"dislike"|null }
//   Comments:
//     GET  /comments        -> { "<oppId>": [ {id, name, text, ts}, ... ], ... }
//     POST /comments        -> { oppId, name?, text }  (adds; returns the comment)
//     POST /comments/delete -> { oppId, commentId }    (removes one)
//
// Open access (no key) by design. KV binding name: VERDICTS.

const VKEY = "all";        // verdicts map
const CKEY = "comments";   // comments map
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

async function readBody(request) {
  try {
    return await request.json();
  } catch {
    return null;
  }
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") return new Response(null, { headers: CORS });
    const path = new URL(request.url).pathname.replace(/\/+$/, "") || "/";

    // ---------- comments ----------
    if (path === "/comments") {
      if (request.method === "GET") {
        const data = (await env.VERDICTS.get(CKEY)) || "{}";
        return new Response(data, { headers: { ...CORS, "Content-Type": "application/json" } });
      }
      if (request.method === "POST") {
        const b = await readBody(request);
        if (!b) return json({ error: "invalid JSON" }, 400);
        const oppId = (b.oppId || "").trim();
        const text = (b.text || "").trim();
        if (!oppId || !text) return json({ error: "oppId and text required" }, 400);
        const name = (b.name || "").trim().slice(0, 80) || "Anonymous";
        const map = JSON.parse((await env.VERDICTS.get(CKEY)) || "{}");
        const comment = {
          id: crypto.randomUUID(),
          name,
          text: text.slice(0, 2000),
          ts: Date.now(),
        };
        (map[oppId] = map[oppId] || []).push(comment);
        await env.VERDICTS.put(CKEY, JSON.stringify(map));
        return json({ ok: true, comment });
      }
    }

    if (path === "/comments/delete" && request.method === "POST") {
      const b = await readBody(request);
      if (!b) return json({ error: "invalid JSON" }, 400);
      const { oppId, commentId } = b;
      if (!oppId || !commentId) return json({ error: "oppId and commentId required" }, 400);
      const map = JSON.parse((await env.VERDICTS.get(CKEY)) || "{}");
      if (map[oppId]) {
        map[oppId] = map[oppId].filter((c) => c.id !== commentId);
        if (!map[oppId].length) delete map[oppId];
        await env.VERDICTS.put(CKEY, JSON.stringify(map));
      }
      return json({ ok: true });
    }

    // ---------- verdicts (default, at "/") ----------
    if (request.method === "GET") {
      const data = (await env.VERDICTS.get(VKEY)) || "{}";
      return new Response(data, { headers: { ...CORS, "Content-Type": "application/json" } });
    }
    if (request.method === "POST") {
      const b = await readBody(request);
      if (!b) return json({ error: "invalid JSON" }, 400);
      const url = (b.url || "").trim();
      if (!url) return json({ error: "url required" }, 400);
      const map = JSON.parse((await env.VERDICTS.get(VKEY)) || "{}");
      if (b.verdict === "like" || b.verdict === "dislike") map[url] = b.verdict;
      else delete map[url];
      await env.VERDICTS.put(VKEY, JSON.stringify(map));
      return json({ ok: true });
    }

    return json({ error: "method not allowed" }, 405);
  },
};
