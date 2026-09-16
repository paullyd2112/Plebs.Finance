# Plebs.Finance

A Bloomberg-style terminal for crypto and prediction markets — built for retail traders who can't afford a $24K/year terminal.

I started this project with a simple question: is there a way to get institutional-quality trading intelligence without paying tens of thousands for a Bloomberg terminal? For stocks, the answer is basically no — the data costs alone make it impossible. But for crypto and prediction markets, the data is public, the APIs are free, and AI can do the analysis. That's what Plebs is.

## What's Inside

- **AI-powered crypto signals** — Claude Sonnet 5 scoring engine with deterministic gate stack (regime filters, volatility gates, evidence gates, correlation caps). Scores 60+ coins on a 2-hour cycle.
- **Prediction markets** — Polymarket integration with AI-scored YES/NO signals, probability tracking, sparklines, smart money consensus, and whale activity detection.
- **Daily morning briefing** — AI-written email covering crypto, prediction markets, geopolitics, tech, health/science, and world news. 7 days/week.
- **Newsletter** — Full world morning brief delivered to subscribers.
- **Portfolio tracker** — Track positions across crypto and prediction markets.
- **Watchlist & alerts** — Price alerts, signal alerts, and custom watchlists.
- **Screener** — Filter and discover assets by technical indicators.
- **Backtesting engine** — Test signal strategies against historical data.
- **On-demand AI scoring** — Generate an AI signal for any asset on the spot.
- **Pleby AI** — Chat assistant that can answer questions about markets, your portfolio, and signals.
- **Congressional trades tracker** — Senate financial disclosures scraped from efdsearch.senate.gov.
- **Prop firm calculator** — Position sizing across 4 prop-firm-modeled risk profiles.

## Screenshots

