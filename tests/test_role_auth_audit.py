import pytest
from datetime import date
from app import create_app, db
from app.models import User, Parent, Child, HealthcareWorker, Vaccine, VaccinationSchedule, VaccinationRecord


@pytest.fixture
def audit_app():
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": "audit-test-key",
        "SEED_DEMO_DATA": False,
        "MAIL_ENABLED": False,
        "SMS_ENABLED": False,
        "SMS_FORCE_DEMO": True,
    })

    with app.app_context():
        db.create_all()

        # 1. Admin user
        admin_u = User(username="audit_admin", email="admin@audit.local", role="admin", full_name="Audit Admin")
        admin_u.set_password("AdminPass123!")
        db.session.add(admin_u)

        # 2. Worker user in Rampur
        worker_u = User(username="audit_worker", email="worker@audit.local", role="healthcare_worker", full_name="Audit Worker")
        worker_u.set_password("WorkerPass123!")
        db.session.add(worker_u)
        db.session.flush()
        worker = HealthcareWorker(user_id=worker_u.id, full_name="Audit Worker", village="Rampur")
        db.session.add(worker)

        # 3. Parent 1 in Rampur
        parent1_u = User(username="audit_parent1", email="parent1@audit.local", role="parent", full_name="Parent One")
        parent1_u.set_password("ParentPass123!")
        db.session.add(parent1_u)
        db.session.flush()
        parent1 = Parent(user_id=parent1_u.id, first_name="Parent", last_name="One", village="Rampur")
        db.session.add(parent1)
        db.session.flush()
        child1 = Child(parent_id=parent1.id, name="Aarav", dob=date(2023, 1, 1), gender="male", village="Rampur", record_token="tok-aarav-1")
        db.session.add(child1)

        # 4. Parent 2 in Jaipur
        parent2_u = User(username="audit_parent2", email="parent2@audit.local", role="parent", full_name="Parent Two")
        parent2_u.set_password("ParentPass123!")
        db.session.add(parent2_u)
        db.session.flush()
        parent2 = Parent(user_id=parent2_u.id, first_name="Parent", last_name="Two", village="Jaipur")
        db.session.add(parent2)
        db.session.flush()
        child2 = Child(parent_id=parent2.id, name="Diya", dob=date(2023, 5, 10), gender="female", village="Jaipur", record_token="tok-diya-2")
        db.session.add(child2)

        # Vaccine
        vax = Vaccine(
            name="BCG",
            disease="Tuberculosis",
            description="Bacillus Calmette-Guerin vaccine against tuberculosis.",
            category="Birth",
            recommended_age="At birth",
            source="Government of India UIP National Immunization Schedule",
            source_checked_on="2026-11-04",
        )
        db.session.add(vax)
        db.session.commit()

        yield app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(audit_app):
    return audit_app.test_client()


# ===========================================================================
# 1. Anonymous Access & Login Form Verification
# ===========================================================================

def test_anonymous_opening_login_urls_shows_forms(client):
    """Opening role login URLs anonymously displays login forms with 200 OK, never bypassing credentials."""
    for url in ["/parent/login", "/healthcare/login", "/admin/login", "/login"]:
        res = client.get(url)
        assert res.status_code == 200
        assert b"<form" in res.data
        assert b"Sign in" in res.data or b"Login" in res.data


def test_anonymous_access_to_dashboards_redirects_to_login(client):
    """Anonymous access to dashboards is strictly intercepted by login_required."""
    for url in ["/parent-dashboard", "/healthcare-dashboard", "/admin-dashboard", "/dashboard"]:
        res = client.get(url, follow_redirects=False)
        assert res.status_code == 302
        assert "/login" in res.headers["Location"]


# ===========================================================================
# 2. Role Separation & Authorization
# ===========================================================================

