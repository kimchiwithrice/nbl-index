// Cloudflare Worker for game pages: returns a game's team list from the NBL match-centre feed.
// The feed only answers requests that come from nbl.com.au, so the page can't ask it directly.
// Deploy: Cloudflare dashboard > Workers & Pages > Create > Hello World, paste this file, Deploy.
// Then put the worker's URL in config.json as "team_list_url".
const FEED = "https://prod.rosetta.nbl.com.au/get/match/{id}/live/all";
const ORIGINS = ["https://nblindex.com", "https://www.nblindex.com"];

export default {
  async fetch(request, env, ctx) {
    const origin = request.headers.get("origin") || "";
    const headers = {
      "access-control-allow-origin": ORIGINS.includes(origin) ? origin : ORIGINS[0],
      "content-type": "application/json",
      "cache-control": "public, max-age=60",
      "vary": "origin",
    };
    const id = new URL(request.url).searchParams.get("id") || "";
    if (!/^[0-9a-f-]{36}$/.test(id)) {
      return new Response('{"error":"bad id"}', { status: 400, headers });
    }

    const cache = caches.default;
    const key = new Request("https://team-lists.cache/" + id + "?o=" + headers["access-control-allow-origin"]);
    const hit = await cache.match(key);
    if (hit) return hit;

    const resp = await fetch(FEED.replace("{id}", id), {
      headers: { accept: "*/*", origin: "https://nbl.com.au", referer: "https://nbl.com.au/" },
    });
    if (!resp.ok) {
      return new Response(JSON.stringify({ error: resp.status }), { status: 502, headers });
    }
    let data = await resp.json();
    data = data && data.data !== undefined ? data.data : data;
    if (Array.isArray(data)) data = data[0] || {};

    // Only the names: the page needs nothing else, and this keeps any scores out.
    const players = (data.player_match_statistics || [])
      .filter((r) => String(r.period ?? "0") === "0")
      .map((r) => {
        const p = r.player || r;
        return { name: [p.first_name, p.last_name || p.family_name].filter(Boolean).join(" ") };
      })
      .filter((p) => p.name);
    const out = new Response(JSON.stringify({ status: data.match_status || "", players }), { headers });
    ctx.waitUntil(cache.put(key, out.clone()));
    return out;
  },
};
