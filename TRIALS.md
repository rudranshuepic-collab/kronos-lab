# Trial log

Every configuration ever evaluated against the Reality Check or ledger, in order. Never delete rows.

| # | Date | What changed | Data used | RankIC | t | Net excess | Kept? |
|---|---|---|---|---|---|---|---|
| 1 | 2026-10-21 | Baseline: Kronos-small, T=1.0, 5 samples, 256 lookback, 20d, close-to-close, 0.20% cost | Jul 2024 – Aug 2026 | 0.069 | 1.97 | +9.8 pts | Superseded by #2 |
| 2 | 2026-10-21 | Same signal, next-open entry, full Zerodha costs (~0.5%) | Jul 2024 – Aug 2026 | 0.063 | 1.84 | +5.7 pts | Current |
| 3 | 2026-10-27 | Settings grid: T=1.0, samples=8, lookback=256, horizon=20. Pre-registered, result pending | Jul 2024 – Aug 2026 | pending | pending | pending | pending |
| 4 | 2026-10-27 | Settings grid: T=1.0, samples=8, lookback=90, horizon=10. Pre-registered, result pending | Jul 2024 – Aug 2026 | pending | pending | pending | pending |
| 5 | 2026-10-27 | Settings grid: T=1.0, samples=10, lookback=256, horizon=20. Pre-registered, result pending | Jul 2024 – Aug 2026 | pending | pending | pending | pending |
| 6 | 2026-10-27 | Settings grid: T=1.0, samples=10, lookback=90, horizon=10. Pre-registered, result pending | Jul 2024 – Aug 2026 | pending | pending | pending | pending |
| 7 | 2026-10-27 | Settings grid: T=0.6, samples=8, lookback=256, horizon=20. Pre-registered, result pending | Jul 2024 – Aug 2026 | pending | pending | pending | pending |
| 8 | 2026-10-27 | Settings grid: T=0.6, samples=8, lookback=90, horizon=10. Pre-registered, result pending | Jul 2024 – Aug 2026 | pending | pending | pending | pending |
| 9 | 2026-10-27 | Settings grid: T=0.6, samples=10, lookback=256, horizon=20. Pre-registered, result pending | Jul 2024 – Aug 2026 | pending | pending | pending | pending |
| 10 | 2026-10-27 | Settings grid: T=0.6, samples=10, lookback=90, horizon=10. Pre-registered, result pending | Jul 2024 – Aug 2026 | pending | pending | pending | pending |

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
