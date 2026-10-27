# Trial log

Every configuration ever evaluated against the Reality Check or ledger, in order. Never delete rows.

| # | Date | What changed | Data used | RankIC | t | Net excess | Kept? |
|---|---|---|---|---|---|---|---|
| 1 | 2026-10-21 | Baseline: Kronos-small, T=1.0, 5 samples, 256 lookback, 20d, close-to-close, 0.20% cost | Jul 2024 – Aug 2026 | 0.069 | 1.97 | +9.8 pts | Superseded by #2 |
| 2 | 2026-10-21 | Same signal, next-open entry, full Zerodha costs (~0.5%) | Jul 2024 – Aug 2026 | 0.063 | 1.84 | +5.7 pts | Current |
| 3 | 2026-10-27 | Settings grid: T=1.0, samples=8, lookback=256, horizon=20 (pre-registered) | Jul 2024 – Aug 2026 | 0.0575 | 1.69 (NW) | +0.44%/rebal, +9.6% total | Baseline (live) |
| 4 | 2026-10-27 | Settings grid: T=1.0, samples=8, lookback=90, horizon=10 (pre-registered) | Jul 2024 – Aug 2026 | 0.0709 | 1.41 (NW) | +0.60%/rebal, +21.1% total | No: fails t<2 |
| 5 | 2026-10-27 | Settings grid: T=1.0, samples=10, lookback=256, horizon=20 (pre-registered) | Jul 2024 – Aug 2026 | 0.0672 | 2.06 (NW) | +0.38%/rebal, +7.5% total | No: fails IC margin |
| 6 | 2026-10-27 | Settings grid: T=1.0, samples=10, lookback=90, horizon=10 (pre-registered) | Jul 2024 – Aug 2026 | 0.0702 | 1.38 (NW) | +0.54%/rebal, +19.0% total | No: fails t<2 |
| 7 | 2026-10-27 | Settings grid: T=0.6, samples=8, lookback=256, horizon=20 (pre-registered) | Jul 2024 – Aug 2026 | 0.0588 | 1.86 (NW) | +0.21%/rebal, +3.2% total | No: fails IC margin, t<2 |
| 8 | 2026-10-27 | Settings grid: T=0.6, samples=8, lookback=90, horizon=10 (pre-registered) | Jul 2024 – Aug 2026 | 0.0757 | 1.60 (NW) | +0.39%/rebal, +14.8% total | No: fails t<2 |
| 9 | 2026-10-27 | Settings grid: T=0.6, samples=10, lookback=256, horizon=20 (pre-registered) | Jul 2024 – Aug 2026 | 0.0635 | 2.18 (NW) | +0.21%/rebal, +2.9% total | No: fails IC margin |
| 10 | 2026-10-27 | Settings grid: T=0.6, samples=10, lookback=90, horizon=10 (pre-registered) | Jul 2024 – Aug 2026 | 0.0645 | 1.37 (NW) | +0.37%/rebal, +14.2% total | No: fails IC margin, t<2 |
| 11 | 2026-10-27 | Reversal check on trial-2 forecasts: regress Kronos ranks on 5d/21d reversal, 12-1 momentum, 60d vol | Jul 2024 – Aug 2026 | residual 0.028 | 0.76 (NW) | – | Finding: 58% of Kronos's ranking is explained by these factors; the rest isn't significant |
| 12 | 2026-10-27 | Baseline factor: plain 5-day reversal, same portfolio rules | Jul 2024 – Aug 2026 | 0.061 | 2.07 (NW) | +4.6% total top-10 net | Comparison only |
| 13 | 2026-10-27 | Baseline factor: plain 21-day reversal, same portfolio rules | Jul 2024 – Aug 2026 | 0.045 | 1.21 (NW) | +19.0% total top-10 net | Comparison only |
| 14 | 2026-10-27 | Fresh-stock test A: Kronos as live (T=1.0, samples=8, 256/20) on the Nifty Next 150 (pre-registered) | Jul 2024 – Aug 2026, ~136 new stocks/month | -0.0006 | -0.02 (NW) | +0.79%/rebal, +16.1% total (top-3 months = 110% of excess) | FAIL |
| 15 | 2026-10-27 | Fresh-stock test B: plain 21-day reversal on the Nifty Next 150 (pre-registered) | same | -0.0151 | -0.41 (NW) | -0.59%/rebal, -16.3% total | FAIL |
| 16 | 2026-10-27 | Fresh-stock test C: 50/50 rank blend of A and B on the Nifty Next 150 (pre-registered) | same | -0.0093 | -0.23 (NW) | +0.56%/rebal, +10.1% total (top-3 months = 117%) | FAIL |
## Pre-registered rules for trials 3–10 (written before any result was seen)

