# Kronos Lab: brief for AI coding agents

Read this whole file before changing anything. It applies to every agent (OmniRush,
OpenCode, Claude Code, Codex, Cursor, Astral or anything else) and to every task in this repo.

## What this project is

A free website that runs the open-source **Kronos** forecasting model (Kronos-small, MIT,
github.com/shiyu-coder/Kronos, paper arXiv 2508.02739) on NSE stocks every weekday evening.

- Live site: https://rudranshuepic-collab.github.io/kronos-lab/
- Owner: Rudy, a solo builder in Mumbai. Capital he'd trade is ₹50k–₹1L, long-only cash equities.
- Cost of running it: ₹0. Keep it that way unless the owner says otherwise.

### How a daily run works
`.github/workflows/daily.yml` runs at ~4:47 pm IST, Mon–Fri, on a free GitHub runner:

1. `app/build_site.py` downloads daily candles from Yahoo Finance (`app/data.py`) for the
   Nifty 50 plus `watchlist.txt`.
2. `app/engine.py` runs Kronos-small: 256-bar lookback, 20-trading-day horizon, 8 sampled
   paths per stock, fixed seed, KV-cached inference. Its output is identical to upstream
   and about 8x faster on CPU. The signal is the mean sampled 20-day return.
3. The top 5 Nifty 50 names become **Picks**, each with a trade plan: buy at the next open,
   skip if it opens >2% above the close, sell at the open 20 trading days later, and a 3×ATR
   emergency stop that is *untested*.
4. `app/ledger.py` appends every forecast to `data/ledger/forecasts.csv` (append-only; never
   rewrite past dates) and scores every forecast whose 20 days are up: next-open in, open out,
   full Zerodha delivery charges, compared with the average Nifty 50 stock.
5. `app/reality.py` extends the walk-forward **Reality Check**, which uses only months after
   Kronos's June 2024 training cutoff. Results are cached in `data/reality_check.json`.
6. Everything is written as static JSON + `web/` (plain HTML/CSS/JS, no build step) to
   `site/` and deployed to GitHub Pages. The bot commits `data/` back, which also keeps the
   cron alive.

### Run it locally
```bash
pip install -r requirements.txt          # Python 3.12+; CPU-only torch is fine
python -m app.build_site                 # ~2-3 min; writes ./site
python -m http.server -d site 8000       # open http://localhost:8000
python -m app.reality                    # rebuild the Reality Check cache (slow)
python -m app.backfill 20                # fill ledger for past days (labelled backfill)
```

## Where things stand (Oct 2026)

These numbers are honest and must stay honest:

- **Reality Check**, 26 months (Jul 2024 to Aug 2026), next-open entry, ~0.5% costs:
  - mean monthly RankIC **0.063**, t = **1.84**. That fails our gate (t ≥ 2).
  - Top-10 picks returned +4.2% against −1.5% for the equal-weight Nifty 50, but the best
    3 months produced all of that excess.
  - Direction hit rate 52%, a coin flip.
  - Calibration: the real price landed inside the sampled range 27% of the time, against
    ~67% expected. The model is badly overconfident.
- **Track record**, 125 finished backfilled picks:
  - 26% made money after charges; 58% beat the market.
  - Average −2.3% against −4.2% for the market, in a falling market.
- **Behaviour:** Kronos mostly acts as a **short-term mean-reversion** signal. It favours
  recent losers and predicts sharp falls after big runs (e.g. −58% for a stock up 8×).
- **Verdict:** promising, not proven. Research and paper trading only.

## Hard rules (do not break these)

1. **AI signals are the point of this project; make them as accurate as possible.** Kronos is
   an AI model and is the core signal. Better models, fine-tuning, ensembles and new
   ML features are all welcome, as long as each one is (a) reproducible: fixed weights,
   fixed seed, versioned in the ledger's `model` column; and (b) testable without look-ahead
   on data it never trained on, and passes the gate in rule 5.
   **Chat-style LLMs (ChatGPT, Claude, Gemini, etc.) must not pick or size trades directly.**
   They already know what happened after any past date, so they can't be backtested
   honestly, and their answers vary run to run. Use them for research, news summaries and
   writing code. An LLM-derived feature may enter the signal only after it passes a
   forward-only paper test of at least 3 months.
