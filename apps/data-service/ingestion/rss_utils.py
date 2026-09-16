"""
Shared helper for RSS-based news ingestion (no API key required).
Used by ingestion.tech_news and ingestion.crypto_news to pull headlines
directly from named outlets, instead of relying on whatever source mix
a third-party news aggregator (e.g. Finnhub) happens to surface.
"""

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

import httpx
import sentry_sdk
from loguru import logger

from supabase_client import supabase
from ingestion.trusted_sources import is_trusted_source

_USER_AGENT = "Mozilla/5.0 (compatible; PlebsBot/1.0)"


def _matches_keywords(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(kw in lowered for kw in keywords)


def _parse_feed(xml_text: str) -> list[dict]:
    """Parse an RSS 2.0 feed into a list of {title, link, published} dicts."""
    items = []
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as e:
        logger.warning("rss_utils: feed parse failed — {}", e)
        return items

    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = item.findtext("pubDate")
        if not title or not link or not pub_date:
            continue
        try:
            published = parsedate_to_datetime(pub_date)
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        items.append({"title": title, "link": link, "published": published})

    return items


def ingest_rss_feeds(
    feeds: list[dict],
    *,
    asset_type: str,
    identifier: str,
    log_prefix: str,
    cutoff_hours: int = 24,
    per_feed_limit: int = 15,
) -> str:
    """
    Fetch, filter, dedup, and store headlines from a list of named RSS feeds.

    Each feed dict: {"url": str, "source": str, "keywords": tuple[str, ...] (optional)}
    A feed with "keywords" set only keeps items whose title matches one of them
    — for general feeds that need topical filtering (e.g. a general tech feed
    filtered down to AI stories).
    """
    total_inserted = 0
    cutoff = datetime.now(timezone.utc) - timedelta(hours=cutoff_hours)

    existing_headlines: set[str] = set()
    try:
        existing = (
            supabase.table("news_items")
            .select("headline")
            .gte("published_at", cutoff.isoformat())
            .limit(500)
            .execute()
        )
        for row in existing.data or []:
            existing_headlines.add(row["headline"][:80].lower())
    except Exception:
        pass

    for feed in feeds:
        if not is_trusted_source(feed["source"]):
            logger.warning("{}: {} is not in the trusted source list, skipping", log_prefix, feed["source"])
            continue

        try:
            resp = httpx.get(feed["url"], timeout=15.0, follow_redirects=True,
                              headers={"User-Agent": _USER_AGENT})
            resp.raise_for_status()
        except Exception as e:
            logger.warning("{}: fetch failed for {} — {}", log_prefix, feed["source"], e)
            sentry_sdk.capture_exception(e)
            continue

        items = _parse_feed(resp.text)
        if not items:
            continue

        keywords = feed.get("keywords")
        rows = []
        for it in items:
            if it["published"] < cutoff:
                continue
            if keywords and not _matches_keywords(it["title"], keywords):
                continue
            rows.append({
                "asset_type": asset_type,
                "identifier": identifier,
                "headline": it["title"][:500],
                "source": feed["source"],
                "url": it["link"][:1000],
                "sentiment_score": None,
                "published_at": it["published"].isoformat(),
            })

        if not rows:
            continue

        deduped = []
        for r in rows:
            key = r["headline"][:80].lower()
            if key not in existing_headlines:
                existing_headlines.add(key)
                deduped.append(r)

        deduped = deduped[:per_feed_limit]

        try:
            supabase.table("news_items").insert(deduped).execute()
            total_inserted += len(deduped)
            logger.info("{}: inserted {} articles from {}", log_prefix, len(deduped), feed["source"])
        except Exception as e:
            logger.warning("{}: insert failed for {} — {}", log_prefix, feed["source"], e)
            sentry_sdk.capture_exception(e)

    return f"{total_inserted} articles ingested"


# ─── Feed health monitor ─────────────────────────────────────────────────────

ALL_FEED_SOURCES = {
    "CoinDesk", "Decrypt", "The Block",
    "TechCrunch", "Ars Technica", "The Verge",
    "Al Jazeera", "Defense News",
    "ESPN", "BBC Sport",
    "NPR", "STAT News",
    "BBC News", "The New York Times",
}

SILENT_THRESHOLD_HOURS = 72


def check_feed_health() -> str:
    """Check which RSS feed sources have gone silent (no articles in 72h).
    Returns a summary and sends an email alert for any dead feeds."""
    import os

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=SILENT_THRESHOLD_HOURS)).isoformat()

    try:
        result = (
            supabase.table("news_items")
            .select("source")
            .gte("published_at", cutoff)
            .limit(1000)
            .execute()
        )
        active_sources = {r["source"] for r in (result.data or [])}
    except Exception as e:
        logger.error("feed_health: DB query failed — {}", e)
        return f"feed health check failed: {e}"

    silent = ALL_FEED_SOURCES - active_sources
    if not silent:
        logger.info("feed_health: all {} sources active in last {}h", len(ALL_FEED_SOURCES), SILENT_THRESHOLD_HOURS)
        return f"all {len(ALL_FEED_SOURCES)} feeds healthy"

    silent_list = ", ".join(sorted(silent))
    logger.warning("feed_health: {} silent sources (no articles in {}h): {}", len(silent), SILENT_THRESHOLD_HOURS, silent_list)

    try:
        import resend
        resend.api_key = os.environ.get("RESEND_API_KEY", "") or os.environ.get("RESEND_API_KEY_", "")
        if resend.api_key:
            alert_email = os.environ.get("ALERT_EMAIL", "")
            if not alert_email:
                return
            resend.Emails.send({
                "from": "Plebs Alerts <alerts@plebs.finance>",
                "to": [alert_email],
                "subject": f"Feed health: {len(silent)} silent source(s)",
                "text": (
                    f"The following news sources have produced 0 articles in the "
                    f"last {SILENT_THRESHOLD_HOURS} hours:\n\n"
                    f"{chr(10).join(f'  - {s}' for s in sorted(silent))}\n\n"
                    f"This likely means the RSS feed URL changed or went offline. "
                    f"Check the feed URLs in apps/data-service/ingestion/."
                ),
            })
    except Exception as e:
        logger.warning("feed_health: alert email failed — {}", e)

    return f"{len(silent)} silent: {silent_list}"
