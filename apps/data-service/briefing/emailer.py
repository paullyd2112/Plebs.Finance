"""
Briefing emailer — runs weekdays at 7:15am ET (15 min after generation).
Sends today's briefing to all Pro and Elite users who have confirmed emails.
"""

import html
import os
from datetime import date, datetime, timezone

import resend
import sentry_sdk
from loguru import logger

from supabase_client import supabase

resend.api_key = os.environ.get("RESEND_API_KEY", "") or os.environ.get("RESEND_API_KEY_", "")

FROM_ADDRESS  = os.environ.get("EMAIL_FROM_BRIEF", "Plebs Morning Brief <brief@plebs.finance>")
SUBJECT_PREFIX = "☀️ Plebs Brief"


# ─── Fetch today's briefing ───────────────────────────────────────────────────

def _get_todays_briefing() -> dict | None:
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
        logger.error("emailer: failed to fetch briefing — {}", e)
        return None


# ─── Fetch Pro/Elite user emails ──────────────────────────────────────────────

def _get_subscriber_emails() -> list[str]:
    try:
        # Join profiles → auth.users via service role to get emails
        result = (
            supabase.table("profiles")
            .select("id, tier")
            .in_("tier", ["pro", "elite"])
            .execute()
        )
        profiles = result.data or []
        if not profiles:
            return []

        user_ids = [p["id"] for p in profiles]
        emails: list[str] = []

        # Fetch emails in batches of 50 using admin auth API
        for i in range(0, len(user_ids), 50):
            batch = user_ids[i : i + 50]
            for uid in batch:
                try:
                    resp = supabase.auth.admin.get_user_by_id(uid)
                    if resp.user and resp.user.email:
                        emails.append(resp.user.email)
                except Exception:
                    pass

        return emails
    except Exception as e:
        logger.error("emailer: failed to fetch subscriber emails — {}", e)
        sentry_sdk.capture_exception(e)
        return []


# ─── Email renderer ───────────────────────────────────────────────────────────

def _render_html(briefing: dict) -> str:
    content  = briefing.get("content_json") or {}
    headline = html.escape(briefing.get("headline", ""))
    tone     = html.escape(briefing.get("day_tone", ""))
    today    = date.today().strftime("%A, %B %-d")

    tone_color = {
        "opportunistic": "#22c55e",
        "volatile":      "#f59e0b",
        "cautious":      "#ef4444",
        "quiet":         "#6b7280",
    }.get(tone, "#6b7280")

    top_trades_html = ""
    for trade in content.get("top_trades", []):
        direction  = html.escape(trade.get("direction", ""))
        identifier = html.escape(trade.get("identifier", ""))
        one_liner  = html.escape(trade.get("one_liner", ""))
        dir_color  = "#22c55e" if direction in ("BUY", "YES") else "#ef4444" if direction in ("SELL", "NO") else "#6b7280"
        top_trades_html += f"""
        <tr>
          <td style="padding:8px 12px;border-bottom:1px solid #27272a;">
            <span style="font-family:monospace;font-weight:700;color:#fff;">{identifier}</span>
            &nbsp;
            <span style="background:{dir_color}22;color:{dir_color};border:1px solid {dir_color}55;border-radius:4px;padding:1px 6px;font-size:11px;font-weight:700;">{direction}</span>
            &nbsp;
            <span style="color:#a1a1aa;font-size:12px;">{trade.get('confidence', 0)}%</span>
          </td>
          <td style="padding:8px 12px;border-bottom:1px solid #27272a;color:#d4d4d8;font-size:13px;">{one_liner}</td>
        </tr>"""

    watch_html = " &nbsp;·&nbsp; ".join(
        f'<span style="font-family:monospace;color:#22c55e;">{html.escape(t)}</span>'
        for t in content.get("watch_today", [])
    )

    pred_edge    = html.escape(content.get("prediction_market_edge", ""))
    pred_section = ""
    if pred_edge:
        pred_section = f"""
        <div style="margin:20px 0;padding:16px;background:#18181b;border-left:3px solid #7c3aed;border-radius:0 8px 8px 0;">
          <div style="font-size:11px;font-weight:700;color:#7c3aed;text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px;">Prediction markets</div>
          <div style="color:#d4d4d8;font-size:14px;">{pred_edge}</div>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#09090b;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <div style="max-width:600px;margin:0 auto;padding:24px 16px;">

    <!-- Header -->
    <div style="margin-bottom:24px;">
      <div style="font-size:22px;font-weight:800;color:#fff;letter-spacing:-0.02em;">
        plebs<span style="color:#22c55e;">.io</span>
      </div>
      <div style="font-size:12px;color:#52525b;margin-top:2px;">{today}</div>
    </div>

    <!-- Tone + Headline -->
    <div style="margin-bottom:20px;">
      <span style="background:{tone_color}22;color:{tone_color};border:1px solid {tone_color}55;border-radius:4px;padding:2px 8px;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;">{tone}</span>
      <h1 style="margin:10px 0 0;font-size:20px;font-weight:700;color:#fff;line-height:1.3;">{headline}</h1>
    </div>

    <!-- Market overview -->
    <div style="margin:20px 0;padding:16px;background:#18181b;border-radius:8px;border:1px solid #27272a;">
      <div style="font-size:11px;font-weight:700;color:#52525b;text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px;">Market overview</div>
      <div style="color:#d4d4d8;font-size:14px;line-height:1.6;">{html.escape(content.get('market_overview',''))}</div>
    </div>

    <!-- Top trades -->
    <div style="margin:20px 0;">
      <div style="font-size:11px;font-weight:700;color:#52525b;text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px;">Top signals</div>
      <table style="width:100%;border-collapse:collapse;background:#18181b;border-radius:8px;border:1px solid #27272a;overflow:hidden;">
        {top_trades_html}
      </table>
    </div>

    <!-- Macro -->
    <div style="margin:20px 0;padding:16px;background:#18181b;border-radius:8px;border:1px solid #27272a;">
      <div style="font-size:11px;font-weight:700;color:#52525b;text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px;">Macro context</div>
      <div style="color:#d4d4d8;font-size:14px;line-height:1.6;">{html.escape(content.get('macro_context',''))}</div>
    </div>

    {pred_section}

    <!-- Watch today -->
    <div style="margin:20px 0;padding:16px;background:#18181b;border-radius:8px;border:1px solid #27272a;">
      <div style="font-size:11px;font-weight:700;color:#52525b;text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px;">Watch today</div>
      <div style="font-size:13px;">{watch_html}</div>
    </div>

    <!-- Risk note -->
    <div style="margin:20px 0;padding:16px;background:#18181b;border-left:3px solid #f59e0b;border-radius:0 8px 8px 0;">
      <div style="font-size:11px;font-weight:700;color:#f59e0b;text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px;">Risk note</div>
      <div style="color:#d4d4d8;font-size:14px;">{html.escape(content.get('risk_note',''))}</div>
    </div>

    <!-- CTA -->
    <div style="text-align:center;margin:28px 0 20px;">
      <a href="https://plebs.finance/dashboard" style="display:inline-block;background:#22c55e;color:#000;font-weight:700;font-size:14px;padding:12px 28px;border-radius:6px;text-decoration:none;">
        Open dashboard →
      </a>
    </div>

    <!-- Footer -->
    <div style="border-top:1px solid #27272a;padding-top:16px;text-align:center;font-size:11px;color:#3f3f46;">
      Plebs.io · Not financial advice · <a href="https://plebs.finance/unsubscribe" style="color:#52525b;">Unsubscribe</a>
    </div>

  </div>
</body>
</html>"""


