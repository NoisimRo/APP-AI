"""Email service for sending verification codes and notifications.

Uses SMTP over SSL. Configure via environment variables:
  - SMTP_HOST (default: mail.exe.org.ro)
  - SMTP_PORT (default: 465)
  - SMTP_USER (email address, e.g. asociatia@exe.org.ro)
  - SMTP_PASSWORD (email account password)
  - SMTP_FROM (sender address, defaults to SMTP_USER)
  - SMTP_SSL (true/false — use implicit SSL via SMTP_SSL, default: true)
"""

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.core.logging import get_logger

logger = get_logger(__name__)

SMTP_HOST = os.getenv("SMTP_HOST", "mail.exe.org.ro")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "") or SMTP_USER
SMTP_SSL = os.getenv("SMTP_SSL", "true").lower() in ("true", "1", "yes")


async def send_verification_email(to_email: str, code: str, name: str | None = None) -> None:
    """Send a 6-digit verification code to the user's email."""
    if not SMTP_USER or not SMTP_PASSWORD:
        logger.warning(
            "smtp_not_configured",
            message="SMTP credentials not set. Verification email not sent.",
            to=to_email,
            code=code,
        )
        return

    display_name = name or to_email.split("@")[0]
    subject = f"ExpertAP — Cod de verificare: {code}"

    html_body = f"""
    <div style="font-family: 'Segoe UI', Arial, sans-serif; max-width: 480px; margin: 0 auto; padding: 32px 24px; background: #f8fafc; border-radius: 12px;">
        <div style="text-align: center; margin-bottom: 24px;">
            <h1 style="color: #1e293b; font-size: 22px; margin: 0;">ExpertAP</h1>
            <p style="color: #64748b; font-size: 13px; margin-top: 4px;">Platformă de date pentru achiziții publice</p>
        </div>

        <div style="background: white; border-radius: 8px; padding: 24px; border: 1px solid #e2e8f0;">
            <p style="color: #334155; font-size: 15px; margin-top: 0;">Salut, <strong>{display_name}</strong>!</p>
            <p style="color: #475569; font-size: 14px;">Codul tău de verificare este:</p>

            <div style="text-align: center; margin: 24px 0;">
                <span style="font-size: 32px; font-weight: 700; letter-spacing: 8px; color: #1d4ed8; background: #eff6ff; padding: 12px 24px; border-radius: 8px; border: 2px dashed #93c5fd;">
                    {code}
                </span>
            </div>

            <p style="color: #64748b; font-size: 13px;">Codul este valabil <strong>24 de ore</strong>. Dacă nu ai solicitat acest cod, ignoră acest email.</p>
        </div>

        <p style="text-align: center; color: #94a3b8; font-size: 11px; margin-top: 20px;">
            &copy; 2026 ExpertAP. Toate drepturile rezervate.
        </p>
    </div>
    """

    text_body = f"Salut, {display_name}!\n\nCodul tău de verificare ExpertAP este: {code}\n\nCodul este valabil 24 de ore.\n\nDacă nu ai solicitat acest cod, ignoră acest email."

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"ExpertAP <{SMTP_FROM}>"
    msg["To"] = to_email
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        if SMTP_SSL:
            # Port 465: implicit SSL (wrapped connection)
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=10) as server:
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(msg)
        else:
            # Port 587: STARTTLS (upgrade plaintext to TLS)
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(msg)
        logger.info("verification_email_sent", to=to_email)
    except Exception as e:
        logger.error("smtp_send_failed", to=to_email, error=str(e))
        raise


async def send_reset_password_email(to_email: str, reset_token: str, name: str | None = None) -> None:
    """Send a password reset email with a token the user enters in the app."""
    if not SMTP_USER or not SMTP_PASSWORD:
        logger.warning(
            "smtp_not_configured",
            message="SMTP credentials not set. Reset email not sent.",
            to=to_email,
        )
        return

    display_name = name or to_email.split("@")[0]
    # Show only first 8 chars in subject for recognition, full token in body
    subject = "ExpertAP — Resetare parolă"

    html_body = f"""
    <div style="font-family: 'Segoe UI', Arial, sans-serif; max-width: 480px; margin: 0 auto; padding: 32px 24px; background: #f8fafc; border-radius: 12px;">
        <div style="text-align: center; margin-bottom: 24px;">
            <h1 style="color: #1e293b; font-size: 22px; margin: 0;">ExpertAP</h1>
            <p style="color: #64748b; font-size: 13px; margin-top: 4px;">Platformă de date pentru achiziții publice</p>
        </div>

        <div style="background: white; border-radius: 8px; padding: 24px; border: 1px solid #e2e8f0;">
            <p style="color: #334155; font-size: 15px; margin-top: 0;">Salut, <strong>{display_name}</strong>!</p>
            <p style="color: #475569; font-size: 14px;">Ai solicitat resetarea parolei. Copiază codul de mai jos și introdu-l în aplicație:</p>

            <div style="text-align: center; margin: 24px 0;">
                <span style="font-size: 14px; font-weight: 600; font-family: monospace; color: #1d4ed8; background: #eff6ff; padding: 12px 16px; border-radius: 8px; border: 2px dashed #93c5fd; word-break: break-all; display: inline-block; max-width: 100%;">
                    {reset_token}
                </span>
            </div>

            <p style="color: #64748b; font-size: 13px;">Codul este valabil <strong>1 oră</strong>. Dacă nu ai solicitat resetarea parolei, ignoră acest email.</p>
        </div>

        <p style="text-align: center; color: #94a3b8; font-size: 11px; margin-top: 20px;">
            &copy; 2026 ExpertAP. Toate drepturile rezervate.
        </p>
    </div>
    """

    text_body = f"Salut, {display_name}!\n\nAi solicitat resetarea parolei ExpertAP.\n\nCodul tău de resetare este:\n{reset_token}\n\nCodul este valabil 1 oră.\n\nDacă nu ai solicitat resetarea parolei, ignoră acest email."

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"ExpertAP <{SMTP_FROM}>"
    msg["To"] = to_email
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        if SMTP_SSL:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=10) as server:
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(msg)
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(msg)
        logger.info("reset_email_sent", to=to_email)
    except Exception as e:
        logger.error("smtp_send_failed_reset", to=to_email, error=str(e))
        raise
