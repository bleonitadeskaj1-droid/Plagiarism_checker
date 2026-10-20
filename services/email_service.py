"""Dërgim email-i (SMTP) për rivendosje fjalëkalimi."""

from __future__ import annotations

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


def smtp_configured() -> bool:
    host = (os.getenv("SMTP_HOST") or "").strip()
    return bool(host)


def send_email(*, to_email: str, subject: str, html_body: str, text_body: str) -> bool:
    """Dërgo email. Kthen True nëse u dërgua, False nëse SMTP nuk është konfiguruar."""
    host = (os.getenv("SMTP_HOST") or "").strip()
    if not host:
        logger.warning("SMTP_HOST mungon — email nuk u dërgua te %s", to_email)
        return False

    port = int(os.getenv("SMTP_PORT", "587"))
    user = (os.getenv("SMTP_USER") or "").strip()
    password = os.getenv("SMTP_PASSWORD") or ""
    from_addr = (os.getenv("SMTP_FROM") or user or "noreply@universitetiaab.com").strip()
    use_tls = os.getenv("SMTP_USE_TLS", "1").lower() in {"1", "true", "yes"}

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_email
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP(host, port, timeout=30) as server:
            if use_tls:
                server.starttls()
            if user and password:
                server.login(user, password)
            server.sendmail(from_addr, [to_email], msg.as_string())
        logger.info("Password reset email sent to %s", to_email)
        return True
    except Exception as exc:
        logger.exception("Email send failed to %s: %s", to_email, exc)
        raise
