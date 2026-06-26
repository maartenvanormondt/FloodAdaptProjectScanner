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

// Kick off the GitHub Actions "answer @claude" workflow immediately when an
// @claude comment is posted, so it's answered in ~30-60s instead of waiting for
// a poll. Needs the GH_DISPATCH_TOKEN secret (and optionally GH_REPO).
function triggerClaude(env, ctx) {
  if (!env.GH_DISPATCH_TOKEN) return;
  const repo = env.GH_REPO || "maartenvanormondt/FloodAdaptProjectScanner";
  ctx.waitUntil(
    fetch(`https://api.github.com/repos/${repo}/dispatches`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GH_DISPATCH_TOKEN}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "grant-seeker-worker",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ event_type: "claude-comment" }),
    }).catch(() => {}),
  );
}

export default {
  async fetch(request, env, ctx) {
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
          text: text.slice(0, 8000),   // generous, so @claude research briefings fit
          ts: Date.now(),
        };
        (map[oppId] = map[oppId] || []).push(comment);
        await env.VERDICTS.put(CKEY, JSON.stringify(map));
        if (text.toLowerCase().startsWith("@claude")) triggerClaude(env, ctx);
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