def _render_text(briefing: dict) -> str:
    content  = briefing.get("content_json") or {}
    headline = briefing.get("headline", "")
    today    = date.today().strftime("%A, %B %-d")

    lines = [
        f"PLEBS.IO — {today}",
        "=" * 40,
        "",
        headline.upper(),
        "",
        "MARKET OVERVIEW",
        content.get("market_overview", ""),
        "",
        "TOP SIGNALS",
    ]
    for t in content.get("top_trades", []):
        lines.append(f"  {t.get('identifier')} → {t.get('direction')} ({t.get('confidence')}%) — {t.get('one_liner')}")

    lines += [
        "",
        "MACRO",
        content.get("macro_context", ""),
        "",
        "RISK NOTE",
        content.get("risk_note", ""),
        "",
        "Watch: " + ", ".join(content.get("watch_today", [])),
        "",
        "Open dashboard: https://plebs.finance/dashboard",
        "",
        "Not financial advice. Unsubscribe: https://plebs.finance/unsubscribe",
    ]
    return "\n".join(lines)


# ─── Main sender ──────────────────────────────────────────────────────────────

def send_briefing_emails() -> str:
    briefing = _get_todays_briefing()
    if not briefing:
        logger.warning("emailer: no briefing found for today — skipping")
        return "no briefing found"

    emails = _get_subscriber_emails()
    if not emails:
        logger.info("emailer: no pro/elite subscribers — nothing to send")
        return "0 emails sent"

    subject   = f"{SUBJECT_PREFIX} — {briefing.get('headline', date.today().strftime('%b %-d'))}"
    html_body = _render_html(briefing)
    text_body = _render_text(briefing)

    sent, failed = 0, 0

    for email in emails:
        try:
            resend.Emails.send({
                "from":    FROM_ADDRESS,
                "to":      [email],
                "subject": subject,
                "html":    html_body,
                "text":    text_body,
            })
            sent += 1
        except Exception as e:
            logger.error("emailer: failed to send to {} — {}", email, e)
            sentry_sdk.capture_exception(e)
            failed += 1

    summary = f"{sent} sent, {failed} failed"
    logger.info("emailer complete — {}", summary)
    return summary