- Same 26 month-end rebalance dates for every combo (last trading day of Jul 2024 … Aug 2026),
  current Nifty 50, top 10 equal weight, buy at the next open, sell at the open `horizon`
  trading days later, full Zerodha charges (`ledger.round_trip_cost` at ₹10,000 a position).
- Primary metric: mean monthly RankIC of expected return vs the realised next-open return over
  the same horizon, with a Newey-West t (lag 2). Secondary: net top-10 excess vs the
  equal-weight universe per rebalance.
- Baseline for comparison = trial 3 (T=1.0, samples=8, 256/20), which is what the live site runs.
- A combo replaces the baseline only if ALL hold: RankIC ≥ baseline + 0.01, t ≥ 2.0, and mean
  net excess per rebalance > 0. Otherwise keep the live settings.
- Caveat accepted in advance: all 8 combos use the same 26 months (no held-out cross-section yet),
  so a narrow winner is likely luck. That is why the margin and t bars are required together.

## Outcome of trials 3–10 (2026-10-27)

- **No setting passed the pre-registered rule. The live settings stay as they are.**
- All 8 settings land in a narrow band (RankIC 0.058–0.076). That plateau is a good sign the
  signal is real, but no setting is clearly better; the gaps are within noise (the standard
  error of a 26-month mean IC is about ±0.034).
- Every 90-day / 10-day version earned more in total than its 256 / 20 twin (+14% to +21%
  vs +3% to +10%) but was less consistent (t 1.4–1.6). That pattern fits short-term
  reversal (trial 12). It's a lead for a dedicated test, not a result.
- Regime check: all versions were flat or negative in Aug–Dec 2024, and positive in 2025
  and 2026. The 90/10 versions swing hardest (RankIC about −0.15 in 2024, +0.17 in 2026).
- Cost check: still positive with slippage at 2× and 3× (baseline +0.38% / +0.33% per rebalance).
- Trial count so far: 13, all on the same 26 months. Treat any narrow winner as likely luck.

## Pre-registered rules for trials 14–16 (written before any result was seen)

- Universe: the 150 stocks in today's Nifty 200 that are NOT in our Nifty 50 list
  (data/universe/nifty200_2026-10-27.csv). None were used in trials 1–13, so this is fresh
  data. Caveat: it's today's list, so there's some survivorship bias; it applies to all
  three candidates equally.
- Same 26 month-end dates, a stock needs 257 bars of history to be included, top 10 equal
  weight, buy at the next open, sell at the open 20 trading days later.
- Charges: the full Zerodha stack at ₹10,000 a position, with slippage raised to 10 bps a
  side (these are smaller, less liquid stocks).
- A candidate PASSES if: mean RankIC ≥ 0.03, Newey-West t ≥ 2.0, mean net top-10 excess per
  rebalance > 0, and positive RankIC in at least 2 of the 3 calendar years (2024 part, 2025, 2026).
- Choosing: if more than one passes, take the highest t; if the top two are within 0.3 of
  each other, take the simpler one (B, then A, then C). If none passes, change nothing and
  keep paper trading the live picks.

## Outcome of trials 14–16 (2026-10-27)

- **None of the three passed. Nothing changes.**
- On 150 stocks it had never been tested on, Kronos's ranking score was zero (RankIC −0.0006,
  t −0.02). Plain reversal and the blend were slightly negative. The Nifty 50 edge from
  trials 2–13 did **not** carry over to new stocks. The most likely reason is that the earlier
  result was partly luck from testing many ideas on the same 50 stocks; a large-cap-only
  effect is also possible.
- Kronos's top-10 picks still showed +16% total, but the best 3 months produced more than
  all of that while the ranking score was zero. That's the signature of luck, not skill.
- Conclusion: Kronos-small as configured has **no proven edge**. Keep it for research and paper
  tracking only. Total trials so far: 16.
