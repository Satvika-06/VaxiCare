from datetime import date, timedelta

from app.models import Child, HealthcareWorker, Parent, ReminderLog, User, VaccinationRecord, VaccinationSchedule, Vaccine, db


def seed_demo_data():
    if User.query.first():
        return

    admin = User(username="admin", email="admin@vaxicare.in", role="admin", mobile="+919876543210")
    admin.set_password("Admin@123")
    db.session.add(admin)

    worker_user = User(username="healthworker", email="worker@vaxicare.in", role="healthcare_worker", mobile="+919800112233")
    worker_user.set_password("Worker@123")
    db.session.add(worker_user)

    parent_user = User(username="parent1", email="parent1@vaxicare.in", role="parent", mobile="+919912345678", mobile_verified=True)
    parent_user.set_password("Parent@123")
    db.session.add(parent_user)
    db.session.flush()

    parent = Parent(user_id=parent_user.id, first_name="Ananya", last_name="Reddy", address="Green Park", village="Madhapur")
    db.session.add(parent)
    db.session.flush()

    worker = HealthcareWorker(user_id=worker_user.id, full_name="Dr. Srujana Rao", qualification="MBBS", department="Immunization", village="Madhapur")
    db.session.add(worker)
    db.session.flush()

    child = Child(parent_id=parent.id, name="Aarav Reddy", dob=date(2022, 4, 5), gender="Male", village="Madhapur", allergies="None", notes="Healthy child", record_token="demo-aarav-token")
    db.session.add(child)
    db.session.flush()

    vaccines = [
        Vaccine(name="BCG", disease="Tuberculosis", description="Protects against severe tuberculosis disease. This is a live attenuated vaccine used under national immunization protocols.", category="Childhood", recommended_age="At birth or as early as possible", source="Government of India UIP National Immunization Schedule", source_checked_on="2026-11-04"),
        Vaccine(name="Hepatitis B", disease="Hepatitis B", description="Protects against hepatitis B infection.", category="Newborn", recommended_age="Birth, 6 weeks, 14 weeks, 9-12 months", source="Government of India UIP National Immunization Schedule", source_checked_on="2026-11-04"),
        Vaccine(name="OPV", disease="Polio", description="Protects against poliovirus.", category="Childhood", recommended_age="Birth, 6 weeks, 10 weeks, 14 weeks, 15-18 months, 4-6 years", source="Government of India UIP National Immunization Schedule", source_checked_on="2026-11-04"),
        Vaccine(name="DPT", disease="Diphtheria, Pertussis, Tetanus", description="Three-dose primary series with booster later.", category="Childhood", recommended_age="6 weeks, 10 weeks, 14 weeks, 18-24 months", source="Government of India UIP National Immunization Schedule", source_checked_on="2026-11-04"),
        Vaccine(name="Pentavalent", disease="Diphtheria, Pertussis, Tetanus, Hib, Hepatitis B", description="Combined vaccine protecting against 5 diseases.", category="Childhood", recommended_age="6 weeks, 10 weeks, 14 weeks", source="Government of India UIP National Immunization Schedule", source_checked_on="2026-11-04"),
        Vaccine(name="IPV", disease="Polio", description="Injectable polio vaccine used as part of the immunization schedule.", category="Childhood", recommended_age="6 weeks, 10 weeks, 14 weeks, 4-6 years", source="Government of India UIP National Immunization Schedule", source_checked_on="2026-11-04"),
        Vaccine(name="Measles Rubella (MR)", disease="Measles and Rubella", description="Protects against measles and rubella infections.", category="Childhood", recommended_age="9-12 months, 16-24 months", source="Government of India UIP National Immunization Schedule", source_checked_on="2026-11-04"),
    ]
    db.session.add_all(vaccines)
    db.session.flush()

    schedule_entries = [
        (vaccines[0].id, "At Birth", 0, "Administer as soon as possible after birth."),
        (vaccines[1].id, "Birth Dose", 0, "Hepatitis B at birth."),
        (vaccines[1].id, "Dose 2", 42, "Hepatitis B at 6 weeks."),
        (vaccines[1].id, "Dose 3", 98, "Hepatitis B at 14 weeks."),
        (vaccines[2].id, "Birth Dose", 0, "OPV at birth."),
        (vaccines[2].id, "Dose 1", 42, "OPV at 6 weeks."),
        (vaccines[2].id, "Dose 2", 70, "OPV at 10 weeks."),
        (vaccines[2].id, "Dose 3", 98, "OPV at 14 weeks."),
        (vaccines[3].id, "Dose 1", 42, "DPT at 6 weeks."),
        (vaccines[3].id, "Dose 2", 70, "DPT at 10 weeks."),
        (vaccines[3].id, "Dose 3", 98, "DPT at 14 weeks."),
        (vaccines[4].id, "Dose 1", 42, "Pentavalent at 6 weeks."),
        (vaccines[4].id, "Dose 2", 70, "Pentavalent at 10 weeks."),
        (vaccines[4].id, "Dose 3", 98, "Pentavalent at 14 weeks."),
        (vaccines[5].id, "Dose 1", 42, "IPV at 6 weeks."),
        (vaccines[5].id, "Dose 2", 70, "IPV at 10 weeks."),
        (vaccines[5].id, "Dose 3", 98, "IPV at 14 weeks."),
        (vaccines[6].id, "Dose 1", 270, "MR at 9 months."),
        (vaccines[6].id, "Dose 2", 540, "MR booster between 16 and 24 months."),
    ]

    for vaccine_id, dose_label, age_days, description in schedule_entries:
        db.session.add(
            VaccinationSchedule(
                vaccine_id=vaccine_id,
                dose_label=dose_label,
                age_days=age_days,
                description=description,
                source_note="Government of India UIP National Immunization Schedule, checked on 2026-11-04",
                source_checked_on="2026-11-04",
            )
        )

    db.session.flush()

    demo_record = VaccinationRecord(
        child_id=child.id,
        vaccine_id=vaccines[1].id,
        schedule_id=None,
        recorded_by_id=worker.id,
        administered_date=date.today() - timedelta(days=30),
        dose_status="received",
        notes="Birth dose administered by healthcare worker.",
    )
    db.session.add(demo_record)

    db.session.commit()
