import os
import shutil
import tempfile
import pytest
from datetime import date
from sqlalchemy.exc import IntegrityError

from app import create_app, db
from app.models import (
    User,
    Parent,
    HealthcareWorker,
    Child,
    Vaccine,
    VaccinationSchedule,
    VaccinationRecord,
    ApplicationSetting,
    utcnow
)
from app.migrations import upgrade_existing_database
from werkzeug.security import generate_password_hash, check_password_hash


@pytest.fixture
def temp_db_app():
    """Create a temporary isolated SQLite database file for testing."""
    temp_dir = tempfile.mkdtemp()
    temp_db_path = os.path.join(temp_dir, "test_vaxicare.db")
    db_uri = "sqlite:///" + temp_db_path.replace("\\", "/")

    app = create_app(test_config={
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": db_uri,
        "SEED_DEMO_DATA": False,
        "WTF_CSRF_ENABLED": False,
    })

    with app.app_context():
        db.create_all()
        upgrade_existing_database()
        yield app, temp_db_path

    shutil.rmtree(temp_dir, ignore_errors=True)


def test_sqlite_connection_and_pragma(temp_db_app):
    """1. Test that application connects to SQLite and enforces foreign keys."""
    app, _ = temp_db_app
    with app.app_context():
        # Check SQLite connection
        result = db.session.execute(db.text("SELECT 1")).scalar()
        assert result == 1

        # Check PRAGMA foreign_keys is ON
        fk_status = db.session.execute(db.text("PRAGMA foreign_keys")).scalar()
        assert fk_status == 1


def test_user_insertion_and_retrieval(temp_db_app):
    """3 & 4. Test that new user records can be inserted and retrieved."""
    app, _ = temp_db_app
    with app.app_context():
        pwd_hash = generate_password_hash("SecurePass123!")
        user = User(
            username="nurse_anita",
            email="anita@testclinic.org",
            password_hash=pwd_hash,
            role="healthcare_worker",
            full_name="Anita Sharma",
            mobile="+919876500001",
            mobile_verified=True,
            mobile_verified_at=utcnow(),
            sms_consent=True
        )
        db.session.add(user)
        db.session.commit()

        # Retrieve and verify
        retrieved = User.query.filter_by(username="nurse_anita").first()
        assert retrieved is not None
        assert retrieved.email == "anita@testclinic.org"
        assert retrieved.role == "healthcare_worker"
        assert retrieved.full_name == "Anita Sharma"
        assert retrieved.mobile_verified is True
        assert retrieved.mobile_verified_at is not None


def test_user_unique_constraints(temp_db_app):
    """Test username and email unique constraints."""
    app, _ = temp_db_app
    with app.app_context():
        u1 = User(
            username="unique_user",
            email="unique@example.com",
            password_hash=generate_password_hash("Pass123"),
            role="parent"
        )
        db.session.add(u1)
        db.session.commit()

        # Duplicate username
        u2 = User(
            username="unique_user",
            email="other@example.com",
            password_hash=generate_password_hash("Pass123"),
            role="parent"
        )
        db.session.add(u2)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()

        # Duplicate email
        u3 = User(
            username="other_user",
            email="unique@example.com",
            password_hash=generate_password_hash("Pass123"),
            role="parent"
        )
        db.session.add(u3)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_password_hash_integrity(temp_db_app):
    """5. Test password hash verification and that hashes remain unchanged."""
    app, _ = temp_db_app
    with app.app_context():
        raw_password = "DoctorPassword#2026"
        hashed = generate_password_hash(raw_password)

        user = User(
            username="dr_rajesh",
            email="rajesh@hospital.org",
            password_hash=hashed,
            role="healthcare_worker",
            full_name="Dr. Rajesh Varma",
            mobile="+919876500002"
        )
        db.session.add(user)
        db.session.commit()

        # Re-query
        saved_user = User.query.filter_by(username="dr_rajesh").first()
        assert saved_user.password_hash == hashed
        assert check_password_hash(saved_user.password_hash, raw_password) is True
        assert check_password_hash(saved_user.password_hash, "WrongPassword") is False


