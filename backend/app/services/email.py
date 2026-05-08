"""
Email sending utility — reads SMTP config from environment.

Required Doppler / .env vars:
  SMTP_HOST       e.g. mail.canhost.ca
  SMTP_PORT       587 (STARTTLS) or 465 (SSL)
  SMTP_USER       noreply@finel.ai
  SMTP_PASSWORD   <password>
  SMTP_FROM       Finel AI Projects <noreply@finel.ai>
  APP_URL         https://projects.finel.ai
"""
import os
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

APP_URL   = os.getenv("APP_URL", "https://projects.finel.ai").rstrip("/")
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", f"Finel AI Projects <{SMTP_USER}>")


def _send(to: str, subject: str, html: str, text: str) -> None:
    if not SMTP_HOST or not SMTP_USER:
        logger.warning("SMTP not configured — email not sent to %s", to)
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = SMTP_FROM
    msg["To"]      = to
    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html,  "html"))
    try:
        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=10) as s:
                s.login(SMTP_USER, SMTP_PASS)
                s.sendmail(SMTP_USER, [to], msg.as_string())
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as s:
                s.ehlo()
                s.starttls()
                s.login(SMTP_USER, SMTP_PASS)
                s.sendmail(SMTP_USER, [to], msg.as_string())
        logger.info("Email sent: %s → %s", subject, to)
    except Exception as exc:
        logger.error("Failed to send email to %s: %s", to, exc)
        raise


# ── Branded HTML wrapper ────────────────────────────────────────────────────

def _wrap(body_html: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"/></head>
<body style="margin:0;padding:0;background:#f4f6f9;font-family:Inter,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f6f9;padding:40px 0;">
    <tr><td align="center">
      <table width="520" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08);">
        <!-- header -->
        <tr><td style="background:linear-gradient(135deg,#005366,#00acff);padding:28px 36px;">
          <span style="font-size:22px;font-weight:700;color:#fff;letter-spacing:-.3px;">Finel AI Projects</span>
        </td></tr>
        <!-- body -->
        <tr><td style="padding:36px 36px 28px;">
          {body_html}
        </td></tr>
        <!-- footer -->
        <tr><td style="padding:16px 36px 24px;border-top:1px solid #f0f0f0;">
          <p style="margin:0;font-size:12px;color:#999;">
            © 2026 Finel AI Financial Services &nbsp;·&nbsp;
            <a href="{APP_URL}" style="color:#00acff;text-decoration:none;">projects.finel.ai</a>
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""


def _btn(url: str, label: str) -> str:
    return (f'<p style="margin:28px 0 8px;">'
            f'<a href="{url}" style="display:inline-block;background:#00acff;color:#fff;'
            f'text-decoration:none;padding:12px 28px;border-radius:8px;font-weight:600;'
            f'font-size:15px;">{label}</a></p>'
            f'<p style="font-size:12px;color:#aaa;margin:6px 0 0;">Or copy this link: '
            f'<a href="{url}" style="color:#00acff;word-break:break-all;">{url}</a></p>')


# ── Public send functions ───────────────────────────────────────────────────

def send_password_reset(to: str, token: str) -> None:
    url = f"{APP_URL}/reset-password?token={token}"
    html = _wrap(f"""
      <h2 style="margin:0 0 8px;color:#0f172a;font-size:22px;">Reset your password</h2>
      <p style="color:#555;line-height:1.6;margin:0 0 4px;">
        We received a request to reset the password for your Finel AI Projects account.
        Click the button below — this link expires in <strong>1 hour</strong>.
      </p>
      {_btn(url, "Reset Password")}
      <p style="margin:20px 0 0;font-size:13px;color:#999;">
        If you didn't request a password reset, you can safely ignore this email.
      </p>""")
    text = f"Reset your Finel AI Projects password:\n{url}\n\nExpires in 1 hour."
    _send(to, "Reset your Finel AI Projects password", html, text)


def send_invite(to: str, org_name: str, inviter_name: str, role: str, token: str) -> None:
    url = f"{APP_URL}/accept-invite?token={token}"
    html = _wrap(f"""
      <h2 style="margin:0 0 8px;color:#0f172a;font-size:22px;">You've been invited</h2>
      <p style="color:#555;line-height:1.6;margin:0 0 4px;">
        <strong>{inviter_name}</strong> has invited you to join
        <strong>{org_name}</strong> on Finel AI Projects as <strong>{role}</strong>.
        Click below to accept and create your account — this invite expires in <strong>7 days</strong>.
      </p>
      {_btn(url, "Accept Invitation")}
      <p style="margin:20px 0 0;font-size:13px;color:#999;">
        If you weren't expecting this invitation, you can ignore it.
      </p>""")
    text = (f"{inviter_name} invited you to {org_name} on Finel AI Projects.\n"
            f"Accept here: {url}\n\nExpires in 7 days.")
    _send(to, f"You've been invited to {org_name} on Finel AI Projects", html, text)


def send_welcome(to: str, username: str, org_name: str) -> None:
    url = f"{APP_URL}"
    html = _wrap(f"""
      <h2 style="margin:0 0 8px;color:#0f172a;font-size:22px;">Welcome to Finel AI Projects!</h2>
      <p style="color:#555;line-height:1.6;margin:0 0 4px;">
        Hi <strong>{username}</strong>, your organisation <strong>{org_name}</strong> is ready.
        Sign in to start uploading invoices and tracking your project finances.
      </p>
      {_btn(url, "Open Finel AI Projects")}""")
    text = f"Welcome {username}! Your org {org_name} is ready. Sign in at {url}"
    _send(to, "Welcome to Finel AI Projects", html, text)
