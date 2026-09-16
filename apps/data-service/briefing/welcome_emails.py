"""
Welcome email sequence — 3 emails sent over the first week.
  Day 0 (immediate): Welcome + getting started
  Day 2: Feature spotlight — signals & accuracy
  Day 4: Feature spotlight — prediction markets & briefing
"""

import os
from datetime import date

import resend
import sentry_sdk
from loguru import logger

from supabase_client import supabase

resend.api_key = os.environ.get("RESEND_API_KEY", "") or os.environ.get("RESEND_API_KEY_", "")

FROM_ADDRESS = os.environ.get("EMAIL_FROM_WELCOME", "Plebs <hello@plebs.finance>")
APP_URL      = os.environ.get("NEXT_PUBLIC_APP_URL", "https://plebs.finance")


# ─── Email templates ──────────────────────────────────────────────────────────

def _email_day0(name: str) -> tuple[str, str, str]:
    subject = "Welcome to Plebs — here's where to start"
    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#09090b;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <div style="max-width:560px;margin:0 auto;padding:32px 20px;">
    <div style="font-size:22px;font-weight:800;color:#fff;margin-bottom:24px;">
      plebs<span style="color:#22c55e;">.finance</span>
    </div>
    <h1 style="color:#fff;font-size:22px;font-weight:700;margin:0 0 12px;">
      Welcome{f", {name}" if name else ""} — you're in.
    </h1>
    <p style="color:#a1a1aa;font-size:15px;line-height:1.6;margin:0 0 24px;">
      You now have full access to everything Plebs offers. Here's where to start:
    </p>
    <div style="background:#18181b;border:1px solid #27272a;border-radius:10px;padding:20px;margin-bottom:20px;">
      <div style="font-size:12px;font-weight:700;color:#52525b;text-transform:uppercase;letter-spacing:.08em;margin-bottom:12px;">Start here</div>
      <div style="margin-bottom:12px;">
        <a href="{APP_URL}/dashboard" style="color:#22c55e;font-weight:600;text-decoration:none;">→ Open your dashboard</a>
        <div style="color:#71717a;font-size:13px;margin-top:2px;">Check the live signal feed — crypto and prediction markets.</div>
      </div>
      <div style="margin-bottom:12px;">
        <a href="{APP_URL}/dashboard/watchlist" style="color:#22c55e;font-weight:600;text-decoration:none;">→ Add assets to your watchlist</a>
        <div style="color:#71717a;font-size:13px;margin-top:2px;">Set up alerts so you never miss a signal on assets you follow.</div>
      </div>
      <div>
        <a href="{APP_URL}/dashboard/predictions" style="color:#22c55e;font-weight:600;text-decoration:none;">→ Browse prediction markets</a>
        <div style="color:#71717a;font-size:13px;margin-top:2px;">See AI-scored Polymarket contracts with probability tracking.</div>
      </div>
    </div>
    <p style="color:#71717a;font-size:13px;line-height:1.6;margin:0 0 24px;">
      Your morning briefing starts arriving at 7am ET daily — it covers crypto, prediction markets, and what's moving the world.
    </p>
    <a href="{APP_URL}/dashboard" style="display:inline-block;background:#22c55e;color:#000;font-weight:700;font-size:14px;padding:12px 24px;border-radius:8px;text-decoration:none;">
      Open dashboard →
    </a>
    <div style="margin-top:32px;padding-top:20px;border-top:1px solid #27272a;font-size:11px;color:#3f3f46;text-align:center;">
      Plebs is open source · Not financial advice · <a href="{APP_URL}/unsubscribe" style="color:#52525b;">Unsubscribe</a>
    </div>
  </div>
</body>
</html>"""
    text = f"""Welcome{f", {name}" if name else ""} — you're in.

Start here:
→ Open your dashboard: {APP_URL}/dashboard
→ Add assets to your watchlist and set alerts
→ Browse prediction markets: {APP_URL}/dashboard/predictions

Your morning briefing starts arriving at 7am ET daily.

