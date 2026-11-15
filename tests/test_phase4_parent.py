"""
Phase 4: Parent Portal Tests
Covers child CRUD, cross-parent access control, profile, notifications, and schedule.
Uses isolated temp SQLite DBs — never touches instance/vaxicare.db.
"""
import os
import shutil
import tempfile
import uuid
import pytest
from datetime import date

from app import create_app, db
from app.models import User, Parent, Child, Vaccine, VaccinationSchedule, Notification


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db_uri(temp_dir, name="test_phase4.db"):
    path = os.path.join(temp_dir, name)
    return "sqlite:///" + path.replace("\\", "/"), path


def _base_config(db_uri):
    return {
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": db_uri,
        "SEED_DEMO_DATA": False,
        "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": "test-secret-phase4",
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
# Main fixture — one fresh DB per test session
# ---------------------------------------------------------------------------

@pytest.fixture
def ctx():
    """
    Yields (app, client, data_dict).
    data_dict contains IDs for all seeded objects so tests never hard-code them.
    """
    temp_dir = tempfile.mkdtemp()
    db_uri, db_path = _make_db_uri(temp_dir)

    app = create_app(test_config=_base_config(db_uri))

    with app.app_context():
        db.create_all()

        # --- Parent 1 ---
        p1_user = User(
            username="p4parent1",
            email="p4parent1@test.vc",
            role="parent",
            full_name="Phase Four Parent",
            mobile="+919900001111",
            mobile_verified=True,
        )
        p1_user.set_password("Parent1Secure#4")
        db.session.add(p1_user)
        db.session.flush()

        p1_profile = Parent(
            user_id=p1_user.id,
            first_name="Phase",
            last_name="Four",
            village="Testville",
        )
        db.session.add(p1_profile)
        db.session.flush()

        child1 = Child(
            parent_id=p1_profile.id,
            name="Child One",
            dob=date(2022, 3, 15),
            gender="Male",
            village="Testville",
            record_token=str(uuid.uuid4()),
        )
        db.session.add(child1)

        # --- Parent 2 (for cross-parent security tests) ---
        p2_user = User(
            username="p4parent2",
            email="p4parent2@test.vc",
            role="parent",
            full_name="Other Parent",
        )
        p2_user.set_password("Parent2Secure#4")
        db.session.add(p2_user)
        db.session.flush()

        p2_profile = Parent(
            user_id=p2_user.id,
            first_name="Other",
            last_name="Person",
            village="Othertown",
        )
        db.session.add(p2_profile)
        db.session.flush()

        child2 = Child(
            parent_id=p2_profile.id,
            name="Child Two",
            dob=date(2023, 7, 1),
            gender="Female",
            village="Othertown",
            record_token=str(uuid.uuid4()),
        )
        db.session.add(child2)

        # --- Vaccine + schedule so schedule tests have data ---
        vaccine = Vaccine(
            name="TestBCG",
            disease="Tuberculosis",
            description="BCG vaccine for TB.",
            category="Childhood",
            recommended_age="At birth",
            source="WHO",
            source_checked_on="2026-01-01",
        )
        db.session.add(vaccine)
        db.session.flush()

        schedule_entry = VaccinationSchedule(
            vaccine_id=vaccine.id,
            dose_label="Dose 1",
            age_days=0,
            description="Birth dose",
        )
        db.session.add(schedule_entry)

        db.session.commit()

        data = {
            "db_path": db_path,
            "p1_user_id": p1_user.id,
            "p2_user_id": p2_user.id,
            "p1_profile_id": p1_profile.id,
            "p2_profile_id": p2_profile.id,
            "child1_id": child1.id,
            "child2_id": child2.id,
            "vaccine_id": vaccine.id,
        }

        client = app.test_client()
        yield app, client, data

    shutil.rmtree(temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 1. test_parent_login
# ---------------------------------------------------------------------------

def test_parent_login(ctx):
    _, client, _ = ctx
    resp = login(client, "p4parent1", "Parent1Secure#4")
    assert resp.status_code == 200
    assert b"Login successful" in resp.data or b"dashboard" in resp.data.lower()


# ---------------------------------------------------------------------------
# 2. test_parent_dashboard_access
# ---------------------------------------------------------------------------

def test_parent_dashboard_access(ctx):
    _, client, _ = ctx
    login(client, "p4parent1", "Parent1Secure#4")
    resp = client.get("/parent-dashboard", follow_redirects=True)
    assert resp.status_code == 200
    logout(client)


# ---------------------------------------------------------------------------
# 3. test_parent_dashboard_requires_login
# ---------------------------------------------------------------------------

def test_parent_dashboard_requires_login(ctx):
    _, client, _ = ctx
    resp = client.get("/parent-dashboard", follow_redirects=False)
    assert resp.status_code == 302
    assert "login" in resp.location.lower()


# ---------------------------------------------------------------------------
# 4. test_add_child_success
# ---------------------------------------------------------------------------

def test_add_child_success(ctx):
    app, client, _ = ctx
    login(client, "p4parent1", "Parent1Secure#4")
    resp = client.post(
        "/child/add",
        data={
            "name": "New Test Child",
            "dob": "2023-06-10",
            "gender": "Female",
            "village": "Testville",
            "allergies": "",
            "notes": "",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        child = Child.query.filter_by(name="New Test Child").first()
        assert child is not None
        assert child.gender == "Female"
    logout(client)


# ---------------------------------------------------------------------------
# 5. test_add_child_missing_fields
# ---------------------------------------------------------------------------

def test_add_child_missing_fields(ctx):
    app, client, _ = ctx
    login(client, "p4parent1", "Parent1Secure#4")
    resp = client.post(
        "/child/add",
        data={
            "name": "",          # required — intentionally blank
            "dob": "2023-06-10",
            "gender": "Male",
            "village": "Testville",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    # Child should NOT have been created
    with app.app_context():
        child = Child.query.filter_by(dob=date(2023, 6, 10), gender="Male").first()
        # If a child with that dob/gender was already in the DB (child1 is Male/2022-03-15)
        # so this would be a new one — confirm it has a name (i.e. the blank-name one wasn't saved)
        blank_name_child = Child.query.filter_by(name="").first()
        assert blank_name_child is None
    logout(client)


# ---------------------------------------------------------------------------
# 6. test_child_detail_access
# ---------------------------------------------------------------------------

def test_child_detail_access(ctx):
    _, client, data = ctx
    login(client, "p4parent1", "Parent1Secure#4")
    resp = client.get(f"/child/{data['child1_id']}", follow_redirects=True)
    assert resp.status_code == 200
    assert b"Child One" in resp.data
    logout(client)


# ---------------------------------------------------------------------------
# 7. test_child_detail_cross_parent_blocked
# ---------------------------------------------------------------------------

def test_child_detail_cross_parent_blocked(ctx):
    _, client, data = ctx
    login(client, "p4parent1", "Parent1Secure#4")
    # child2 belongs to parent2 — parent1 must be blocked
    resp = client.get(f"/child/{data['child2_id']}", follow_redirects=True)
    assert resp.status_code == 403
    logout(client)


# ---------------------------------------------------------------------------
# 8. test_edit_child_success
# ---------------------------------------------------------------------------

def test_edit_child_success(ctx):
    app, client, data = ctx
    login(client, "p4parent1", "Parent1Secure#4")
    resp = client.post(
        f"/child/{data['child1_id']}/edit",
        data={
            "name": "Child One Renamed",
            "dob": "2022-03-15",
            "gender": "Male",
            "village": "Testville",
            "allergies": "",
            "notes": "",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        child = db.session.get(Child, data["child1_id"])
        assert child.name == "Child One Renamed"
    logout(client)


# ---------------------------------------------------------------------------
# 9. test_edit_child_cross_parent_blocked
# ---------------------------------------------------------------------------

def test_edit_child_cross_parent_blocked(ctx):
    _, client, data = ctx
    login(client, "p4parent1", "Parent1Secure#4")
    resp = client.post(
        f"/child/{data['child2_id']}/edit",
        data={
            "name": "Hijacked Child",
            "dob": "2023-07-01",
            "gender": "Female",
            "village": "Othertown",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 403
    logout(client)


# ---------------------------------------------------------------------------
# 10. test_delete_child_success
# ---------------------------------------------------------------------------

def test_delete_child_success(ctx):
    app, client, data = ctx
    # Add a throwaway child to delete so we don't disturb other tests
    with app.app_context():
        p1_profile = db.session.get(Parent, data["p1_profile_id"])
        throwaway = Child(
            parent_id=p1_profile.id,
            name="Delete Me",
            dob=date(2021, 1, 1),
            gender="Male",
            village="Testville",
            record_token=str(uuid.uuid4()),
        )
        db.session.add(throwaway)
        db.session.commit()
        throwaway_id = throwaway.id

    login(client, "p4parent1", "Parent1Secure#4")
    resp = client.post(
        f"/child/{throwaway_id}/delete",
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        gone = db.session.get(Child, throwaway_id)
        assert gone is None
    logout(client)


# ---------------------------------------------------------------------------
# 11. test_delete_child_cross_parent_blocked
# ---------------------------------------------------------------------------

def test_delete_child_cross_parent_blocked(ctx):
    app, client, data = ctx
    login(client, "p4parent1", "Parent1Secure#4")
    resp = client.post(
        f"/child/{data['child2_id']}/delete",
        follow_redirects=True,
    )
    assert resp.status_code == 403
    # Verify child2 still exists
    with app.app_context():
        still_there = db.session.get(Child, data["child2_id"])
        assert still_there is not None
    logout(client)


# ---------------------------------------------------------------------------
# 12. test_parent_profile_access
# ---------------------------------------------------------------------------

def test_parent_profile_access(ctx):
    _, client, _ = ctx
    login(client, "p4parent1", "Parent1Secure#4")
    resp = client.get("/parent/profile", follow_redirects=True)
    assert resp.status_code == 200
    logout(client)


# ---------------------------------------------------------------------------
# 13. test_parent_profile_update
# ---------------------------------------------------------------------------

def test_parent_profile_update(ctx):
    app, client, data = ctx
    login(client, "p4parent1", "Parent1Secure#4")
    resp = client.post(
        "/parent/profile",
        data={
            "first_name": "UpdatedFirst",
            "last_name": "Four",
            "village": "Testville",
            "address": "",
            "mobile": "+919900001111",
            "sms_consent": "y",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        profile = db.session.get(Parent, data["p1_profile_id"])
        assert profile.first_name == "UpdatedFirst"
    logout(client)


# ---------------------------------------------------------------------------
# 14. test_parent_notifications_access
# ---------------------------------------------------------------------------

def test_parent_notifications_access(ctx):
    _, client, _ = ctx
    login(client, "p4parent1", "Parent1Secure#4")
    resp = client.get("/parent/notifications", follow_redirects=True)
    assert resp.status_code == 200
    logout(client)


# ---------------------------------------------------------------------------
# 15. test_vaccination_schedule_shown
# ---------------------------------------------------------------------------

def test_vaccination_schedule_shown(ctx):
    app, client, data = ctx
    # Confirm the VaccinationSchedule seeded in the fixture is in DB
    with app.app_context():
        schedules = VaccinationSchedule.query.all()
        assert len(schedules) > 0, "No VaccinationSchedule rows seeded — schedule test cannot run"

    login(client, "p4parent1", "Parent1Secure#4")
    resp = client.get(f"/child/{data['child1_id']}", follow_redirects=True)
    assert resp.status_code == 200
    # The child_detail view calls build_child_schedule(child) which uses VaccinationSchedule rows.
    # At minimum the page should load with schedule-related content.
    assert b"schedule" in resp.data.lower() or b"vaccine" in resp.data.lower() or b"dose" in resp.data.lower()
    logout(client)


# ---------------------------------------------------------------------------
# 16. test_child_persists_after_app_restart
# ---------------------------------------------------------------------------

def test_child_persists_after_app_restart(ctx):
    app, client, data = ctx

    # Add a child via the route
    login(client, "p4parent1", "Parent1Secure#4")
    client.post(
        "/child/add",
        data={
            "name": "PersistChild",
            "dob": "2024-01-20",
            "gender": "Female",
            "village": "Testville",
            "allergies": "",
            "notes": "",
        },
        follow_redirects=True,
    )
    logout(client)

    db_uri = app.config["SQLALCHEMY_DATABASE_URI"]

    # Simulate app restart — fresh create_app against same DB file
    app2 = create_app(test_config=_base_config(db_uri))
    with app2.app_context():
        child = Child.query.filter_by(name="PersistChild").first()
        assert child is not None, "Child did not persist after simulated app restart"
        assert child.village == "Testville"


# ---------------------------------------------------------------------------
# 17. test_no_fake_children
# ---------------------------------------------------------------------------

def test_no_fake_children(ctx):
    app, client, _ = ctx

    # Create a brand-new parent account (not in the fixture)
    fresh_temp = tempfile.mkdtemp()
    fresh_uri, _ = _make_db_uri(fresh_temp, "fresh_parent.db")

    fresh_app = create_app(test_config=_base_config(fresh_uri))
    with fresh_app.app_context():
        db.create_all()

        fresh_user = User(
            username="freshparent_p4",
            email="freshparent_p4@test.vc",
            role="parent",
            full_name="Fresh Parent",
        )
        fresh_user.set_password("FreshParent#99")
        db.session.add(fresh_user)
        db.session.flush()

        fresh_profile = Parent(
            user_id=fresh_user.id,
            first_name="Fresh",
            last_name="Start",
            village="Nowhere",
        )
        db.session.add(fresh_profile)
        db.session.commit()

        fresh_client = fresh_app.test_client()
        fresh_client.post(
            "/login",
            data={"username": "freshparent_p4", "password": "FreshParent#99"},
            follow_redirects=True,
        )
        resp = fresh_client.get("/parent-dashboard", follow_redirects=True)
        assert resp.status_code == 200

        # Confirm zero children in DB for this parent
        children = Child.query.filter_by(parent_id=fresh_profile.id).all()
        assert len(children) == 0, f"Expected 0 children for brand-new parent, found {len(children)}"

    shutil.rmtree(fresh_temp, ignore_errors=True)
