@AGENTS.md

# Plebs.Finance — Architecture Reference

AI-scored trading signals for crypto and prediction markets. This file documents how the
scoring pipeline works so you can pick up the codebase without re-deriving it.

**Repo layout:** `apps/web` (Next.js frontend) · `apps/data-service` (Python ingestion +
scoring) · `supabase/migrations` (SQL schema). See README.md for setup.

---

# SCOPE: CRYPTO + PREDICTION MARKETS

The platform originally covered stocks too. Stock features are **disabled and hidden, not
deleted** — the code stays intact behind flags.

**Active:**
- Crypto scoring every 2h. CORE_CRYPTO (BTC, ETH, SOL, XRP, ADA, DOGE, BNB, AVAX, LINK, UNI)
  scored directly with Sonnet; TIER1_CRYPTO (53 coins) prescreened with Haiku first; lower-tier
  coins only if the daily move exceeds `CRYPTO_MOVER_THRESHOLD` (5%).
- Prediction markets 2x/day via Polymarket, up to 10 signals/day from a 30-candidate pool.
- News ingestion, congressional trades, newsletter and briefings (7 days/week).

**Disabled:**
- Stock scoring jobs are gated behind `ENABLE_STOCK_SCORING=true` (default off).
- Congress and Insiders nav links hidden; "Stocks" tab removed from the signal feed.

**Why:** real-time stock data is prohibitively expensive at retail scale (the free Alpaca IEX
feed is one exchange, not the consolidated tape), and stock BUY-side edge was unproven in
backtests (31-37%) versus crypto (88-94%, small n).

---

# MODEL USAGE

All scoring, briefing, newsletter, and backtest calls run on `claude-sonnet-5`. The prescreen
stage runs on `claude-haiku-4-5-20251001`.

**Two-stage pipeline (cost optimization):** Haiku prescreens TIER1 coins at ~$0.001/call and
filters 70-80% of them; only candidates scoring 64+ escalate to Sonnet for full scoring. Core
coins skip the prescreen. Estimated cost: **$6-12/month** for crypto + predictions + briefings
+ newsletter.

Files: `scoring/engine.py`, `scoring/haiku_prescreen.py`, `briefing/generator.py`,
`briefing/newsletter.py`, `briefing/elite_briefing.py`, `analysis/claude_backtest.py`.

---

# SCORING ENGINE ARCHITECTURE

## Deterministic gate stack (`scoring/engine.py`)

Every signal Claude produces passes through these gates **in order**. Any one can downgrade a
BUY/SELL to HOLD. Gates are code-enforced — they override the model regardless of the prompt.

1. **Confidence gate** (`risk_engine.CONFIDENCE_MINIMUM = 64`) — BUY/SELL below 64% becomes HOLD
2. **Circuit breaker** (±8% change_24h) — no SELLs after a >8% gap up, no BUYs after a >8% gap down
3. **SPY regime gate** (stocks) — SPY below SMA-50 blocks BUYs; >5% above blocks SELLs
4. **SPY 1h SMA-20 gate** (stocks) — SPY below the 20-period SMA on 1h candles blocks BUYs
5. **RVOL gate** (stocks) — relative volume under 2.5x becomes HOLD (3.5x for high-beta names)
6. **High-beta sector alignment** (AMD/NVDA/COIN/SMCI/AVGO) — QQQ must be green for BUYs
7. **BTC regime gate** (crypto, non-BTC) — BTC MACD bearish and deepening blocks alt-coin BUYs
8. **Crypto correlation cap** — max 2 concurrent open crypto longs, and at most 1 alt alongside
   an open BTC/ETH major. Alts track BTC, so a basket of longs is really one directional bet.
9. **Breadth cap** (stocks) — max 4 extended BUYs (>8% above SMA-50) per run
10. **Evidence gate** (stocks) — if indicators match a validated pattern favoring the opposite
    direction, downgrade to HOLD

After the gates, signals hit the **risk engine**, which computes stop/target/position sizing
across 4 prop-firm-modeled profiles. Signals resolving to zero units, or below the minimum R:R,
are suppressed.

## Accuracy penalty