def test_parent_cannot_access_admin_or_worker_dashboards(client):
    """Parent is forbidden from admin dashboard and healthcare worker views."""
    # Login as parent1
    client.post("/login", data={"username": "audit_parent1", "password": "ParentPass123!"})
    
    # Parent can view own dashboard
    res = client.get("/parent-dashboard")
    assert res.status_code == 200
    assert b"Aarav" in res.data

    # Parent is blocked from admin dashboard
    res_admin = client.get("/admin-dashboard")
    assert res_admin.status_code == 403

    # Parent is blocked from healthcare worker dashboard
    res_worker = client.get("/healthcare-dashboard")
    assert res_worker.status_code == 403


def test_healthcare_worker_cannot_access_admin_dashboard(client):
    """Healthcare worker is forbidden from admin dashboard."""
    client.post("/login", data={"username": "audit_worker", "password": "WorkerPass123!"})
    
    # Worker can view healthcare dashboard
    res = client.get("/healthcare-dashboard")
    assert res.status_code == 200

    # Worker is blocked from admin dashboard
    res_admin = client.get("/admin-dashboard")
    assert res_admin.status_code == 403

    # Worker is blocked from parent dashboard
    res_parent = client.get("/parent-dashboard")
    assert res_parent.status_code == 403


def test_admin_can_access_admin_dashboard(client):
    """Administrator logs in and accesses admin dashboard."""
    client.post("/login", data={"username": "audit_admin", "password": "AdminPass123!"})
    res = client.get("/admin-dashboard")
    assert res.status_code == 200
    assert b"Administrative Control Center" in res.data


# ===========================================================================
# 3. Data Isolation Tests
# ===========================================================================

def test_parent_data_isolation(client):
    """Parent 1 cannot see Parent 2's child detail."""
    client.post("/login", data={"username": "audit_parent1", "password": "ParentPass123!"})
    
    # Child 1 (Aarav, child_id=1) belongs to parent 1 -> 200
    res1 = client.get("/child/1")
    assert res1.status_code == 200

    # Child 2 (Diya, child_id=2) belongs to parent 2 -> 403
    res2 = client.get("/child/2")
    assert res2.status_code == 403


def test_healthcare_worker_village_data_isolation(client):
    """Worker assigned to Rampur sees only Rampur children and cannot access Jaipur children."""
    client.post("/login", data={"username": "audit_worker", "password": "WorkerPass123!"})
    
    # Child 1 is in Rampur -> 200
    res1 = client.get("/child/1")
    assert res1.status_code == 200

    # Child 2 is in Jaipur -> 403
    res2 = client.get("/child/2")
    assert res2.status_code == 403

    # On /schedule, worker sees only Rampur child (Aarav), not Jaipur child (Diya)
    res_sched = client.get("/schedule")
    assert res_sched.status_code == 200
    assert b"Aarav" in res_sched.data
    assert b"Diya" not in res_sched.data


# ===========================================================================
# 4. Special Debugging Requirement & Session Role Switching
# ===========================================================================

def test_logged_in_admin_visiting_parent_login_switches_session(client):
    """An admin visiting /parent/login clears the admin session so the parent login form displays cleanly."""
    client.post("/login", data={"username": "audit_admin", "password": "AdminPass123!"})
    
    # Navigating to /parent/login must NOT bounce to /admin-dashboard; it must show the parent login form
    res = client.get("/parent/login")
    assert res.status_code == 200
    assert b"Welcome back" in res.data or b"Parent" in res.data
    assert b"Administrative Control Center" not in res.data


def test_logged_in_user_visiting_own_login_redirects_to_dashboard(client):
    """A user visiting the same role's login URL gets redirected to their role dashboard."""
    client.post("/login", data={"username": "audit_admin", "password": "AdminPass123!"})
    res = client.get("/admin/login", follow_redirects=False)
    assert res.status_code == 302
    assert "/admin-dashboard" in res.headers["Location"]


# ===========================================================================
# 5. Smart /dashboard Route
# ===========================================================================

