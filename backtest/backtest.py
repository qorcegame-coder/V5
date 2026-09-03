#!/usr/bin/env python3
"""
Backtest the QCAL Fed-sentiment index against actual FOMC decisions.

What it does
------------
For every past FOMC meeting it reconstructs the sentiment index from the macro
data *as it was known the day before the decision* (FRED/ALFRED vintage data,
so there is no revision / look-ahead bias), maps the index to a predicted
action (hawkish -> HIKE, dovish -> CUT, neutral -> HOLD), and compares that to
the decision the Fed actually made -- which is read from the target-rate series
itself, not hard-coded from memory.

Why the honest metrics matter (read this before trusting any number)
-------------------------------------------------------------------
1. Most meetings are HOLDs, so plain accuracy is misleading: a model that
   always says HOLD already scores high. We therefore report BALANCED ACCURACY
   (mean recall across HIKE/HOLD/CUT) and the per-class recall for HIKE and CUT
   -- the meetings that actually matter.
2. Rate decisions come in long runs (hike cycle -> hold -> cut cycle), so a
   "predict the same as last meeting" (persistence) baseline is very strong.
   If the index cannot beat persistence, it is not adding much.
3. The Fed telegraphs decisions; by meeting day futures price them ~95%. Beating
   a coin flip proves nothing. This script does NOT claim to beat the market --
   it only tests face validity against the realised decision.
4. Sample size is tiny (~8 meetings/yr) and the Fed's reaction function drifts
   over time, so treat every number here as directional, not significant.

Usage
-----
    pip install requests
    export FRED_API_KEY=your_key_here      # or pass --key
    python backtest.py                     # full run
    python backtest.py --threshold 0.5     # fix the hawkish/dovish cutoff
    python backtest.py --start 2022-01-01 --end 2025-12-31
    python backtest.py --csv results.csv   # also dump per-meeting rows

Responses are cached under ./.fred_cache so re-runs are fast and gentle on the
FRED rate limit.
"""

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".fred_cache")

# ----------------------------------------------------------------------------
# FOMC meeting announcement dates (second day of the meeting). The ACTION at
# each meeting is derived from the target-rate data, not from this list, so a
# date being off by a day or two still works. Extend this as new meetings occur.
# ----------------------------------------------------------------------------
FOMC_DATES = [
    # 2022
    "2022-01-26", "2022-03-16", "2022-05-04", "2022-06-15",
    "2022-07-27", "2022-09-21", "2022-11-02", "2022-12-14",
    # 2023
    "2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14",
    "2023-07-26", "2023-09-20", "2023-11-01", "2023-12-13",
    # 2024
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12",
    "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
    # 2025
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
    "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
    # 2026 (dates approximate; future meetings are skipped automatically)
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-11-04", "2026-12-16",
]

# ----------------------------------------------------------------------------
# The index definition -- mirrors the dashboard's weights & anchors. ISM PMI is
# omitted because FRED has no free ISM series (the weights renormalise over
# whatever data is available at each meeting).
#   tf: 'level' | 'mom' (% m/m) | 'yoy' (% y/y) | 'chg' (level diff) | 'claims4'
#   anchors: (dovish, neutral, hawkish) -- higher=hawkish unless haw < neu
# ----------------------------------------------------------------------------
INDICATORS = [
    dict(key="pce",    w=10, series="PCEPILFE",      tf="yoy",     anchors=(1.0, 2.0, 3.5)),
    dict(key="nfp",    w=9,  series="PAYEMS",        tf="chg",     anchors=(0, 150, 300)),
    dict(key="cpi",    w=8,  series="CPILFESL",      tf="yoy",     anchors=(1.5, 2.5, 4.0)),
    dict(key="unemp",  w=7,  series="UNRATE",        tf="level",   anchors=(4.8, 4.2, 3.5), sahm=True),
    dict(key="claims", w=4,  series="ICSA",          tf="claims4", anchors=(290, 230, 190)),
    dict(key="ppi",    w=3,  series="PPIACO",        tf="yoy",     anchors=(0.5, 2.0, 4.0)),
    dict(key="retail", w=2,  series="RSAFS",         tf="mom",     anchors=(-0.4, 0.3, 1.0)),
]
TARGET_SERIES = "DFEDTARU"  # federal funds target range, upper limit

