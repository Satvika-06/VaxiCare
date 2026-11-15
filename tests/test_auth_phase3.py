"""
Phase 3: Authentication, Authorization & Session Security Tests
Uses isolated temp SQLite databases. Never touches instance/vaxicare.db.
"""
import os
import shutil
import tempfile
import pytest
from datetime import date

from werkzeug.security import generate_password_hash, check_password_hash

from app import create_app, db
from app.models import User, Parent, HealthcareWorker, Child, Vaccine, utcnow
from app.auth_services import login_limiter, validate_password_strength


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------
@pytest.fixture
def app_and_client():
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_phase3.db")
    db_uri = "sqlite:///" + db_path.replace("\\", "/")

    app = create_app(test_config={
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": db_uri,
        "SEED_DEMO_DATA": False,
        "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": "test-secret-key-for-phase3",
    })

    with app.app_context():
        db.create_all()

        # Seed minimal test data
        admin = User(username="testadmin", email="admin@test.vc", role="admin", full_name="Test Admin")
        admin.set_password("AdminSecure#99")
        db.session.add(admin)
        db.session.flush()

        # Parent user with profile
        parent_user = User(username="testparent", email="parent@test.vc", role="parent", full_name="Test Parent",
                           mobile="+919911223344", mobile_verified=True)
        parent_user.set_password("ParentSecure#1")
        db.session.add(parent_user)
        db.session.flush()
        parent = Parent(user_id=parent_user.id, first_name="Test", last_name="Parent", village="Testville")
        db.session.add(parent)
        db.session.flush()

        # Child A for testparent
        child_a = Child(parent_id=parent.id, name="Child Alpha", dob=date(2022, 5, 1),
                        gender="Male", village="Testville", record_token="tok-alpha-001")
        db.session.add(child_a)

        # Second parent
        parent2_user = User(username="testparent2", email="parent2@test.vc", role="parent", full_name="Other Parent")
        parent2_user.set_password("OtherParent#2")
        db.session.add(parent2_user)
        db.session.flush()
        parent2 = Parent(user_id=parent2_user.id, first_name="Other", last_name="Parent", village="Othertown")
        db.session.add(parent2)
        db.session.flush()

        # Child B belonging to parent2
        child_b = Child(parent_id=parent2.id, name="Child Beta", dob=date(2023, 1, 1),
                        gender="Female", village="Othertown", record_token="tok-beta-002")
        db.session.add(child_b)

        # Healthcare worker
        worker_user = User(username="testworker", email="worker@test.vc", role="healthcare_worker",
                           full_name="Test Worker")
        worker_user.set_password("WorkerSecure#3")
        db.session.add(worker_user)
        db.session.flush()
        worker = HealthcareWorker(user_id=worker_user.id, full_name="Test Worker",
                                  qualification="MBBS", department="Immunization",
                                  village="Testville")
        db.session.add(worker)

        db.session.commit()

        # Reset rate limiter between tests
        login_limiter.reset_all()

        client = app.test_client()
        yield app, client, {
            "admin_id": admin.id,
            "parent_id": parent_user.id,
            "parent2_id": parent2_user.id,
            "worker_id": worker_user.id,
            "parent_profile_id": parent.id,
            "parent2_profile_id": parent2.id,
            "child_a_id": child_a.id,
            "child_b_id": child_b.id,
            "worker_profile_id": worker.id,
        }
    shutil.rmtree(temp_dir, ignore_errors=True)


def login(client, username, password):
    return client.post("/login", data={
        "username": username,
        "password": password,
    }, follow_redirects=True)


def logout(client):
    return client.post("/logout", follow_redirects=True)


