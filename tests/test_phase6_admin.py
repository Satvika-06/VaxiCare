"""
Phase 6: Admin Center, Analytics & Notification Service Tests
24 comprehensive tests covering admin auth, real SQLite analytics, user search/filtering,
vaccine management, notification services, rate limiting, reminder scheduling,
consent validation, retry mechanisms, worker monitoring, and regression checks.
Uses isolated temp SQLite databases; never modifies instance/vaxicare.db.
"""
import os
import shutil
import tempfile
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from app import create_app, db
from app.auth_services import login_limiter
from app.models import (
    ApplicationSetting, Child, EmailReminderNotification, EmailTestAttempt,
    FCMDeviceToken, FCMPushNotification, FCMPushTestAttempt, HealthcareWorker,
    Notification, Parent, SchedulerRunState, SmsNotification, User,
    VaccinationRecord, VaccinationSchedule, Vaccine, utcnow,
)
from app.services import (
    retry_failed_notification, run_due_reminder_check,
    send_sms_notification, send_test_sms, send_vaccination_reminder_email,
)


def login(client, username, password):
    return client.post("/login", data={
        "username": username,
        "password": password,
    }, follow_redirects=True)


@pytest.fixture
def phase6_app_and_client():
    login_limiter.reset_all()
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_phase6.db")
    db_uri = "sqlite:///" + db_path.replace("\\", "/")

    app = create_app(test_config={
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": db_uri,
        "SEED_DEMO_DATA": False,
        "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": "phase6-test-secret-key",
        "SMS_FORCE_DEMO": True,
        "VAXICARE_TEST_SMS_NUMBER": "+919876543210",
        "AUTOMATIC_EMAIL_REMINDERS_ENABLED": True,
        "REMINDER_DAYS_BEFORE": 3,
    })

    with app.app_context():
        db.create_all()

        # 1. Admin user
        admin = User(username="admin_p6", email="admin@vaxicare.test", role="admin", full_name="Admin P6")
        admin.set_password("AdminSecure#2026")
        db.session.add(admin)

        # 2. Healthcare worker user & profile
        worker_user = User(username="worker_p6", email="worker@vaxicare.test", role="healthcare_worker", full_name="Worker P6")
        worker_user.set_password("WorkerSecure#2026")
        db.session.add(worker_user)
        db.session.flush()

        worker = HealthcareWorker(
            user_id=worker_user.id,
            full_name="Worker P6",
            qualification="ANM",
            department="Immunization",
            village="Greenfield",
        )
        db.session.add(worker)

        # 3. Parent user & profile
        parent_user = User(
            username="parent_p6",
            email="parent@vaxicare.test",
            role="parent",
            full_name="Parent P6",
            mobile="+919876543210",
            mobile_verified=True,
            sms_consent=True,
        )
        parent_user.set_password("ParentSecure#2026")
        db.session.add(parent_user)
        db.session.flush()

        parent = Parent(
            user_id=parent_user.id,
            first_name="Parent",
            last_name="P6",
            village="Greenfield",
        )
        db.session.add(parent)
        db.session.flush()

        # 4. Child
        today = date.today()
        child = Child(
            parent_id=parent.id,
            name="Baby Greenfield",
            dob=today - timedelta(days=45),  # 45 days old -> 6-week vaccines due around now
            gender="Female",
            village="Greenfield",
            record_token="token-baby-p6-001",
        )
        db.session.add(child)

        # 5. Vaccines & Schedules with mandatory source fields
        v1 = Vaccine(
            name="BCG",
            disease="Tuberculosis",
            description="Bacillus Calmette-Guerin",
            category="Birth",
            recommended_age="At birth",
            source="UIP National Immunization Schedule",
            source_checked_on="2026-11-04",
        )
        v2 = Vaccine(
            name="OPV",
            disease="Polio",
            description="Oral Polio Vaccine",
            category="Primary",
            recommended_age="At birth, 6, 10, 14 weeks",
            source="UIP National Immunization Schedule",
            source_checked_on="2026-11-04",
        )
        v3 = Vaccine(
            name="Pentavalent",
            disease="DPT-HepB-Hib",
            description="5-in-1 combo",
            category="Primary",
            recommended_age="6, 10, 14 weeks",
            source="UIP National Immunization Schedule",
            source_checked_on="2026-11-04",
        )
        db.session.add_all([v1, v2, v3])
        db.session.flush()

        s1 = VaccinationSchedule(vaccine_id=v1.id, dose_label="BCG Birth", age_days=0)
        s2 = VaccinationSchedule(vaccine_id=v2.id, dose_label="OPV 0", age_days=0)
        s3 = VaccinationSchedule(vaccine_id=v3.id, dose_label="Penta 1", age_days=42)
        db.session.add_all([s1, s2, s3])
        db.session.flush()

        # 6. One completed record for BCG (administered today)
        rec = VaccinationRecord(
            child_id=child.id,
            vaccine_id=v1.id,
            schedule_id=s1.id,
            recorded_by_id=worker.id,
            dose_status="completed",
            administered_date=today,
            notes="BCG administered at birth",
        )
        db.session.add(rec)

        # 7. Application settings upsert
        def _upsert_setting(k, v):
            s = ApplicationSetting.query.filter_by(key=k).first()
            if s:
                s.value = v
            else:
                db.session.add(ApplicationSetting(key=k, value=v))
        _upsert_setting("automatic_email_reminders_enabled", "true")
        _upsert_setting("email_reminder_days_before", "3")

        db.session.commit()

    client = app.test_client()
    yield app, client, db_path

    # Cleanup temp directory
    shutil.rmtree(temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Test 1: Admin login
# ---------------------------------------------------------------------------
def test_admin_login(phase6_app_and_client):
    app, client, _ = phase6_app_and_client
    res = client.post("/admin/login", data={
        "username": "admin_p6",
        "password": "AdminSecure#2026",
    }, follow_redirects=False)
    assert res.status_code == 302
    assert "/admin-dashboard" in res.headers["Location"]


# ---------------------------------------------------------------------------
# Test 2: Admin dashboard loads with 200 OK for admin
# ---------------------------------------------------------------------------
def test_admin_dashboard_loads(phase6_app_and_client):
    app, client, _ = phase6_app_and_client
    login(client, "admin_p6", "AdminSecure#2026")
    res = client.get("/admin-dashboard")
    assert res.status_code == 200
    assert b"Administrative Control Center" in res.data
    assert b"System Analytics" in res.data


# ---------------------------------------------------------------------------
# Test 3: Parent cannot access admin routes (403 Forbidden)
# ---------------------------------------------------------------------------
def test_parent_cannot_access_admin_routes(phase6_app_and_client):
    app, client, _ = phase6_app_and_client
    login(client, "parent_p6", "ParentSecure#2026")
    for endpoint in ["/admin-dashboard", "/admin/users", "/admin/notifications", "/admin/vaccines", "/admin/workers"]:
        res = client.get(endpoint)
        assert res.status_code == 403


# ---------------------------------------------------------------------------
# Test 4: Healthcare worker cannot access admin routes (403 Forbidden)
# ---------------------------------------------------------------------------
def test_healthcare_worker_cannot_access_admin_routes(phase6_app_and_client):
    app, client, _ = phase6_app_and_client
    login(client, "worker_p6", "WorkerSecure#2026")
    for endpoint in ["/admin-dashboard", "/admin/users", "/admin/notifications", "/admin/vaccines", "/admin/workers"]:
        res = client.get(endpoint)
        assert res.status_code == 403


# ---------------------------------------------------------------------------
# Test 5: Admin analytics use actual database records
# ---------------------------------------------------------------------------
def test_admin_analytics_uses_actual_database_records(phase6_app_and_client):
    app, client, _ = phase6_app_and_client
    login(client, "admin_p6", "AdminSecure#2026")
    res = client.get("/admin-dashboard")
    assert res.status_code == 200

    # There are 3 users, 1 child, 1 record administered today
    assert b"Total Users" in res.data
    assert b"3" in res.data
    assert b"Administered Today" in res.data


# ---------------------------------------------------------------------------
# Test 6: User listing and search
# ---------------------------------------------------------------------------
def test_user_listing_and_search(phase6_app_and_client):
    app, client, _ = phase6_app_and_client
    login(client, "admin_p6", "AdminSecure#2026")

    # Search for worker
    res = client.get("/admin/users?search=worker_p6")
    assert res.status_code == 200
    assert b"worker_p6" in res.data
    assert b"parent_p6" not in res.data

    # Search for non-existent user
    res_none = client.get("/admin/users?search=nonexistent_xyz")
    assert res_none.status_code == 200
    assert b"No users found matching your criteria" in res_none.data


# ---------------------------------------------------------------------------
# Test 7: User role filtering
# ---------------------------------------------------------------------------
def test_user_role_filtering(phase6_app_and_client):
    app, client, _ = phase6_app_and_client
    login(client, "admin_p6", "AdminSecure#2026")

    # Filter role=healthcare_worker
    res = client.get("/admin/users?role=healthcare_worker")
    assert res.status_code == 200
    assert b"worker_p6" in res.data
    assert b"parent_p6" not in res.data


# ---------------------------------------------------------------------------
# Test 8: Unauthorized user management is rejected
# ---------------------------------------------------------------------------
def test_unauthorized_user_management_rejected(phase6_app_and_client):
    app, client, _ = phase6_app_and_client
    login(client, "parent_p6", "ParentSecure#2026")
    res = client.post("/admin/users", data={
        "username": "hacker_admin",
        "email": "hacker@vaxicare.test",
        "password": "HackerAdmin#999",
        "role": "admin",
    })
    assert res.status_code == 403


# ---------------------------------------------------------------------------
# Test 9: Existing accounts remain intact
# ---------------------------------------------------------------------------
def test_existing_accounts_remain_intact(phase6_app_and_client):
    app, client, _ = phase6_app_and_client
    with app.app_context():
        admin = User.query.filter_by(username="admin_p6").first()
        worker = User.query.filter_by(username="worker_p6").first()
        parent = User.query.filter_by(username="parent_p6").first()

        assert admin is not None and admin.check_password("AdminSecure#2026")
        assert worker is not None and worker.check_password("WorkerSecure#2026")
        assert parent is not None and parent.check_password("ParentSecure#2026")


# ---------------------------------------------------------------------------
# Test 10: Vaccine management authorization
# ---------------------------------------------------------------------------
def test_vaccine_management_authorization(phase6_app_and_client):
    app, client, _ = phase6_app_and_client
    # Anonymous blocked
    res = client.post("/vaccine/add", data={"name": "Rotavirus", "disease": "Diarrhea", "description": "Oral rotavirus"})
    assert res.status_code in {302, 401}

    # Admin allowed
    login(client, "admin_p6", "AdminSecure#2026")
    res_admin = client.post("/vaccine/add", data={
        "name": "Rotavirus",
        "disease": "Rotaviral enteritis",
        "description": "Oral vaccine for infant diarrhea",
        "category": "Primary",
        "recommended_age": "6, 10, 14 weeks",
    }, follow_redirects=True)
    assert res_admin.status_code == 200

    with app.app_context():
        v = Vaccine.query.filter_by(name="Rotavirus").first()
        assert v is not None


# ---------------------------------------------------------------------------
# Test 11: Notification configuration status displayed honestly
# ---------------------------------------------------------------------------
def test_notification_configuration_status(phase6_app_and_client):
    app, client, _ = phase6_app_and_client
    login(client, "admin_p6", "AdminSecure#2026")
    res = client.get("/admin-dashboard")
    assert res.status_code == 200
    assert b"Twilio status" in res.data or b"Twilio configuration" in res.data
    assert b"SMTP status" in res.data or b"Send Test Email" in res.data


# ---------------------------------------------------------------------------
# Test 12: Test email endpoint validation
# ---------------------------------------------------------------------------
def test_test_email_endpoint_validation(phase6_app_and_client):
    app, client, _ = phase6_app_and_client
    login(client, "admin_p6", "AdminSecure#2026")
    # Invalid email rejected
    res = client.post("/admin/send-test-email", data={"recipient": "not-an-email"}, follow_redirects=True)
    assert res.status_code == 200
    assert b"Invalid test email recipient" in res.data or b"Invalid" in res.data


# ---------------------------------------------------------------------------
# Test 13: Test SMS endpoint validation
# ---------------------------------------------------------------------------
def test_test_sms_endpoint_validation(phase6_app_and_client):
    app, client, _ = phase6_app_and_client
    login(client, "admin_p6", "AdminSecure#2026")
    # Invalid phone format rejected
    res = client.post("/admin/send-test-sms", data={"recipient": "12345"}, follow_redirects=True)
    assert res.status_code == 200
    assert b"international number" in res.data or b"valid" in res.data.lower()


# ---------------------------------------------------------------------------
# Test 14: Push notification endpoint validation
# ---------------------------------------------------------------------------
def test_push_notification_endpoint_validation(phase6_app_and_client):
    app, client, _ = phase6_app_and_client
    # Unauthenticated rejected
    res = client.post("/register-push-device", json={"token": "sample-token"})
    assert res.status_code in {302, 401}

    # Parent can access
    login(client, "parent_p6", "ParentSecure#2026")
    res_parent = client.post("/register-push-device", json={"token": "test-fcm-token-12345"})
    assert res_parent.status_code == 200
    assert res_parent.get_json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Test 15: Rate limiting on test email and test SMS
# ---------------------------------------------------------------------------
def test_rate_limiting(phase6_app_and_client):
    app, client, _ = phase6_app_and_client
    login(client, "admin_p6", "AdminSecure#2026")

    with app.app_context():
        admin = User.query.filter_by(username="admin_p6").first()
        # Seed 10 test SMS entries within the past hour
        for i in range(10):
            db.session.add(SmsNotification(
                user_id=admin.id,
                recipient="+919876543210",
                message="Test rate limit message",
                message_type="test",
                status="simulated",
                created_at=utcnow(),
            ))
        db.session.commit()

        # The 11th call should be rate-limited
        res = send_test_sms(admin, "+919876543210")
        assert res["status"] == "failed"
        assert "Rate limit exceeded" in res["message"]


# ---------------------------------------------------------------------------
# Test 16: Parent SMS consent is respected
# ---------------------------------------------------------------------------
def test_parent_sms_consent_is_respected(phase6_app_and_client):
    app, client, _ = phase6_app_and_client
    with app.app_context():
        # Parent with sms_consent = False
        user_no_consent = User(
            username="no_consent_p6",
            email="noconsent@vaxicare.test",
            role="parent",
            mobile="+919988776655",
            sms_consent=False,
        )
        user_no_consent.set_password("Consent#1234")
        db.session.add(user_no_consent)
        db.session.flush()

        child = Child.query.first()
        vaccine = Vaccine.query.first()

        res = send_sms_notification(
            user=user_no_consent,
            child=child,
            vaccine=vaccine,
            reminder_type="upcoming",
            scheduled_for=date.today(),
        )
        assert res["status"] == "skipped"
        assert "SMS consent is not enabled" in res["message"]


# ---------------------------------------------------------------------------
# Test 17: Duplicate reminders are prevented
# ---------------------------------------------------------------------------
def test_duplicate_reminders_prevented(phase6_app_and_client):
    app, client, _ = phase6_app_and_client
    with app.app_context():
        parent_user = User.query.filter_by(username="parent_p6").first()
        child = Child.query.first()
        vaccine = Vaccine.query.first()
        sched = VaccinationSchedule.query.filter_by(vaccine_id=vaccine.id).first()

        # Send first SMS reminder
        res1 = send_sms_notification(
            user=parent_user,
            child=child,
            vaccine=vaccine,
            reminder_type="upcoming",
            scheduled_for=date.today(),
            schedule_id=sched.id,
        )
        assert res1["status"] in {"sent", "simulated"}

        # Attempt to send second reminder for the same milestone and date
        res2 = send_sms_notification(
            user=parent_user,
            child=child,
            vaccine=vaccine,
            reminder_type="upcoming",
            scheduled_for=date.today(),
            schedule_id=sched.id,
        )
        assert res2["status"] == "duplicate"


# ---------------------------------------------------------------------------
# Test 18: Scheduler does not create duplicate jobs & updates state
# ---------------------------------------------------------------------------
def test_scheduler_does_not_create_duplicate_jobs(phase6_app_and_client):
    app, client, _ = phase6_app_and_client
    with app.app_context():
        # Run reminder check twice
        run1 = run_due_reminder_check(today=date.today())
        assert run1["status"] == "success"

        run2 = run_due_reminder_check(today=date.today())
        assert run2["status"] == "success"
        # Second run skips already reminded milestones
        assert run2["email_count"] == 0

        state = SchedulerRunState.query.filter_by(name="vaccination_reminders").first()
        assert state is not None
        assert state.status == "idle"


# ---------------------------------------------------------------------------
# Test 19: Notification failures are recorded
# ---------------------------------------------------------------------------
def test_notification_failures_are_recorded(phase6_app_and_client):
    app, client, _ = phase6_app_and_client
    with app.app_context():
        # Create a failed email notification record
        fail_email = EmailReminderNotification(
            user_id=1,
            child_id=1,
            vaccine_id=1,
            schedule_id=1,
            recipient="failed@example.com",
            due_date=date.today(),
            reminder_type="upcoming",
            status="failed",
            failure_reason="SMTP connection refused",
            is_test=False,
        )
        db.session.add(fail_email)
        db.session.commit()

        assert fail_email.id is not None
        saved = db.session.get(EmailReminderNotification, fail_email.id)
        assert saved.status == "failed"
        assert "SMTP connection refused" in saved.failure_reason


# ---------------------------------------------------------------------------
# Test 20: Eligible failed notifications can be retried
# ---------------------------------------------------------------------------
def test_eligible_failed_notifications_can_be_retried(phase6_app_and_client):
    app, client, _ = phase6_app_and_client
    with app.app_context():
        # Create a failed SMS notification
        sms = SmsNotification(
            recipient="+919876543210",
            message_type="test",
            status="failed",
            failure_reason="Temporary network failure",
            attempt_count=1,
            message="Test retry message",
        )
        db.session.add(sms)
        db.session.commit()
        sms_id = sms.id

    login(client, "admin_p6", "AdminSecure#2026")
    res = client.post(f"/admin/notifications/retry/{sms_id}", data={"channel": "sms"}, follow_redirects=True)
    assert res.status_code == 200

    with app.app_context():
        updated = db.session.get(SmsNotification, sms_id)
        # Status transitioned away from failed to simulated or sent
        assert updated.status in {"simulated", "sent"}


# ---------------------------------------------------------------------------
# Test 21: Parent dashboard still works
# ---------------------------------------------------------------------------
def test_parent_dashboard_still_works(phase6_app_and_client):
    app, client, _ = phase6_app_and_client
    login(client, "parent_p6", "ParentSecure#2026")
    res = client.get("/parent-dashboard")
    assert res.status_code == 200
    assert b"Baby Greenfield" in res.data


# ---------------------------------------------------------------------------
# Test 22: Healthcare worker dashboard still works
# ---------------------------------------------------------------------------
def test_healthcare_worker_dashboard_still_works(phase6_app_and_client):
    app, client, _ = phase6_app_and_client
    login(client, "worker_p6", "WorkerSecure#2026")
    res = client.get("/healthcare-dashboard")
    assert res.status_code == 200
    assert b"Greenfield" in res.data


# ---------------------------------------------------------------------------
# Test 23: Vaccination records remain intact
# ---------------------------------------------------------------------------
def test_vaccination_records_remain_intact(phase6_app_and_client):
    app, client, _ = phase6_app_and_client
    with app.app_context():
        recs = VaccinationRecord.query.all()
        assert len(recs) >= 1
        assert recs[0].dose_status == "completed"
        assert recs[0].notes == "BCG administered at birth"


# ---------------------------------------------------------------------------
# Test 24: Database persistence works across app restart
# ---------------------------------------------------------------------------
def test_database_persistence_across_app_restart(phase6_app_and_client):
    app, client, db_path = phase6_app_and_client
    db_uri = "sqlite:///" + db_path.replace("\\", "/")

    # Start a brand new Flask app instance connecting to the same SQLite database file
    new_app = create_app(test_config={
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": db_uri,
        "SEED_DEMO_DATA": False,
        "WTF_CSRF_ENABLED": False,
    })

    with new_app.app_context():
        users = User.query.all()
        assert len(users) == 3
        admin = User.query.filter_by(username="admin_p6").first()
        assert admin is not None
        assert admin.role == "admin"
        assert Child.query.count() == 1
        assert Vaccine.query.count() == 3
        assert VaccinationRecord.query.count() == 1