2. **Only out-of-sample results count.** Kronos's training data ends June 2024. Never
   fine-tune or tune settings on a period and then report results on that same period.
   Hold out the months you test on.
3. **Every backtest includes realistic costs** (`ledger.round_trip_cost`: STT both sides,
   stamp duty, exchange + GST, ₹15.34 + GST demat charge per sell, slippage) **and
   next-open entry.** A backtest without costs is fiction.
4. **Expectancy after costs is the metric, not win rate.** Report gross and net side by side.
5. **Count every experiment.** Append each configuration you evaluate to `TRIALS.md` (create
   it if missing): date, what changed, the result. Many tweaks against a t≈2 gate guarantee a
   false discovery. The gate for adopting a change: it beats the current setup on held-out
   data, and the overall result reaches t ≥ 2 (aim for t ≥ 3 after many trials).
6. **Never edit or delete past ledger rows** in `data/ledger/forecasts.csv`. New behaviour
   applies from the next run. If the signal changes, record a new `model` value so the
   track record shows which version made which call.
7. **Never commit secrets.** API keys, the Telegram bot token and Kite credentials go in
   GitHub Actions Secrets or a local `.env` that is gitignored.
8. **Keep it free:** GitHub Actions + Pages. A daily run must finish in well under 30 minutes.
9. **Don't break the site's honesty.** Keep the "promising, not proven" verdict logic, the
   untested-stop label, `noindex` and `robots.txt`. Never show Kronos's % numbers as price
   targets. This site names stocks and suggests trades; Indian SEBI rules restrict that for
   unregistered people, so it must stay a personal research tool and never be advertised,
   sold or posted publicly.
10. **Indian legal limits:** no foreign derivatives, margin or offshore forex (LRS/FEMA).
    Long-only NSE cash equities is the scope.

## Code style
- Python: small modules, config constants at the top of each file, logging rather than print,
  error handling on all network and file I/O, type hints.
- Frontend: plain HTML/CSS/JS, mobile-first, human-looking design (no generic AI-template
  look). Charts are hand-built SVG.
- Test before claiming something works: run `python -m app.build_site`, open the site and
  check every tab on a phone-width screen.

## Improvement roadmap (in priority order)
Do these in order. The ones near the top decide whether the later ones are worth doing.

1. **Settings test.** The paper used T=0.6, 10 samples, a 90-day lookback and a 10-day
   horizon for its investment test. We use T=1.0, 8 samples, 256 / 20. Test at most 8 combos
   on the Reality Check months, pre-registered in `TRIALS.md`.
2. **Is it just reversal?** Each month, regress Kronos's ranks on 5-day and 21-day reversal,
   12-1 momentum and 60-day volatility. Report the residual RankIC and the R². If plain
   reversal does as well, a 3-line deterministic factor is cheaper and Kronos becomes a
   visual tool.
3. **A bigger, survivorship-free universe:** Nifty 200 using historical constituent lists,
   with a liquidity floor and NSE ASM/GSM/ESM exclusions (NSE publishes CSVs). This doubles
   the statistical power.
4. **Fine-tune Kronos on NSE data** (free Kaggle/Colab GPU; the repo has finetune scripts).
   Train up to Dec 2025, test Jan–Sep 2026 against the original model. Adopt it only if it
   wins clearly on held-out months.
5. **Calibrate the cones.** Widen the bands with a multiplier fitted on the ledger, or with
   adaptive conformal prediction, targeting 80% coverage.
6. **Portfolio rules:** a buffer band (buy in the top 5, sell only below rank 15), volatility
   weighting, and a regime filter (Nifty above its 200-DMA). Test each one; keep only what
   helps on held-out data.
7. **Telegram alert** after each run: the picks, any new surveillance flags, run failures.
   Uses the free Bot API; the token goes in Secrets.
8. **Data hardening:** Kite Connect historical API as the primary source (the owner pays
   ₹500/mo), Yahoo as the fallback, sanity checks, and an alert on failure.

## When you finish a task, report back in this format
- What you changed (files), and why
- How you tested it (commands run, what you saw)
- Results: numbers before and after, gross and net, the t-stat, the number of months, and
  the trial count so far
- What you did NOT do or could not verify
- What you'd do next