Assets with 3+ resolved signals since `ENGINE_CUTOFF` and under 15% win rate are capped at 55%
confidence; under 30% are capped at 60%. Stops the engine from repeatedly signaling losers.

## Validated factors (`scoring/validated_factors.py`)

Empirically derived by `analysis/factor_discovery.py` — a model-free study over 62 stocks,
~9,800 observations, ~10 months, train/test split at 2026-03-15. Only patterns with test
n >= 100 are enforced.

- **BUY-favorable:** deep uptrend + MACD expanding, RSI>70 + MACD expanding, deep-uptrend
  pullback, RSI>70 + MACD contracting, healthy RSI + MACD recovering
- **SELL-favorable:** fading momentum below SMA-50, fading momentum in a flat trend, weak RSI +
  MACD contracting, neutral RSI + MACD contracting, weak RSI + MACD expanding, shallow recovery
  in a flat trend

Headline findings: don't fade strength (>15% above SMA-50, or RSI>70 with expanding MACD, runs
~57-63% BUY-favorable); a positive-but-*contracting* MACD is an early SELL tell (~56-61% down)
except in deep uptrends where it's a buyable pause; RSI 30-45 with a positive MACD bleeds.

Signals contradicting a validated pattern are downgraded to HOLD and tagged `[Evidence gate]`;
aligned ones are annotated `[Validated pattern: ...]` so `/accuracy` can compare them later.

Six additional moderate-condition BUY patterns (MACD crossovers, moderate uptrend continuation,
oversold bounce) are marked **theoretical** — not yet at n >= 100 — and escalate to Claude for
confirmation rather than being enforced.

## Risk engine (`scoring/risk_engine.py`)

- 4 profiles: `retail_standard` ($10k), `25k_prop_conservative`, `50k_prop_moderate` (default),
  `150k_prop_boss` ($150k)
- 10% slippage friction buffer on all sizing
- Per-profile daily kill switches
- Asset-class R:R configs — stocks 2:1-3:1, crypto 2:1-3:1, options 2.5:1-3:1, predictions 2:1-5:1
- ETF-to-futures translation (SPY→ES/MES, QQQ→NQ/MNQ)
- 3:30 PM EST market cutoff for stock signals

Prediction markets are `PROP_EXCLUDED`, so `score_setup()` uses `retail_standard` as the
effective profile for prop-excluded asset classes — otherwise the default profile suppresses
every prediction allocation and silently converts the signal to HOLD at write time.

## Crypto portfolio defense

Ported the stock-only guardrails to crypto, which needs them more given how correlated the
asset class is:

- **ATR stops** — `ingestion/crypto.py` computes `atr_14` on 1h candles; the risk engine uses
  1.5×ATR (clamped 1-5%) instead of a flat 3%. A model-provided `invalidation_price` still wins.
- **Correlation cap** — gate #8 above.
- **Escalating daily cap** — soft cap at 6 signals/day (60% floor), rising to 70% for signals
  7-10 and 80% for 11-15, hard ceiling 15. Strong setups always pass; marginal ones filter out
  as the day fills.

## Hybrid rules engine (`scoring/rules_engine.py`)

Parallel scoring path. SELLs are handled entirely by deterministic pattern matching ($0 cost);
BUYs escalate to Claude for confirmation. Same gate stack as `engine.py`, drop-in replacement
for `score_stocks()` / `score_crypto()`.

## Key constants

| Constant | Value | Meaning |
|---|---|---|
| `ENGINE_CUTOFF` | `2026-07-15T00:00:00Z` | v2 engine launch; earlier signals are a different product |
| `SIGNAL_COOLDOWN_H` | 4 | Skip if a signal was generated within 4 hours |
| `CRYPTO_SIGNAL_COOLDOWN_H` | 1 | Shorter cooldown for 24/7 markets |
| `MAX_STOCK_SIGNALS_PER_DAY` | 3 | Hard cap per calendar day |
| `CRYPTO_SOFT_CAP` / `CRYPTO_HARD_CAP` | 8 / 15 | Threshold escalates past the soft cap |
| `MAX_CONCURRENT_CRYPTO_BUYS` | 2 | Max simultaneous open crypto longs |
| `CRYPTO_MAJORS` | BTC, ETH | At most 1 alt alongside an open major |
| `STOCK_RVOL_MINIMUM` | 2.5 (3.5 high-beta) | Relative volume floor |
| `CONFIDENCE_MINIMUM` | 64 | Backtest showed conf 60-63 wins 31.8% (PF 0.71); 64+ wins 68.8% (PF 3.23) |

