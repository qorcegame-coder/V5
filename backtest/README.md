# Sentiment-index backtest

Tests whether the QCAL Fed-sentiment index has any predictive value for FOMC
hike / hold / cut decisions, using **point-in-time (vintage) data** so the index
only ever "sees" what was known before each meeting.

## Run it

```bash
pip install requests
export FRED_API_KEY=your_key          # free: https://fred.stlouisfed.org/docs/api/api_key.html
python backtest.py
```

Options: `--threshold 0.5` (fix the hawkish/dovish cutoff instead of sweeping),
`--start 2022-01-01 --end 2025-12-31`, `--csv results.csv`.

Responses cache under `./.fred_cache` — delete it to force a fresh pull.

## What it does

1. For each past FOMC meeting, rebuilds the index from FRED/ALFRED data **as
   known the day before** the decision (`realtime_start/end` = meeting − 1 day).
2. Maps the composite to a prediction: `>= +thr` HIKE, `<= -thr` CUT, else HOLD.
3. Reads the **actual** decision from the target-rate series (`DFEDTARU`) around
   the meeting — not hard-coded, so it can't drift from reality.
4. Scores it and compares to two baselines.

## How to read the output (important)

- **Ignore raw accuracy.** HOLDs dominate, so "always HOLD" already scores high.
- Look at **balanced accuracy** (mean recall over HIKE/HOLD/CUT) and the
  **per-class HIKE / CUT recall** — the meetings that matter.
- The index has to beat **both** baselines the script prints:
  - *always-HOLD* (majority class), and
  - *persistence* (predict the same as last meeting) — a hard baseline, because
    rate regimes persist.
- Sample is ~30 meetings and the Fed's reaction function drifts, so treat
  everything as **directional, not statistically significant**. This tests face
  validity against the realised decision; it does **not** test whether the index
  beats market-implied odds (futures already price meetings ~95% by the day).

## v1 vs v2 (real-rate restrictiveness)

The run now reports **two** versions side by side:

- **v1 — data only:** the original index (inflation/labor/demand levels).
- **v2 — + real-rate:** adds one feature, **policy rate − core PCE y/y**. A high
  real rate is restrictive, so it scores **dovish** (the Fed has room to ease).
  This is the one piece of the "why they cut" story that lives in the data.

Watch the **CUT recall** and **balanced accuracy** lines to see whether it helps.
Expected: v2 catches the *deeply restrictive* cuts (2024) it can now justify, but
still misses the more *discretionary/political* ones (2025) — because cut **timing**
is a judgment call no macro feature can predict. `--rr-weight N` tunes its weight.

## Known simplifications

- **ISM PMI is dropped** (no free FRED series); weights renormalise over the rest.
- Uses headline series where the app uses a mix (e.g. `PPIACO`, `CPILFESL`).
- FOMC dates for 2025–26 are approximate; the action is still derived from the
  rate data, and future (undecided) meetings are skipped automatically.
- Threshold sweeping on the same data it reports is **in-sample** — a good bar
  would hold out later years. The sweep is there to show sensitivity, not to
  claim the best number is real out-of-sample skill.