Not financial advice. Unsubscribe: {APP_URL}/unsubscribe"""
    return subject, html, text


def _email_day2(name: str) -> tuple[str, str, str]:
    subject = "Two features most traders miss on Plebs"
    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#09090b;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <div style="max-width:560px;margin:0 auto;padding:32px 20px;">
    <div style="font-size:22px;font-weight:800;color:#fff;margin-bottom:24px;">
      plebs<span style="color:#22c55e;">.finance</span>
    </div>
    <h1 style="color:#fff;font-size:20px;font-weight:700;margin:0 0 12px;">
      Two features worth checking out
    </h1>
    <p style="color:#a1a1aa;font-size:15px;line-height:1.6;margin:0 0 24px;">
      Most users find these two the most valuable:
    </p>
    <div style="background:#18181b;border:1px solid #27272a;border-radius:10px;padding:20px;margin-bottom:16px;">
      <div style="font-size:16px;font-weight:700;color:#fff;margin-bottom:6px;">⚡ Signal accuracy per asset</div>
      <div style="color:#a1a1aa;font-size:14px;line-height:1.6;margin-bottom:12px;">
        Every signal we generate gets tracked against the actual outcome. You can see win rates per asset — so you know which signals actually make money over time.
      </div>
      <a href="{APP_URL}/dashboard" style="color:#22c55e;font-size:13px;font-weight:600;text-decoration:none;">View signal feed →</a>
    </div>
    <div style="background:#18181b;border:1px solid #27272a;border-radius:10px;padding:20px;margin-bottom:24px;">
      <div style="font-size:16px;font-weight:700;color:#fff;margin-bottom:6px;">🎯 On-demand scoring</div>
      <div style="color:#a1a1aa;font-size:14px;line-height:1.6;margin-bottom:12px;">
        Want an AI analysis on a specific coin? Open any asset page and generate a signal on demand.
      </div>
      <a href="{APP_URL}/dashboard/asset/crypto/BTC" style="color:#22c55e;font-size:13px;font-weight:600;text-decoration:none;">Try it on BTC →</a>
    </div>
    <a href="{APP_URL}/dashboard" style="display:inline-block;background:#22c55e;color:#000;font-weight:700;font-size:14px;padding:12px 24px;border-radius:8px;text-decoration:none;">
      Open dashboard →
    </a>
    <div style="margin-top:32px;padding-top:20px;border-top:1px solid #27272a;font-size:11px;color:#3f3f46;text-align:center;">
      Plebs is open source · Not financial advice · <a href="{APP_URL}/unsubscribe" style="color:#52525b;">Unsubscribe</a>
    </div>
  </div>
</body>
</html>"""
    text = f"""Two features worth checking out.

Signal accuracy per asset:
Every signal gets tracked against actual outcomes. See win rates per asset.
{APP_URL}/dashboard

On-demand scoring:
Generate an AI signal on any coin from its asset page.
{APP_URL}/dashboard/asset/crypto/BTC

Not financial advice. Unsubscribe: {APP_URL}/unsubscribe"""
    return subject, html, text


def _email_day4(name: str) -> tuple[str, str, str]:
    subject = "Prediction markets + your morning briefing"
    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#09090b;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <div style="max-width:560px;margin:0 auto;padding:32px 20px;">
    <div style="font-size:22px;font-weight:800;color:#fff;margin-bottom:24px;">
      plebs<span style="color:#22c55e;">.finance</span>
    </div>
    <h1 style="color:#fff;font-size:20px;font-weight:700;margin:0 0 12px;">
      Two more things you should be using
    </h1>
    <div style="background:#18181b;border:1px solid #27272a;border-radius:10px;padding:20px;margin-bottom:16px;">
      <div style="font-size:16px;font-weight:700;color:#fff;margin-bottom:6px;">🎯 Prediction markets</div>
      <div style="color:#a1a1aa;font-size:14px;line-height:1.6;margin-bottom:12px;">
        Browse Polymarket contracts with AI-scored YES/NO signals. See probabilities, sparklines, and volume data for elections, macro, crypto, and more.
      </div>
      <a href="{APP_URL}/dashboard/predictions" style="color:#22c55e;font-size:13px;font-weight:600;text-decoration:none;">Browse predictions →</a>
    </div>
    <div style="background:#18181b;border:1px solid #27272a;border-radius:10px;padding:20px;margin-bottom:24px;">
      <div style="font-size:16px;font-weight:700;color:#fff;margin-bottom:6px;">☀️ Morning briefing</div>
      <div style="color:#a1a1aa;font-size:14px;line-height:1.6;margin-bottom:12px;">
        Hits your inbox daily. Crypto markets, prediction markets, geopolitics, tech, and whatever else is moving the world. Five minutes and you're caught up.
      </div>
      <a href="{APP_URL}/dashboard/briefing" style="color:#22c55e;font-size:13px;font-weight:600;text-decoration:none;">Read today's briefing →</a>
    </div>
    <a href="{APP_URL}/dashboard" style="display:inline-block;background:#22c55e;color:#000;font-weight:700;font-size:14px;padding:12px 24px;border-radius:8px;text-decoration:none;">
      Open dashboard →
    </a>
    <div style="margin-top:32px;padding-top:20px;border-top:1px solid #27272a;font-size:11px;color:#3f3f46;text-align:center;">
      Plebs is open source · Not financial advice · <a href="{APP_URL}/unsubscribe" style="color:#52525b;">Unsubscribe</a>
    </div>
  </div>