# ----------------------------------------------------------------------------
# FRED access (with on-disk caching)
# ----------------------------------------------------------------------------
def _cache_path(url):
    h = hashlib.sha1(url.encode()).hexdigest()
    return os.path.join(CACHE_DIR, h + ".json")


def fred_get(key, series, realtime=None, obs_end=None, obs_start=None, limit=200):
    """Return observations newest-first as list[(date_str, float)] skipping '.'.
    If `realtime` (YYYY-MM-DD) is set, values are as they were KNOWN on that date
    (ALFRED vintage). `obs_end`/`obs_start` bound the observation dates."""
    params = {
        "series_id": series, "api_key": key, "file_type": "json",
        "sort_order": "desc", "limit": str(limit),
    }
    if realtime:
        params["realtime_start"] = realtime
        params["realtime_end"] = realtime
    if obs_end:
        params["observation_end"] = obs_end
    if obs_start:
        params["observation_start"] = obs_start
    url = FRED_BASE + "?" + urllib.parse.urlencode(params)

    cp = _cache_path(url)
    if os.path.exists(cp):
        with open(cp) as f:
            data = json.load(f)
    else:
        for attempt in range(4):
            try:
                with urllib.request.urlopen(url, timeout=30) as r:
                    data = json.loads(r.read().decode())
                break
            except Exception as e:  # noqa
                if attempt == 3:
                    raise
                time.sleep(1.5 * (attempt + 1))
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(cp, "w") as f:
            json.dump(data, f)
        time.sleep(0.15)  # be gentle on the rate limit

    out = []
    for o in data.get("observations", []):
        v = o.get("value", ".")
        if v not in (".", "", None):
            try:
                out.append((o["date"], float(v)))
            except ValueError:
                pass
    return out


# ----------------------------------------------------------------------------
# Scoring -- identical logic to the dashboard
# ----------------------------------------------------------------------------
def clamp(v, a, b):
    return max(a, min(b, v))


def anchor_score(v, dov, neu, haw):
    if v is None:
        return None
    if v == neu:
        return 0.0
    if (haw > neu and v > neu) or (haw < neu and v < neu):
        s = 2 * (v - neu) / (haw - neu)
    else:
        s = 2 * (v - neu) / (neu - dov)
    return clamp(s, -2, 2)


def indicator_value(key, ind, obs):
    """Compute the indicator's value from vintage observations (newest-first)."""
    tf = ind["tf"]
    if tf == "level":
        return obs[0][1] if obs else None
    if tf == "chg":
        return obs[0][1] - obs[1][1] if len(obs) > 1 else None
    if tf == "mom":
        return (obs[0][1] / obs[1][1] - 1) * 100 if len(obs) > 1 and obs[1][1] else None
    if tf == "yoy":
        return (obs[0][1] / obs[12][1] - 1) * 100 if len(obs) > 12 and obs[12][1] else None
    if tf == "claims4":
        vals = [v for _, v in obs[:4]]
        return sum(vals) / len(vals) / 1000.0 if len(vals) == 4 else None
    return None


def indicator_score(ind, value, unemp_obs):
    s = anchor_score(value, *ind["anchors"])
    if s is None:
        return None
    if ind.get("sahm") and unemp_obs and len(unemp_obs) >= 3:
        window = [v for _, v in unemp_obs[:12]]
        sahm = value - min(window)
        if sahm >= 0.5:
            s = min(s, -1.5)
        elif sahm >= 0.3:
            s -= 0.5
    return clamp(s, -2, 2)


def composite_at(key, asof):
    """Reconstruct the index using data known on `asof` (day before a meeting).
    Returns (composite, n_used, detail dict)."""
    # unemployment obs is needed for the Sahm rule; fetch once.
    unemp_obs = None
    total_w, acc, detail = 0.0, 0.0, {}
    for ind in INDICATORS:
        # For yoy we need 13 monthly points; grab generous history ending at asof.
        obs = fred_get(key, ind["series"], realtime=asof, obs_end=asof, limit=80)
        if ind["series"] == "UNRATE":
            unemp_obs = obs
        val = indicator_value(ind["key"], ind, obs)
        sc = indicator_score(ind, val, unemp_obs if ind.get("sahm") else None)
        if sc is None:
            detail[ind["key"]] = None
            continue
        total_w += ind["w"]
        acc += sc * ind["w"]
        detail[ind["key"]] = round(sc, 2)
    if total_w == 0:
        return None, 0, detail
    return acc / total_w, sum(1 for v in detail.values() if v is not None), detail


