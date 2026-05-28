"""
email.py — Async email delivery via SMTP.

Falls back silently when SMTP is not configured (smtp_user empty) so the app
works locally without email setup. All templates are plain-text + HTML pairs.
"""
from __future__ import annotations

import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib

from app.core.config import get_settings

log = logging.getLogger(__name__)


def _sender() -> str:
    s = get_settings()
    addr = s.smtp_from_email or s.smtp_user
    return f"{s.smtp_from_name} <{addr}>" if addr else ""


async def send_email(to: str, subject: str, html: str, text: str) -> None:
    """Send an email. Logs a warning and returns silently if SMTP is not configured."""
    s = get_settings()
    if not s.smtp_user or not s.smtp_password:
        log.info("Email not configured — skipping send to %s: %s", to, subject)
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = _sender()
    msg["To"] = to
    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))

    try:
        await aiosmtplib.send(
            msg,
            hostname=s.smtp_host,
            port=s.smtp_port,
            username=s.smtp_user,
            password=s.smtp_password,
            start_tls=True,
        )
        log.info("Email sent to %s: %s", to, subject)
    except Exception as e:
        log.warning("Email send failed to %s: %s", to, e)


# ── Templates ─────────────────────────────────────────────────────────────────

def _base_html(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>{title}</title></head>
<body style="font-family:Arial,sans-serif;background:#0f1117;color:#e2e8f0;margin:0;padding:0">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr><td align="center" style="padding:40px 20px">
      <table width="520" cellpadding="0" cellspacing="0"
             style="background:#1a1d27;border-radius:12px;border:1px solid #2d3148;overflow:hidden">
        <tr>
          <td style="background:#4f46e5;padding:20px 32px">
            <span style="color:#fff;font-size:20px;font-weight:700;letter-spacing:-0.5px">TiTiBet</span>
            <span style="color:#a5b4fc;font-size:11px;margin-left:8px;text-transform:uppercase;letter-spacing:2px">Intelligence Platform</span>
          </td>
        </tr>
        <tr><td style="padding:32px">{body}</td></tr>
        <tr>
          <td style="padding:16px 32px;border-top:1px solid #2d3148">
            <p style="color:#64748b;font-size:11px;margin:0">
              TiTiBet · Value Betting Intelligence Platform · Malawi<br>
              This email was sent to {"{email}"}. If you didn't expect it, you can ignore it.
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


async def send_welcome(to: str, name: str) -> None:
    display = name or to.split("@")[0]
    s = get_settings()
    body_html = f"""
      <h2 style="color:#e2e8f0;margin:0 0 16px">Welcome, {display}! 🎯</h2>
      <p style="color:#94a3b8;line-height:1.6">
        Your TiTiBet account is ready. You're on the <strong style="color:#e2e8f0">Free</strong> plan —
        explore value signals, track bets, and run backtests.
      </p>
      <p style="color:#94a3b8;line-height:1.6;margin-top:12px">
        When you're ready for accumulators and AI analysis, upgrade to Pro or Elite from the
        <strong style="color:#e2e8f0">Plans</strong> page inside the app.
      </p>
      <a href="{s.app_url}" style="display:inline-block;margin-top:24px;padding:12px 28px;
         background:#4f46e5;color:#fff;border-radius:8px;text-decoration:none;font-weight:600;font-size:14px">
        Open TiTiBet
      </a>
    """
    body_text = f"Welcome to TiTiBet, {display}!\n\nYour account is ready. Visit {s.app_url} to get started."
    html = _base_html("Welcome to TiTiBet", body_html).replace("{email}", to)
    await send_email(to, "Welcome to TiTiBet 🎯", html, body_text)


async def send_password_reset(to: str, reset_url: str) -> None:
    body_html = f"""
      <h2 style="color:#e2e8f0;margin:0 0 16px">Reset your password</h2>
      <p style="color:#94a3b8;line-height:1.6">
        We received a request to reset your TiTiBet password.
        Click the button below — the link expires in <strong style="color:#e2e8f0">1 hour</strong>.
      </p>
      <a href="{reset_url}" style="display:inline-block;margin-top:24px;padding:12px 28px;
         background:#4f46e5;color:#fff;border-radius:8px;text-decoration:none;font-weight:600;font-size:14px">
        Reset Password
      </a>
      <p style="color:#64748b;font-size:12px;margin-top:24px;line-height:1.6">
        If you didn't request this, ignore this email — your password won't change.
      </p>
    """
    body_text = f"Reset your TiTiBet password:\n\n{reset_url}\n\nThis link expires in 1 hour."
    html = _base_html("Reset your TiTiBet password", body_html).replace("{email}", to)
    await send_email(to, "Reset your TiTiBet password", html, body_text)


async def send_payment_confirmation(to: str, name: str, tier: str, expires_at: str) -> None:
    display = name or to.split("@")[0]
    tier_cap = tier.capitalize()
    s = get_settings()
    body_html = f"""
      <h2 style="color:#e2e8f0;margin:0 0 16px">You're now on {tier_cap}! ✅</h2>
      <p style="color:#94a3b8;line-height:1.6">
        Hi {display}, your payment was successful. Your <strong style="color:#e2e8f0">{tier_cap}</strong>
        subscription is now active until <strong style="color:#e2e8f0">{expires_at}</strong>.
      </p>
      <p style="color:#94a3b8;line-height:1.6;margin-top:12px">
        {"Accumulators (Safe &amp; Value tiers), CLV tracking, and full analytics are unlocked." if tier == "pro"
          else "All Pro features plus Bold accumulators, AI Advisory Council, and Admin panel are unlocked."}
      </p>
      <a href="{s.app_url}" style="display:inline-block;margin-top:24px;padding:12px 28px;
         background:#4f46e5;color:#fff;border-radius:8px;text-decoration:none;font-weight:600;font-size:14px">
        Start Betting Smarter
      </a>
    """
    body_text = f"Hi {display}, your {tier_cap} subscription is active until {expires_at}. Visit {s.app_url}"
    html = _base_html(f"You're on {tier_cap}!", body_html).replace("{email}", to)
    await send_email(to, f"TiTiBet {tier_cap} subscription activated ✅", html, body_text)
