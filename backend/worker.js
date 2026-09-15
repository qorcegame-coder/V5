/**
 * QCAL data proxy — a Cloudflare Worker.
 *
 * Why this exists: a browser can't call FRED or Yahoo directly (no CORS
 * headers). This Worker runs server-side (no CORS restriction), fetches the
 * data, and returns it to your dashboard with CORS enabled — so the macro
 * indicators and market tiles update automatically, no pencil, no public proxy.
 *
 * Deploy (free, ~5 min, no credit card): see backend/README.md.
 * Endpoints:
 *   /fred?series=PCEPILFE&limit=60          -> FRED observations (newest-first)
 *   /yahoo?symbol=^VIX&range=6mo            -> Yahoo daily chart
 * Then paste your Worker URL into PROXY at the top of TradingCalendar.html.
 */

const FRED_KEY = "a88f8d945edc2f9debece326dce2f216"; // your FRED key stays here, server-side

const CORS = {
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "GET, OPTIONS",
  "content-type": "application/json",
};

export default {
  async fetch(request) {
    if (request.method === "OPTIONS") return new Response(null, { headers: CORS });
    const u = new URL(request.url);
    try {
      if (u.pathname.endsWith("/fred")) {
        const series = u.searchParams.get("series");
        if (!series) return json({ error: "missing series" }, 400);
        const limit = u.searchParams.get("limit") || "60";
        let f = `https://api.stlouisfed.org/fred/series/observations`
              + `?series_id=${encodeURIComponent(series)}&api_key=${FRED_KEY}`
              + `&file_type=json&sort_order=desc&limit=${encodeURIComponent(limit)}`;
        const rt = u.searchParams.get("realtime");   // ALFRED vintage (optional)
        const oe = u.searchParams.get("obs_end");
        if (rt) f += `&realtime_start=${rt}&realtime_end=${rt}`;
        if (oe) f += `&observation_end=${oe}`;
        const r = await fetch(f, { cf: { cacheTtl: 300 } });
        return new Response(await r.text(), { status: r.status, headers: CORS });
      }

      if (u.pathname.endsWith("/yahoo")) {
        const sym = u.searchParams.get("symbol");
        if (!sym) return json({ error: "missing symbol" }, 400);
        const range = u.searchParams.get("range") || "6mo";
        const y = `https://query1.finance.yahoo.com/v8/finance/chart/`
                + `${encodeURIComponent(sym)}?range=${encodeURIComponent(range)}&interval=1d`;
        const r = await fetch(y, { headers: { "User-Agent": "Mozilla/5.0" }, cf: { cacheTtl: 300 } });
        return new Response(await r.text(), { status: r.status, headers: CORS });
      }

      return json({ ok: true, usage: "/fred?series=UNRATE  |  /yahoo?symbol=SPY" });
    } catch (e) {
      return json({ error: String(e) }, 502);
    }
  },
};

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), { status, headers: CORS });
}
