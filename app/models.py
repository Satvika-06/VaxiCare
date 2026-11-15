from datetime import UTC, datetime

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app import db


def utcnow():
    return datetime.now(UTC).replace(tzinfo=None)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(30), default="parent", nullable=False)
    full_name = db.Column(db.String(120), nullable=True)
    mobile = db.Column(db.String(20), nullable=True)
    mobile_verified = db.Column(db.Boolean, default=False, nullable=False)
    sms_consent = db.Column(db.Boolean, default=False, nullable=False)
    auth_version = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)

    parent = db.relationship("Parent", back_populates="user", uselist=False, cascade="all, delete-orphan")
    healthcare_worker = db.relationship("HealthcareWorker", back_populates="user", uselist=False, cascade="all, delete-orphan")
    notifications = db.relationship("Notification", back_populates="user", cascade="all, delete-orphan")
    reminder_logs = db.relationship("ReminderLog", back_populates="user", cascade="all, delete-orphan")
    sms_notifications = db.relationship("SmsNotification", back_populates="user")

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)
        self.auth_version = (self.auth_version or 0) + 1

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def get_id(self):
        return f"{self.id}:{self.auth_version}"


class PasswordResetToken(db.Model):
    __tablename__ = "password_reset_tokens"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    token_hash = db.Column(db.String(64), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship("User")


class Parent(db.Model):
    __tablename__ = "parents"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False)
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=True)
    address = db.Column(db.String(200), nullable=True)
    village = db.Column(db.String(80), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    user = db.relationship("User", back_populates="parent")
    children = db.relationship("Child", back_populates="parent", cascade="all, delete-orphan")


class Child(db.Model):
    __tablename__ = "children"

    id = db.Column(db.Integer, primary_key=True)
    parent_id = db.Column(db.Integer, db.ForeignKey("parents.id"), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    dob = db.Column(db.Date, nullable=False)
    gender = db.Column(db.String(20), nullable=False)
    village = db.Column(db.String(80), nullable=False)
    allergies = db.Column(db.String(200), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    record_token = db.Column(db.String(64), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)

    parent = db.relationship("Parent", back_populates="children")
    records = db.relationship("VaccinationRecord", back_populates="child", cascade="all, delete-orphan")
    notifications = db.relationship("Notification", back_populates="child", cascade="all, delete-orphan")
    reminder_logs = db.relationship("ReminderLog", back_populates="child", cascade="all, delete-orphan")


class HealthcareWorker(db.Model):
    __tablename__ = "healthcare_workers"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False)
    full_name = db.Column(db.String(120), nullable=False)
    qualification = db.Column(db.String(120), nullable=True)
    department = db.Column(db.String(80), nullable=True)
    village = db.Column(db.String(80), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    user = db.relationship("User", back_populates="healthcare_worker")
    vaccination_records = db.relationship("VaccinationRecord", back_populates="recorded_by")


class Vaccine(db.Model):
    __tablename__ = "vaccines"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False, unique=True)
    disease = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(80), nullable=True)
    recommended_age = db.Column(db.String(120), nullable=True)
    source = db.Column(db.String(200), nullable=False)
    source_checked_on = db.Column(db.String(30), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)

    schedules = db.relationship("VaccinationSchedule", back_populates="vaccine", cascade="all, delete-orphan")
    records = db.relationship("VaccinationRecord", back_populates="vaccine", cascade="all, delete-orphan")
    notifications = db.relationship("Notification", back_populates="vaccine", cascade="all, delete-orphan")
    reminder_logs = db.relationship("ReminderLog", back_populates="vaccine", cascade="all, delete-orphan")


class VaccinationSchedule(db.Model):
    __tablename__ = "vaccination_schedules"

    id = db.Column(db.Integer, primary_key=True)
    vaccine_id = db.Column(db.Integer, db.ForeignKey("vaccines.id"), nullable=False)
    dose_label = db.Column(db.String(80), nullable=False)
    age_days = db.Column(db.Integer, nullable=False)
    description = db.Column(db.Text, nullable=True)
    source_note = db.Column(db.String(200), nullable=True)
    source_checked_on = db.Column(db.String(30), nullable=True)

    vaccine = db.relationship("Vaccine", back_populates="schedules")
    records = db.relationship("VaccinationRecord", back_populates="schedule", cascade="all, delete-orphan")


class VaccinationRecord(db.Model):
    __tablename__ = "vaccination_records"

    id = db.Column(db.Integer, primary_key=True)
    child_id = db.Column(db.Integer, db.ForeignKey("children.id"), nullable=False)
    vaccine_id = db.Column(db.Integer, db.ForeignKey("vaccines.id"), nullable=False)
    schedule_id = db.Column(db.Integer, db.ForeignKey("vaccination_schedules.id"), nullable=True)
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("healthcare_workers.id"), nullable=True)
    administered_date = db.Column(db.Date, nullable=False)
    dose_status = db.Column(db.String(30), default="received", nullable=False)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    child = db.relationship("Child", back_populates="records")
    vaccine = db.relationship("Vaccine", back_populates="records")
    schedule = db.relationship("VaccinationSchedule", back_populates="records")
    recorded_by = db.relationship("HealthcareWorker", back_populates="vaccination_records")