def test_child_parent_relationship(temp_db_app):
    """6. Test child records remain linked to the correct parents."""
    app, _ = temp_db_app
    with app.app_context():
        parent_user = User(
            username="parent_ramesh",
            email="ramesh@example.com",
            password_hash=generate_password_hash("ParentPass123"),
            role="parent",
            full_name="Ramesh Kumar",
            mobile="+919876500003"
        )
        db.session.add(parent_user)
        db.session.commit()

        parent = Parent(
            user_id=parent_user.id,
            first_name="Ramesh",
            last_name="Kumar",
            address="Street 4, Sector 7",
            village="Chandigarh"
        )
        db.session.add(parent)
        db.session.commit()

        child = Child(
            parent_id=parent.id,
            name="Rahul Kumar",
            dob=date(2023, 6, 15),
            gender="Male",
            village="Chandigarh",
            record_token="token-rahul-test-123"
        )
        db.session.add(child)
        db.session.commit()

        # Verify relationship from both sides
        queried_child = Child.query.filter_by(name="Rahul Kumar").first()
        assert queried_child is not None
        assert queried_child.parent_id == parent.id
        assert queried_child.parent.first_name == "Ramesh"
        assert len(parent.children) == 1
        assert parent.children[0].name == "Rahul Kumar"


def test_vaccination_record_linking(temp_db_app):
    """7. Test vaccination records remain linked correctly."""
    app, _ = temp_db_app
    with app.app_context():
        # Setup parent & child
        u = User(
            username="p_sunita",
            email="sunita@example.com",
            password_hash=generate_password_hash("Sunita123"),
            role="parent"
        )
        db.session.add(u)
        db.session.commit()

        parent = Parent(user_id=u.id, first_name="Sunita", last_name="Devi", village="Madhapur")
        db.session.add(parent)
        db.session.commit()

        child = Child(
            parent_id=parent.id,
            name="Priya Devi",
            dob=date(2024, 1, 10),
            gender="Female",
            village="Madhapur",
            record_token="token-priya-456"
        )
        db.session.add(child)

        # Setup vaccine
        vaccine = Vaccine(
            name="BCG",
            disease="Tuberculosis",
            description="Protects against severe tuberculosis disease.",
            category="Childhood",
            recommended_age="At birth",
            source="Government UIP",
            source_checked_on="2026-11-04"
        )
        db.session.add(vaccine)
        db.session.commit()

        # Setup record
        record = VaccinationRecord(
            child_id=child.id,
            vaccine_id=vaccine.id,
            dose_status="received",
            administered_date=date(2024, 1, 11),
            notes="Administered at district hospital"
        )
        db.session.add(record)
        db.session.commit()

        # Verify linking
        rec = VaccinationRecord.query.filter_by(child_id=child.id, vaccine_id=vaccine.id).first()
        assert rec is not None
        assert rec.child.name == "Priya Devi"
        assert rec.vaccine.name == "BCG"
        assert rec.dose_status == "received"


def test_persistence_across_app_restart(temp_db_app):
    """8. Test database changes persist after application restart."""
    app, temp_db_path = temp_db_app
    with app.app_context():
        u = User(
            username="persist_user",
            email="persist@example.com",
            password_hash=generate_password_hash("PersistPass1"),
            role="admin",
            full_name="Persist User"
        )
        db.session.add(u)
        db.session.commit()
        db.session.close()

    # Re-create application pointing to the exact same SQLite database file
    new_app = create_app(test_config={
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///" + temp_db_path.replace("\\", "/"),
        "SEED_DEMO_DATA": False,
        "WTF_CSRF_ENABLED": False,
    })

    with new_app.app_context():
        persisted_user = User.query.filter_by(username="persist_user").first()
        assert persisted_user is not None
        assert persisted_user.email == "persist@example.com"
        assert persisted_user.full_name == "Persist User"


def test_foreign_key_constraints(temp_db_app):
    """9. Test foreign-key constraints are enforced."""
    app, _ = temp_db_app
    with app.app_context():
        # Attempt to insert a child with non-existent parent_id
        orphan_child = Child(
            parent_id=99999,  # Does not exist
            name="Orphan Baby",
            dob=date(2025, 1, 1),
            gender="Female",
            village="City",
            record_token="orphan-token"
        )
        db.session.add(orphan_child)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_migration_preserves_existing_data(temp_db_app):
    """10 & 2. Test database migrations work without losing data."""
    app, _ = temp_db_app
    with app.app_context():
        # Insert a user before migration
        u = User(
            username="existing_vaxicare_user",
            email="existing@vaxicare.in",
            password_hash=generate_password_hash("Existing123!"),
            role="parent",
            mobile="+919999988888",
            mobile_verified=True
        )
        db.session.add(u)
        db.session.commit()
        user_id = u.id

        # Re-run migration
        upgrade_existing_database()

        # Verify data still exists and mobile_verified_at was backfilled
        rechecked = db.session.get(User, user_id)
        assert rechecked is not None
        assert rechecked.username == "existing_vaxicare_user"
        assert rechecked.mobile_verified_at is not None