# ----------------------------------------------------------------------------
# Truth: what did the Fed actually do at each meeting? (from the rate itself)
# ----------------------------------------------------------------------------
def actual_action(key, meeting):
    d = dt.date.fromisoformat(meeting)
    pre = fred_get(key, TARGET_SERIES,
                   obs_start=(d - dt.timedelta(days=12)).isoformat(),
                   obs_end=(d - dt.timedelta(days=1)).isoformat(), limit=20)
    post = fred_get(key, TARGET_SERIES,
                    obs_start=(d + dt.timedelta(days=1)).isoformat(),
                    obs_end=(d + dt.timedelta(days=10)).isoformat(), limit=20)
    if not pre or not post:
        return None, None
    before = pre[0][1]     # newest before the meeting
    after = post[-1][1]    # oldest after the meeting (the new level)
    delta = round(after - before, 3)
    if delta > 0.01:
        return "HIKE", delta
    if delta < -0.01:
        return "CUT", delta
    return "HOLD", delta


def predict(comp, thr):
    if comp is None:
        return None
    if comp >= thr:
        return "HIKE"
    if comp <= -thr:
        return "CUT"
    return "HOLD"


# ----------------------------------------------------------------------------
# Evaluation helpers
# ----------------------------------------------------------------------------
CLASSES = ["HIKE", "HOLD", "CUT"]


def balanced_accuracy(pairs):
    """mean recall over classes actually present."""
    recalls = []
    for c in CLASSES:
        tot = sum(1 for a, p in pairs if a == c)
        if tot:
            hit = sum(1 for a, p in pairs if a == c and p == c)
            recalls.append(hit / tot)
    return sum(recalls) / len(recalls) if recalls else 0.0


def evaluate(rows, thr):
    pairs = [(r["actual"], predict(r["comp"], thr)) for r in rows if r["comp"] is not None and r["actual"]]
    if not pairs:
        return None
    acc = sum(1 for a, p in pairs if a == p) / len(pairs)
    bal = balanced_accuracy(pairs)
    # per-class recall
    rec = {}
    for c in CLASSES:
        tot = sum(1 for a, _ in pairs if a == c)
        hit = sum(1 for a, p in pairs if a == c and p == c)
        rec[c] = (hit, tot)
    return dict(n=len(pairs), acc=acc, bal=bal, rec=rec, pairs=pairs)


def confusion(pairs):
    idx = {c: i for i, c in enumerate(CLASSES)}
    m = [[0] * 3 for _ in range(3)]
    for a, p in pairs:
        if p is None:
            continue
        m[idx[a]][idx[p]] += 1
    return m


# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Backtest the QCAL Fed-sentiment index vs FOMC decisions.")
    ap.add_argument("--key", default=os.environ.get("FRED_API_KEY"), help="FRED API key (or set FRED_API_KEY)")
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--end", default=dt.date.today().isoformat())
    ap.add_argument("--threshold", type=float, default=None, help="fix hawkish/dovish cutoff; default sweeps")
    ap.add_argument("--csv", default=None, help="write per-meeting rows to this file")
    args = ap.parse_args()

    if not args.key:
        sys.exit("ERROR: no FRED key. Pass --key or set FRED_API_KEY.  Get one free at https://fred.stlouisfed.org/docs/api/api_key.html")

    meetings = [m for m in FOMC_DATES if args.start <= m <= args.end and m < dt.date.today().isoformat()]
    print(f"Backtesting {len(meetings)} FOMC meetings ({meetings[0]} .. {meetings[-1]})")
    print("Reconstructing the index from vintage (point-in-time) FRED data ...\n")

    rows = []
    for m in meetings:
        asof = (dt.date.fromisoformat(m) - dt.timedelta(days=1)).isoformat()
        try:
            comp, n, detail = composite_at(args.key, asof)
            action, delta = actual_action(args.key, m)
        except Exception as e:  # noqa
            print(f"  {m}: data error ({e}); skipping")
            continue
        rows.append(dict(meeting=m, comp=comp, n=n, actual=action, delta=delta, detail=detail))

    rows = [r for r in rows if r["actual"] is not None]
    if not rows:
        sys.exit("No usable meetings (check key / connectivity).")

    # ---- threshold selection ----
    if args.threshold is not None:
        thresholds = [args.threshold]
    else:
        thresholds = [round(x * 0.1, 1) for x in range(2, 11)]  # 0.2 .. 1.0

    print("Threshold sweep (balanced accuracy = mean recall over HIKE/HOLD/CUT):")
    print(f"  {'thr':>5} {'acc':>6} {'bal.acc':>8}   HIKE   HOLD   CUT")
    best = None
    for thr in thresholds:
        ev = evaluate(rows, thr)
        r = ev["rec"]
        print(f"  {thr:>5} {ev['acc']*100:>5.0f}% {ev['bal']*100:>7.0f}%   "
              f"{r['HIKE'][0]}/{r['HIKE'][1]:<3} {r['HOLD'][0]}/{r['HOLD'][1]:<3} {r['CUT'][0]}/{r['CUT'][1]:<3}")
        if best is None or ev["bal"] > best[1]["bal"]:
            best = (thr, ev)
    thr, ev = best
    print(f"\nBest balanced-accuracy threshold: {thr}  (bal.acc {ev['bal']*100:.0f}%, acc {ev['acc']*100:.0f}%, n={ev['n']})")

    # ---- baselines ----
    actuals = [r["actual"] for r in rows]
    majority = max(set(actuals), key=actuals.count)
    maj_pairs = [(a, majority) for a in actuals]
    maj_acc = sum(1 for a, p in maj_pairs if a == p) / len(maj_pairs)
    # persistence: predict same as previous meeting's actual
    pers_pairs = []
    for i in range(1, len(rows)):
        pers_pairs.append((rows[i]["actual"], rows[i - 1]["actual"]))
    pers_acc = sum(1 for a, p in pers_pairs if a == p) / len(pers_pairs) if pers_pairs else 0
    pers_bal = balanced_accuracy(pers_pairs) if pers_pairs else 0

    print("\nBaselines to beat:")
    print(f"  always-'{majority}'   acc {maj_acc*100:.0f}%   bal.acc {balanced_accuracy(maj_pairs)*100:.0f}%")
    print(f"  persistence        acc {pers_acc*100:.0f}%   bal.acc {pers_bal*100:.0f}%")
    verdict = "ADDS signal over" if ev["bal"] > max(balanced_accuracy(maj_pairs), pers_bal) + 0.02 else "does NOT clearly beat"
    print(f"  -> index {verdict} the baselines (balanced accuracy).")

    # ---- confusion matrix at best threshold ----
    m = confusion(ev["pairs"])
    print("\nConfusion matrix @ thr", thr, " (rows=actual, cols=predicted):")
    print(f"  {'':6}" + "".join(f"{c:>7}" for c in CLASSES))
    for i, c in enumerate(CLASSES):
        print(f"  {c:6}" + "".join(f"{m[i][j]:>7}" for j in range(3)))

    # ---- per-meeting table ----
    print("\nPer-meeting detail:")
    print(f"  {'meeting':11} {'comp':>6} {'pred':>5} {'actual':>6} {'Δbps':>6}  ok")
    for r in rows:
        p = predict(r["comp"], thr)
        ok = "✓" if p == r["actual"] else "·"
        cs = f"{r['comp']:+.2f}" if r["comp"] is not None else "  n/a"
        db = f"{int(round(r['delta']*100)):+d}" if r["delta"] is not None else ""
        print(f"  {r['meeting']:11} {cs:>6} {str(p):>5} {r['actual']:>6} {db:>6}  {ok}")

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["meeting", "composite", "n_indicators", "predicted", "actual", "delta_pct"] + [i["key"] for i in INDICATORS])
            for r in rows:
                w.writerow([r["meeting"], r["comp"], r["n"], predict(r["comp"], thr), r["actual"], r["delta"]]
                           + [r["detail"].get(i["key"]) for i in INDICATORS])
        print(f"\nWrote {args.csv}")

    print("\nRemember: HOLD dominates and decisions are telegraphed. Read balanced")
    print("accuracy vs the baselines above -- not raw accuracy -- and treat a ~30")
    print("meeting sample as directional, not statistically significant.")


if __name__ == "__main__":
    main()
