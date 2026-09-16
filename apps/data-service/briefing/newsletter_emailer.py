"""
Newsletter emailer — sends newsletter via Resend based on subscriber frequency.
Free subscribers get editorial + CTA.
Pro/Elite subscribers get the same editorial + personalized signal data.

Frequency options (stored on newsletter_subscribers.newsletter_frequency):
  daily          — every day (default)
  weekdays       — Monday–Friday only
  every_other_day — odd day-of-year
  weekly         — Monday only (week recap framing)
  weekends       — Saturday & Sunday only
"""

import html
import os
import re
import time
from datetime import date

import resend
import sentry_sdk
from loguru import logger

from supabase_client import supabase

MAX_RETRIES    = 3
RETRY_DELAYS   = [2, 5, 12]

resend.api_key   = os.environ.get("RESEND_API_KEY", "") or os.environ.get("RESEND_API_KEY_", "")
FROM_ADDRESS     = os.environ.get("EMAIL_FROM_NEWSLETTER", "Pleby from Plebs <daily@plebs.finance>")
APP_URL          = os.environ.get("NEXT_PUBLIC_APP_URL", "https://plebs.finance")


# ─── Fetch data ───────────────────────────────────────────────────────────────

def _get_todays_newsletter() -> dict | None:
    today = date.today().isoformat()
    try:
        result = (
            supabase.table("daily_briefings")
            .select("*")
            .eq("date", today)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error("newsletter_emailer: fetch failed — {}", e)
        return None


def _get_subscribers() -> list[dict]:
    try:
        result = (
            supabase.table("newsletter_subscribers")
            .select("email, user_id, tier, newsletter_frequency")
            .eq("unsubscribed", False)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error("newsletter_emailer: subscriber fetch failed — {}", e)
        sentry_sdk.capture_exception(e)
        return []


def _should_send_today(frequency: str) -> bool:
    """Check if a subscriber's frequency matches today's date."""
    today = date.today()
    dow = today.weekday()  # 0=Mon … 6=Sun

    if frequency == "daily":
        return True
    if frequency == "weekdays":
        return dow < 5
    if frequency == "weekends":
        return dow >= 5
    if frequency == "weekly":
        return dow == 0  # Monday
    if frequency == "every_other_day":
        return today.timetuple().tm_yday % 2 == 1
    return True  # unknown frequency → send


def _get_user_signals(user_id: str, top_signals: list[dict]) -> list[dict]:
    """For paid users, try to match signals to their watchlist."""
    if not user_id or not top_signals:
        return top_signals
    try:
        wl = (
            supabase.table("watchlist")
            .select("identifier")
            .eq("user_id", user_id)
            .execute()
        )
        tickers = {w["identifier"] for w in (wl.data or [])}
        if not tickers:
            return top_signals
        matched = [s for s in top_signals if s.get("identifier") in tickers]
        return matched if matched else top_signals
    except Exception:
        return top_signals


# ─── HTML renderers ───────────────────────────────────────────────────────────
#
# Outlook desktop renders HTML email with Microsoft Word's engine, not a real
# browser — it ignores `max-width` on <div>s (breaks centering), is
# inconsistent about `background` on <div>/<p> (breaks card backgrounds), and
# has no flexbox support. Every "card" below is built as a <table
# bgcolor="..."> instead of a styled <div>, since Outlook honors table
# background/padding reliably. `border-radius` is allowed to degrade to
# square corners in Outlook — cosmetic only, not a layout break.

def _card(inner_html: str, padding: str = "20px 22px", extra_style: str = "") -> str:
    """Outlook-safe card: bgcolor attribute (not just CSS) + table layout."""
    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="#111113" class="og-bg1"
           style="background-color:#111113;border:1px solid #1e1e22;border-radius:10px;{extra_style}">
      <tr><td style="padding:{padding};">
        {inner_html}
      </td></tr>
    </table>"""


def _md_to_html(text: str) -> str:
    """Convert markdown links and bold to styled HTML for email."""
    safe = html.escape(text)
    safe = re.sub(
        r'\[(.+?)\]\((.+?)\)',
        lambda m: f'<a href="{m.group(2)}" style="color:#22c55e;text-decoration:underline;">{m.group(1)}</a>',
        safe,
    )
    safe = re.sub(
        r'\*\*(.+?)\*\*',
        r'<strong style="color:#fff;font-weight:700;">\1</strong>',
        safe,
    )
    return safe


def _story_html(story: dict) -> str:
    category      = html.escape(story.get("category", ""))
    headline      = html.escape(story.get("headline", ""))
    what_happened = _md_to_html(story.get("what_happened", ""))
    what_we_know  = _md_to_html(story.get("what_we_know", ""))
    could_mean    = _md_to_html(story.get("could_mean", ""))
    watch         = _md_to_html(story.get("watch", ""))

    inner = f"""
      <div style="margin-bottom:8px;">
        <span style="display:inline-block;background:#22c55e18;color:#22c55e;border:1px solid #22c55e44;border-radius:4px;padding:3px 10px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;">{category}</span>
      </div>
      <div style="font-size:19px;font-weight:700;color:#f4f4f5;margin-bottom:16px;line-height:1.35;">{headline}</div>
      <div style="margin-bottom:12px;">
        <span style="font-size:10px;font-weight:700;color:#71717a;text-transform:uppercase;letter-spacing:.08em;">What happened</span>
        <div style="color:#d4d4d8;font-size:14px;line-height:1.75;margin-top:5px;">{what_happened}</div>
      </div>
      <div style="margin-bottom:12px;">
        <span style="font-size:10px;font-weight:700;color:#71717a;text-transform:uppercase;letter-spacing:.08em;">What we know</span>
        <div style="color:#d4d4d8;font-size:14px;line-height:1.75;margin-top:5px;">{what_we_know}</div>
      </div>
      <div style="margin-bottom:12px;">
        <span style="font-size:10px;font-weight:700;color:#71717a;text-transform:uppercase;letter-spacing:.08em;">What it could mean</span>
        <div style="color:#d4d4d8;font-size:14px;line-height:1.75;margin-top:5px;">{could_mean}</div>
      </div>
      <div>
        <span style="font-size:10px;font-weight:700;color:#22c55e;text-transform:uppercase;letter-spacing:.08em;">What to watch</span>
        <div style="color:#d4d4d8;font-size:14px;line-height:1.75;margin-top:5px;">{watch}</div>
      </div>"""

    return f'<div style="margin-bottom:28px;">{_card(inner)}</div>'


MONO = "'SF Mono','Menlo','Consolas',monospace"


def _confidence_bar_color(confidence: int) -> str:
    if confidence >= 75:
        return "#22c55e"
    if confidence >= 50:
        return "#f59e0b"
    return "#71717a"


def _resolve_prediction_titles(signals: list[dict]) -> dict[str, str]:
    """Prediction-market identifiers are Polymarket conditionId hex hashes,
    not readable names (observed live 2026-07-06 in the newsletter's "Your
    signals today" table) — look up raw_prices.metadata.title so the email
    can show the actual market question instead."""
    ids = list({s["identifier"] for s in signals if s.get("asset_type") == "prediction" and s.get("identifier")})
    if not ids:
        return {}
    try:
        rows = (
            supabase.table("raw_prices")
            .select("identifier, metadata")
            .eq("asset_type", "prediction")
            .in_("identifier", ids)
            .execute()
        )
        titles: dict[str, str] = {}
        for row in rows.data or []:
            title = (row.get("metadata") or {}).get("title")
            if title and row["identifier"] not in titles:
                titles[row["identifier"]] = title
        return titles
    except Exception as e:
        logger.warning("newsletter_emailer: prediction title lookup failed — {}", e)
        return {}


def _signals_html(signals: list[dict], prediction_titles: dict[str, str] | None = None) -> str:
    if not signals:
        return ""
    prediction_titles = prediction_titles or {}
    rows = ""
    for s in signals:
        direction  = html.escape(s.get("direction", ""))
        identifier = s.get("identifier", "")
        asset_type = html.escape(s.get("asset_type", "stock"))
        confidence = s.get("confidence", 0)
        horizon    = html.escape(s.get("time_horizon", ""))
        dir_color  = "#22c55e" if direction in ("BUY", "YES") else "#ef4444"
        bar_color  = _confidence_bar_color(confidence)
        asset_url  = f"{APP_URL}/dashboard/asset/{asset_type}/{identifier}"
        display_name = prediction_titles.get(identifier, identifier) if s.get("asset_type") == "prediction" else identifier
        display_name = html.escape(display_name)
        rows += f"""
        <tr>
          <td style="padding:9px 12px;border-bottom:1px solid #1e1e22;">
            <a href="{asset_url}" style="font-family:{MONO};font-weight:700;color:#fff;text-decoration:none;">{display_name}</a>
            &nbsp;
            <span style="background:{dir_color}22;color:{dir_color};border:1px solid {dir_color}55;border-radius:4px;padding:1px 6px;font-size:11px;font-weight:700;">{direction}</span>
          </td>
          <td style="padding:9px 12px;border-bottom:1px solid #1e1e22;text-align:right;">
            <span style="font-family:{MONO};font-weight:700;color:{bar_color};font-size:13px;">{confidence}%</span>
            <span style="color:#52525b;font-size:11px;text-transform:uppercase;">&nbsp;{horizon}</span>
          </td>
        </tr>"""
    inner = f"""
      <div style="font-size:11px;font-weight:700;color:#71717a;text-transform:uppercase;letter-spacing:.08em;margin-bottom:10px;">Your signals today</div>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">{rows}</table>
      <a href="{APP_URL}/dashboard" style="display:inline-block;margin-top:12px;color:#22c55e;font-size:12px;font-weight:600;text-decoration:none;">Full signal feed &rarr;</a>"""
    return f'<div style="margin:24px 0;">{_card(inner)}</div>'


def _options_html(options: list[dict]) -> str:
    if not options:
        return ""
    rows = ""
    for o in options:
        ticker      = html.escape(o.get("ticker", ""))
        option_type = html.escape(o.get("option_type", "").upper())
        volume      = o.get("volume", 0)
        oi          = o.get("open_interest", 0)
        color       = "#22c55e" if option_type == "CALL" else "#ef4444"
        rows += f"""
        <tr>
          <td style="padding:6px 0;border-bottom:1px solid #1e1e22;font-family:{MONO};font-weight:700;color:#fff;">{ticker}</td>
          <td style="padding:6px 0;border-bottom:1px solid #1e1e22;color:{color};font-size:12px;font-weight:700;text-align:center;">{option_type}</td>
          <td style="padding:6px 0;border-bottom:1px solid #1e1e22;color:#71717a;font-size:12px;text-align:right;">vol {volume:,} / OI {oi:,}</td>
        </tr>"""
    inner = f"""
      <div style="font-size:11px;font-weight:700;color:#71717a;text-transform:uppercase;letter-spacing:.08em;margin-bottom:10px;">Unusual options flow</div>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">{rows}</table>"""
    return f'<div style="margin:16px 0;">{_card(inner)}</div>'


_FREQUENCY_LABELS: dict[str, str] = {
    "weekly":   "Weekly recap",
    "weekends": "Weekend edition",
}


def _render_html(briefing: dict, tier: str, user_id: str | None, prediction_titles: dict[str, str] | None = None, frequency: str = "daily") -> str:
    content      = briefing.get("content_json") or {}
    subject_line = html.escape(briefing.get("headline", ""))
    opening      = _md_to_html(content.get("opening_line", ""))
    closing      = _md_to_html(content.get("closing_line", ""))
    stories      = content.get("stories", [])
    quick_hits   = content.get("quick_hits")
    today        = date.today().strftime("%A, %B %-d")
    is_paid      = tier in ("pro", "elite")

    freq_label = _FREQUENCY_LABELS.get(frequency)
    if freq_label:
        opening = _md_to_html(f"Here's what happened since your last briefing. {content.get('opening_line', '')}")

    stories_html = "".join(_story_html(s) for s in stories)

    quick_hits_block = "" if not quick_hits else f"""
    <div style="margin:20px 0;padding:14px 16px;border-left:2px solid #27272a;color:#71717a;font-size:13px;line-height:1.6;font-style:italic;">
      {_md_to_html(quick_hits)}
    </div>"""

    paid_block = ""
    if is_paid:
        top_signals = _get_user_signals(user_id or "", content.get("top_signals", []))
        options     = content.get("options_flow", [])
        paid_block  = _signals_html(top_signals, prediction_titles) + _options_html(options)

    free_cta = "" if is_paid else f"""
    <div style="margin:28px 0;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="#111113" class="og-bg1"
             style="background-color:#111113;border:1px solid #1e1e22;border-radius:10px;">
        <tr><td style="padding:22px;text-align:center;">
          <div style="color:#f4f4f5;font-weight:700;font-size:15px;margin-bottom:6px;">Want the full signal feed?</div>
          <div style="color:#a1a1aa;font-size:13px;margin-bottom:16px;">Real-time AI signals, options flow, congressional trades. Start your trial.</div>
          <a href="{APP_URL}/signup" style="display:inline-block;background:#22c55e;color:#000;font-weight:700;font-size:13px;padding:10px 24px;border-radius:8px;text-decoration:none;">Try Plebs free &rarr;</a>
        </td></tr>
      </table>
    </div>"""

    closing_block = f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="#111113" class="og-bg1"
           style="background-color:#111113;border-radius:8px;margin:28px 0;">
      <tr>
        <td width="3" bgcolor="#22c55e" style="background-color:#22c55e;font-size:1px;line-height:1px;">&nbsp;</td>
        <td style="padding:16px 20px;color:#a1a1aa;font-size:14px;line-height:1.65;">{closing}</td>
      </tr>
    </table>"""

    opening_block = f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 28px;">
      <tr>
        <td width="3" bgcolor="#22c55e" style="background-color:#22c55e;font-size:1px;line-height:1px;opacity:0.4;">&nbsp;</td>
        <td style="padding-left:14px;color:#a1a1aa;font-size:15px;line-height:1.7;">{opening}</td>
      </tr>
    </table>"""

    # Outlook (Word engine) ignores max-width on <div>s, so the centered
    # column falls back to the full body width without the mso conditional
    # table below enforcing an actual fixed-width table.
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark">
<meta name="supported-color-schemes" content="dark">
<!--[if mso]>
<style type="text/css">table {{border-collapse:collapse;}}</style>
<![endif]-->
<style type="text/css">
/* Outlook.com / New Outlook's "dark mode" ignores the color-scheme meta tags
   above and auto-recolors backgrounds it judges near-black, tagging <body>
   with data-ogsc/data-ogsb when it does. Force our real colors back. */
[data-ogsc] .og-bg0, [data-ogsb] .og-bg0 {{ background-color:#0a0a0c !important; }}
[data-ogsc] .og-bg1, [data-ogsb] .og-bg1 {{ background-color:#111113 !important; }}
</style>
</head>
<body class="og-bg0" style="margin:0;padding:0;background-color:#0a0a0c;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="og-bg0" style="background-color:#0a0a0c;">
<tr><td align="center">
<!--[if mso]>
<table role="presentation" align="center" width="620" cellpadding="0" cellspacing="0"><tr><td>
<![endif]-->
  <div style="max-width:620px;margin:0 auto;padding:36px 20px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;text-align:left;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:24px;">
      <tr>
        <td style="font-size:20px;font-weight:800;color:#f4f4f5;letter-spacing:-0.01em;">
          plebs<span style="color:#22c55e;">.finance</span>
        </td>
        <td style="text-align:right;font-size:11px;color:#71717a;font-weight:600;letter-spacing:.04em;">{(freq_label + ' · ' if freq_label else '') + today}</td>
      </tr>
    </table>
    <h1 style="color:#f4f4f5;font-size:22px;font-weight:700;margin:0 0 16px;line-height:1.35;">{subject_line}</h1>
    {opening_block}
    <div style="padding-top:8px;">
      {stories_html}
    </div>
    {quick_hits_block}
    {paid_block}
    {free_cta}
    {closing_block}
    <div style="border-top:1px solid #1e1e22;padding-top:18px;text-align:center;font-size:11px;color:#52525b;">
      Plebs.finance · Not financial advice ·
      <a href="{APP_URL}/unsubscribe" style="color:#71717a;">Unsubscribe</a>
    </div>
  </div>
<!--[if mso]>
</td></tr></table>
<![endif]-->
</td></tr>
</table>
</body>
</html>"""


def _md_to_text(text: str) -> str:
    """Strip markdown to plain text — links become 'text (url)', bold becomes plain."""
    result = re.sub(r'\[(.+?)\]\((.+?)\)', r'\1 (\2)', text)
    result = re.sub(r'\*\*(.+?)\*\*', r'\1', result)
    return result


def _render_text(briefing: dict) -> str:
    content  = briefing.get("content_json") or {}
    today    = date.today().strftime("%A, %B %-d")
    stories  = content.get("stories", [])

    lines = [
        f"PLEBS.FINANCE — {today}",
        briefing.get("headline", ""),
        "",
        _md_to_text(content.get("opening_line", "")),
        "",
    ]
    for s in stories:
        category = s.get("category", "").upper()
        lines += [
            f"[{category}] {s.get('headline', '').upper()}" if category else s.get("headline", "").upper(),
            f"What happened: {_md_to_text(s.get('what_happened', ''))}",
            f"What we know: {_md_to_text(s.get('what_we_know', ''))}",
            f"What it could mean: {_md_to_text(s.get('could_mean', ''))}",
            f"What to watch: {_md_to_text(s.get('watch', ''))}",
            "",
        ]
    quick_hits = content.get("quick_hits")
    if quick_hits:
        lines += [_md_to_text(quick_hits), ""]
    lines += [
        _md_to_text(content.get("closing_line", "")),
        "",
        f"Full platform: {APP_URL}",
        f"Not financial advice. Unsubscribe: {APP_URL}/unsubscribe",
    ]
    return "\n".join(lines)


# ─── Main sender ──────────────────────────────────────────────────────────────

def _send_with_retry(payload: dict) -> bool:
    for attempt in range(MAX_RETRIES):
        try:
            resend.Emails.send(payload)
            return True
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAYS[attempt]
                logger.warning(
                    "newsletter_emailer: attempt {}/{} failed for {}, retrying in {}s: {}",
                    attempt + 1, MAX_RETRIES, payload["to"], delay, e,
                )
                time.sleep(delay)
            else:
                raise
    return False


def _already_sent_today() -> set[str]:
    today = date.today().isoformat()
    try:
        result = (
            supabase.table("newsletter_sends")
            .select("email")
            .eq("send_date", today)
            .execute()
        )
        return {r["email"] for r in (result.data or [])}
    except Exception:
        return set()


def _record_send(email: str) -> None:
    today = date.today().isoformat()
    try:
        supabase.table("newsletter_sends").upsert(
            {"email": email, "send_date": today},
            on_conflict="email,send_date",
        ).execute()
    except Exception as e:
        logger.warning("newsletter_emailer: failed to record send for {}: {}", email, e)


def _subject_for_frequency(base_subject: str, frequency: str) -> str:
    if frequency == "weekly":
        return f"This week on Plebs — {date.today().strftime('%b %-d')}"
    if frequency == "weekends":
        return f"Weekend briefing — {date.today().strftime('%b %-d')}"
    return base_subject


def send_newsletter() -> str:
    briefing = _get_todays_newsletter()
    if not briefing:
        logger.warning("newsletter_emailer: no briefing found for today, skipping")
        sentry_sdk.capture_message("Newsletter send skipped: no briefing found for today")
        return "no briefing found"

    subscribers = _get_subscribers()
    if not subscribers:
        logger.info("newsletter_emailer: no subscribers")
        return "0 sent"

    already_sent = _already_sent_today()
    base_subject = briefing.get("headline", f"Plebs — {date.today().strftime('%b %-d')}")
    text_body = _render_text(briefing)
    prediction_titles = _resolve_prediction_titles((briefing.get("content_json") or {}).get("top_signals", []))
    sent, skipped, freq_skipped, failed = 0, 0, 0, 0
    failed_emails: list[str] = []
    last_error: str = ""

    for sub in subscribers:
        email   = sub.get("email")
        tier    = sub.get("tier", "free")
        user_id = sub.get("user_id")
        frequency = sub.get("newsletter_frequency", "daily")

        if tier == "free":
            frequency = "weekly"

        if not email:
            continue

        if email in already_sent:
            skipped += 1
            continue

        if not _should_send_today(frequency):
            freq_skipped += 1
            continue

        try:
            subject = _subject_for_frequency(base_subject, frequency)
            html_body = _render_html(briefing, tier, user_id, prediction_titles, frequency)
            _send_with_retry({
                "from":    FROM_ADDRESS,
                "to":      [email],
                "subject": subject,
                "html":    html_body,
                "text":    text_body,
            })
            _record_send(email)
            sent += 1
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            logger.error("newsletter_emailer: all {} retries exhausted for {}: {}", MAX_RETRIES, email, last_error)
            sentry_sdk.capture_exception(e)
            failed += 1
            failed_emails.append(email)

    if failed > 0:
        sentry_sdk.capture_message(
            f"Newsletter send partially failed: {failed} of {sent + failed} emails failed. "
            f"Failed addresses: {', '.join(failed_emails)}"
        )

    summary = f"{sent} sent, {skipped} already sent, {freq_skipped} frequency skipped, {failed} failed"
    if last_error:
        summary += f" | last_error: {last_error}"
    logger.info("newsletter_emailer complete: {}", summary)
    return summary
