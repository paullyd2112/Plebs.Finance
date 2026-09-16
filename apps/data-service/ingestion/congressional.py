"""
Congressional trades ingestion — multi-source.
Primary: direct scraper against efdsearch.senate.gov (free, official source — the
         Senate Stock Watcher/other third-party feeds have all gone stale or paid).
Secondary: Senate Stock Watcher's static JSON (kept as a harmless fallback; dead
           since ~Dec 2020 but costs nothing to still try).
Tertiary: Finnhub /stock/congressional-trading (premium).
Quaternary: FMP /v4/senate-trading-rss-feed + house-disclosure-rss-feed (premium v4).
Runs daily at 8:00am ET via scheduler.
"""

import os
import re
import time
from datetime import date, datetime, timedelta, timezone

import httpx
import sentry_sdk
from bs4 import BeautifulSoup
from loguru import logger
from dotenv import load_dotenv

from supabase_client import supabase
from ingestion.congress_members import get_party

load_dotenv()

FINNHUB_KEY   = os.environ.get("FINNHUB_API_KEY", "")
FMP_BASE      = "https://financialmodelingprep.com/api/v4"
LOOKBACK_DAYS = 90
FMP_MAX_PAGES = 5

SENATE_WATCHER_URL = (
    "https://raw.githubusercontent.com/"
    "timothycarambat/senate-stock-watcher-data/master/aggregate/all_transactions.json"
)

# ── efdsearch.senate.gov scraper constants ──────────────────────────────────
EFD_ROOT         = "https://efdsearch.senate.gov"
EFD_LANDING_URL  = f"{EFD_ROOT}/search/home/"
EFD_SEARCH_URL   = f"{EFD_ROOT}/search/report/data/"
EFD_PTR_TYPE     = "[11]"   # "Periodic Transaction Report" — the STOCK Act trade-disclosure filing type
EFD_PDF_PREFIX   = "/search/view/paper/"  # paper-filed reports are scanned PDFs — skipped, not OCR'd
EFD_BATCH_SIZE   = 100
EFD_MAX_RUNTIME_S = 120  # wall-clock budget so a slow/hanging report page can't stall the whole run
# efdsearch.senate.gov expects a contact address in the UA string — set EFD_CONTACT_EMAIL to yours.
EFD_USER_AGENT   = (
    "PlebsFinance/1.0 (congressional trade disclosure aggregator; "
    f"contact: {os.environ.get('EFD_CONTACT_EMAIL', 'opensource@example.com')})"
)


def _fmp_key() -> str:
    return os.environ.get("FMP_API_KEY", "")


def _get_tracked_tickers() -> list[str]:
    try:
        result = (
            supabase.table("raw_prices")
            .select("identifier")
            .eq("asset_type", "stock")
            .execute()
        )
        return list({r["identifier"] for r in (result.data or [])})
    except Exception as e:
        logger.warning("congressional: could not fetch tracked tickers — {}", e)
        return []


def _normalize_transaction(raw: str) -> str | None:
    raw = (raw or "").lower()
    if "purchase" in raw or "buy" in raw:
        return "buy"
    if "sale" in raw or "sell" in raw or "exchange" in raw:
        return "sell"
    return None


# ── efdsearch.senate.gov scraper (primary, free, official source) ──────────

def _efd_get_csrf_and_agree(client: httpx.Client) -> str | None:
    """Load the landing page, extract the CSRF token, and accept the required
    legal agreement — efdsearch.senate.gov blocks all search requests for a
    session until this is done. Returns None on success, or a diagnostic
    string identifying exactly which step failed."""
    try:
        resp = client.get(EFD_LANDING_URL)
        resp.raise_for_status()
    except Exception as e:
        return f"landing page fetch failed: {type(e).__name__}: {e}"

    soup = BeautifulSoup(resp.text, "html.parser")
    token_input = soup.find(attrs={"name": "csrfmiddlewaretoken"})
    if not token_input or not token_input.get("value"):
        return f"csrfmiddlewaretoken not found in landing page HTML (status {resp.status_code}, {len(resp.text)} bytes)"
    csrf_token = token_input["value"]

    try:
        agree_resp = client.post(
            EFD_LANDING_URL,
            headers={"Referer": EFD_LANDING_URL, "Origin": EFD_ROOT},
            data={"csrfmiddlewaretoken": csrf_token, "prohibition_agreement": "1"},
        )
        agree_resp.raise_for_status()
    except Exception as e:
        return f"agreement POST failed: {type(e).__name__}: {e}"

    if "csrftoken" not in client.cookies:
        return f"agreement POST succeeded (status {agree_resp.status_code}) but no csrftoken cookie was set — cookies present: {list(client.cookies.keys())}"

    return None


