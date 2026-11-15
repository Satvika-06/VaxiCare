import time
from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import current_app

from app.models import Child
from app.scheduling import build_child_schedule
from app.services import retry_due_notifications, send_sms_notification


def process_scheduled_reminders(app, today=None):
    with app.app_context():
        timezone = ZoneInfo(app.config["REMINDER_TIMEZONE"])
        local_today = today or datetime.now(timezone).date()
        retry_due_notifications()
        days_before = app.config["REMINDER_DAYS_BEFORE"]
        overdue_after = app.config["REMINDER_OVERDUE_AFTER_DAYS"]
        overdue_enabled = app.config["REMINDER_OVERDUE_ENABLED"]
        outcomes = Counter()

        children = Child.query.all()
        for child in children:
            user = child.parent.user
            for entry in build_child_schedule(child, today=local_today):
                if entry["status"] == "completed":
                    continue
                due_date = entry["due_date"]
                reminder_type = None
                if due_date == local_today:
                    reminder_type = "due_today"
                elif days_before > 0 and due_date == local_today + timedelta(days=days_before):
                    reminder_type = "upcoming"
                elif overdue_enabled and due_date == local_today - timedelta(days=overdue_after):
                    reminder_type = "overdue"
                if not reminder_type:
                    continue
                result = send_sms_notification(
                    user,
                    child,
                    entry["vaccine"],
                    reminder_type,
                    language="en",
                    scheduled_for=due_date,
                    schedule_id=entry["schedule"].id,
                )
                outcomes[result["status"]] += 1
        current_app.logger.info("Reminder run completed: %s", dict(outcomes))
        return dict(outcomes)


def run_forever(app, interval_seconds=60):
    if interval_seconds < 10:
        raise ValueError("REMINDER_CHECK_INTERVAL_SECONDS must be at least 10.")
    while True:
        process_scheduled_reminders(app)
        time.sleep(interval_seconds)



def list_test_reminder_targets(app):
    with app.app_context():
        test_email = app.config.get("REMINDER_TEST_EMAIL")
        if not test_email or not test_email.strip():
            raise ValueError("REMINDER_TEST_EMAIL must be configured to list test reminder targets.")

        from app.models import User
        user = User.query.filter_by(email=test_email.strip().lower()).first()
        if not user or not user.parent:
            return []

        targets = []
        for child in user.parent.children:
            for entry in build_child_schedule(child):
                if entry["status"] != "completed":
                    targets.append({
                        "child_id": child.id,
                        "schedule_id": entry["schedule"].id,
                        "child_name": child.name,
                        "vaccine_name": entry["vaccine"].name,
                        "dose_label": entry["schedule"].dose_label,
                        "due_date": entry["due_date"],
                    })
        return targets


def send_test_reminder(app, child_id, schedule_id=None, reminder_type="upcoming"):
    with app.app_context():
        test_email = app.config.get("REMINDER_TEST_EMAIL")
        if not test_email or not test_email.strip():
            raise ValueError("REMINDER_TEST_EMAIL must be configured to run reminder tests.")

        from app.models import Child, VaccinationSchedule
        child = Child.query.get(child_id)
        if not child:
            raise ValueError(f"Child with ID {child_id} not found.")
        if not child.parent or not child.parent.user or child.parent.user.email.strip().lower() != test_email.strip().lower():
            raise ValueError("Selected child does not belong to the configured test parent.")

        schedule = VaccinationSchedule.query.get(schedule_id) if schedule_id else None
        vaccine = schedule.vaccine if schedule else None

        from app.services import send_vaccination_reminder_email
        result = send_vaccination_reminder_email(
            recipient=test_email.strip().lower(),
            child_name=child.name,
            vaccine_name=vaccine.name if vaccine else "General Vaccine",
            dose_label=schedule.dose_label if schedule else "Standard",
            due_date=datetime.now().date(),
            reminder_type=reminder_type,
            is_test=True,
        )
        return result
