import os
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Flask, session
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from dotenv import dotenv_values, load_dotenv
from sqlalchemy import event
from sqlalchemy.engine import Engine

load_dotenv(override=True)

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if dbapi_connection.__class__.__module__.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


from app.translations import get_translation


@login_manager.user_loader
def load_user(user_id):
    from app.models import User

    try:
        user_value, version_value = user_id.split(":", maxsplit=1)
        user = db.session.get(User, int(user_value))
        if user and user.auth_version == int(version_value):
            return user
    except (AttributeError, TypeError, ValueError):
        return None
    return None


login_manager.login_view = "main.login"
login_manager.login_message_category = "info"


def _ensure_sqlite_directory(app):
    db_uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if db_uri.startswith("sqlite:///"):
        sqlite_file_path = db_uri[len("sqlite:///"):]
        if sqlite_file_path in {":memory:", ""}:
            return
        is_abs = os.path.isabs(sqlite_file_path) or sqlite_file_path.startswith("/") or sqlite_file_path.startswith("\\")
        if not is_abs:
            os.makedirs(app.instance_path, exist_ok=True)
        else:
            import posixpath
            if sqlite_file_path.startswith("/"):
                db_dir = posixpath.dirname(sqlite_file_path)
            else:
                db_dir = os.path.dirname(sqlite_file_path)
            if db_dir and db_dir not in {"/", "\\"}:
                try:
                    os.makedirs(db_dir, exist_ok=True)
                except OSError:
                    pass


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    environment = os.getenv("FLASK_ENV", "development").lower()
    secret_key = os.getenv("SECRET_KEY")
    app.config["SECRET_KEY"] = secret_key or "vaxicare-demo-secret"

    raw_db_url = os.getenv("DATABASE_URL", "sqlite:///vaxicare.db")
    if raw_db_url.startswith("postgres://"):
        raw_db_url = raw_db_url.replace("postgres://", "postgresql://", 1)

    app.config["SQLALCHEMY_DATABASE_URI"] = raw_db_url
    if raw_db_url.startswith("postgresql://"):
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
            "pool_pre_ping": True,
            "pool_recycle": 300,
            "pool_size": 5,
            "max_overflow": 2,
            "pool_timeout": 30,
        }
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    _ensure_sqlite_directory(app)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = environment == "production"
    app.config["REMEMBER_COOKIE_HTTPONLY"] = True
    app.config["REMEMBER_COOKIE_SAMESITE"] = "Lax"
    app.config["REMEMBER_COOKIE_SECURE"] = environment == "production"
    app.config["PASSWORD_RESET_TTL_SECONDS"] = int(os.getenv("PASSWORD_RESET_TTL_SECONDS", "1800"))
    mail_server = os.getenv("MAIL_SERVER") or os.getenv("SMTP_HOST")
    mail_port_str = os.getenv("MAIL_PORT") or os.getenv("SMTP_PORT") or "587"
    try:
        mail_port = int(mail_port_str)
    except (ValueError, TypeError):
        mail_port = 587
    mail_username = os.getenv("MAIL_USERNAME") or os.getenv("SMTP_USERNAME")
    mail_password = os.getenv("MAIL_PASSWORD") or os.getenv("SMTP_PASSWORD")
    mail_default_sender = os.getenv("MAIL_DEFAULT_SENDER") or os.getenv("SMTP_FROM")
    mail_use_tls_str = os.getenv("MAIL_USE_TLS") or os.getenv("SMTP_USE_TLS") or "true"
    mail_use_tls = mail_use_tls_str.lower() in {"true", "1", "yes", "on"}
    mail_enabled_str = os.getenv("MAIL_ENABLED", "true")
    mail_enabled = mail_enabled_str.lower() in {"true", "1", "yes", "on"}

    if mail_password and ("gmail" in (mail_server or "").lower() or len(mail_password.replace(" ", "")) == 16):
        mail_password = mail_password.replace(" ", "").strip()

    app.config["MAIL_SERVER"] = mail_server
    app.config["MAIL_PORT"] = mail_port
    app.config["MAIL_USERNAME"] = mail_username
    app.config["MAIL_PASSWORD"] = mail_password
    app.config["MAIL_DEFAULT_SENDER"] = mail_default_sender
    app.config["MAIL_USE_TLS"] = mail_use_tls
    app.config["MAIL_ENABLED"] = mail_enabled

    app.config["SMTP_HOST"] = mail_server
    app.config["SMTP_PORT"] = mail_port
    app.config["SMTP_USERNAME"] = mail_username
    app.config["SMTP_PASSWORD"] = mail_password
    app.config["SMTP_FROM"] = mail_default_sender
    app.config["SMTP_USE_TLS"] = mail_use_tls
    app.config["REMINDER_TIMEZONE"] = os.getenv("REMINDER_TIMEZONE", "Asia/Kolkata")
    app.config["REMINDER_DAYS_BEFORE"] = int(os.getenv("REMINDER_DAYS_BEFORE", "3"))
    app.config["REMINDER_OVERDUE_ENABLED"] = os.getenv("REMINDER_OVERDUE_ENABLED", "false").lower() == "true"
    app.config["REMINDER_OVERDUE_AFTER_DAYS"] = int(os.getenv("REMINDER_OVERDUE_AFTER_DAYS", "7"))
    app.config["SMS_MAX_ATTEMPTS"] = int(os.getenv("SMS_MAX_ATTEMPTS", "3"))
    app.config["SMS_RETRY_MINUTES"] = int(os.getenv("SMS_RETRY_MINUTES", "15"))
    app.config["SMS_FORCE_DEMO"] = os.getenv("SMS_FORCE_DEMO", "false").lower() in {"true", "1", "yes", "on"}
    app.config["FCM_ENABLED"] = os.getenv("FCM_ENABLED", "false").lower() == "true"
    app.config["FIREBASE_PROJECT_ID"] = os.getenv("FIREBASE_PROJECT_ID")
    app.config["FIREBASE_SERVICE_ACCOUNT_PATH"] = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH") or os.getenv("FIREBASE_CREDENTIALS_PATH")
    app.config["FIREBASE_VAPID_PUBLIC_KEY"] = os.getenv("FIREBASE_VAPID_PUBLIC_KEY") or os.getenv("FIREBASE_VAPID_KEY")
    app.config["FIREBASE_API_KEY"] = os.getenv("FIREBASE_API_KEY", "")
    app.config["FIREBASE_AUTH_DOMAIN"] = os.getenv("FIREBASE_AUTH_DOMAIN", "")
    app.config["FIREBASE_STORAGE_BUCKET"] = os.getenv("FIREBASE_STORAGE_BUCKET", "")
    app.config["FIREBASE_MESSAGING_SENDER_ID"] = os.getenv("FIREBASE_MESSAGING_SENDER_ID", "")
    app.config["FIREBASE_APP_ID"] = os.getenv("FIREBASE_APP_ID", "")
    app.config["ANDROID_SMS_GATEWAY_ENABLED"] = os.getenv("ANDROID_SMS_GATEWAY_ENABLED", "false").lower() == "true"
    _env_vals = dotenv_values()
    twilio_sid = os.getenv("TWILIO_ACCOUNT_SID") or _env_vals.get("TWILIO_ACCOUNT_SID")
    twilio_token = os.getenv("TWILIO_AUTH_TOKEN") or _env_vals.get("TWILIO_AUTH_TOKEN")
    twilio_phone = os.getenv("TWILIO_PHONE_NUMBER") or _env_vals.get("TWILIO_PHONE_NUMBER")
    app.config["TWILIO_ACCOUNT_SID"] = twilio_sid.strip() if twilio_sid and twilio_sid.strip() else None
    app.config["TWILIO_AUTH_TOKEN"] = twilio_token.strip() if twilio_token and twilio_token.strip() else None
    app.config["TWILIO_PHONE_NUMBER"] = twilio_phone.strip() if twilio_phone and twilio_phone.strip() else None
    app.config["SEED_DEMO_DATA"] = os.getenv(
        "SEED_DEMO_DATA",
        "false" if environment == "production" else "true",
    ).lower() == "true"

    if test_config:
        app.config.update(test_config)
        _ensure_sqlite_directory(app)
        for mail_k, smtp_k in [
            ("MAIL_SERVER", "SMTP_HOST"),
            ("MAIL_PORT", "SMTP_PORT"),
            ("MAIL_USERNAME", "SMTP_USERNAME"),
            ("MAIL_PASSWORD", "SMTP_PASSWORD"),
            ("MAIL_DEFAULT_SENDER", "SMTP_FROM"),
            ("MAIL_USE_TLS", "SMTP_USE_TLS"),
        ]:
            if mail_k in test_config and smtp_k not in test_config:
                app.config[smtp_k] = test_config[mail_k]
            elif smtp_k in test_config and mail_k not in test_config:
                app.config[mail_k] = test_config[smtp_k]

    if (
        environment == "production"
        and not secret_key
        and not app.config.get("TESTING")
    ):
        raise RuntimeError("SECRET_KEY must be configured in production.")
    if environment == "production" and app.config.get("SMS_FORCE_DEMO") and not app.config.get("TESTING"):
        raise RuntimeError(
            "Configuration Error: SMS_FORCE_DEMO=true is enabled while FLASK_ENV=production. "
            "SMS Demo Mode is strictly prohibited in production to prevent simulated messaging. "
            "Set SMS_FORCE_DEMO=false or configure live SMS provider credentials."
        )
    try:
        ZoneInfo(app.config["REMINDER_TIMEZONE"])
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError("REMINDER_TIMEZONE must be a valid IANA timezone.") from exc
    if app.config["REMINDER_DAYS_BEFORE"] < 0:
        raise ValueError("REMINDER_DAYS_BEFORE cannot be negative.")
    if app.config["REMINDER_OVERDUE_AFTER_DAYS"] < 1:
        raise ValueError("REMINDER_OVERDUE_AFTER_DAYS must be at least 1.")
    if app.config["SMS_MAX_ATTEMPTS"] < 1 or app.config["SMS_RETRY_MINUTES"] < 1:
        raise ValueError("SMS retry attempts and retry minutes must be positive.")
    if app.config["PASSWORD_RESET_TTL_SECONDS"] < 60:
        raise ValueError("PASSWORD_RESET_TTL_SECONDS must be at least 60.")

    if app.config.get("TESTING"):
        if not test_config or "SMS_FORCE_DEMO" not in test_config:
            app.config["SMS_FORCE_DEMO"] = True
        if test_config and "SEED_DEMO_DATA" not in test_config:
            app.config["SEED_DEMO_DATA"] = True

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    from app.routes import main

    app.register_blueprint(main)

    @app.before_request
    def set_language():
        if "language" not in session:
            session["language"] = "en"

    @app.context_processor
    def inject_globals():
        def get_unread_count():
            try:
                from flask_login import current_user
                if current_user and current_user.is_authenticated:
                    from app.models import Notification
                    return Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
            except Exception:
                pass
            return 0

        return {
            "t": lambda key, **kwargs: get_translation(key, session.get("language", "en"), **kwargs),
            "current_language": session.get("language", "en"),
            "current_year": datetime.now(UTC).year,
            "unread_count": get_unread_count,
        }

    with app.app_context():
        from app.seed_data import seed_demo_data

        db.create_all()
        from app.migrations import upgrade_existing_database

        upgrade_existing_database()
        if app.config["SEED_DEMO_DATA"]:
            seed_demo_data()

    return app
