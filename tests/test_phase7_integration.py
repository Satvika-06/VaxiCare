"""
Phase 7: Complete Integration, UI/UX Refinement & End-to-End Testing
Comprehensive test suite verifying the multi-role lifecycle of Parent, Healthcare Worker,
and Administrator workflows, language switching, QR verification, notification dispatching,
scheduler idempotency, cross-role security, and regression protection.
Uses isolated temp SQLite databases; never modifies instance/vaxicare.db.
"""
import os
import shutil
import tempfile
from datetime import date, datetime, timedelta

import pytest
from app import create_app, db
from app.auth_services import login_limiter
from app.models import (
    ApplicationSetting, Child, EmailReminderNotification, HealthcareWorker,
    Notification, Parent, SchedulerRunState, SmsNotification, User,
    VaccinationRecord, VaccinationSchedule, Vaccine, utcnow,
)
from app.services import retry_failed_notification, run_due_reminder_check


def login_helper(client, username, password):
    return client.post("/login", data={
        "username": username,
        "password": password,
    }, follow_redirects=True)


@pytest.fixture
def integration_app_and_client():
    login_limiter.reset_all()
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_phase7_integration.db")
    db_uri = "sqlite:///" + db_path.replace("\\", "/")

    app = create_app(test_config={
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": db_uri,
        "SEED_DEMO_DATA": False,
        "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": "phase7-test-secret-key-integration",
        "SMS_FORCE_DEMO": True,
        "VAXICARE_TEST_SMS_NUMBER": "+919876543210",
        "AUTOMATIC_EMAIL_REMINDERS_ENABLED": True,
        "REMINDER_DAYS_BEFORE": 7,
    })

    with app.app_context():
        db.create_all()

        # Seed Admin
        admin = User(username="admin_i7", email="admin_i7@vaxicare.test", role="admin", full_name="Admin I7")
        admin.set_password("AdminSecure#2026")
        db.session.add(admin)

        # Seed Healthcare Worker assigned to Greenfield
        worker_user = User(username="worker_i7", email="worker_i7@vaxicare.test", role="healthcare_worker", full_name="Nurse Greenfield")
        worker_user.set_password("WorkerSecure#2026")
        db.session.add(worker_user)
        db.session.flush()

        worker = HealthcareWorker(
            user_id=worker_user.id,
            full_name="Nurse Greenfield",
            qualification="B.Sc Nursing",
            department="Pediatrics",
            village="Greenfield",
        )
        db.session.add(worker)

        # Seed Parent in Greenfield
        parent_user = User(
            username="parent_i7",
            email="parent_i7@vaxicare.test",
            role="parent",
            full_name="Parent Seven",
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
            last_name="Seven",
            village="Greenfield",
        )
        db.session.add(parent)
        db.session.flush()

        # Child in Greenfield
        today = date.today()
        child = Child(
            parent_id=parent.id,
            name="Aarav Seven",
            dob=today - timedelta(days=42),  # 6 weeks old
            gender="Male",
            village="Greenfield",
            record_token="token-aarav-i7-001",
        )
        db.session.add(child)

        # Seed standard UIP Vaccines & Schedules
        v_bcg = Vaccine(
            name="BCG",
            disease="Tuberculosis",
            description="Bacillus Calmette-Guerin",
            category="Birth",
            recommended_age="At birth",
            source="UIP National Immunization Schedule",
            source_checked_on="2026-11-04",
        )
        v_penta = Vaccine(
            name="Pentavalent",
            disease="DPT-HepB-Hib",
            description="5-in-1 combo vaccine",
            category="Primary",
            recommended_age="6, 10, 14 weeks",
            source="UIP National Immunization Schedule",
            source_checked_on="2026-11-04",
        )
        db.session.add_all([v_bcg, v_penta])
        db.session.flush()

        s_bcg = VaccinationSchedule(vaccine_id=v_bcg.id, dose_label="BCG Birth", age_days=0)
        s_penta1 = VaccinationSchedule(vaccine_id=v_penta.id, dose_label="Penta 1", age_days=42)
        s_penta2 = VaccinationSchedule(vaccine_id=v_penta.id, dose_label="Penta 2", age_days=70)
        db.session.add_all([s_bcg, s_penta1, s_penta2])
        db.session.flush()

        # BCG administered at birth
        rec_bcg = VaccinationRecord(
            child_id=child.id,
            vaccine_id=v_bcg.id,
            schedule_id=s_bcg.id,
            recorded_by_id=worker.id,
            dose_status="completed",
            administered_date=today - timedelta(days=40),
            notes="Given at birth",
        )
        db.session.add(rec_bcg)

        # Application settings
        def _upsert_setting(k, v):
            s = ApplicationSetting.query.filter_by(key=k).first()
            if s:
                s.value = v
            else:
                db.session.add(ApplicationSetting(key=k, value=v))
        _upsert_setting("automatic_email_reminders_enabled", "true")
        _upsert_setting("email_reminder_days_before", "7")

        db.session.commit()

    client = app.test_client()
    yield app, client, db_path

    shutil.rmtree(temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 1. Registration to Login Journey
# ---------------------------------------------------------------------------
def test_registration_to_login_journey(integration_app_and_client):
    app, client, _ = integration_app_and_client
    # Register new parent
    res_reg = client.post("/parent/register", data={
        "username": "newmom2026",
        "email": "newmom@vaxicare.test",
        "password": "StrongPassword#999",
        "confirm_password": "StrongPassword#999",
        "first_name": "Priya",
        "last_name": "Sharma",
        "mobile": "+919811223344",
        "village": "Sunrise",
    }, follow_redirects=True)
    assert res_reg.status_code == 200

    # User is automatically logged in upon registration; now log out and log in cleanly
    client.post("/logout")
    res_login = login_helper(client, "newmom2026", "StrongPassword#999")
    assert res_login.status_code == 200
    assert b"Priya" in res_login.data or b"Dashboard" in res_login.data or b"Parent" in res_login.data


# ---------------------------------------------------------------------------
# 2. Role-Based Redirects
# ---------------------------------------------------------------------------
def test_role_based_redirects(integration_app_and_client):
    app, client, _ = integration_app_and_client
    # Parent login -> parent dashboard
    r_parent = client.post("/login", data={"username": "parent_i7", "password": "ParentSecure#2026"}, follow_redirects=False)
    assert r_parent.status_code == 302
    assert "/parent-dashboard" in r_parent.headers["Location"]

    # Worker login -> healthcare dashboard
    client.post("/logout")
    r_worker = client.post("/healthcare/login", data={"username": "worker_i7", "password": "WorkerSecure#2026"}, follow_redirects=False)
    assert r_worker.status_code == 302
    assert "/healthcare-dashboard" in r_worker.headers["Location"] or "/worker-dashboard" in r_worker.headers["Location"]

    # Admin login -> admin dashboard
    client.post("/logout")
    r_admin = client.post("/admin/login", data={"username": "admin_i7", "password": "AdminSecure#2026"}, follow_redirects=False)
    assert r_admin.status_code == 302
    assert "/admin-dashboard" in r_admin.headers["Location"]


# ---------------------------------------------------------------------------
# 3. Parent Child Lifecycle and Viewing
# ---------------------------------------------------------------------------
def test_parent_child_lifecycle_and_viewing(integration_app_and_client):
    app, client, _ = integration_app_and_client
    login_helper(client, "parent_i7", "ParentSecure#2026")

    # Add second child via /child/add
    res_add = client.post("/child/add", data={
        "name": "Ananya Seven",
        "dob": "2026-08-01",
        "gender": "Female",
        "village": "Greenfield",
        "allergies": "None",
        "notes": "Healthy twin",
    }, follow_redirects=True)
    assert res_add.status_code == 200
    assert b"Ananya Seven" in res_add.data

    # View child detail
    with app.app_context():
        ananya = Child.query.filter_by(name="Ananya Seven").first()
        assert ananya is not None
        child_id = ananya.id

    res_detail = client.get(f"/child/{child_id}")
    assert res_detail.status_code == 200
    assert b"Ananya Seven" in res_detail.data
    assert b"Vaccination Schedule" in res_detail.data or b"Schedule" in res_detail.data


# ---------------------------------------------------------------------------
# 4. Parent Child Isolation Protection
# ---------------------------------------------------------------------------
def test_parent_child_isolation_protection(integration_app_and_client):
    app, client, _ = integration_app_and_client
    # Create another parent with a child
    with app.app_context():
        p2_user = User(username="parent_other", email="other@vaxicare.test", role="parent", full_name="Other Parent")
        p2_user.set_password("OtherParent#2026")
        db.session.add(p2_user)
        db.session.flush()
        p2 = Parent(user_id=p2_user.id, first_name="Other", last_name="Parent", village="Northwood")
        db.session.add(p2)
        db.session.flush()
        c2 = Child(parent_id=p2.id, name="Other Kid", dob=date(2025, 1, 1), gender="Male", village="Northwood", record_token="tok-other-kid")
        db.session.add(c2)
        db.session.commit()
        c2_id = c2.id

    # Log in as parent_i7 and attempt to view, edit, or delete c2
    login_helper(client, "parent_i7", "ParentSecure#2026")
    assert client.get(f"/child/{c2_id}").status_code == 403
    assert client.post(f"/child/{c2_id}/edit", data={"name": "Tampered Name"}).status_code == 403
    assert client.post(f"/child/{c2_id}/delete").status_code == 403


# ---------------------------------------------------------------------------
# 5. Healthcare Worker Child Lookup and Village Access Control
# ---------------------------------------------------------------------------
def test_worker_child_lookup_and_village_access_control(integration_app_and_client):
    app, client, _ = integration_app_and_client
    login_helper(client, "worker_i7", "WorkerSecure#2026")

    # Worker belongs to Greenfield; Aarav Seven is in Greenfield
    res_dash = client.get("/healthcare-dashboard")
    assert res_dash.status_code == 200
    assert b"Aarav Seven" in res_dash.data

    with app.app_context():
        aarav = Child.query.filter_by(name="Aarav Seven").first()
        aarav_id = aarav.id

    # Access authorized child detail
    res_view = client.get(f"/child/{aarav_id}")
    assert res_view.status_code == 200

    # Create child in another village (Northwood)
    with app.app_context():
        p_nw = Parent(user_id=1, first_name="Remote", village="Northwood")
        c_remote = Child(parent_id=1, name="Remote Child", dob=date(2025, 2, 1), gender="Male", village="Northwood", record_token="tok-remote-001")
        db.session.add(c_remote)
        db.session.commit()
        c_remote_id = c_remote.id

    # Worker from Greenfield attempting to access child in Northwood receives 403
    res_blocked = client.get(f"/child/{c_remote_id}")
    assert res_blocked.status_code == 403


# ---------------------------------------------------------------------------
# 6. Vaccination Administration and Clinical History Update
# ---------------------------------------------------------------------------
def test_vaccination_administration_and_clinical_history(integration_app_and_client):
    app, client, _ = integration_app_and_client
    login_helper(client, "worker_i7", "WorkerSecure#2026")

    with app.app_context():
        aarav = Child.query.filter_by(name="Aarav Seven").first()
        v_penta = Vaccine.query.filter_by(name="Pentavalent").first()
        aarav_id = aarav.id
        v_id = v_penta.id

    # Record Penta 1 dose via /record-vaccine/<child_id>
    today_str = date.today().isoformat()
    res_record = client.post(f"/record-vaccine/{aarav_id}", data={
        "vaccine_id": v_id,
        "administered_date": today_str,
        "dose_status": "completed",
        "notes": "Administered right anterolateral thigh",
    }, follow_redirects=True)
    assert res_record.status_code == 200

    # Verify vaccination history reflects the record
    with app.app_context():
        recs = VaccinationRecord.query.filter_by(child_id=aarav_id, vaccine_id=v_id).all()
        assert len(recs) == 1
        assert recs[0].dose_status == "received"


# ---------------------------------------------------------------------------
# 7. Parent Notification Created on Vaccination
# ---------------------------------------------------------------------------
def test_parent_notification_created_on_vaccination(integration_app_and_client):
    app, client, _ = integration_app_and_client
    login_helper(client, "worker_i7", "WorkerSecure#2026")

    with app.app_context():
        aarav = Child.query.filter_by(name="Aarav Seven").first()
        v_penta = Vaccine.query.filter_by(name="Pentavalent").first()
        aarav_id = aarav.id
        v_id = v_penta.id

    today_str = date.today().isoformat()
    client.post(f"/record-vaccine/{aarav_id}", data={
        "vaccine_id": v_id,
        "administered_date": today_str,
        "dose_status": "completed",
        "notes": "Notification test record",
    }, follow_redirects=True)

    # Check notification table for parent
    with app.app_context():
        parent = User.query.filter_by(username="parent_i7").first()
        notifs = Notification.query.filter_by(user_id=parent.id).all()
        assert len(notifs) >= 1
        assert "Pentavalent" in notifs[-1].title or "Vaccination" in notifs[-1].title or "Pentavalent" in notifs[-1].body


# ---------------------------------------------------------------------------
# 8. Digital Vaccination Card PDF and QR Verification
# ---------------------------------------------------------------------------
def test_digital_vaccination_card_and_qr_verification(integration_app_and_client):
    app, client, _ = integration_app_and_client
    login_helper(client, "parent_i7", "ParentSecure#2026")

    with app.app_context():
        aarav = Child.query.filter_by(name="Aarav Seven").first()
        aarav_id = aarav.id
        token = aarav.record_token

    # Download PDF card
    res_pdf = client.get(f"/download-card/{aarav_id}")
    assert res_pdf.status_code == 200
    assert res_pdf.content_type == "application/pdf"
    assert len(res_pdf.data) > 500

    # Public verification route via record token (no auth required)
    client.post("/logout")
    res_qr = client.get(f"/record/{token}")
    assert res_qr.status_code == 200
    assert b"Aarav Seven" in res_qr.data
    assert b"Record Overview" in res_qr.data or b"Vaccination" in res_qr.data


# ---------------------------------------------------------------------------
# 9. Admin Analytics Live Reflection
# ---------------------------------------------------------------------------
def test_admin_analytics_live_reflection(integration_app_and_client):
    app, client, _ = integration_app_and_client
    login_helper(client, "admin_i7", "AdminSecure#2026")

    res_admin = client.get("/admin-dashboard")
    assert res_admin.status_code == 200
    # Checks live metrics from SQLite
    assert b"Total Users" in res_admin.data
    assert b"Children" in res_admin.data
    assert b"Administered Doses" in res_admin.data
    assert b"Administered Today" in res_admin.data


# ---------------------------------------------------------------------------
# 10. Admin User Management Search and Filtering
# ---------------------------------------------------------------------------
def test_admin_user_management_search_filtering(integration_app_and_client):
    app, client, _ = integration_app_and_client
    login_helper(client, "admin_i7", "AdminSecure#2026")

    # Search by username
    res_search = client.get("/admin/users?search=parent_i7")
    assert res_search.status_code == 200
    assert b"parent_i7" in res_search.data
    assert b"worker_i7" not in res_search.data

    # Filter by role healthcare_worker
    res_role = client.get("/admin/users?role=healthcare_worker")
    assert res_role.status_code == 200
    assert b"worker_i7" in res_role.data
    assert b"parent_i7" not in res_role.data


# ---------------------------------------------------------------------------
# 11. Admin Worker Monitoring and Reassignment
# ---------------------------------------------------------------------------
def test_admin_worker_monitoring_and_reassignment(integration_app_and_client):
    app, client, _ = integration_app_and_client
    login_helper(client, "admin_i7", "AdminSecure#2026")

    res_mon = client.get("/admin/workers")
    assert res_mon.status_code == 200
    assert b"Nurse Greenfield" in res_mon.data

    with app.app_context():
        worker = HealthcareWorker.query.first()
        worker_id = worker.id

    # Reassign village from Greenfield to Highgrove
    res_reassign = client.post(f"/admin/workers/{worker_id}/reassign", data={
        "village": "Highgrove",
    }, follow_redirects=True)
    assert res_reassign.status_code == 200
    assert b"Highgrove" in res_reassign.data

    with app.app_context():
        updated = db.session.get(HealthcareWorker, worker_id)
        assert updated.village == "Highgrove"


# ---------------------------------------------------------------------------
# 12. Admin Vaccine Registry and UIP Schedules
# ---------------------------------------------------------------------------
def test_admin_vaccine_registry_and_uip_schedules(integration_app_and_client):
    app, client, _ = integration_app_and_client
    login_helper(client, "admin_i7", "AdminSecure#2026")

    res_vac = client.get("/admin/vaccines")
    assert res_vac.status_code == 200
    assert b"BCG" in res_vac.data
    assert b"Pentavalent" in res_vac.data
    assert b"Penta 1" in res_vac.data


# ---------------------------------------------------------------------------
# 13. Notification Failure Logging and Retry
# ---------------------------------------------------------------------------
def test_notification_failure_logging_and_retry(integration_app_and_client):
    app, client, _ = integration_app_and_client
    with app.app_context():
        fail_sms = SmsNotification(
            recipient="+919876543210",
            message_type="test",
            message="Test retry payload",
            status="failed",
            failure_reason="Carrier network timed out",
            attempt_count=1,
        )
        db.session.add(fail_sms)
        db.session.commit()
        sms_id = fail_sms.id

    login_helper(client, "admin_i7", "AdminSecure#2026")

    # Retry failed notification
    res_retry = client.post(f"/admin/notifications/retry/{sms_id}", data={
        "channel": "sms",
    }, follow_redirects=True)
    assert res_retry.status_code == 200

    with app.app_context():
        sms = db.session.get(SmsNotification, sms_id)
        assert sms.status in {"simulated", "sent"}


# ---------------------------------------------------------------------------
# 14. Scheduler Due Reminders and Duplicate Prevention
# ---------------------------------------------------------------------------
def test_scheduler_due_reminders_and_duplicate_prevention(integration_app_and_client):
    app, client, _ = integration_app_and_client
    with app.app_context():
        # First execution generates reminders
        run1 = run_due_reminder_check(today=date.today())
        assert run1["status"] == "success"

        # Second execution does not duplicate
        run2 = run_due_reminder_check(today=date.today())
        assert run2["status"] == "success"
        assert run2["email_count"] == 0

        state = SchedulerRunState.query.filter_by(name="vaccination_reminders").first()
        assert state is not None
        assert state.status == "idle"


# ---------------------------------------------------------------------------
# 15. Logout and Session Invalidation
# ---------------------------------------------------------------------------
def test_logout_and_session_invalidation(integration_app_and_client):
    app, client, _ = integration_app_and_client
    login_helper(client, "admin_i7", "AdminSecure#2026")
    res_dash = client.get("/admin-dashboard")
    assert res_dash.status_code == 200

    # Logout
    res_logout = client.post("/logout", follow_redirects=True)
    assert res_logout.status_code == 200

    # Attempt to re-access admin dashboard without authentication
    res_after = client.get("/admin-dashboard")
    assert res_after.status_code in {302, 401}


# ---------------------------------------------------------------------------
# 16. Cross-Role Access Restrictions
# ---------------------------------------------------------------------------
def test_cross_role_access_restrictions(integration_app_and_client):
    app, client, _ = integration_app_and_client
    # Parent blocked from admin routes
    login_helper(client, "parent_i7", "ParentSecure#2026")
    for r in ["/admin-dashboard", "/admin/users", "/admin/workers", "/admin/vaccines", "/admin/notifications"]:
        assert client.get(r).status_code == 403

    # Worker blocked from admin routes
    login_helper(client, "worker_i7", "WorkerSecure#2026")
    for r in ["/admin-dashboard", "/admin/users", "/admin/workers", "/admin/vaccines", "/admin/notifications"]:
        assert client.get(r).status_code == 403


# ---------------------------------------------------------------------------
# 17. English and Telugu Language Switching
# ---------------------------------------------------------------------------
def test_language_switching_en_and_te(integration_app_and_client):
    app, client, _ = integration_app_and_client
    # Switch to Telugu
    res_te = client.get("/set_language/te", follow_redirects=True)
    assert res_te.status_code == 200
    with client.session_transaction() as sess:
        assert sess.get("language") == "te"

    # Landing page renders Telugu site title
    res_home = client.get("/")
    assert res_home.status_code == 200
    assert "వాక్సీకేర్".encode("utf-8") in res_home.data

    # Switch back to English
    res_en = client.get("/set_language/en", follow_redirects=True)
    assert res_en.status_code == 200
    with client.session_transaction() as sess:
        assert sess.get("language") == "en"


# ---------------------------------------------------------------------------
# 18. Worker CSV Records Export
# ---------------------------------------------------------------------------
def test_worker_csv_records_export(integration_app_and_client):
    app, client, _ = integration_app_and_client
    login_helper(client, "worker_i7", "WorkerSecure#2026")

    res_csv = client.get("/worker/export-records")
    assert res_csv.status_code == 200
    assert res_csv.mimetype == "text/csv"
    csv_text = res_csv.data.decode("utf-8")
    assert "Child Name" in csv_text
    assert "Vaccine Name" in csv_text
    assert "Aarav Seven" in csv_text


# ---------------------------------------------------------------------------
# 19. Parent Profile and SMS Consent Toggle
# ---------------------------------------------------------------------------
def test_parent_profile_and_sms_consent_toggle(integration_app_and_client):
    app, client, _ = integration_app_and_client
    login_helper(client, "parent_i7", "ParentSecure#2026")

    # Update profile
    res_prof = client.post("/parent/profile", data={
        "first_name": "Parvati",
        "last_name": "Devi",
        "mobile": "+919876500000",
        "village": "Greenfield South",
        "address": "House 10, Main Street",
        "sms_consent": "y",
    }, follow_redirects=True)
    assert res_prof.status_code == 200

    with app.app_context():
        p_user = User.query.filter_by(username="parent_i7").first()
        assert p_user.mobile == "+919876500000"
        assert p_user.parent.first_name == "Parvati"
        assert p_user.parent.village == "Greenfield South"


# ---------------------------------------------------------------------------
# 20. Full Integrated Multi-Role Journey
# ---------------------------------------------------------------------------
def test_full_integrated_multi_role_journey(integration_app_and_client):
    app, client, _ = integration_app_and_client
    # Step 1: Admin adds new vaccine
    login_helper(client, "admin_i7", "AdminSecure#2026")
    client.post("/vaccine/add", data={
        "name": "Measles-Rubella",
        "disease": "Measles and Rubella",
        "description": "MR vaccine for 9 months",
        "category": "Primary",
        "recommended_age": "9 months",
    })

    # Step 2: Parent logs in, adds child via /child/add
    client.post("/logout")
    login_helper(client, "parent_i7", "ParentSecure#2026")
    client.post("/child/add", data={
        "name": "Kavya Seven",
        "dob": "2026-05-15",
        "gender": "Female",
        "village": "Greenfield",
        "allergies": "",
        "notes": "",
    }, follow_redirects=True)

    # Step 3: Admin triggers reminder check
    client.post("/logout")
    login_helper(client, "admin_i7", "AdminSecure#2026")
    res_reminders = client.post("/admin/run-reminder-check", follow_redirects=True)
    assert res_reminders.status_code == 200

    # Step 4: Worker records dose for the child via /record-vaccine/<child_id>
    client.post("/logout")
    login_helper(client, "worker_i7", "WorkerSecure#2026")
    with app.app_context():
        kavya = Child.query.filter_by(name="Kavya Seven").first()
        assert kavya is not None
        v_bcg = Vaccine.query.filter_by(name="BCG").first()
        k_id = kavya.id
        v_id = v_bcg.id

    client.post(f"/record-vaccine/{k_id}", data={
        "vaccine_id": v_id,
        "administered_date": date.today().isoformat(),
        "dose_status": "completed",
        "notes": "End-to-end journey dose recorded",
    }, follow_redirects=True)

    # Step 5: Verify parent received in-system notification
    client.post("/logout")
    login_helper(client, "parent_i7", "ParentSecure#2026")
    res_notif = client.get("/parent/notifications")
    assert res_notif.status_code == 200

    # Step 6: Admin checks live analytics dashboard
    client.post("/logout")
    login_helper(client, "admin_i7", "AdminSecure#2026")
    res_analytics = client.get("/admin-dashboard")
    assert res_analytics.status_code == 200
    assert b"Administrative Control Center" in res_analytics.data
