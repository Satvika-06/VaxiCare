import smtplib
from math import ceil
from email.message import EmailMessage

from flask import current_app


class PasswordResetDeliveryError(RuntimeError):
    pass


def send_password_reset_email(recipient, reset_url):
    host = current_app.config.get("SMTP_HOST")
    sender = current_app.config.get("SMTP_FROM")
    if not host or not sender:
        raise PasswordResetDeliveryError("Password reset email is not configured.")

    ttl_minutes = ceil(current_app.config["PASSWORD_RESET_TTL_SECONDS"] / 60)
    message = EmailMessage()
    message["Subject"] = "Reset your VaxiCare password"
    message["From"] = sender
    message["To"] = recipient
    message.set_content(
        "A password reset was requested for your VaxiCare account.\n\n"
        f"Use this one-time link within {ttl_minutes} minutes:\n{reset_url}\n\n"
        "If you did not request this, you can ignore this email."
    )

    try:
        port = current_app.config["SMTP_PORT"]
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=15) as smtp:
                _send_message(smtp, message)
        else:
            with smtplib.SMTP(host, port, timeout=15) as smtp:
                if current_app.config.get("SMTP_USE_TLS", True):
                    smtp.starttls()
                _send_message(smtp, message)
    except (OSError, smtplib.SMTPException) as exc:
        raise PasswordResetDeliveryError("Password reset email could not be sent.") from exc


def _send_message(smtp, message):
    username = current_app.config.get("SMTP_USERNAME")
    password = current_app.config.get("SMTP_PASSWORD")
    if username:
        smtp.login(username, password or "")
    smtp.send_message(message)
