import hashlib
import os
import re
import smtplib
import threading
import time
from email.message import EmailMessage
from functools import wraps
from math import ceil

from flask import abort, current_app, flash, redirect, request, session, url_for
from flask_login import current_user

from app.models import utcnow


class PasswordResetDeliveryError(RuntimeError):
    pass


class LoginRateLimiter:
    """Thread-safe in-memory login attempt rate limiter and brute-force protector."""

    def __init__(self, max_attempts=5, lockout_seconds=900):
        self.max_attempts = max_attempts
        self.lockout_seconds = lockout_seconds
        self._lock = threading.Lock()
        # Storage format: {key: [timestamp1, timestamp2, ...]}
        self._attempts = {}
        # Storage format: {key: lockout_until_timestamp}
        self._lockouts = {}

    def _clean_old_attempts(self, now):
        cutoff = now - self.lockout_seconds
        keys_to_delete = []
        for key, timestamps in self._attempts.items():
            valid = [ts for ts in timestamps if ts > cutoff]
            if valid:
                self._attempts[key] = valid
            else:
                keys_to_delete.append(key)
        for key in keys_to_delete:
            del self._attempts[key]

        lockouts_to_delete = [
            key for key, until in self._lockouts.items() if until <= now
        ]
        for key in lockouts_to_delete:
            del self._lockouts[key]

    def is_locked_out(self, identity: str, ip_address: str):
        now = time.time()
        with self._lock:
            self._clean_old_attempts(now)
            id_key = f"id:{identity.strip().lower()}"
            ip_key = f"ip:{ip_address.strip()}"

            for key in (id_key, ip_key):
                until = self._lockouts.get(key)
                if until and until > now:
                    return True, until - now
            return False, 0

    def record_failed_attempt(self, identity: str, ip_address: str):
        now = time.time()
        with self._lock:
            self._clean_old_attempts(now)
            id_key = f"id:{identity.strip().lower()}"
            ip_key = f"ip:{ip_address.strip()}"

            for key in (id_key, ip_key):
                if key not in self._attempts:
                    self._attempts[key] = []
                self._attempts[key].append(now)

                if len(self._attempts[key]) >= self.max_attempts:
                    self._lockouts[key] = now + self.lockout_seconds

    def clear_attempts(self, identity: str, ip_address: str):
        with self._lock:
            id_key = f"id:{identity.strip().lower()}"
            ip_key = f"ip:{ip_address.strip()}"
            self._attempts.pop(id_key, None)
            self._attempts.pop(ip_key, None)
            self._lockouts.pop(id_key, None)
            self._lockouts.pop(ip_key, None)

    def reset_all(self):
        with self._lock:
            self._attempts.clear()
            self._lockouts.clear()


login_limiter = LoginRateLimiter()


def validate_password_strength(password: str) -> tuple[bool, str]:
    """Validate password strength according to healthcare application security standards.
    
    Requirements:
    - Minimum 8 characters.
    - At least one letter (uppercase or lowercase).
    - At least one number or special character.
    """
    if not password or len(password) < 8:
        return False, "Password must be at least 8 characters long."
    if not re.search(r"[A-Za-z]", password):
        return False, "Password must contain at least one letter."
    if not re.search(r"[0-9!@#$%^&*(),.?\":{}|<>]", password):
        return False, "Password must contain at least one number or special character."
    return True, ""


def role_required(*roles):
    """Decorator to enforce strict role-based access control on routes."""
    def decorator(fn):
        @wraps(fn)
        def decorated_view(*args, **kwargs):
            if not current_user.is_authenticated:
                return current_app.login_manager.unauthorized()
            if current_user.role not in roles:
                abort(403)
            return fn(*args, **kwargs)
        return decorated_view
    return decorator


def is_sms_configured() -> bool:
    """Check if a live SMS provider (Twilio) is configured with non-demo credentials."""
    force_demo = current_app.config.get("SMS_FORCE_DEMO", False)
    sid = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    from_number = os.getenv("TWILIO_PHONE_NUMBER")
    return not force_demo and bool(sid and token and from_number)


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
