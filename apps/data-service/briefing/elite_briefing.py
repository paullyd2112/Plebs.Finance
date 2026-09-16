"""
Personalized Elite morning briefing — 7:20am ET weekdays.

Each Elite subscriber gets a separate email tailored to their watchlist:
recent signals, news, and an AI-generated summary for the tickers they care about.
This is distinct from the main newsletter (which all subscribers get).
"""

import html
import os
from datetime import date, datetime, timedelta, timezone

import anthropic
import resend
import sentry_sdk
from loguru import logger

from supabase_client import supabase

resend.api_key = os.environ.get("RESEND_API_KEY", "") or os.environ.get("RESEND_API_KEY_", "")
FROM_ADDRESS   = os.environ.get("EMAIL_FROM_BRIEFING", "Pleby from Plebs <daily@plebs.finance>")
APP_URL        = os.environ.get("NEXT_PUBLIC_APP_URL", "https://plebs.finance")

_anthropic = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))


# ─── Data fetching ───────────────────────────────────────────────────────────

def _get_elite_subscribers() -> list[dict]:
    try:
        result = (
            supabase.table("newsletter_subscribers")
            .select("email, user_id")
            .eq("tier", "elite")
            .eq("unsubscribed", False)
            .execute()
        )
        return [s for s in (result.data or []) if s.get("user_id")]
    except Exception as e:
        logger.error("elite_briefing: subscriber fetch failed — {}", e)
        sentry_sdk.capture_exception(e)
        return []