class Notification(db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    child_id = db.Column(db.Integer, db.ForeignKey("children.id"), nullable=True)
    vaccine_id = db.Column(db.Integer, db.ForeignKey("vaccines.id"), nullable=True)
    reminder_type = db.Column(db.String(40), nullable=False)
    title = db.Column(db.String(120), nullable=False)
    body = db.Column(db.Text, nullable=False)
    channel = db.Column(db.String(30), default="sms", nullable=False)
    status = db.Column(db.String(30), default="queued", nullable=False)
    sent_at = db.Column(db.DateTime, default=utcnow)

    user = db.relationship("User", back_populates="notifications")
    child = db.relationship("Child", back_populates="notifications")
    vaccine = db.relationship("Vaccine", back_populates="notifications")


class ReminderLog(db.Model):
    __tablename__ = "reminder_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    child_id = db.Column(db.Integer, db.ForeignKey("children.id"), nullable=False)
    vaccine_id = db.Column(db.Integer, db.ForeignKey("vaccines.id"), nullable=False)
    reminder_type = db.Column(db.String(40), nullable=False)
    language = db.Column(db.String(10), default="en", nullable=False)
    message = db.Column(db.Text, nullable=False)
    delivery_status = db.Column(db.String(40), default="pending", nullable=False)
    scheduled_for = db.Column(db.Date, nullable=False)
    sent_at = db.Column(db.DateTime, default=utcnow)

    __table_args__ = (db.UniqueConstraint("child_id", "vaccine_id", "reminder_type", "scheduled_for", name="uq_child_vaccine_reminder"),)

    user = db.relationship("User", back_populates="reminder_logs")
    child = db.relationship("Child", back_populates="reminder_logs")
    vaccine = db.relationship("Vaccine", back_populates="reminder_logs")


class SmsNotification(db.Model):
    __tablename__ = "sms_notifications"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    child_id = db.Column(db.Integer, db.ForeignKey("children.id"), nullable=True)
    vaccine_id = db.Column(db.Integer, db.ForeignKey("vaccines.id"), nullable=True)
    schedule_id = db.Column(db.Integer, db.ForeignKey("vaccination_schedules.id"), nullable=True)
    recipient = db.Column(db.String(20), nullable=False)
    due_date = db.Column(db.Date, nullable=True)
    message_type = db.Column(db.String(40), nullable=False)
    language = db.Column(db.String(10), default="en", nullable=False)
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(30), default="pending", nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    provider_message_id = db.Column(db.String(100), nullable=True)
    failure_reason = db.Column(db.String(500), nullable=True)
    attempt_count = db.Column(db.Integer, default=0, nullable=False)
    next_attempt_at = db.Column(db.DateTime, nullable=True)
    legacy_reminder_id = db.Column(db.Integer, unique=True, nullable=True)

    __table_args__ = (
        db.Index(
            "uq_sms_recipient_child_vaccine_type_due",
            "recipient",
            "child_id",
            "vaccine_id",
            "message_type",
            "due_date",
            unique=True,
        ),
    )

    user = db.relationship("User", back_populates="sms_notifications")
    child = db.relationship("Child")
    vaccine = db.relationship("Vaccine")
    schedule = db.relationship("VaccinationSchedule")


class EmailReminderNotification(db.Model):
    __tablename__ = "email_reminder_notifications"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    child_id = db.Column(db.Integer, db.ForeignKey("children.id"), nullable=True)
    vaccine_id = db.Column(db.Integer, db.ForeignKey("vaccines.id"), nullable=True)
    schedule_id = db.Column(db.Integer, db.ForeignKey("vaccination_schedules.id"), nullable=True)
    recipient = db.Column(db.String(120), nullable=False)
    reminder_type = db.Column(db.String(40), nullable=False)
    status = db.Column(db.String(30), default="sent", nullable=False)
    is_test = db.Column(db.Boolean, default=False, nullable=False)
    sent_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    failure_reason = db.Column(db.String(500), nullable=True)
    smtp_response = db.Column(db.String(255), nullable=True)

    child = db.relationship("Child")
    vaccine = db.relationship("Vaccine")
    schedule = db.relationship("VaccinationSchedule")


class ApplicationSetting(db.Model):
    __tablename__ = "application_settings"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(80), unique=True, nullable=False)
    value = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    EMAIL_REMINDERS_SETTING = "email_reminders_enabled"


class MobileVerificationChallenge(db.Model):
    __tablename__ = "mobile_verification_challenges"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    mobile = db.Column(db.String(20), nullable=True)
    mobile_hash = db.Column(db.String(64), nullable=False)
    code_hash = db.Column(db.String(64), nullable=False)
    status = db.Column(db.String(30), default="pending", nullable=False)
    attempt_count = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)

    user = db.relationship("User")


class FCMDeviceToken(db.Model):
    __tablename__ = "fcm_device_tokens"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    installation_id = db.Column(db.String(128), nullable=True)
    token = db.Column(db.Text, nullable=True)
    token_hash = db.Column(db.String(64), nullable=True)
    target_type = db.Column(db.String(30), default="fid", nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    last_seen_at = db.Column(db.DateTime, default=utcnow, nullable=False)


class FCMPushNotification(db.Model):
    __tablename__ = "fcm_push_notifications"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    child_id = db.Column(db.Integer, db.ForeignKey("children.id"), nullable=True)
    vaccine_id = db.Column(db.Integer, db.ForeignKey("vaccines.id"), nullable=True)
    schedule_id = db.Column(db.Integer, db.ForeignKey("vaccination_schedules.id"), nullable=True)
    status = db.Column(db.String(30), default="sent", nullable=False)
    provider_message_id = db.Column(db.String(100), nullable=True)
    failure_reason = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)


class FCMPushTestAttempt(db.Model):
    __tablename__ = "fcm_push_test_attempts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    status = db.Column(db.String(30), default="sent", nullable=False)
    provider_message_id = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