def _efd_search_ptrs(client: httpx.Client, start_date: date) -> tuple[list[list], str | None]:
    """Page through Periodic Transaction Report filings since start_date.
    Returns (raw DataTables rows [first, last, _, link_html, date_received], diagnostic).
    diagnostic is None on a clean run (including a legitimate zero-results page)."""
    rows: list[list] = []
    offset = 0
    csrf_token = client.cookies.get("csrftoken", "")

    while True:
        try:
            resp = client.post(
                EFD_SEARCH_URL,
                headers={"X-CSRFToken": csrf_token, "Referer": EFD_LANDING_URL},
                data={
                    "report_types": EFD_PTR_TYPE,
                    "submitted_start_date": start_date.strftime("%m/%d/%Y 00:00:00"),
                    "submitted_end_date": "",
                    "start": str(offset),
                    "length": str(EFD_BATCH_SIZE),
                },
            )
            resp.raise_for_status()
        except Exception as e:
            body_snippet = getattr(e, "response", None)
            body_snippet = body_snippet.text[:300] if body_snippet is not None else ""
            diag = f"search POST at offset {offset} failed: {type(e).__name__}: {e} — body: {body_snippet!r}"
            logger.warning("congressional: EFD {}", diag)
            return rows, diag

        try:
            payload = resp.json()
        except Exception as e:
            diag = f"search response at offset {offset} wasn't JSON (status {resp.status_code}): {resp.text[:300]!r}"
            logger.warning("congressional: EFD {}", diag)
            return rows, diag

        batch = payload.get("data")
        if batch is None:
            diag = f"search response JSON at offset {offset} had no 'data' key — keys present: {list(payload.keys())}"
            logger.warning("congressional: EFD {}", diag)
            return rows, diag

        rows.extend(batch)
        if len(batch) < EFD_BATCH_SIZE:
            break
        offset += EFD_BATCH_SIZE

    return rows, None


def _efd_parse_report_link(link_html: str) -> str | None:
    """Extract the href from the anchor tag in a search result row."""
    m = re.search(r'href="([^"]+)"', link_html or "")
    return m.group(1) if m else None


def _efd_fetch_report_transactions(client: httpx.Client, report_url: str) -> list[dict]:
    """Fetch and parse one PTR filing's transaction table.
    Column layout (positional, matches efdsearch's report template):
    [owner, tx_date, notif_date, ticker, asset_name, asset_type, order_type, tx_amount]."""
    resp = client.get(report_url)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    tbody = soup.find("tbody")
    if not tbody:
        return []

    txs = []
    for tr in tbody.find_all("tr"):
        cols = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cols) < 8:
            continue
        txs.append({
            "tx_date":    cols[1],
            "ticker":     cols[3],
            "order_type": cols[6],
            "tx_amount":  cols[7],
        })
    return txs


