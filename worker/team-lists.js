// Cloudflare Worker for game pages: returns a game's team list from the NBL match-centre feed.
// The feed only answers requests that come from nbl.com.au, so the page can't ask it directly.
// Deploy: Cloudflare dashboard > Workers & Pages > Create > Hello World, paste this file, Deploy.
// Then put the worker's URL in config.json as "team_list_url".
//
// Kept light on the feed: one shared answer per game for 2 minutes, and nothing is passed on to the
// feed for games more than 2 hours from tip (that answer is kept for 15 minutes).
const FEED = "https://prod.rosetta.nbl.com.au/get/match/{id}/live/all";
const ORIGINS = ["https://nblindex.com", "https://www.nblindex.com"];
const WINDOW = 2 * 3600 * 1000;

export default {
  async fetch(request, env, ctx) {
    const origin = request.headers.get("origin") || "";
    const cors = ORIGINS.includes(origin) ? origin : ORIGINS[0];
    const reply = (body, status, maxAge) => new Response(body, {
      status,
      headers: {
        "access-control-allow-origin": cors,
        "content-type": "application/json",
        "cache-control": "public, max-age=" + maxAge,
        "vary": "origin",
      },
    });

    const id = new URL(request.url).searchParams.get("id") || "";
    if (!/^[0-9a-f-]{36}$/.test(id)) return reply('{"error":"bad id"}', 400, 3600);

    const cache = caches.default;
    const key = new Request("https://team-lists.cache/" + id);
    const hit = await cache.match(key);
    if (hit) return reply(await hit.text(), 200, 120);

    const resp = await fetch(FEED.replace("{id}", id), {
      headers: { accept: "*/*", origin: "https://nbl.com.au", referer: "https://nbl.com.au/" },
    });
    if (!resp.ok) return reply(JSON.stringify({ error: resp.status }), 502, 60);
    let data = await resp.json();
    data = data && data.data !== undefined ? data.data : data;
    if (Array.isArray(data)) data = data[0] || {};

    // Only the names: the page needs nothing else, and this keeps any scores out.
    let players = (data.player_match_statistics || [])
      .filter((r) => String(r.period ?? "0") === "0")
      .map((r) => {
        const p = r.player || r;
        return { name: [p.first_name, p.last_name || p.family_name].filter(Boolean).join(" ") };
      })
      .filter((p) => p.name);
    const tip = Date.parse((data.start_time_datetime || "").replace(" ", "T") + "Z");
    const early = tip - Date.now() > WINDOW;
    if (early) players = [];

    const body = JSON.stringify({ status: data.match_status || "", players });
    const keep = early ? 900 : 120;
    ctx.waitUntil(cache.put(key, new Response(body, { headers: { "cache-control": "public, max-age=" + keep } })));
    return reply(body, 200, keep);
  },
};