## Files map

| File | Role |
|---|---|
| `scoring/engine.py` | Main AI scoring, all gates, batch scoring |
| `scoring/rules_engine.py` | Hybrid deterministic + AI escalation |
| `scoring/validated_factors.py` | Empirical pattern table + evidence gate |
| `scoring/risk_engine.py` | Position sizing, stops, prop profiles, kill switches |
| `scoring/haiku_prescreen.py` | Cheap Haiku first-pass filter |
| `scoring/scanner.py` | Which tickers to score |
| `scoring/price_data.py` | Indicator-merging helper (see the gotcha below) |
| `scoring/accuracy.py` | Win-rate tracking, `/accuracy` endpoint |
| `scoring/resolver.py` | Signal outcome resolution (WIN/LOSS/EXPIRED) |
| `scoring/smart_money.py` | Wallet consensus for prediction markets |
| `scoring/whale_sentinel.py` | Large-trade detection |
| `analysis/factor_discovery.py` | The study behind `validated_factors` |
| `analysis/claude_backtest.py` | Backtesting engine |

## Known gotchas

- **Thin streaming rows shadow indicators.** `streaming/alpaca_ws.py` flushes thin
  `{source, updated_at}` rows every 60s. A naive "newest row" query gets one of these and sees
  no indicators. Use `scoring/price_data.get_scoring_price_row()`, which merges the freshest
  price with the newest indicator-bearing row.
- **Indicator column names are case-sensitive.** pandas_ta emits `RSI_14`, not `rsi_14`. The
  crypto matcher is case-insensitive; the stock matcher historically was not, which silently
  nulled every ta-derived indicator.
- **Multi-source OHLCV merges need consistent tz-awareness.** Concatenating tz-aware Alpaca
  frames with tz-naive fallback frames raises inside `sort_index()`.

---

# PREDICTION MARKET GUARDRAILS

Markets pass through this stack before any signal is written:

1. **Category gate** (`prediction_filters.is_allowed_category`) — title-based inference, since
   the Polymarket Gamma API returns empty categories. Allowed: politics, elections, geopolitics,
   economics, macro, fed, finance, crypto, web3, technology, science. Sports blocked unless a
   verified cross-exchange arb >= 7%.
2. **Pricing bracket** — both YES and NO must sit in $0.10-$0.90. Blocks penny meme markets and
   near-certain outcomes with no upside.
3. **Volume gate** — 24h volume >= $10,000, as a liquidity proxy.
4. **Ground-truth mismatch** — >= 7% divergence from a reference probability. Fails open when no
   reference data is available.
5. **CLOB liquidity check** — **disabled.** Polymarket's CLOB returns $0.001-$0.999 spreads on
   every token regardless of real liquidity, which blocked 100% of markets. Function kept intact.

Pass rate is roughly 22% (110 of 500 tested). The pricing bracket rejects the most.

**Resolution** (`scoring/resolver.py`) is two-tier:
- **Settlement-based** (authoritative) — Polymarket CLOB settlement API (`closed=True`,
  `tokens[].winner`). Takes priority once a market settles.
- **Price-drift** (fast feedback) — a YES move of >= 15pp from entry resolves WIN/LOSS without
  waiting. After 30 days the threshold relaxes to 10pp so old signals don't sit PENDING forever.
- **Ratchet** — once peak favorable drift hits 20pp, a 15pp floor locks in the WIN so a position
  can't round-trip to a loss. Tagged `ratchet` in logs.

Constants: `MAX_PREDICTION_SIGNALS_PER_DAY = 10`, `PREDICTION_CANDIDATE_LIMIT = 30`,
`ADAPTIVE_HAIKU_BASE_THRESHOLD = 64`, `ADAPTIVE_HAIKU_TIGHT_THRESHOLD = 80`.

