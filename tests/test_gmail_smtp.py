"""
Tests for VaxiCare Gmail SMTP Integration & Diagnostic Tools
Verifies:
- Gmail SMTP configuration loading & interoperability with SMTP_* keys
- Google App Password space stripping (e.g. 'xxxx xxxx xxxx xxxx' -> 'xxxxxxxxxxxxxxxx')
- Successful test email delivery verification (250 OK acceptance)
- Exception handling (Auth failure, Connection error, Timeout, Refused recipients)
- Credential leak prevention in logs and messages
- Admin dashboard Send Test Email route & rate limiting
- Reminder email generation (plain text + branded HTML alternative)
- Password reset email delivery with Gmail credentials
"""

import os
import socket
import tempfile
from datetime import date, datetime, timedelta, UTC
from unittest.mock import MagicMock, patch
import smtplib

import pytest

from app import create_app, db
from app.models import EmailTestAttempt, User, utcnow
from app.services import (
    is_valid_email_address,
    send_test_email,
    send_vaccination_reminder_email,
)
from app.auth_services import send_password_reset_email


@pytest.fixture
def smtp_app():
    db_fd, db_path = tempfile.mkstemp(suffix="_test_smtp.db")
    os.close(db_fd)

    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
        "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": "test-smtp-secret-key-456",
        "SEED_DEMO_DATA": False,
        "MAIL_SERVER": "smtp.gmail.com",
        "MAIL_PORT": 587,
        "MAIL_USE_TLS": True,
        "MAIL_USERNAME": "tallamsatvika@gmail.com",
        "MAIL_PASSWORD": "abcd efgh ijkl mnop",
        "MAIL_DEFAULT_SENDER": "tallamsatvika@gmail.com",
        "MAIL_ENABLED": True,
    })

    with app.app_context():
        db.create_all()
        admin = User(
            username="smtp_admin",
            email="smtp_admin@example.com",
            role="admin",
            auth_version=1,
        )
        admin.set_password("AdminSecure#2026")
        db.session.add(admin)
        db.session.commit()

    yield app

    with app.app_context():
        db.session.remove()
        db.engine.dispose()
    try:
        if os.path.exists(db_path):
            os.remove(db_path)
    except OSError:
        pass


@pytest.fixture
def smtp_client(smtp_app):
    return smtp_app.test_client()


def login_admin(client, username="smtp_admin", password="AdminSecure#2026"):
    return client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=True,
    )


# ---------------------------------------------------------------------------
# Test 1: Configuration loaded and mapped to both MAIL_* and SMTP_*
# ---------------------------------------------------------------------------
def test_gmail_smtp_configuration_mapping(smtp_app):
    with smtp_app.app_context():
        assert smtp_app.config["MAIL_SERVER"] == "smtp.gmail.com"
        assert smtp_app.config["SMTP_HOST"] == "smtp.gmail.com"
        assert smtp_app.config["MAIL_PORT"] == 587
        assert smtp_app.config["SMTP_PORT"] == 587
        assert smtp_app.config["MAIL_USERNAME"] == "tallamsatvika@gmail.com"
        assert smtp_app.config["SMTP_USERNAME"] == "tallamsatvika@gmail.com"
        assert smtp_app.config["MAIL_DEFAULT_SENDER"] == "tallamsatvika@gmail.com"
        assert smtp_app.config["SMTP_FROM"] == "tallamsatvika@gmail.com"
        assert smtp_app.config["MAIL_USE_TLS"] is True
        assert smtp_app.config["SMTP_USE_TLS"] is True
        assert smtp_app.config["MAIL_ENABLED"] is True


# ---------------------------------------------------------------------------
# Test 2: Email format validation
# ---------------------------------------------------------------------------
def test_is_valid_email_address():
    assert is_valid_email_address("user@gmail.com") is True
    assert is_valid_email_address("user.name+tag@sub.domain.co.uk") is True
    assert is_valid_email_address("bad-email") is False
    assert is_valid_email_address("@missing-local.com") is False
    assert is_valid_email_address("missing-domain@") is False
    assert is_valid_email_address("") is False
    assert is_valid_email_address(None) is False


# ---------------------------------------------------------------------------
# Test 3: Successful SMTP sending with Google App Password space stripping
# ---------------------------------------------------------------------------
def test_send_test_email_success_with_password_strip(smtp_app):
    with smtp_app.app_context():
        with patch("smtplib.SMTP") as mock_smtp_cls:
            mock_smtp = MagicMock()
            mock_smtp_cls.return_value.__enter__.return_value = mock_smtp
            mock_smtp.send_message.return_value = {}  # Empty dict means all accepted

            result = send_test_email("parent@example.com")

            assert result["status"] == "sent"
            assert result["smtp_response"] == "250 OK"
            assert "accepted" in result["message"].lower() or "sent" in result["message"].lower()

            # Verify TLS and login
            mock_smtp.starttls.assert_called_once()
            # Verify password spaces were stripped ('abcd efgh ijkl mnop' -> 'abcdefghijklmnop')
            mock_smtp.login.assert_called_once_with("tallamsatvika@gmail.com", "abcdefghijklmnop")
            mock_smtp.send_message.assert_called_once()


# ---------------------------------------------------------------------------
# Test 4: SMTP Authentication Failure (Credentials protected)
# ---------------------------------------------------------------------------
def test_send_test_email_auth_failure(smtp_app):
    with smtp_app.app_context():
        with patch("smtplib.SMTP") as mock_smtp_cls:
            mock_smtp = MagicMock()
            mock_smtp_cls.return_value.__enter__.return_value = mock_smtp
            mock_smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")

            result = send_test_email("parent@example.com")

            assert result["status"] == "failed"
            assert "535" in result["smtp_response"]
            assert "authentication failed" in result["message"].lower()
            # Ensure sensitive credentials are NOT leaked in output message
            assert "abcd" not in result["message"]
            assert "mnop" not in result["message"]