def _fetch_senate_efd() -> tuple[list[dict], str]:
    """Scrape efdsearch.senate.gov directly for Periodic Transaction Reports —
    the official, always-current source now that Senate Stock Watcher's static
    feed has gone stale. Paper-filed (scanned PDF) reports are skipped, matching
    the same tradeoff every open-source scraper for this site makes.

    Returns (rows, diagnostic) — diagnostic explains exactly where the funnel
    stopped when rows is empty, since "0 rows" alone doesn't distinguish a
    broken session bootstrap from a search-format mismatch from a genuinely
    quiet reporting period."""
    cutoff = date.today() - timedelta(days=LOOKBACK_DAYS)
    rows: list[dict] = []
    start = datetime.now(timezone.utc)
    reports_parsed, reports_failed, skipped_pdf, txs_seen, txs_filtered_out = 0, 0, 0, 0, 0

    try:
        with httpx.Client(
            timeout=30, headers={"User-Agent": EFD_USER_AGENT}, follow_redirects=True,
        ) as client:
            bootstrap_error = _efd_get_csrf_and_agree(client)
            if bootstrap_error:
                diag = f"session bootstrap failed — {bootstrap_error}"
                logger.warning("congressional: EFD {}", diag)
                return [], diag

            search_rows, search_error = _efd_search_ptrs(client, cutoff)
            if search_error:
                return [], f"search failed after {len(search_rows)} rows — {search_error}"
            if not search_rows:
                return [], f"session bootstrap OK, search returned 0 PTR filings since {cutoff.isoformat()} (report_types={EFD_PTR_TYPE})"

            for row in search_rows:
                if (datetime.now(timezone.utc) - start).total_seconds() > EFD_MAX_RUNTIME_S:
                    logger.warning(
                        "congressional: EFD hit {}s runtime budget — stopping early with partial results",
                        EFD_MAX_RUNTIME_S,
                    )
                    break
                if len(row) < 5:
                    continue

                first, last, _unused, link_html, date_received = row[0], row[1], row[2], row[3], row[4]
                # efdsearch's own name cells occasionally carry a trailing comma
                # (e.g. "Moran," instead of "Moran") — strip stray punctuation
                # from each part before joining.
                first = (first or "").strip().rstrip(",").strip()
                last = (last or "").strip().rstrip(",").strip()
                politician = f"{first} {last}".strip()

                href = _efd_parse_report_link(link_html)
                if not href:
                    continue
                if href.startswith(EFD_PDF_PREFIX):
                    skipped_pdf += 1
                    continue

                report_date = _parse_watcher_date(date_received)

                try:
                    txs = _efd_fetch_report_transactions(client, f"{EFD_ROOT}{href}")
                    reports_parsed += 1
                except Exception as e:
                    reports_failed += 1
                    logger.debug("congressional: EFD report parse failed for {} — {}", politician, e)
                    continue

                for tx in txs:
                    txs_seen += 1
                    ticker = (tx["ticker"] or "").strip().upper()
                    if not ticker or ticker in ("--", "N/A"):
                        txs_filtered_out += 1
                        continue
                    txn = _normalize_transaction(tx["order_type"])
                    if txn is None:
                        txs_filtered_out += 1
                        continue
                    trade_date = _parse_watcher_date(tx["tx_date"])
                    if not trade_date or trade_date < cutoff:
                        txs_filtered_out += 1
                        continue

                    rows.append({
                        "politician":   politician,
                        "party":        get_party(politician),
                        "ticker":       ticker,
                        "transaction":  txn,
                        "amount_range": tx["tx_amount"].strip(),
                        "trade_date":   trade_date.isoformat(),
                        "report_date":  report_date.isoformat() if report_date else None,
                    })

                time.sleep(0.5)

            funnel = (
                f"{len(search_rows)} filings found, {skipped_pdf} paper-filed (skipped), "
                f"{reports_parsed} parsed ({reports_failed} failed to parse), "
                f"{txs_seen} transaction rows seen ({txs_filtered_out} filtered out)"
            )

            if not rows:
                return [], f"funnel produced 0 final rows — {funnel}"

    except Exception as e:
        diag = f"unhandled exception: {type(e).__name__}: {e}"
        logger.error("congressional: EFD scraper failed — {}", diag)
        sentry_sdk.capture_exception(e)
        return [], diag

    logger.info("congressional: EFD scraper parsed {} trades — {}", len(rows), funnel)
    return rows, funnel


# ── Senate Stock Watcher source (secondary, free but stale) ─────────────────