**Kalshi is disabled by policy** (`KALSHI_ENABLED = False` in `prediction_markets.py`) — it's a
CFTC-regulated exchange and not a fit for this platform. Polymarket's Gamma API (public, no auth)
carries prediction markets alone. The code is intact behind a one-line flip.

---

# NEWSLETTER & BRIEFING

The newsletter is a **full world morning brief**, not a finance recap. Coverage spans crypto
(primary, 1-2 stories), prediction markets as narrative anchors, geopolitics, AI/tech,
health/science, sports/culture, and US domestic policy.

- `_fetch_prediction_markets()` pulls top Polymarket markets by volume, dedupes, filters sports,
  and joins AI signals from the `signals` table. Results land in `content_json.predictions`.
- Generates and sends **7 days/week**. Weekend editions use a lighter prompt — 3-4 stories,
  leaning on crypto (24/7), predictions, sports, and world events.
- `_alert_generation_failure()` emails an alert from both the Claude-call-failure and
  DB-write-failure branches; `job_send_newsletter_retry()` actually regenerates rather than
  re-sending nothing.

**Known limitation:** the Outlook mobile app renders the near-black background as washed-out
gray. Its dark-mode engine doesn't expose the `data-ogsc` hook and largely ignores
`color-scheme` meta tags. No reliable CSS-only fix exists. Gmail, Apple Mail, and
OWA/New-Outlook-desktop all render correctly.

---

# NEWS FEED INFRASTRUCTURE

All RSS, zero API cost. Scheduled 6:45am ET, before newsletter generation at 7am.

| Module | Sources | Identifier | Schedule |
|---|---|---|---|
| `ingestion/news.py` | Finnhub API | `market` | Weekdays |
| `ingestion/crypto_news.py` | CoinDesk, Decrypt, The Block | `CRYPTO_GENERAL` | With crypto ingestion |
| `ingestion/tech_news.py` | TechCrunch, Ars Technica, The Verge (AI-filtered) | `AI` | Weekdays |
| `ingestion/geopolitics_news.py` | Al Jazeera, Defense News | `GEOPOLITICS` | Weekdays |
| `ingestion/sports_news.py` | ESPN, BBC Sport | `SPORTS` | Daily |
| `ingestion/health_science_news.py` | NPR Health, STAT News | `HEALTH_SCIENCE` | Daily |
| `ingestion/world_news.py` | BBC World, NPR, NYT World | `WORLD` | Daily |

- **Cross-feed dedup** — `rss_utils.py` checks existing `news_items` headlines before inserting.
- **Feed health monitor** — `check_feed_health()` runs daily at 8am ET, flags sources with zero
  articles in 72 hours, and emails a report. Manual trigger: `POST /run-job/check_feed_health`.
- **Trusted sources** — `ingestion/trusted_sources.py` whitelists ~40 vetted outlets. New feeds
  must be added there before they'll be ingested.

---

# DATABASE CONVENTIONS

Supabase Postgres with RLS on every table.

- An `rls_auto_enable()` event trigger enables RLS **and** grants `service_role` full CRUD on
  every newly created table. Before it was extended to grant, several tables shipped with a
  correct RLS policy but no underlying `GRANT`, so writes silently failed.
- **Upsert needs UPDATE privilege.** `.upsert(..., {onConflict})` compiles to
  `INSERT ... ON CONFLICT DO UPDATE`, which Postgres requires UPDATE privilege to *plan* — so it
  fails whether or not a conflict occurs. Any table the frontend upserts into needs an UPDATE
  policy and grant, not just INSERT.
- `SECURITY DEFINER` functions taking a user id must check `auth.uid()`, or they're an IDOR.
  Revoke public execute on anything that doesn't.

---

# CONGRESSIONAL TRADES

Scraped directly from `efdsearch.senate.gov` (`ingestion/congressional.py`). The CSRF agreement
POST requires `Referer` and `Origin` headers or it 403s. Data is real but disclosure-lagged —
the STOCK Act allows 30-45 days to file, so `last_30d` counts look sparse by design.

Set `EFD_CONTACT_EMAIL` so the scraper's User-Agent carries a real contact address.
