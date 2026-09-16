"""
Transactional alert emails — sent the moment a user's alert fires.

Fired alerts are detected in scoring/resolver.py::evaluate_alerts(); this module
turns a fired alert + its trigger context into an email and delivers it via Resend.

Respects the per-user `profiles.email_alerts` toggle (default on). Each email
links back to /dashboard/alerts so the user can pause or delete the alert.
"""

import os

import resend
import sentry_sdk
from loguru import logger

from supabase_client import supabase

resend.api_key = os.environ.get("RESEND_API_KEY", "") or os.environ.get("RESEND_API_KEY_", "")

FROM_ADDRESS = os.environ.get("EMAIL_FROM_ALERTS", "Plebs Alerts <alerts@plebs.finance>")
APP_URL      = os.environ.get("NEXT_PUBLIC_APP_URL", "https://plebs.finance")


# ─── User lookup ──────────────────────────────────────────────────────────────

def _user_wants_email(user_id: str) -> bool:
    """Honour the profiles.email_alerts toggle; default to True if unavailable."""
    try:
        result = (
            supabase.table("profiles")
            .select("email_alerts")
            .eq("id", user_id)
            .limit(1)
            .execute()
        )
        if result.data:
            return result.data[0].get("email_alerts", True) is not False
    except Exception:
        # Column may not exist yet (migration 005 not applied) — fail open.
        pass
    return True


def _user_email(user_id: str) -> str | None:
    try:
        resp = supabase.auth.admin.get_user_by_id(user_id)
        if resp and resp.user and resp.user.email:
            return resp.user.email
    except Exception as e:
        logger.error("alert_emails: email lookup failed for {} — {}", user_id, e)
    return None


# ─── Body rendering ───────────────────────────────────────────────────────────

def _fmt_price(asset_type: str, value) -> str:
    if value is None:
        return "—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    if asset_type == "prediction":
        return f"{v * 100:.1f}%"
    return f"${v:,.2f}"


def _subject_and_lines(alert: dict, ctx: dict) -> tuple[str, list[str]]:
    """Return (subject, [body lines]) tailored to the trigger type."""
    ident      = alert.get("identifier", "")
    asset_type = alert.get("asset_type", "stock")
    kind       = alert.get("trigger_type")

    if kind == "signal_fired":
        direction  = ctx.get("direction", "")
        confidence = ctx.get("confidence")
        reasoning  = ctx.get("reasoning", "")
        conf_txt   = f" · {confidence}% confidence" if confidence is not None else ""
        subject    = f"🔔 New {direction} signal on {ident}"
        lines = [
            f"A new <strong>{direction}</strong> signal just fired on "
            f"<strong>{ident}</strong>{conf_txt}.",
        ]
        if reasoning:
            lines.append(f"<em>{reasoning}</em>")
        return subject, lines

    if kind == "price_threshold":
        price     = _fmt_price(asset_type, ctx.get("price"))
        threshold = _fmt_price(asset_type, alert.get("threshold"))
        subject   = f"🔔 {ident} crossed {threshold}"
        return subject, [
            f"<strong>{ident}</strong> is now at <strong>{price}</strong>, "
            f"crossing your alert threshold of {threshold}.",
        ]

    if kind == "news_drop":
        headline = ctx.get("headline", "")
        subject  = f"🔔 News on {ident}"
        lines    = [f"Fresh news just dropped for <strong>{ident}</strong>."]
        if headline:
            lines.append(f"<em>{headline}</em>")
        return subject, lines

    return f"🔔 Alert fired on {ident}", [f"Your alert on <strong>{ident}</strong> fired."]


def _render(alert: dict, ctx: dict) -> tuple[str, str, str]:
    asset_type = alert.get("asset_type", "stock")
    ident      = alert.get("identifier", "")
    subject, lines = _subject_and_lines(alert, ctx)

    asset_url   = f"{APP_URL}/dashboard/asset/{asset_type}/{ident}"
    alerts_url  = f"{APP_URL}/dashboard/alerts"
    body_html   = "".join(
        f'<p style="color:#a1a1aa;font-size:15px;line-height:1.6;margin:0 0 16px;">{ln}</p>'
        for ln in lines
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#09090b;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <div style="max-width:560px;margin:0 auto;padding:32px 20px;">
    <div style="font-size:22px;font-weight:800;color:#fff;margin-bottom:24px;">
      plebs<span style="color:#22c55e;">.finance</span>
    </div>
    {body_html}
    <a href="{asset_url}" style="display:inline-block;background:#22c55e;color:#000;font-weight:600;font-size:14px;text-decoration:none;padding:10px 20px;border-radius:6px;margin:8px 0 24px;">
      View {ident} →
    </a>
    <hr style="border:none;border-top:1px solid #27272a;margin:24px 0;">
    <p style="color:#52525b;font-size:12px;line-height:1.5;margin:0;">
      You're receiving this because you set an alert on {ident}.
      <a href="{alerts_url}" style="color:#71717a;">Manage your alerts</a>.
    </p>
  </div>
</body>
</html>"""

    text_lines = [ln.replace("<strong>", "").replace("</strong>", "")
                    .replace("<em>", "").replace("</em>", "") for ln in lines]
    text = "\n\n".join(text_lines) + f"\n\nView {ident}: {asset_url}\nManage alerts: {alerts_url}"
    return subject, html, text


# ─── Public entry point ─────────────────────────────────────────────────────────

def send_alert_notification(alert: dict, ctx: dict) -> bool:
    """Send an email for a fired alert. Returns True if an email was dispatched."""
    user_id = alert.get("user_id")
    if not user_id:
        return False

    if not _user_wants_email(user_id):
        return False

    email = _user_email(user_id)
    if not email:
        return False

    subject, html, text = _render(alert, ctx)
    try:
        resend.Emails.send({
            "from":    FROM_ADDRESS,
            "to":      [email],
            "subject": subject,
            "html":    html,
            "text":    text,
        })
        logger.info("alert_emails: sent {} alert for {} to user {}",
                    alert.get("trigger_type"), alert.get("identifier"), user_id)
        return True
    except Exception as e:
        logger.error("alert_emails: send failed for user {} — {}", user_id, e)
        sentry_sdk.capture_exception(e)
        return False
