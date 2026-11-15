from sqlalchemy import inspect, text

from app import db
from app.models import ApplicationSetting, ReminderLog, SmsNotification, utcnow


def upgrade_existing_database():
    inspector = inspect(db.engine)
    existing_tables = set(inspector.get_table_names())

    # 1. User table migrations
    if "users" in existing_tables:
        columns = {column["name"] for column in inspector.get_columns("users")}
        upgrades = {
            "sms_consent": "BOOLEAN NOT NULL DEFAULT FALSE",
            "full_name": "VARCHAR(120)",
            "auth_version": "INTEGER NOT NULL DEFAULT 0",
            "mobile_verified_at": "DATETIME",
        }
        for column, definition in upgrades.items():
            if column not in columns:
                db.session.execute(
                    text(f"ALTER TABLE users ADD COLUMN {column} {definition}")
                )
        db.session.commit()

        # Backfill mobile_verified_at if user was already mobile_verified
        db.session.execute(
            text(
                "UPDATE users SET mobile_verified_at = created_at "
                "WHERE mobile_verified = 1 AND mobile_verified_at IS NULL"
            )
        )
        db.session.commit()

    # 1b. Notifications table migrations
    if "notifications" in existing_tables:
        notif_columns = {column["name"] for column in inspector.get_columns("notifications")}
        if "is_read" not in notif_columns:
            db.session.execute(text("ALTER TABLE notifications ADD COLUMN is_read BOOLEAN NOT NULL DEFAULT 0"))
            db.session.commit()

    # 1c. FCM device tokens table migrations
    if "fcm_device_tokens" in existing_tables:
        token_columns = {column["name"] for column in inspector.get_columns("fcm_device_tokens")}
        if "device_name" not in token_columns:
            db.session.execute(text("ALTER TABLE fcm_device_tokens ADD COLUMN device_name VARCHAR(120)"))
        if "permission_status" not in token_columns:
            db.session.execute(text("ALTER TABLE fcm_device_tokens ADD COLUMN permission_status VARCHAR(30) DEFAULT 'granted'"))
        db.session.commit()

    # 2. Synchronize empty auxiliary tables if schema needs alignment
    empty_tables_to_sync = [
        "application_settings",
        "email_reminder_notifications",
        "fcm_device_tokens",
        "fcm_push_notifications",
        "fcm_push_test_attempts",
        "mobile_verification_challenges",
    ]
    for t_name in empty_tables_to_sync:
        if t_name in existing_tables:
            # Verify row count is strictly 0 before any re-creation
            cnt = db.session.execute(text(f'SELECT COUNT(*) FROM "{t_name}"')).scalar()
            if cnt == 0:
                current_cols = {c["name"] for c in inspector.get_columns(t_name)}
                expected_cols = {c.name for c in db.metadata.tables[t_name].columns}
                if current_cols != expected_cols:
                    db.session.execute(text(f'DROP TABLE "{t_name}"'))
                    db.session.commit()
                    db.metadata.tables[t_name].create(db.engine)

    # 3. Ensure all metadata tables exist
    db.create_all()

    # 4. Initialize default application settings if empty
    if "application_settings" in inspector.get_table_names():
        try:
            if ApplicationSetting.query.count() == 0:
                defaults = {
                    "automatic_email_reminders_enabled": "false",
                    "email_reminder_days_before": "3",
                    "email_reminder_overdue_after_days": "1",
                    "reminder_check_interval_seconds": "60",
                }
                for k, v in defaults.items():
                    db.session.add(ApplicationSetting(key=k, value=v, updated_at=utcnow()))
                db.session.commit()
        except Exception:
            db.session.rollback()

    # 5. Migrate legacy reminder logs to sms_notifications
    if "reminder_logs" in existing_tables and "sms_notifications" in existing_tables:
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
