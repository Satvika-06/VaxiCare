"""
Phase 5: Healthcare Worker Dashboard & Clinical Records Tests
Covers worker dashboard, village assignment, child clinical records,
vaccination administration, duplicate prevention, parent notification,
clinical history, CSV export, profile management, and database persistence.
Uses isolated temp SQLite DBs — never touches instance/vaxicare.db.
"""
import os
import shutil
import tempfile
import uuid
import pytest
from datetime import date, timedelta

from app import create_app, db
from app.models import User, Parent, Child, HealthcareWorker, Vaccine, VaccinationSchedule, VaccinationRecord, Notification


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db_uri(temp_dir, name="test_phase5.db"):
    path = os.path.join(temp_dir, name)
    return "sqlite:///" + path.replace("\\", "/"), path


def _base_config(db_uri):
    return {
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": db_uri,
        "SEED_DEMO_DATA": False,
        "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": "test-secret-phase5",
        "REMINDER_TIMEZONE": "UTC",
    }


def login(client, username, password):
    return client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=True,
    )


def logout(client):
    return client.post("/logout", follow_redirects=True)


# ---------------------------------------------------------------------------
# Main fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def ctx():
    temp_dir = tempfile.mkdtemp()
    db_uri, db_path = _make_db_uri(temp_dir)

    app = create_app(test_config=_base_config(db_uri))

    with app.app_context():
        db.create_all()

        # 1. Healthcare Worker 1 (Assigned to Village Alpha)
        w1_user = User(
            username="p5worker1",
            email="p5worker1@test.vc",
            role="healthcare_worker",
            full_name="Dr. Alpha Worker",
            mobile="+919800000001",
        )
        w1_user.set_password("Worker1Secure#5")
        db.session.add(w1_user)
        db.session.flush()

        w1_profile = HealthcareWorker(
            user_id=w1_user.id,
            full_name="Dr. Alpha Worker",
            qualification="MBBS, DCH",
            department="Pediatrics",
            village="Village Alpha",
        )
        db.session.add(w1_profile)

        # 2. Healthcare Worker 2 (Assigned to Village Beta)
        w2_user = User(
            username="p5worker2",
            email="p5worker2@test.vc",
            role="healthcare_worker",
            full_name="Nurse Beta Worker",
            mobile="+919800000002",
        )
        w2_user.set_password("Worker2Secure#5")
        db.session.add(w2_user)
        db.session.flush()

        w2_profile = HealthcareWorker(
            user_id=w2_user.id,
            full_name="Nurse Beta Worker",
            qualification="GNM",
            department="Immunization",
            village="Village Beta",
        )
        db.session.add(w2_profile)

        # 3. Parent with child in Village Alpha
        p1_user = User(
            username="p5parent1",
            email="p5parent1@test.vc",
            role="parent",
            full_name="Alpha Parent",
            mobile="+919900000001",
        )
        p1_user.set_password("Parent1Secure#5")
        db.session.add(p1_user)
        db.session.flush()

        p1_profile = Parent(
            user_id=p1_user.id,
            first_name="Alpha",
            last_name="Parent",
            village="Village Alpha",
        )
        db.session.add(p1_profile)
        db.session.flush()

        child_alpha = Child(
            parent_id=p1_profile.id,
            name="Alpha Child",
            dob=date(2025, 1, 1),
            gender="Male",
            village="Village Alpha",
            allergies="None",
            notes="Healthy infant",
            record_token=str(uuid.uuid4()),
        )
        db.session.add(child_alpha)

        # 4. Parent with child in Village Beta
        p2_user = User(
            username="p5parent2",
            email="p5parent2@test.vc",
            role="parent",
            full_name="Beta Parent",
            mobile="+919900000002",
        )
        p2_user.set_password("Parent2Secure#5")
        db.session.add(p2_user)
        db.session.flush()

        p2_profile = Parent(
            user_id=p2_user.id,
            first_name="Beta",
            last_name="Parent",
            village="Village Beta",
        )
        db.session.add(p2_profile)
        db.session.flush()

        child_beta = Child(
            parent_id=p2_profile.id,
            name="Beta Child",
            dob=date(2025, 3, 1),
            gender="Female",
            village="Village Beta",
            allergies="Egg allergy",
            notes="Preterm baby",
            record_token=str(uuid.uuid4()),
        )
        db.session.add(child_beta)

        # 5. Admin
        adm_user = User(
            username="p5admin",
            email="p5admin@test.vc",
            role="admin",
            full_name="Phase Five Admin",
        )
        adm_user.set_password("AdminSecure#5")
        db.session.add(adm_user)

        # 6. Vaccine & Schedule
        vax = Vaccine(
            name="BCG-Test",
            disease="Tuberculosis",
            description="Bacillus Calmette-Guerin vaccine",
            category="Birth",
            recommended_age="At birth",
            source="WHO UIP Guidelines",
            source_checked_on="2026-11-01",
        )
        db.session.add(vax)
        db.session.flush()

        vax_schedule = VaccinationSchedule(
            vaccine_id=vax.id,
            dose_label="BCG Birth Dose",
            age_days=0,
            description="Give at birth or as early as possible",
            source_note="UIP standard",
            source_checked_on="2026-11-01",
        )
        db.session.add(vax_schedule)

        vax2 = Vaccine(
            name="HepB-Test",
            disease="Hepatitis B",
            description="Hepatitis B vaccine",
            category="Birth",
            recommended_age="At birth",
            source="WHO UIP Guidelines",
            source_checked_on="2026-11-01",
        )
        db.session.add(vax2)
        db.session.flush()

        vax2_schedule = VaccinationSchedule(
            vaccine_id=vax2.id,
            dose_label="HepB Birth Dose",
            age_days=0,
            description="Give within 24 hours of birth",
            source_note="UIP standard",
            source_checked_on="2026-11-01",
        )
        db.session.add(vax2_schedule)

        db.session.commit()

        data = {
            "worker1_id": w1_user.id,
            "worker1_profile_id": w1_profile.id,
            "worker2_id": w2_user.id,
            "child_alpha_id": child_alpha.id,
            "child_beta_id": child_beta.id,
            "parent1_user_id": p1_user.id,
            "vaccine_id": vax.id,
            "vaccine2_id": vax2.id,
            "schedule_id": vax_schedule.id,
            "db_path": db_path,
            "temp_dir": temp_dir,
        }

    client = app.test_client()
    yield app, client, data

    shutil.rmtree(temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------

def test_worker_login_and_dashboard_access(ctx):
    app, client, data = ctx
    resp = login(client, "p5worker1", "Worker1Secure#5")
    assert resp.status_code == 200

    dash = client.get("/healthcare-dashboard")
    assert dash.status_code == 200
    assert b"Dr. Alpha Worker" in dash.data
    assert b"Assigned Sector" in dash.data or b"Village Alpha" in dash.data
    logout(client)


def test_worker_dashboard_alias(ctx):
    app, client, data = ctx
    login(client, "p5worker1", "Worker1Secure#5")
    resp = client.get("/worker-dashboard")
    assert resp.status_code == 200
    assert b"Assigned Sector" in resp.data or b"Village Alpha" in resp.data
    logout(client)


def test_parent_cannot_access_worker_dashboard(ctx):
    app, client, data = ctx
    login(client, "p5parent1", "Parent1Secure#5")
    resp = client.get("/healthcare-dashboard")
    assert resp.status_code == 403
    alias_resp = client.get("/worker-dashboard")
    assert alias_resp.status_code == 403
    logout(client)


def test_anonymous_redirected_from_worker_dashboard(ctx):
    app, client, data = ctx
    resp = client.get("/healthcare-dashboard", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_assigned_village_filtering(ctx):
    app, client, data = ctx
    login(client, "p5worker1", "Worker1Secure#5")
    resp = client.get("/healthcare-dashboard")
    assert resp.status_code == 200
    # Worker 1 is in Village Alpha -> Sees Alpha Child, does NOT see Beta Child
    assert b"Alpha Child" in resp.data
    assert b"Beta Child" not in resp.data
    logout(client)


def test_worker_2_sees_village_beta_only(ctx):
    app, client, data = ctx
    login(client, "p5worker2", "Worker2Secure#5")
    resp = client.get("/healthcare-dashboard")
    assert resp.status_code == 200
    assert b"Beta Child" in resp.data
    assert b"Alpha Child" not in resp.data
    logout(client)


def test_admin_sees_all_villages(ctx):
    app, client, data = ctx
    login(client, "p5admin", "AdminSecure#5")
    resp = client.get("/healthcare-dashboard")
    assert resp.status_code == 200
    assert b"Alpha Child" in resp.data
    assert b"Beta Child" in resp.data
    logout(client)


def test_child_search_by_name(ctx):
    app, client, data = ctx
    login(client, "p5admin", "AdminSecure#5")
    resp = client.get("/healthcare-dashboard?search=Alpha")
    assert resp.status_code == 200
    assert b"Alpha Child" in resp.data
    assert b"Beta Child" not in resp.data
    logout(client)


def test_worker_can_access_own_village_child_record(ctx):
    app, client, data = ctx
    login(client, "p5worker1", "Worker1Secure#5")
    resp = client.get(f"/health-record/{data['child_alpha_id']}")
    assert resp.status_code == 200
    assert b"Alpha Child" in resp.data
    assert b"BCG-Test" in resp.data
    logout(client)


def test_worker_blocked_from_other_village_child_record(ctx):
    app, client, data = ctx
    login(client, "p5worker1", "Worker1Secure#5")
    # Worker 1 tries to access child in Village Beta -> Expect 403
    resp = client.get(f"/health-record/{data['child_beta_id']}")
    assert resp.status_code == 403
    logout(client)


def test_record_vaccine_success(ctx):
    app, client, data = ctx
    login(client, "p5worker1", "Worker1Secure#5")
    today_str = date.today().isoformat()
    resp = client.post(
        f"/record-vaccine/{data['child_alpha_id']}",
        data={
            "vaccine_id": data["vaccine_id"],
            "administered_date": today_str,
            "notes": "Administered intramuscularly, Lot #BCG-2026-X",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Vaccination record saved" in resp.data

    with app.app_context():
        rec = VaccinationRecord.query.filter_by(
            child_id=data["child_alpha_id"],
            vaccine_id=data["vaccine_id"],
        ).first()
        assert rec is not None
        assert rec.dose_status == "received"
        assert rec.recorded_by_id == data["worker1_profile_id"]
        assert "BCG-2026-X" in rec.notes

    logout(client)


def test_record_vaccine_rejects_date_before_dob(ctx):
    app, client, data = ctx
    login(client, "p5worker1", "Worker1Secure#5")
    # Alpha child DOB is 2025-01-01; try 2024-12-31
    bad_date = "2024-12-31"
    resp = client.post(
        f"/record-vaccine/{data['child_alpha_id']}",
        data={
            "vaccine_id": data["vaccine2_id"],
            "administered_date": bad_date,
            "notes": "Invalid date test",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"cannot be before the child" in resp.data

    with app.app_context():
        rec = VaccinationRecord.query.filter_by(
            child_id=data["child_alpha_id"],
            vaccine_id=data["vaccine2_id"],
        ).first()
        assert rec is None  # Must NOT be recorded

    logout(client)


def test_record_vaccine_rejects_future_date(ctx):
    app, client, data = ctx
    login(client, "p5worker1", "Worker1Secure#5")
    future_date = (date.today() + timedelta(days=10)).isoformat()
    resp = client.post(
        f"/record-vaccine/{data['child_alpha_id']}",
        data={
            "vaccine_id": data["vaccine2_id"],
            "administered_date": future_date,
            "notes": "Future date test",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"cannot be in the future" in resp.data

    with app.app_context():
        rec = VaccinationRecord.query.filter_by(
            child_id=data["child_alpha_id"],
            vaccine_id=data["vaccine2_id"],
        ).first()
        assert rec is None

    logout(client)


def test_record_vaccine_duplicate_prevention(ctx):
    app, client, data = ctx
    login(client, "p5worker1", "Worker1Secure#5")
    today_str = date.today().isoformat()

    # First administration: success (if not already recorded)
    client.post(
        f"/record-vaccine/{data['child_alpha_id']}",
        data={
            "vaccine_id": data["vaccine_id"],
            "administered_date": today_str,
            "notes": "First attempt",
        },
        follow_redirects=True,
    )

    # Second administration of same dose: must be rejected
    dup_resp = client.post(
        f"/record-vaccine/{data['child_alpha_id']}",
        data={
            "vaccine_id": data["vaccine_id"],
            "administered_date": today_str,
            "notes": "Duplicate attempt",
        },
        follow_redirects=True,
    )
    assert dup_resp.status_code == 200
    assert (b"already been recorded" in dup_resp.data) or (b"already exists" in dup_resp.data)

    logout(client)


def test_record_vaccine_creates_parent_notification(ctx):
    app, client, data = ctx
    login(client, "p5worker1", "Worker1Secure#5")
    today_str = date.today().isoformat()

    client.post(
        f"/record-vaccine/{data['child_alpha_id']}",
        data={
            "vaccine_id": data["vaccine2_id"],
            "administered_date": today_str,
            "notes": "HepB given",
        },
        follow_redirects=True,
    )

    with app.app_context():
        notif = Notification.query.filter_by(
            user_id=data["parent1_user_id"],
            child_id=data["child_alpha_id"],
            reminder_type="completed",
        ).first()
        assert notif is not None
        assert "HepB-Test" in notif.title
        assert notif.status == "sent"

    logout(client)


def test_worker_records_history_access(ctx):
    app, client, data = ctx
    login(client, "p5worker1", "Worker1Secure#5")
    resp = client.get("/worker/records")
    assert resp.status_code == 200
    assert b"Vaccination Records Registry" in resp.data

    alias_resp = client.get("/healthcare/records")
    assert alias_resp.status_code == 200
    logout(client)


def test_worker_records_csv_export(ctx):
    app, client, data = ctx
    login(client, "p5worker1", "Worker1Secure#5")
    resp = client.get("/worker/export-records")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers.get("Content-Type", "")
    assert b"Child Name" in resp.data
    assert b"Vaccine Name" in resp.data
    logout(client)


def test_worker_profile_access_and_update(ctx):
    app, client, data = ctx
    login(client, "p5worker1", "Worker1Secure#5")

    # 1. GET profile
    get_res = client.get("/worker/profile")
    assert get_res.status_code == 200
    assert b"Healthcare Worker Profile" in get_res.data
    assert b"Dr. Alpha Worker" in get_res.data

    # 2. POST update
    post_res = client.post(
        "/worker/profile",
        data={
            "full_name": "Dr. Alpha Updated",
            "qualification": "MBBS, MD Pediatrics",
            "department": "Neonatal Care",
            "village": "Village Alpha",
            "mobile": "+919876543210",
        },
        follow_redirects=True,
    )
    assert post_res.status_code == 200
    assert b"Profile updated successfully" in post_res.data

    with app.app_context():
        worker = db.session.get(HealthcareWorker, data["worker1_profile_id"])
        assert worker.full_name == "Dr. Alpha Updated"
        assert worker.qualification == "MBBS, MD Pediatrics"
        assert worker.department == "Neonatal Care"

    logout(client)


def test_worker_cannot_modify_role(ctx):
    app, client, data = ctx
    login(client, "p5worker1", "Worker1Secure#5")

    # Attempt to inject role="admin" via profile update
    client.post(
        "/worker/profile",
        data={
            "full_name": "Dr. Alpha Worker",
            "qualification": "MBBS",
            "department": "Pediatrics",
            "village": "Village Alpha",
            "mobile": "+919800000001",
            "role": "admin",
        },
        follow_redirects=True,
    )

    with app.app_context():
        user = db.session.get(User, data["worker1_id"])
        assert user.role == "healthcare_worker"  # Role MUST remain healthcare_worker

    logout(client)


def test_worker_records_persist_after_app_restart(ctx):
    app, client, data = ctx
    login(client, "p5worker1", "Worker1Secure#5")

    # Record a vaccination
    client.post(
        f"/record-vaccine/{data['child_alpha_id']}",
        data={
            "vaccine_id": data["vaccine_id"],
            "administered_date": date.today().isoformat(),
            "notes": "Persistence verification test",
        },
        follow_redirects=True,
    )
    logout(client)

    # Recreate app instance pointing to the EXACT same database file
    db_uri = "sqlite:///" + data["db_path"].replace("\\", "/")
    new_app = create_app(test_config=_base_config(db_uri))
    new_client = new_app.test_client()

    login(new_client, "p5worker1", "Worker1Secure#5")
    hist_resp = new_client.get("/worker/records")
    assert hist_resp.status_code == 200
    assert b"Alpha Child" in hist_resp.data
    logout(new_client)