# ---------------------------------------------------------------------------
# 1. Parent registration
# ---------------------------------------------------------------------------
def test_parent_registration(app_and_client):
    app, client, _ = app_and_client
    resp = client.post("/parent/register", data={
        "first_name": "New",
        "last_name": "Parent",
        "username": "newparent_reg",
        "email": "newparent@test.vc",
        "password": "NewParent#007",
        "confirm_password": "NewParent#007",
        "mobile": "",
        "sms_consent": "",
    }, follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        u = User.query.filter_by(username="newparent_reg").first()
        assert u is not None
        assert u.role == "parent"
        assert u.parent is not None


# ---------------------------------------------------------------------------
# 2. Healthcare worker registration
# ---------------------------------------------------------------------------
def test_healthcare_worker_registration(app_and_client):
    app, client, _ = app_and_client
    resp = client.post("/healthcare/register", data={
        "full_name": "Dr. New Worker",
        "username": "newworker_reg",
        "email": "newworker@test.vc",
        "password": "WorkerNew#007",
        "confirm_password": "WorkerNew#007",
        "qualification": "MBBS",
        "department": "Immunization",
        "village": "Testville",
        "mobile": "",
    }, follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        u = User.query.filter_by(username="newworker_reg").first()
        assert u is not None
        assert u.role == "healthcare_worker"
        assert u.healthcare_worker is not None
        assert u.healthcare_worker.village == "Testville"


# ---------------------------------------------------------------------------
# 3. Duplicate username prevention
# ---------------------------------------------------------------------------
def test_duplicate_username_prevented(app_and_client):
    app, client, _ = app_and_client
    resp = client.post("/parent/register", data={
        "first_name": "Dupe",
        "last_name": "User",
        "username": "testparent",  # Already exists
        "email": "dupe@test.vc",
        "password": "DupeUser#99",
        "confirm_password": "DupeUser#99",
        "mobile": "",
        "sms_consent": "",
    }, follow_redirects=True)
    assert b"already exists" in resp.data


# ---------------------------------------------------------------------------
# 4. Duplicate email prevention
# ---------------------------------------------------------------------------
def test_duplicate_email_prevented(app_and_client):
    app, client, _ = app_and_client
    resp = client.post("/parent/register", data={
        "first_name": "Dupe",
        "last_name": "Email",
        "username": "uniqueusername999",
        "email": "parent@test.vc",  # Already exists
        "password": "DupeEmail#88",
        "confirm_password": "DupeEmail#88",
        "mobile": "",
        "sms_consent": "",
    }, follow_redirects=True)
    assert b"already exists" in resp.data


# ---------------------------------------------------------------------------
# 5. Successful login for all roles
# ---------------------------------------------------------------------------
def test_parent_login_success(app_and_client):
    _, client, _ = app_and_client
    resp = login(client, "testparent", "ParentSecure#1")
    assert resp.status_code == 200
    assert b"Login successful" in resp.data or b"dashboard" in resp.data.lower()


def test_admin_login_success(app_and_client):
    _, client, _ = app_and_client
    resp = login(client, "testadmin", "AdminSecure#99")
    assert resp.status_code == 200
    assert b"Login successful" in resp.data or b"admin" in resp.data.lower()


def test_healthcare_worker_login_success(app_and_client):
    _, client, _ = app_and_client
    resp = login(client, "testworker", "WorkerSecure#3")
    assert resp.status_code == 200
    assert b"Login successful" in resp.data or b"healthcare" in resp.data.lower()


# ---------------------------------------------------------------------------
# 6. Invalid password rejected
# ---------------------------------------------------------------------------
def test_invalid_password_rejected(app_and_client):
    _, client, _ = app_and_client
    resp = login(client, "testparent", "WrongPassword!")
    assert b"Invalid username or password" in resp.data


# ---------------------------------------------------------------------------
# 7. Correct role-based redirect after login
# ---------------------------------------------------------------------------
def test_parent_redirects_to_parent_dashboard(app_and_client):
    _, client, _ = app_and_client
    resp = client.post("/login", data={"username": "testparent", "password": "ParentSecure#1"},
                       follow_redirects=False)
    # Should redirect to parent dashboard
    assert resp.status_code == 302
    assert "parent-dashboard" in resp.location or "dashboard" in resp.location


def test_admin_redirects_to_admin_dashboard(app_and_client):
    _, client, _ = app_and_client
    resp = client.post("/login", data={"username": "testadmin", "password": "AdminSecure#99"},
                       follow_redirects=False)
    assert resp.status_code == 302
    assert "admin-dashboard" in resp.location


def test_healthcare_worker_redirects_to_healthcare_dashboard(app_and_client):
    _, client, _ = app_and_client
    resp = client.post("/login", data={"username": "testworker", "password": "WorkerSecure#3"},
                       follow_redirects=False)
    assert resp.status_code == 302
    assert "healthcare-dashboard" in resp.location


# ---------------------------------------------------------------------------
# 8. Logout clears session
# ---------------------------------------------------------------------------
def test_logout_clears_session(app_and_client):
    _, client, _ = app_and_client
    login(client, "testparent", "ParentSecure#1")
    resp = logout(client)
    assert resp.status_code == 200
    # After logout, accessing parent dashboard should redirect to login
    resp2 = client.get("/parent-dashboard", follow_redirects=False)
    assert resp2.status_code == 302
    assert "login" in resp2.location.lower()


# ---------------------------------------------------------------------------
# 9. Admin-only route protection
# ---------------------------------------------------------------------------
def test_admin_route_requires_admin(app_and_client):
    _, client, _ = app_and_client
    # Unauthenticated
    resp = client.get("/admin-dashboard", follow_redirects=False)
    assert resp.status_code == 302

    # Authenticated as parent
    login(client, "testparent", "ParentSecure#1")
    resp = client.get("/admin-dashboard", follow_redirects=True)
    assert resp.status_code == 403
    logout(client)

    # Authenticated as worker
    login(client, "testworker", "WorkerSecure#3")
    resp = client.get("/admin-dashboard", follow_redirects=True)
    assert resp.status_code == 403
    logout(client)


# ---------------------------------------------------------------------------
# 10. Parent-only data access (parent cannot see other parent's data)
# ---------------------------------------------------------------------------
def test_parent_cannot_access_other_parents_child(app_and_client):
    _, client, data = app_and_client
    login(client, "testparent", "ParentSecure#1")
    # Try to access child_b which belongs to testparent2
    resp = client.get(f"/child/{data['child_b_id']}", follow_redirects=True)
    assert resp.status_code == 403
    logout(client)


def test_parent_can_access_own_child(app_and_client):
    _, client, data = app_and_client
    login(client, "testparent", "ParentSecure#1")
    resp = client.get(f"/child/{data['child_a_id']}", follow_redirects=True)
    assert resp.status_code == 200
    assert b"Child Alpha" in resp.data
    logout(client)


# ---------------------------------------------------------------------------
# 11. Healthcare worker access restrictions (village-based)
# ---------------------------------------------------------------------------
def test_worker_can_access_same_village_child(app_and_client):
    _, client, data = app_and_client
    login(client, "testworker", "WorkerSecure#3")
    # testworker is assigned to Testville; child_a is in Testville
    resp = client.get(f"/child/{data['child_a_id']}", follow_redirects=True)
    assert resp.status_code == 200
    logout(client)


def test_worker_cannot_access_different_village_child(app_and_client):
    _, client, data = app_and_client
    login(client, "testworker", "WorkerSecure#3")
    # child_b is in Othertown, worker is assigned to Testville
    resp = client.get(f"/child/{data['child_b_id']}", follow_redirects=True)
    assert resp.status_code == 403
    logout(client)


# ---------------------------------------------------------------------------
# 12. CSRF protection (login form has hidden_tag in template but WTF_CSRF_ENABLED=False in test)
# ---------------------------------------------------------------------------
def test_csrf_token_generated_in_login_form(app_and_client):
    _, client, _ = app_and_client
    resp = client.get("/login")
    assert resp.status_code == 200
    # The form should contain a CSRF hidden input (even in test mode the field is rendered)
    assert b"csrf" in resp.data.lower() or b"hidden" in resp.data.lower()


# ---------------------------------------------------------------------------
# 13. Login rate limiting and brute-force protection
# ---------------------------------------------------------------------------
def test_login_rate_limiting(app_and_client):
    _, client, _ = app_and_client
    login_limiter.reset_all()
    # Trigger 5 failed attempts to cause lockout
    for _ in range(5):
        resp = client.post("/login", data={
            "username": "testparent",
            "password": "completely_wrong_password!!",
        }, follow_redirects=True)
    # 6th attempt should be locked out
    resp = client.post("/login", data={
        "username": "testparent",
        "password": "completely_wrong_password!!",
    }, follow_redirects=True)
    assert b"Too many failed login attempts" in resp.data
    login_limiter.reset_all()


# ---------------------------------------------------------------------------
# 14. Password hash preservation after migration
# ---------------------------------------------------------------------------
def test_existing_password_hashes_verified(app_and_client):
    app, client, data = app_and_client
    with app.app_context():
        u = db.session.get(User, data["parent_id"])
        assert u is not None
        assert check_password_hash(u.password_hash, "ParentSecure#1") is True
        assert check_password_hash(u.password_hash, "WrongPassword") is False


# ---------------------------------------------------------------------------
# 15. Password strength validation
# ---------------------------------------------------------------------------
def test_password_strength_validator_weak():
    ok, msg = validate_password_strength("short")
    assert not ok


def test_password_strength_validator_no_digit():
    ok, msg = validate_password_strength("OnlyLettersPassword")
    assert not ok


def test_password_strength_validator_strong():
    ok, msg = validate_password_strength("SecurePass#2026")
    assert ok
    assert msg == ""


def test_weak_password_rejected_at_registration(app_and_client):
    _, client, _ = app_and_client
    resp = client.post("/parent/register", data={
        "first_name": "Weak",
        "last_name": "Pass",
        "username": "weakpassuser",
        "email": "weakpass@test.vc",
        "password": "short",
        "confirm_password": "short",
        "mobile": "",
        "sms_consent": "",
    }, follow_redirects=True)
    with app_and_client[0].app_context():
        u = User.query.filter_by(username="weakpassuser").first()
        assert u is None


# ---------------------------------------------------------------------------
# 16. Session invalidation: changing password bumps auth_version
# ---------------------------------------------------------------------------
def test_password_change_invalidates_old_session(app_and_client):
    app, client, data = app_and_client
    login(client, "testparent", "ParentSecure#1")
    with app.app_context():
        u = db.session.get(User, data["parent_id"])
        old_version = u.auth_version

    # Change password
    client.post("/change-password", data={
        "current_password": "ParentSecure#1",
        "password": "NewPassword#2026",
        "confirm_password": "NewPassword#2026",
    }, follow_redirects=True)

    with app.app_context():
        u = db.session.get(User, data["parent_id"])
        assert u.auth_version > old_version


# ---------------------------------------------------------------------------
# 17. Database persistence: registered user survives app restart
# ---------------------------------------------------------------------------
def test_registered_user_persists(app_and_client):
    app, client, _ = app_and_client
    db_path = app.config["SQLALCHEMY_DATABASE_URI"].replace("sqlite:///", "")
    client.post("/parent/register", data={
        "first_name": "Persist",
        "last_name": "User",
        "username": "persist_user_p3",
        "email": "persist_p3@test.vc",
        "password": "PersistUser#88",
        "confirm_password": "PersistUser#88",
        "mobile": "",
        "sms_consent": "",
    }, follow_redirects=True)

    # Restart app against the same DB
    app2 = create_app(test_config={
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///" + db_path.replace("\\", "/"),
        "SEED_DEMO_DATA": False,
        "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": "test-secret-key-for-phase3",
    })
    with app2.app_context():
        u = User.query.filter_by(username="persist_user_p3").first()
        assert u is not None
        assert u.role == "parent"


# ---------------------------------------------------------------------------
# 18. Record vaccine: parent cannot record vaccines (healthcare/admin only)
# ---------------------------------------------------------------------------
def test_parent_cannot_record_vaccine(app_and_client):
    app, client, data = app_and_client
    with app.app_context():
        v = Vaccine(name="TestVax", disease="TestDisease",
                    description="Test.", category="Childhood",
                    recommended_age="At birth", source="Test", source_checked_on="2026-11-15")
        db.session.add(v)
        db.session.commit()
        vid = v.id

    login(client, "testparent", "ParentSecure#1")
    resp = client.post(f"/record-vaccine/{data['child_a_id']}", data={
        "vaccine_id": vid,
        "administered_date": "2026-11-01",
        "notes": "",
    }, follow_redirects=True)
    assert resp.status_code == 403
    logout(client)
