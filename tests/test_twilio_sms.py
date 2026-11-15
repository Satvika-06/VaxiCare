
"""
Tests for VaxiCare Twilio SMS Integration & Diagnostic Tools
"""

import os
import socket
import tempfile
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from twilio.base.exceptions import TwilioRestException

from app import create_app, db
from app.models import (
    Child,
    Parent,
    SmsNotification,
    User,
    Vaccine,
    VaccinationSchedule,
    utcnow,
)
from app.services import (
    is_valid_mobile_number,
    normalize_phone_number,
    send_sms_notification,
    send_test_sms,
)


@pytest.fixture
def twilio_app():
    db_fd, db_path = tempfile.mkstemp(suffix="_test_twilio.db")
    os.close(db_fd)

    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
        "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": "test-only-secret-key",
        "SEED_DEMO_DATA": False,
        "TWILIO_ACCOUNT_SID": os.getenv("TWILIO_ACCOUNT_SID"),
        "TWILIO_AUTH_TOKEN": os.getenv("TWILIO_AUTH_TOKEN"),
        "TWILIO_PHONE_NUMBER": os.getenv("TWILIO_PHONE_NUMBER"),
        "SMS_FORCE_DEMO": False,
    })

    with app.app_context():
        db.create_all()

        admin = User(
            username="twilio_admin",
            email="twilio_admin@example.com",
            role="admin",
            auth_version=1,
        )
        admin.set_password("AdminSecure#2026")
        db.session.add(admin)

        parent_u = User(
            username="parent_twilio",
            email="parent_tw@example.com",
            role="parent",
            mobile="+919876543210",
            sms_consent=True,
            auth_version=1,
        )
        parent_u.set_password("ParentSecure#2026")
        db.session.add(parent_u)
        db.session.commit()

        parent_prof = Parent(
            user_id=parent_u.id,
            first_name="Aarohi",
            last_name="Sharma",
            village="Kalyan",
        )
        db.session.add(parent_prof)
        db.session.commit()

        v_bcg = Vaccine(
            name="BCG",
            disease="Tuberculosis",
            description="Bacillus Calmette-Guerin",
            source="MoHFW",
            source_checked_on="2026-01-01",
        )
        db.session.add(v_bcg)
        db.session.commit()

        child = Child(
            parent_id=parent_prof.id,
            name="Aarav Sharma",
            dob=date(2026, 1, 1),
            gender="male",
            village="Kalyan",
            record_token="token-twilio-test-1",
        )
        db.session.add(child)
        db.session.commit()

        sched = VaccinationSchedule(
            vaccine_id=v_bcg.id,
            dose_label="At Birth",
            age_days=0,
            description="BCG at birth",
        )
        db.session.add(sched)
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
def twilio_client(twilio_app):
    return twilio_app.test_client()


def login_admin(client, username="twilio_admin", password="AdminSecure#2026"):
    return client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=True,
    )


# ---------------------------------------------------------------------------
# Test 1: Phone number normalization
# ---------------------------------------------------------------------------

def test_phone_normalization():
    assert normalize_phone_number("+919121478435") == "+919121478435"
    assert normalize_phone_number("+91 91214 78435") == "+919121478435"
    assert normalize_phone_number("+91-91214-78435") == "+919121478435"
    assert normalize_phone_number("9121478435") == "+919121478435"
    assert normalize_phone_number("09121478435") == "+919121478435"
    assert normalize_phone_number("919121478435") == "+919121478435"
    assert normalize_phone_number("+1 (555) 123-4567") == "+15551234567"
    assert normalize_phone_number("12345") is None
    assert normalize_phone_number("invalid-text") is None
    assert normalize_phone_number("") is None
    assert normalize_phone_number(None) is None


# ---------------------------------------------------------------------------
# Test 2: Mobile validation
# ---------------------------------------------------------------------------

def test_is_valid_mobile_number():
    assert is_valid_mobile_number("+919121478435") is True
    assert is_valid_mobile_number("9121478435") is True
    assert is_valid_mobile_number("+91 91214 78435") is True
    assert is_valid_mobile_number("12345") is False
    assert is_valid_mobile_number("not-a-number") is False
    assert is_valid_mobile_number("") is False
    assert is_valid_mobile_number(None) is False