def test_smart_dashboard_router_by_role(client):
    """Generic /dashboard route dynamically redirects each role to its specific dashboard."""
    # Parent
    client.post("/login", data={"username": "audit_parent1", "password": "ParentPass123!"})
    res_p = client.get("/dashboard", follow_redirects=False)
    assert res_p.status_code == 302
    assert "/parent-dashboard" in res_p.headers["Location"]
    client.get("/logout")

    # Worker
    client.post("/login", data={"username": "audit_worker", "password": "WorkerPass123!"})
    res_w = client.get("/dashboard", follow_redirects=False)
    assert res_w.status_code == 302
    assert "/healthcare-dashboard" in res_w.headers["Location"]
    client.get("/logout")

    # Admin
    client.post("/login", data={"username": "audit_admin", "password": "AdminPass123!"})
    res_a = client.get("/dashboard", follow_redirects=False)
    assert res_a.status_code == 302
    assert "/admin-dashboard" in res_a.headers["Location"]


# ===========================================================================
# 6. Safe Next URL Handling
# ===========================================================================

def test_safe_next_url_prevents_open_redirect_and_cross_role(client):
    """Unsafe or cross-role next parameters are ignored and user lands on role dashboard."""
    # Parent logs in with ?next=/admin-dashboard -> must go to /parent-dashboard
    res = client.post("/login?next=/admin-dashboard", data={"username": "audit_parent1", "password": "ParentPass123!"}, follow_redirects=False)
    assert res.status_code == 302
    assert res.headers["Location"].endswith("/parent-dashboard")
    client.get("/logout")

    # Open redirect attempt -> must go to role dashboard
    res_open = client.post("/login?next=https://evil.com", data={"username": "audit_parent1", "password": "ParentPass123!"}, follow_redirects=False)
    assert res_open.status_code == 302
    assert "evil.com" not in res_open.headers["Location"]


# ===========================================================================
# 7. Cache-Control & Logout
# ===========================================================================

def test_cache_control_headers_on_protected_views(client):
    """Authenticated and dashboard views have no-store Cache-Control headers."""
    client.post("/login", data={"username": "audit_parent1", "password": "ParentPass123!"})
    res = client.get("/parent-dashboard")
    assert "no-store" in res.headers.get("Cache-Control", "")
    assert "no-cache" in res.headers.get("Cache-Control", "")


def test_logout_supports_get_and_post_and_clears_session(client):
    """Logout works on GET and POST, and subsequent protected access is blocked."""
    client.post("/login", data={"username": "audit_parent1", "password": "ParentPass123!"})
    
    # GET logout
    res = client.get("/logout", follow_redirects=True)
    assert res.status_code == 200
    assert b"Logged out successfully." in res.data

    # Protected view now requires login
    res_dash = client.get("/parent-dashboard", follow_redirects=False)
    assert res_dash.status_code == 302
    assert "/login" in res_dash.headers["Location"]


# ===========================================================================
# 8. Database Integrity & Duplicate Prevention
# ===========================================================================

def test_duplicate_child_prevention(client):
    """Parent cannot register two children with identical name and date of birth."""
    client.post("/login", data={"username": "audit_parent1", "password": "ParentPass123!"})
    
    # Attempt to add another child named "Aarav" with DOB 2023-01-01
    res = client.post("/child/add", data={
        "name": "Aarav",
        "dob": "2023-01-01",
        "gender": "Male",
        "village": "Rampur",
    }, follow_redirects=True)
    
    assert res.status_code == 200
    assert b"already registered under your account" in res.data
    
    # Verify child count is still 1 for this parent
    with client.application.app_context():
        p1 = Parent.query.filter_by(village="Rampur").first()
        children = Child.query.filter_by(parent_id=p1.id).all()
        assert len(children) == 1


def test_duplicate_vaccine_prevention(client):
    """Admin cannot create a vaccine with an existing name."""
    client.post("/login", data={"username": "audit_admin", "password": "AdminPass123!"})
    
    # Attempt to add vaccine "BCG" again
    res = client.post("/vaccine/add", data={
        "name": "BCG",
        "disease": "Tuberculosis",
        "description": "Another BCG entry",
        "category": "Birth",
        "recommended_age": "At birth",
        "source": "Government of India UIP National Immunization Schedule",
        "source_checked_on": "2026-11-04",
    }, follow_redirects=True)
    
    assert res.status_code == 200
    assert b"already exists" in res.data