def _parse_watcher_date(raw: str) -> date | None:
    """Parse MM/DD/YYYY or YYYY-MM-DD date strings."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except (ValueError, TypeError):
        pass
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            from datetime import datetime as _dt
            return _dt.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _fetch_senate_watcher() -> list[dict]:
    """Pull from the Senate Stock Watcher GitHub data repo — free, no auth."""
    cutoff = date.today() - timedelta(days=LOOKBACK_DAYS)
    rows: list[dict] = []

    try:
        resp = httpx.get(SENATE_WATCHER_URL, timeout=30)
        if resp.status_code != 200:
            logger.error(
                "congressional: Senate Stock Watcher returned HTTP {} — expected raw JSON",
                resp.status_code,
            )
            sentry_sdk.capture_message(
                f"Senate Stock Watcher HTTP {resp.status_code}"
            )
            return []

        records: list[dict] = resp.json()
        logger.info("congressional: Senate Stock Watcher returned {} raw records", len(records))
    except Exception as e:
        logger.error("congressional: Senate Stock Watcher fetch failed — {}", e)
        sentry_sdk.capture_exception(e)
        return []

    for r in records:
        ticker = (r.get("ticker") or "").strip().upper()
        if not ticker or ticker in ("--", "N/A", ""):
            continue

        txn = _normalize_transaction(r.get("type") or "")
        if txn is None:
            continue

        trade_date = _parse_watcher_date(r.get("transaction_date"))
        if not trade_date or trade_date < cutoff:
            continue

        politician = (r.get("senator") or "").strip()
        if not politician:
            continue

        rows.append({
            "politician":   politician,
            "party":        get_party(politician),
            "ticker":       ticker,
            "transaction":  txn,
            "amount_range": (r.get("amount") or "").strip(),
            "trade_date":   trade_date.isoformat(),
            "report_date":  None,
        })

    logger.info(
        "congressional: Senate Stock Watcher parsed {} trades within {}-day window",
        len(rows), LOOKBACK_DAYS,
    )
    return rows


# ── Finnhub source (secondary, premium) ─────────────────────────────────────

def _fetch_finnhub(tickers: list[str]) -> list[dict]:
    if not FINNHUB_KEY:
        logger.info("congressional: FINNHUB_API_KEY not set — skipping Finnhub")
        return []

    cutoff = date.today() - timedelta(days=LOOKBACK_DAYS)
    from_date = cutoff.isoformat()
    to_date = date.today().isoformat()
    rows: list[dict] = []

    for ticker in tickers:
        try:
            resp = httpx.get(
                "https://finnhub.io/api/v1/stock/congressional-trading",
                params={
                    "symbol": ticker,
                    "from": from_date,
                    "to": to_date,
                    "token": FINNHUB_KEY,
                },
                timeout=15,
            )
            if resp.status_code == 429:
                logger.warning("congressional: Finnhub rate limit hit at {} — stopping", ticker)
                break
            if resp.status_code in (401, 403):
                logger.warning("congressional: Finnhub congressional endpoint requires premium (HTTP {})", resp.status_code)
                sentry_sdk.capture_message(f"Finnhub congressional 401/403: HTTP {resp.status_code}")
                return []
            if resp.status_code == 404:
                logger.warning("congressional: Finnhub congressional endpoint not found (404) — not on this plan")
                sentry_sdk.capture_message("Finnhub congressional 404: endpoint not available")
                return []
            resp.raise_for_status()

            data = resp.json().get("data") or []
            for r in data:
                txn_date_raw = r.get("transactionDate") or ""
                try:
                    txn_date = date.fromisoformat(txn_date_raw[:10])
                except (ValueError, TypeError):
                    continue
                if txn_date < cutoff:
                    continue

                txn = _normalize_transaction(r.get("transactionType") or r.get("transaction") or "")
                if txn is None:
                    continue

                filing_raw = r.get("filingDate") or ""
                try:
                    filing_date = date.fromisoformat(filing_raw[:10])
                except (ValueError, TypeError):
                    filing_date = None

                rows.append({
                    "politician":   (r.get("name") or r.get("representative") or "").strip(),
                    "party":        (r.get("party") or "").strip(),
                    "ticker":       ticker,
                    "transaction":  txn,
                    "amount_range": (r.get("amountFrom") or r.get("amount") or ""),
                    "trade_date":   txn_date.isoformat(),
                    "report_date":  filing_date.isoformat() if filing_date else None,
                })

            time.sleep(0.12)
        except Exception as e:
            logger.warning("congressional: Finnhub fetch failed for {} — {}", ticker, e)
            continue

    logger.info("congressional: Finnhub returned {} records across {} tickers", len(rows), len(tickers))
    return rows


# ── FMP source (tertiary, premium) ──────────────────────────────────────────

def _fetch_fmp_chamber(endpoint: str, chamber: str) -> list[dict]:
    if not _fmp_key():
        return []
    rows: list[dict] = []
    for page in range(FMP_MAX_PAGES):
        try:
            resp = httpx.get(
                f"{FMP_BASE}/{endpoint}",
                params={"page": page, "apikey": _fmp_key()},
                timeout=30,
            )
            if resp.status_code in (401, 403):
                logger.error("congressional: FMP {} key invalid/expired (HTTP {}) — v4 may require premium", chamber, resp.status_code)
                sentry_sdk.capture_message(f"FMP {chamber} 401/403: HTTP {resp.status_code}")
                return []
            resp.raise_for_status()
            data: list[dict] = resp.json() or []
            if not data:
                break
            rows.extend(data)
            if len(data) < 100:
                break
        except Exception as e:
            logger.error("congressional: FMP {} page {} fetch failed — {}", chamber, page, e)
            sentry_sdk.capture_exception(e)
            break
    return rows


def _parse_fmp(records: list[dict], chamber: str) -> list[dict]:
    cutoff = date.today() - timedelta(days=LOOKBACK_DAYS)
    name_key = "senator" if chamber == "senate" else "representative"
    rows = []
    for r in records:
        ticker = (r.get("ticker") or "").strip().upper()
        if not ticker or ticker in ("--", "N/A", ""):
            continue

        txn = _normalize_transaction(r.get("type") or r.get("transaction") or "")
        if txn is None:
            continue

        try:
            trade_date = date.fromisoformat((r.get("transactionDate") or r.get("date") or "")[:10])
        except (ValueError, TypeError):
            continue
        if trade_date < cutoff:
            continue

        try:
            report_date = date.fromisoformat((r.get("disclosureDate") or "")[:10])
        except (ValueError, TypeError):
            report_date = None

        politician = (r.get(name_key) or r.get("officialFullName") or "").strip()
        # Finnhub covers House + Senate; get_party() only has current senators,
        # so House names fall through to "" harmlessly.
        rows.append({
            "politician":   politician,
            "party":        get_party(politician),
            "ticker":       ticker,
            "transaction":  txn,
            "amount_range": (r.get("amount") or "").strip(),
            "trade_date":   trade_date.isoformat(),
            "report_date":  report_date.isoformat() if report_date else None,
        })
    return rows


def _fetch_fmp_all() -> list[dict]:
    if not _fmp_key():
        logger.info("congressional: FMP_API_KEY not set — skipping FMP fallback")
        return []

    senate_raw = _fetch_fmp_chamber("senate-trading-rss-feed", "senate")
    house_raw = _fetch_fmp_chamber("house-disclosure-rss-feed", "house")

    senate = _parse_fmp(senate_raw, "senate")
    house = _parse_fmp(house_raw, "house")

    combined = senate + house
    logger.info("congressional: FMP returned {} senate + {} house = {} total", len(senate), len(house), len(combined))
    return combined


# ── Dedup + insert ───────────────────────────────────────────────────────────

def _dedup(rows: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    out = []
    for r in rows:
        key = (r["politician"], r["ticker"], r["transaction"], r["trade_date"])
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _get_existing_keys() -> set[tuple]:
    cutoff = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    try:
        result = (
            supabase.table("congressional_trades")
            .select("politician, ticker, transaction, trade_date")
            .gte("trade_date", cutoff)
            .execute()
        )
        return {
            (r["politician"], r["ticker"], r["transaction"], r["trade_date"])
            for r in (result.data or [])
        }
    except Exception as e:
        logger.warning("congressional: failed to fetch existing keys — {}", e)
        return set()


def ingest_congressional() -> str:
    sources_tried = []

    # 1. efdsearch.senate.gov direct scraper (free, official, always-current)
    all_rows, efd_diag = _fetch_senate_efd()
    if all_rows:
        # efd_diag carries the full funnel breakdown (filings found, paper-filed
        # skipped, parsed/failed, tx rows seen/filtered) even on success -- surface
        # it always, not just on the 0-rows failure path below, since "55 rows"
        # alone can't distinguish a healthy-but-sparse period from real undercounting.
        sources_tried.append(f"efd({len(all_rows)}: {efd_diag})")

    # 2. Senate Stock Watcher (free, but its static feed has been stale since
    #    ~Dec 2020 — kept only as a harmless zero-cost fallback)
    if not all_rows:
        sources_tried.append(f"efd(0: {efd_diag})")
        all_rows = _fetch_senate_watcher()
        if all_rows:
            sources_tried.append(f"senate_watcher({len(all_rows)})")

    # 3. Finnhub (premium, per-ticker)
    if not all_rows:
        sources_tried.append("senate_watcher(0)")
        tickers = _get_tracked_tickers()
        all_rows = _fetch_finnhub(tickers) if tickers else []
        if all_rows:
            sources_tried.append(f"finnhub({len(all_rows)})")

    # 4. FMP (premium, bulk)
    if not all_rows:
        sources_tried.append("finnhub(0)")
        all_rows = _fetch_fmp_all()
        sources_tried.append(f"fmp({len(all_rows)})")

    all_rows = _dedup(all_rows)
    source_log = ", ".join(sources_tried)

    if not all_rows:
        msg = f"0 inserted — all sources returned empty [{source_log}]"
        logger.warning("congressional: {}", msg)
        sentry_sdk.capture_message(f"congressional: {msg}")
        return msg

    existing = _get_existing_keys()
    new_rows = [
        r for r in all_rows
        if (r["politician"], r["ticker"], r["transaction"], r["trade_date"]) not in existing
    ]

    if not new_rows:
        logger.info("congressional: {} fetched, all already stored [{}]", len(all_rows), source_log)
        return f"0 inserted (all duplicates) [{source_log}]"

    try:
        supabase.table("congressional_trades").insert(new_rows).execute()
        logger.info("congressional: inserted {} new trades ({} fetched) [{}]", len(new_rows), len(all_rows), source_log)
        return f"{len(new_rows)} inserted [{source_log}]"
    except Exception as e:
        logger.error("congressional: insert failed — {}", e)
        sentry_sdk.capture_exception(e)
        return f"insert error: {e}"