def _get_watchlist(user_id: str) -> list[dict]:
    try:
        result = (
            supabase.table("watchlist")
            .select("identifier, asset_type")
            .eq("user_id", user_id)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning("elite_briefing: watchlist fetch failed for {} — {}", user_id, e)
        return []


def _get_signals_for_tickers(tickers: list[str]) -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(hours=36)).isoformat()
    try:
        result = (
            supabase.table("signals")
            .select("identifier, asset_type, direction, confidence, reasoning, time_horizon, created_at")
            .eq("is_backtest", False)
            .in_("identifier", tickers)
            .gte("created_at", since)
            .neq("direction", "HOLD")
            .order("created_at", desc=True)
            .limit(20)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning("elite_briefing: signals fetch failed — {}", e)
        return []


def _get_news_for_tickers(tickers: list[str]) -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(hours=36)).isoformat()
    try:
        result = (
            supabase.table("news_items")
            .select("identifier, headline, source, url, published_at")
            .in_("identifier", tickers)
            .gte("published_at", since)
            .order("published_at", desc=True)
            .limit(30)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning("elite_briefing: news fetch failed — {}", e)
        return []


def _get_prediction_highlights(limit: int = 5) -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
    try:
        result = (
            supabase.table("raw_prices")
            .select("identifier, price, volume, metadata")
            .eq("asset_type", "prediction")
            .gte("captured_at", since)
            .order("volume", desc=True)
            .limit(100)
        ).execute()

        seen, markets = set(), []
        for r in result.data or []:
            if r["identifier"] in seen:
                continue
            seen.add(r["identifier"])
            meta = r.get("metadata") or {}
            title = meta.get("title", "")
            cat = meta.get("category", "")
            if not title or cat == "sports":
                continue
            markets.append({
                "title": title,
                "yes_price": meta.get("yes_price", r.get("price")),
                "volume": r.get("volume", 0),
            })
        return markets[:limit]
    except Exception as e:
        logger.warning("elite_briefing: prediction markets fetch failed — {}", e)
        return []


# ─── AI summary ──────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are Pleby, the AI trading analyst at Plebs.finance. You're writing a personalized morning briefing for an Elite subscriber.

VOICE: Direct, data-driven, conversational. No filler. No corporate speak. Short sentences.

FORMAT: Write 2-4 short paragraphs covering the subscriber's watchlist and the broader market picture. Lead with the most actionable item.
- Bold ticker symbols: **BTC**, **ETH**, **SOL**
- Mention signal directions and confidence when available
- Reference specific news headlines when relevant
- If prediction market data is provided, weave in the most interesting probabilities as narrative anchors (e.g. "Polymarket has Fed cuts at 73% by September")
- Keep it under 300 words total
- End with one forward-looking sentence about what to watch today

Do NOT use: "delve", "navigate", "unpack", "game-changer", "it remains to be seen", exclamation marks, em dashes, or rhetorical questions."""


def _generate_summary(
    watchlist: list[dict],
    signals: list[dict],
    news: list[dict],
    predictions: list[dict] | None = None,
) -> str | None:
    tickers_str = ", ".join(f"{w['identifier']} ({w.get('asset_type', 'stock')})" for w in watchlist)

    parts = [f"The subscriber watches: {tickers_str}\n"]

    if signals:
        parts.append("RECENT SIGNALS ON THEIR WATCHLIST:")
        for s in signals:
            parts.append(
                f"  {s['identifier']} — {s['direction']} {s['confidence']}% "
                f"({s.get('time_horizon', 'short')}) — {s.get('reasoning', '')[:200]}"
            )

    if news:
        parts.append("\nRECENT NEWS ON THEIR WATCHLIST:")
        for n in news:
            parts.append(f"  [{n['identifier']}] {n['headline']} — {n.get('source', '')}")

    if predictions:
        parts.append("\nPREDICTION MARKETS (Polymarket — top markets by volume):")
        for p in predictions:
            yes_pct = round(float(p.get("yes_price", 0)) * 100)
            parts.append(f"  {p['title']} — YES {yes_pct}% (vol ${float(p.get('volume', 0)):,.0f})")
        parts.append("  Weave the most interesting prediction into the briefing as a data point.")

    if not signals and not news:
        parts.append("No new signals or news on their watchlist in the last 36 hours.")
        parts.append("Write a brief note acknowledging the quiet day and suggest they check back later.")

    try:
        response = _anthropic.messages.create(
            model="claude-sonnet-5",
            max_tokens=800,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": "\n".join(parts)}],
        )
        return response.content[0].text
    except Exception as e:
        logger.error("elite_briefing: AI generation failed — {}", e)
        sentry_sdk.capture_exception(e)
        return None


# ─── HTML rendering ──────────────────────────────────────────────────────────

def _md_to_html(text: str) -> str:
    import re
    safe = html.escape(text)
    safe = re.sub(
        r'\*\*(.+?)\*\*',
        r'<strong style="color:#fff;font-weight:700;">\1</strong>',
        safe,
    )
    return safe.replace("\n\n", "</p><p style='color:#d4d4d8;font-size:15px;line-height:1.7;margin:0 0 16px;'>")


MONO = "'SF Mono','Menlo','Consolas',monospace"


def _confidence_bar_color(confidence: int) -> str:
    if confidence >= 75:
        return "#22c55e"
    if confidence >= 50:
        return "#f59e0b"
    return "#71717a"


def _signal_row_html(s: dict) -> str:
    direction  = html.escape(s.get("direction", ""))
    identifier = html.escape(s.get("identifier", ""))
    asset_type = html.escape(s.get("asset_type", "stock"))
    confidence = s.get("confidence", 0)
    horizon    = html.escape(s.get("time_horizon", ""))
    dir_color  = "#22c55e" if direction in ("BUY", "YES") else "#ef4444"
    bar_color  = _confidence_bar_color(confidence)
    asset_url  = f"{APP_URL}/dashboard/asset/{asset_type}/{identifier}"

    return f"""
    <tr>
      <td style="padding:9px 12px;border-bottom:1px solid #27272a;">
        <a href="{asset_url}" style="font-family:{MONO};font-weight:700;color:#fff;text-decoration:none;">{identifier}</a>
        &nbsp;
        <span style="background:{dir_color}22;color:{dir_color};border:1px solid {dir_color}55;border-radius:4px;padding:1px 6px;font-size:11px;font-weight:700;">{direction}</span>
      </td>
      <td style="padding:9px 12px;border-bottom:1px solid #27272a;text-align:right;">
        <span style="font-family:{MONO};font-weight:700;color:{bar_color};font-size:13px;">{confidence}%</span>
        <span style="color:#52525b;font-size:11px;text-transform:uppercase;">&nbsp;{horizon}</span>
      </td>
    </tr>"""


def _render_email(summary_html: str, signals: list[dict], watchlist: list[dict]) -> tuple[str, str]:
    today = date.today().strftime("%A, %B %-d")
    ticker_count = len(watchlist)

    signals_table = ""
    if signals:
        rows = "".join(_signal_row_html(s) for s in signals[:8])
        signals_table = f"""
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="#18181b"
               style="background-color:#18181b;border-radius:10px;border:1px solid #27272a;margin:24px 0;">
          <tr><td style="padding:20px;">
            <div style="font-size:11px;font-weight:700;color:#52525b;text-transform:uppercase;letter-spacing:.08em;margin-bottom:10px;">Your watchlist signals</div>
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">{rows}</table>
            <a href="{APP_URL}/dashboard" style="display:inline-block;margin-top:12px;color:#22c55e;font-size:12px;font-weight:600;text-decoration:none;">Full signal feed →</a>
          </td></tr>
        </table>"""

    email_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark">
<meta name="supported-color-schemes" content="dark">
<!--[if mso]>
<style type="text/css">table {{border-collapse:collapse;}}</style>
<![endif]-->
</head>
<body style="margin:0;padding:0;background-color:#09090b;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#09090b;">
<tr><td align="center">
<!--[if mso]>
<table role="presentation" align="center" width="600" cellpadding="0" cellspacing="0"><tr><td>
<![endif]-->
  <div style="max-width:600px;margin:0 auto;padding:32px 20px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;text-align:left;">
    <div style="margin-bottom:20px;">
      <div style="font-size:20px;font-weight:800;color:#fff;letter-spacing:-0.01em;">
        plebs<span style="color:#22c55e;">.finance</span>
      </div>
      <div style="font-size:11px;color:#52525b;margin-top:2px;">{today} · Your personalized briefing</div>
    </div>

    <div style="margin-bottom:8px;">
      <span style="display:inline-block;background:#22c55e22;color:#22c55e;border:1px solid #22c55e55;border-radius:4px;padding:2px 8px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;">Elite briefing</span>
      <span style="color:#52525b;font-size:11px;margin-left:8px;">Tracking {ticker_count} assets</span>
    </div>

    <div style="border-top:1px solid #27272a;padding-top:20px;margin-top:16px;">
      <p style="color:#d4d4d8;font-size:15px;line-height:1.7;margin:0 0 16px;">{summary_html}</p>
    </div>

    {signals_table}

    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="#18181b"
           style="background-color:#18181b;border-radius:10px;border:1px solid #27272a;margin-top:24px;">
      <tr><td style="padding:16px;text-align:center;">
        <a href="{APP_URL}/dashboard/watchlist" style="color:#22c55e;font-weight:600;font-size:13px;text-decoration:none;">Manage your watchlist →</a>
      </td></tr>
    </table>

    <div style="border-top:1px solid #27272a;padding-top:16px;margin-top:24px;text-align:center;font-size:11px;color:#3f3f46;">
      Plebs.finance · Elite personalized briefing · Not financial advice ·
      <a href="{APP_URL}/unsubscribe" style="color:#52525b;">Unsubscribe</a>
    </div>
  </div>
<!--[if mso]>
</td></tr></table>
<![endif]-->
</td></tr>
</table>
</body>
</html>"""

    text = summary_html.replace("<strong style=\"color:#fff;font-weight:700;\">", "").replace("</strong>", "")
    text = text.replace("</p><p style='color:#d4d4d8;font-size:15px;line-height:1.7;margin:0 0 16px;'>", "\n\n")
    text += f"\n\nView dashboard: {APP_URL}/dashboard\nManage watchlist: {APP_URL}/dashboard/watchlist"

    return email_html, text


# ─── Main sender ─────────────────────────────────────────────────────────────

def send_elite_briefings() -> str:
    subscribers = _get_elite_subscribers()
    if not subscribers:
        logger.info("elite_briefing: no elite subscribers")
        return "0 elite subscribers"

    predictions = _get_prediction_highlights()

    sent, failed, skipped = 0, 0, 0
    last_error: str = ""

    for sub in subscribers:
        email   = sub.get("email")
        user_id = sub["user_id"]

        if not email:
            skipped += 1
            continue

        watchlist = _get_watchlist(user_id)
        if not watchlist:
            skipped += 1
            continue

        tickers = [w["identifier"] for w in watchlist]
        signals = _get_signals_for_tickers(tickers)
        news    = _get_news_for_tickers(tickers)

        summary = _generate_summary(watchlist, signals, news, predictions)
        if not summary:
            failed += 1
            continue

        summary_html = _md_to_html(summary)
        html_body, text_body = _render_email(summary_html, signals, watchlist)

        today_str = date.today().strftime("%b %-d")
        subject = f"Your watchlist briefing — {today_str}"

        try:
            resend.Emails.send({
                "from":    FROM_ADDRESS,
                "to":      [email],
                "subject": subject,
                "html":    html_body,
                "text":    text_body,
            })
            sent += 1
            logger.debug("elite_briefing: sent to {} ({} tickers)", email, len(tickers))
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            logger.error("elite_briefing: send failed for {} — {}", email, last_error)
            sentry_sdk.capture_exception(e)
            failed += 1

    summary = f"{sent} sent, {failed} failed, {skipped} skipped"
    if last_error:
        summary += f" | last_error: {last_error}"
    logger.info("elite_briefing complete — {}", summary)
    return summary
