# QCAL data proxy (Cloudflare Worker)

A tiny always-on cloud function that fetches FRED + Yahoo **server-side** (no
browser CORS limits) and serves them to the dashboard with CORS enabled. This
makes the macro indicators and market tiles **update automatically** — no
pencil, no flaky public proxy, no one-release feed lag.

It covers **Core PCE, jobless claims, PPI, NFP, and the VIX/SPY/NQ tiles**
automatically. (ISM PMI has no free source anywhere, so it stays pencil/feed.)

## Deploy — free, ~5 minutes, no credit card

1. Go to **dash.cloudflare.com** → sign up / log in.
2. Left sidebar → **Workers & Pages** → **Create** → **Create Worker**.
3. Give it a name (e.g. `qcal`), click **Deploy** (deploys a starter).
4. Click **Edit code**. Select all, delete, and paste the contents of
   **`worker.js`** from this folder. Your FRED key is already in it.
5. Click **Deploy**.
6. Copy your Worker URL — it looks like `https://qcal.<your-subdomain>.workers.dev`.

## Connect it to the dashboard

Open `TradingCalendar.html`, find this line near the top of the `<script>`:

```js
const PROXY = '';   // e.g. 'https://qcal.you.workers.dev'
```

Paste your Worker URL (no trailing slash) between the quotes, save, reload. Done —
those indicators now pull live from FRED/Yahoo through your Worker and refresh on
their own. Leave `PROXY` empty and the dashboard still works exactly as before
(Alpha Vantage + Forex Factory + pencil).

## Test it

Visit `https://qcal.<you>.workers.dev/fred?series=UNRATE` — you should see JSON
observations. `…/yahoo?symbol=SPY` should return a Yahoo chart payload.

## Notes

- The FRED key lives in the Worker (server-side), not in the dashboard.
- Cloudflare's free tier is 100,000 requests/day — the dashboard uses a handful
  per load, so you will not get near it.
- Responses are cached ~5 min at the edge to stay fast and gentle on the APIs.