# ---------------------------------------------------------------------------
# Test 3: Mocked Twilio send test SMS success with SID
# ---------------------------------------------------------------------------

def test_send_test_sms_success_mocked(twilio_app):
    with twilio_app.app_context():
        admin = User.query.filter_by(username="twilio_admin").first()

        with patch("app.services.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client

            mock_message = MagicMock()
            mock_message.sid = "SM998877665544332211"
            mock_client.messages.create.return_value = mock_message

            res = send_test_sms(admin, "+919121478435")

            assert res["status"] == "sent"
            assert res["sid"] == "SM998877665544332211"
            assert "SM998877665544332211" in res["message"]

            mock_client.messages.create.assert_called_once()
            call_kwargs = mock_client.messages.create.call_args[1]

            assert call_kwargs["to"] == "+919121478435"
            assert call_kwargs["from_"] == "+919121478435"
            assert "VaxiCare" in call_kwargs["body"]

            saved = SmsNotification.query.filter_by(
                provider_message_id="SM998877665544332211"
            ).first()

            assert saved is not None
            assert saved.status == "sent"
            assert saved.recipient == "+919121478435"


# ---------------------------------------------------------------------------
# Test 4: Twilio Authentication Failure
# ---------------------------------------------------------------------------

def test_send_test_sms_auth_failure_mocked(twilio_app):
    with twilio_app.app_context():
        admin = User.query.filter_by(username="twilio_admin").first()

        with patch("app.services.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client

            mock_client.messages.create.side_effect = TwilioRestException(
                status=401,
                uri="/2010-04-01/Accounts/AC0dc.../Messages.json",
                msg="Authentication Error - No credentials provided",
                code=20003,
            )

            res = send_test_sms(admin, "+919121478435")

            assert res["status"] == "failed"
            assert "Twilio authentication failed" in res["message"]
            assert "fbb163d" not in res["message"]


# ---------------------------------------------------------------------------
# Test 5: Twilio Unverified Trial Number Restriction
# ---------------------------------------------------------------------------

def test_send_test_sms_unverified_number_mocked(twilio_app):
    with twilio_app.app_context():
        admin = User.query.filter_by(username="twilio_admin").first()

        with patch("app.services.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client

            mock_client.messages.create.side_effect = TwilioRestException(
                status=400,
                uri="/2010-04-01/Accounts/AC0dc.../Messages.json",
                msg="The number +919999999999 is unverified.",
                code=21608,
            )

            res = send_test_sms(admin, "+919999999999")

            assert res["status"] == "failed"
            assert "unverified number" in res["message"].lower()


# ---------------------------------------------------------------------------
# Test 6: Twilio Insufficient Balance / Permissions
# ---------------------------------------------------------------------------

def test_send_test_sms_insufficient_balance_mocked(twilio_app):
    with twilio_app.app_context():
        admin = User.query.filter_by(username="twilio_admin").first()

        with patch("app.services.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client

            mock_client.messages.create.side_effect = TwilioRestException(
                status=400,
                uri="/2010-04-01/Accounts/AC0dc.../Messages.json",
                msg="Account balance is zero.",
                code=20008,
            )

            res = send_test_sms(admin, "+919121478435")

            assert res["status"] == "failed"
            assert "balance insufficient" in res["message"].lower()


# ---------------------------------------------------------------------------
# Test 7: Network timeout / connection error
# ---------------------------------------------------------------------------

def test_send_test_sms_network_error(twilio_app):
    with twilio_app.app_context():
        admin = User.query.filter_by(username="twilio_admin").first()

        with patch("app.services.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client

            mock_client.messages.create.side_effect = ConnectionError(
                "Connection refused by gateway"
            )

            res = send_test_sms(admin, "+919121478435")

            assert res["status"] == "failed"
            assert "unable to connect" in res["message"].lower()


# ---------------------------------------------------------------------------
# Test 8: Rate limit on Send Test SMS
# ---------------------------------------------------------------------------

def test_send_test_sms_rate_limit(twilio_app):
    with twilio_app.app_context():
        admin = User.query.filter_by(username="twilio_admin").first()

        for _ in range(10):
            db.session.add(
                SmsNotification(
                    recipient="+919121478435",
                    message_type="test",
                    message="Rate limit test",
                    status="sent",
                    created_at=utcnow(),
                )
            )

        db.session.commit()

        res = send_test_sms(admin, "+919121478435")

        assert res["status"] == "failed"
        assert "Rate limit exceeded" in res["message"]


# ---------------------------------------------------------------------------
# Test 9: Admin Dashboard Send Test SMS Route
# ---------------------------------------------------------------------------

def test_admin_send_test_sms_route_mocked(twilio_app, twilio_client):
    login_admin(twilio_client)

    with patch("app.services.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        mock_message = MagicMock()
        mock_message.sid = "SMabcdef1234567890"
        mock_client.messages.create.return_value = mock_message

        res = twilio_client.post(
            "/admin/send-test-sms",
            data={"recipient": "+919121478435"},
            follow_redirects=True,
        )

        assert res.status_code == 200

        html = res.get_data(as_text=True)
        assert "SMabcdef1234567890" in html


# ---------------------------------------------------------------------------
# Test 10: Vaccination Milestone SMS Reminder
# ---------------------------------------------------------------------------

def test_send_vaccination_sms_reminder_mocked(twilio_app):
    with twilio_app.app_context():
        parent_user = User.query.filter_by(username="parent_twilio").first()
        child = Child.query.filter_by(name="Aarav Sharma").first()
        vaccine = Vaccine.query.filter_by(name="BCG").first()
        sched = VaccinationSchedule.query.filter_by(
            vaccine_id=vaccine.id
        ).first()

        with patch("app.services.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client

            mock_message = MagicMock()
            mock_message.sid = "SMmilestone1234567"
            mock_client.messages.create.return_value = mock_message

            res = send_sms_notification(
                user=parent_user,
                child=child,
                vaccine=vaccine,
                reminder_type="upcoming",
                language="en",
                scheduled_for=date.today(),
                schedule_id=sched.id,
            )

            assert res["status"] == "sent"
            assert res["sid"] == "SMmilestone1234567"

            call_kwargs = mock_client.messages.create.call_args[1]
            assert "Aarav Sharma" in call_kwargs["body"]
            assert "BCG" in call_kwargs["body"]


# ---------------------------------------------------------------------------
# Test 11: Twilio Error 572006
# ---------------------------------------------------------------------------

def test_twilio_trial_template_restriction_572006(twilio_app):
    with twilio_app.app_context():
        admin_user = User.query.filter_by(username="twilio_admin").first()

        with patch("app.services.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client

            exc = TwilioRestException(
                status=400,
                uri="/Accounts/AC123/Messages",
                msg=(
                    "Unable to create record: Invalid template name. "
                    "Trial accounts can only use predefined SMS templates."
                ),
                code=572006,
            )

            mock_client.messages.create.side_effect = exc

            result = send_test_sms(admin_user, "+919121478435")

            assert result["status"] == "failed"
            assert "572006" in result["message"]
            assert "Trial" in result["message"]


# ---------------------------------------------------------------------------
# Test 12: Demo Mode makes zero Twilio API calls
# ---------------------------------------------------------------------------

def test_demo_mode_zero_twilio_api_calls(twilio_app):
    with twilio_app.app_context():
        twilio_app.config["SMS_FORCE_DEMO"] = True
        admin_user = User.query.filter_by(username="twilio_admin").first()

        with patch("app.services.Client") as mock_client_cls:
            result = send_test_sms(admin_user, "+919121478435")

            mock_client_cls.assert_not_called()

            assert result["status"] == "simulated"
            assert result["status"] != "sent"
            assert result["message"] == (
                "SMS simulated successfully — no message was sent."
            )
            assert result.get("sid") is None

            last_record = SmsNotification.query.order_by(
                SmsNotification.id.desc()
            ).first()

            assert last_record is not None
            assert last_record.status == "simulated"
            assert last_record.failure_reason == (
                "SMS simulated successfully — no message was sent."
            )
            assert last_record.provider_message_id is None


# ---------------------------------------------------------------------------
# Test 13: Demo Mode with Child Vaccination Reminder
# ---------------------------------------------------------------------------

def test_demo_mode_child_vaccination_reminder(twilio_app):
    with twilio_app.app_context():
        twilio_app.config["SMS_FORCE_DEMO"] = True

        parent_user = User.query.filter_by(username="parent_twilio").first()
        child = Child.query.filter_by(name="Aarav Sharma").first()
        vaccine = Vaccine.query.filter_by(name="BCG").first()
        sched = VaccinationSchedule.query.filter_by(
            vaccine_id=vaccine.id
        ).first()

        with patch("app.services.Client") as mock_client_cls:
            res = send_sms_notification(
                user=parent_user,
                child=child,
                vaccine=vaccine,
                reminder_type="upcoming",
                language="en",
                scheduled_for=date.today(),
                schedule_id=sched.id,
            )

            mock_client_cls.assert_not_called()

            assert res["status"] == "simulated"
            assert res["status"] != "sent"
            assert res["message"] == (
                "SMS simulated successfully — no message was sent."
            )

            last_record = SmsNotification.query.order_by(
                SmsNotification.id.desc()
            ).first()

            assert last_record.child_id == child.id
            assert last_record.vaccine_id == vaccine.id
            assert "Aarav Sharma" in last_record.message
            assert "BCG" in last_record.message
            assert last_record.status == "simulated"


# ---------------------------------------------------------------------------
# Test 14: Real Twilio path when demo mode is false
# ---------------------------------------------------------------------------

def test_real_twilio_path_when_demo_mode_false(twilio_app):
    with twilio_app.app_context():
        twilio_app.config["SMS_FORCE_DEMO"] = False
        admin_user = User.query.filter_by(username="twilio_admin").first()

        with patch("app.services.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client

            mock_msg = MagicMock()
            mock_msg.sid = "SMreal_delivery_sid_999"
            mock_client.messages.create.return_value = mock_msg

            res = send_test_sms(admin_user, "+919121478435")

            mock_client_cls.assert_called_once()
            mock_client.messages.create.assert_called_once()

            assert res["status"] == "sent"
            assert res["sid"] == "SMreal_delivery_sid_999"


# ---------------------------------------------------------------------------
# Test 15: Demo Mode prohibited in production
# ---------------------------------------------------------------------------

def test_demo_mode_prohibited_in_production(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.setenv(
        "SECRET_KEY",
        "super-secret-production-key-1234567890",
    )
    monkeypatch.setenv("SMS_FORCE_DEMO", "true")

    with pytest.raises(
        RuntimeError,
        match="SMS Demo Mode is strictly prohibited in production",
    ):
        create_app({"TESTING": False})


# ---------------------------------------------------------------------------
# Test 16: Admin dashboard displays DEMO MODE indicator
# ---------------------------------------------------------------------------

def test_admin_dashboard_demo_mode_display(twilio_client, twilio_app):
    with twilio_app.app_context():
        twilio_app.config["SMS_FORCE_DEMO"] = True

    login_admin(twilio_client)

    res = twilio_client.get("/admin-dashboard")
    assert res.status_code == 200

    html = res.get_data(as_text=True)
    assert "DEMO MODE — NOT SENT" in html

    post_res = twilio_client.post(
        "/admin/send-test-sms",
        data={"recipient": "+919121478435"},
        follow_redirects=True,
    )

    assert post_res.status_code == 200

    post_html = post_res.get_data(as_text=True)

    assert "SMS simulated successfully — no message was sent." in post_html
    assert "alert-warning" in post_html
    assert "DEMO MODE — NOT SENT" in post_html


# ---------------------------------------------------------------------------
# Test 17: Unified Notification Center displays DEMO MODE indicator
# ---------------------------------------------------------------------------

def test_unified_notification_center_demo_mode_display(
    twilio_client,
    twilio_app,
):
    with twilio_app.app_context():
        twilio_app.config["SMS_FORCE_DEMO"] = True
        admin = User.query.filter_by(username="twilio_admin").first()
        send_test_sms(admin, "+919121478435")

    login_admin(twilio_client)

    res = twilio_client.get("/admin/notifications?channel=sms")
    assert res.status_code == 200

    html = res.get_data(as_text=True)

    assert "DEMO MODE — NOT SENT" in html
    assert "Simulated (Demo Mode)" in html
    assert "SMS simulated successfully — no message was sent." in html