Visit [plebs.finance](https://plebs.finance) to see the live app.

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js, TypeScript, Tailwind CSS |
| Backend | Python 3.11, APScheduler, Pandas-TA |
| Database | Supabase (Postgres + Auth + RLS) |
| AI | Claude Sonnet 5 (scoring, briefings, newsletter) + Haiku 4.5 (prescreen) |
| Market Data | Alpaca (crypto), Polymarket Gamma API (predictions) |
| News | RSS feeds — CoinDesk, Decrypt, The Block, ESPN, BBC, NPR, Al Jazeera, + more |
| Email | Resend |
| Hosting | Vercel (web) + Railway (data service) |

## Architecture

```
apps/
├── web/                  # Next.js frontend
│   └── src/
│       ├── app/          # Pages and API routes
│       ├── components/   # React components
│       └── lib/          # Utilities, Supabase clients, helpers
└── data-service/         # Python backend
    ├── ingestion/        # Market data, news, congressional trades
    ├── scoring/          # AI scoring engine, risk engine, resolver
    ├── briefing/         # Newsletter, briefings, welcome emails
    ├── notifications/    # Push, email, Telegram, SMS
    ├── analysis/         # Backtesting, factor discovery
    ├── prompts/          # Claude prompt templates
    └── scheduler.py      # APScheduler — runs all jobs on cron
supabase/
└── migrations/           # 19 SQL migrations
```

**Data flow:** Ingestion jobs pull market data and news on cron schedules → scoring engine runs every 2 hours (crypto) and 2x/day (predictions) → signals are written to Supabase → frontend reads from Supabase in real time → newsletter/briefing jobs generate and email AI summaries each morning.

## Self-Hosting

### Prerequisites

**Supabase is required.** Plebs uses Supabase for everything — database, auth, row-level security, and real-time subscriptions. You need a Supabase project before anything else works.

1. Create a project at [supabase.com](https://supabase.com)
2. Run all 19 migrations in `supabase/migrations/` in order (via Supabase CLI or the SQL editor in the dashboard)
3. Note your project URL, anon key, and service role key

### Web app (`apps/web/`)

```bash
cd apps/web
npm install
cp .env.example .env.local
# Fill in your Supabase credentials and other keys (see Environment Variables below)
npm run dev
```

### Data service (`apps/data-service/`)

```bash
cd apps/data-service
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill in your credentials (see Environment Variables below)
python scheduler.py
```

### Environment Variables

#### Web app (`apps/web/.env.local`)

| Variable | Required | Description |
|---|---|---|
| `NEXT_PUBLIC_SUPABASE_URL` | Yes | Your Supabase project URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Yes | Supabase anonymous/public key |
| `SUPABASE_SERVICE_ROLE_KEY` | Yes | Supabase service role key (server-side only) |
| `ANTHROPIC_API_KEY` | Yes | For Pleby AI chat assistant |
| `DATA_SERVICE_URL` | Yes | URL where your data service is running |
| `NEXT_PUBLIC_APP_URL` | Yes | Your app's public URL |
| `ADMIN_EMAILS` | No | Comma-separated emails for admin access |
| `RESEND_API_KEY` | No | For transactional emails |
| `UPSTASH_REDIS_REST_URL` | No | For rate limiting (falls back to in-memory) |
| `UPSTASH_REDIS_REST_TOKEN` | No | For rate limiting |
| `NEXT_PUBLIC_VAPID_PUBLIC_KEY` | No | For browser push notifications |
| `NEXT_PUBLIC_SENTRY_DSN` | No | Error monitoring |
| `ALPACA_API_KEY` | No | For real-time crypto price quotes on ticker pages |
| `ALPACA_API_SECRET` | No | Alpaca API secret |
| `FMP_API_KEY` | No | Financial Modeling Prep — landing page quotes |
| `FINNHUB_API_KEY` | No | Finnhub — fallback price data |
| `BEEHIIV_API_KEY` | No | Newsletter subscriber management |
| `BEEHIIV_PUBLICATION_ID` | No | Beehiiv publication ID |

#### Data service (`apps/data-service/.env`)

| Variable | Required | Description |
|---|---|---|
| `SUPABASE_URL` | Yes | Your Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Yes | Supabase service role key |
| `ANTHROPIC_API_KEY` | Yes | For AI scoring engine (Claude Sonnet 5 + Haiku 4.5) |
| `RESEND_API_KEY` | Yes | For newsletter, briefings, and alert emails |
| `NEXT_PUBLIC_APP_URL` | Yes | Your app's public URL (used in email links) |
| `ENABLE_SCHEDULER` | Yes | Set to `true` to run scheduled jobs |
| `ALPACA_API_KEY` | No | Crypto market data via Alpaca |
| `ALPACA_API_SECRET` | No | Alpaca API secret |
| `FMP_API_KEY` | No | Financial Modeling Prep — stock/macro data |
| `FINNHUB_API_KEY` | No | News ingestion |
| `NEWS_API_KEY` | No | Prediction market news enrichment |
| `VAPID_PUBLIC_KEY` | No | Browser push notifications |
| `VAPID_PRIVATE_KEY` | No | Browser push notifications |
| `SENTRY_DSN` | No | Error monitoring |
| `ALERT_EMAIL` | No | Where uptime/failure alerts go. Blank disables them. |
| `EMAIL_FROM_*` | No | Sender addresses (`_WELCOME`, `_BRIEFING`, `_BRIEF`, `_NEWSLETTER`, `_ALERTS`, `_SIGNALS`). Must be on a domain you control in Resend. |
| `EFD_CONTACT_EMAIL` | No | Contact address sent in the congressional scraper's User-Agent |

### API Costs

| Service | Cost | What it's for |
|---|---|---|
| **Supabase** | Free tier available | Database, auth, real-time |
| **Anthropic (Claude)** | ~$6-12/month | AI scoring, briefings, newsletter |
| **Alpaca** | Free (IEX feed) | Crypto market data |
| **Polymarket** | Free | Prediction market data |
| **Resend** | Free tier available | Transactional email |
| **All news sources** | Free (RSS) | CoinDesk, ESPN, BBC, NPR, etc. |

The whole platform runs on roughly **$6-12/month** in AI costs. Everything else is free-tier or RSS.

## Crypto-Only Note

Plebs was originally built for stocks + crypto + prediction markets. Stock features are **disabled but not deleted** — the code is all still there. Stocks were disabled because:

1. The data costs for real-time stock data are prohibitive at the retail level (Alpaca's free IEX feed is a single exchange, not the full consolidated tape)
2. Stock signal accuracy was lower than crypto in backtests (31-37% vs 88-94% for crypto, small sample)
3. Running costs needed to stay under $30/month

To re-enable stocks, set `ENABLE_STOCK_SCORING=true` in the data service environment and un-hide the stock UI elements in the frontend Sidebar and SignalFeed components.

## Contributing

Contributions welcome. The codebase is large — here's where to start:

- **Frontend** — `apps/web/src/` — standard Next.js app with Tailwind
- **Scoring engine** — `apps/data-service/scoring/engine.py` — the core AI scoring logic with all deterministic gates
- **Ingestion** — `apps/data-service/ingestion/` — market data and news pipelines
- **Migrations** — `supabase/migrations/` — database schema

Open an issue before starting large changes.

## License

MIT — see [LICENSE](LICENSE).

## Disclaimer

Plebs.Finance is not a registered investment advisor. Nothing on this platform constitutes financial, investment, legal, or tax advice. All signals, analysis, and content are for informational purposes only. Trading and investing involve significant risk of loss. Past signal performance does not guarantee future results. Always do your own research.
