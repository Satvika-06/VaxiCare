from sqlalchemy import inspect, text

from app import db
from app.models import ReminderLog, SmsNotification, utcnow


def upgrade_existing_database():
    inspector = inspect(db.engine)
    if "users" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("users")}
        upgrades = {
            "sms_consent": "BOOLEAN NOT NULL DEFAULT FALSE",
            "full_name": "VARCHAR(120)",
            "auth_version": "INTEGER NOT NULL DEFAULT 0",
        }
        for column, definition in upgrades.items():
            if column not in columns:
                db.session.execute(
                    text(f"ALTER TABLE users ADD COLUMN {column} {definition}")
                )
        db.session.commit()

    existing_legacy_ids = {
        legacy_id
        for legacy_id, in db.session.query(SmsNotification.legacy_reminder_id).filter(
            SmsNotification.legacy_reminder_id.isnot(None)
        )
    }
    for legacy in ReminderLog.query.order_by(ReminderLog.id.asc()).all():
        if legacy.id in existing_legacy_ids:
            continue
        legacy_failed = legacy.delivery_status == "failed"
        status = {"demo_mode": "simulated", "queued": "pending"}.get(
            legacy.delivery_status, legacy.delivery_status
        )
        notification = SmsNotification(
            user_id=legacy.user_id,
            child_id=legacy.child_id,
            vaccine_id=legacy.vaccine_id,
            recipient=legacy.user.mobile or "",
            due_date=legacy.scheduled_for,
            message_type=legacy.reminder_type,
            language=legacy.language,
            message="Legacy SMS failure; original error details are hidden."
            if legacy_failed
            else legacy.message,
            status=status,
            failure_reason="Legacy provider error details are hidden." if legacy_failed else None,
            created_at=legacy.sent_at or utcnow(),
            next_attempt_at=utcnow() if status == "pending" else None,
            legacy_reminder_id=legacy.id,
        )
        db.session.add(notification)
    db.session.commit()