</body>
</html>"""
    text = f"""Prediction markets + your morning briefing.

Prediction markets:
Browse Polymarket contracts with AI-scored signals.
{APP_URL}/dashboard/predictions

Morning briefing:
Hits your inbox daily. Crypto, predictions, geopolitics, tech.
{APP_URL}/dashboard/briefing

Not financial advice. Unsubscribe: {APP_URL}/unsubscribe"""
    return subject, html, text


# ─── Sender ───────────────────────────────────────────────────────────────────

def _send(to_email: str, subject: str, html: str, text: str) -> bool:
    try:
        resend.Emails.send({
            "from":    FROM_ADDRESS,
            "to":      [to_email],
            "subject": subject,
            "html":    html,
            "text":    text,
        })
        return True
    except Exception as e:
        logger.error("welcome_email: failed to send to {} — {}", to_email, e)
        sentry_sdk.capture_exception(e)
        return False


def send_welcome_sequence() -> str:
    """Send the appropriate welcome email to users based on their signup day.

    NOTE: this is a daily batch scan — day-0 emails can arrive up to 24h
    after signup, and a missed cron run skips that day's steps entirely.
    The replacement is Dreamlit (event-driven off the profiles table, fires
    on INSERT). Set WELCOME_VIA_DREAMLIT=true in Railway once the Dreamlit
    workflows are live to retire this path without double-sending.
    """
    from datetime import datetime, timezone as tz

    if os.environ.get("WELCOME_VIA_DREAMLIT", "").lower() in ("1", "true", "yes"):
        logger.info("welcome_sequence: skipped — handled by Dreamlit (WELCOME_VIA_DREAMLIT set)")
        return "skipped — welcome emails handled by Dreamlit"

    today  = date.today()
    sent   = 0
    failed = 0

    try:
        result = supabase.table("profiles").select("id, full_name, created_at").execute()
        profiles = result.data or []
    except Exception as e:
        logger.error("welcome_sequence: failed to fetch profiles — {}", e)
        return "failed to fetch profiles"

    for profile in profiles:
        created = profile.get("created_at", "")
        if not created:
            continue

        try:
            signup_date = datetime.fromisoformat(created.replace("Z", "+00:00")).date()
        except Exception:
            continue

        day = (today - signup_date).days
        if day not in (0, 2, 4):
            continue

        try:
            user_resp = supabase.auth.admin.get_user_by_id(profile["id"])
            email     = user_resp.user.email if user_resp.user else None
        except Exception:
            continue

        if not email:
            continue

        name = profile.get("full_name") or ""

        if day == 0:
            subject, html, text = _email_day0(name)
        elif day == 2:
            subject, html, text = _email_day2(name)
        else:
            subject, html, text = _email_day4(name)

        if _send(email, subject, html, text):
            sent += 1
        else:
            failed += 1

    summary = f"{sent} sent, {failed} failed"
    logger.info("welcome_sequence complete — {}", summary)
    return summary