# ---------------------------------------------------------------------------
# Test 5: Connection Error & Timeout Handling
# ---------------------------------------------------------------------------
def test_send_test_email_connection_error(smtp_app):
    with smtp_app.app_context():
        with patch("smtplib.SMTP", side_effect=OSError("Network is unreachable")):
            result = send_test_email("parent@example.com")
            assert result["status"] == "failed"
            assert "unable to connect" in result["message"].lower()

        with patch("smtplib.SMTP", side_effect=socket.timeout("Timed out")):
            result = send_test_email("parent@example.com")
            assert result["status"] == "failed"
            assert "timed out" in result["message"].lower()


# ---------------------------------------------------------------------------
# Test 6: Invalid Recipient Format
# ---------------------------------------------------------------------------
def test_send_test_email_invalid_recipient(smtp_app):
    with smtp_app.app_context():
        with patch("smtplib.SMTP") as mock_smtp_cls:
            result = send_test_email("not-a-valid-email")
            assert result["status"] == "failed"
            assert "invalid" in result["message"].lower()
            # Ensure SMTP connection was never even attempted
            mock_smtp_cls.assert_not_called()


# ---------------------------------------------------------------------------
# Test 7: Admin Send Test Email Route (Success & Rate Limit)
# ---------------------------------------------------------------------------
def test_admin_send_test_email_route_success(smtp_app, smtp_client):
    login_admin(smtp_client)

    with patch("smtplib.SMTP") as mock_smtp_cls:
        mock_smtp = MagicMock()
        mock_smtp_cls.return_value.__enter__.return_value = mock_smtp
        mock_smtp.send_message.return_value = {}

        res = smtp_client.post(
            "/admin/send-test-email",
            data={"recipient": "verified_parent@gmail.com"},
            follow_redirects=True,
        )

        assert res.status_code == 200
        html = res.get_data(as_text=True)
        assert "Test email sent successfully to verified_parent@gmail.com" in html

        with smtp_app.app_context():
            attempts = EmailTestAttempt.query.all()
            assert len(attempts) == 1


def test_admin_send_test_email_cooldown(smtp_app, smtp_client):
    login_admin(smtp_client)

    with patch("smtplib.SMTP") as mock_smtp_cls:
        mock_smtp = MagicMock()
        mock_smtp_cls.return_value.__enter__.return_value = mock_smtp
        mock_smtp.send_message.return_value = {}

        # First request succeeds
        res1 = smtp_client.post(
            "/admin/send-test-email",
            data={"recipient": "test1@gmail.com"},
            follow_redirects=True,
        )
        assert res1.status_code == 200
        assert "Test email sent successfully" in res1.get_data(as_text=True)

        # Immediate second request hits 300-second cooldown
        res2 = smtp_client.post(
            "/admin/send-test-email",
            data={"recipient": "test2@gmail.com"},
            follow_redirects=True,
        )
        assert res2.status_code == 200
        assert "Cooldown active" in res2.get_data(as_text=True)


# ---------------------------------------------------------------------------
# Test 8: Vaccination Reminder Email with Plain Text + HTML Parts
# ---------------------------------------------------------------------------
def test_send_vaccination_reminder_email_mocked(smtp_app):
    with smtp_app.app_context():
        with patch("smtplib.SMTP") as mock_smtp_cls:
            mock_smtp = MagicMock()
            mock_smtp_cls.return_value.__enter__.return_value = mock_smtp
            mock_smtp.send_message.return_value = {}

            res = send_vaccination_reminder_email(
                recipient="parent@gmail.com",
                child_name="Aarav",
                vaccine_name="Polio (OPV)",
                dose_label="Dose 1",
                due_date=date(2026, 12, 1),
                reminder_type="upcoming",
                is_test=False,
            )

            assert res["status"] == "sent"
            assert mock_smtp.send_message.call_count == 1
            sent_msg = mock_smtp.send_message.call_args[0][0]

            assert sent_msg["To"] == "parent@gmail.com"
            assert "Aarav" in sent_msg["Subject"]
            assert "Polio (OPV)" in sent_msg["Subject"]

            # Verify content in plain text part
            plain_part = sent_msg.get_body(preferencelist=("plain",))
            assert plain_part is not None
            text = plain_part.get_content()
            assert "Aarav" in text
            assert "Polio (OPV)" in text

            # Verify HTML part exists
            html_part = sent_msg.get_body(preferencelist=("html",))
            assert html_part is not None
            html = html_part.get_content()
            assert "Aarav" in html
            assert "Polio (OPV)" in html


# ---------------------------------------------------------------------------
# Test 9: Password Reset Email with Gmail SMTP credentials
# ---------------------------------------------------------------------------
def test_send_password_reset_email_with_gmail(smtp_app):
    with smtp_app.app_context():
        with patch("smtplib.SMTP") as mock_smtp_cls:
            mock_smtp = MagicMock()
            mock_smtp_cls.return_value.__enter__.return_value = mock_smtp

            send_password_reset_email(
                recipient="reset_user@gmail.com",
                reset_url="https://vaxicare.onrender.com/reset-password/abc123token",
            )

            mock_smtp.starttls.assert_called_once()
            mock_smtp.login.assert_called_once_with("tallamsatvika@gmail.com", "abcdefghijklmnop")
            mock_smtp.send_message.assert_called_once()
