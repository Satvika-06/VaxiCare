import os
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Flask, session
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from dotenv import load_dotenv

load_dotenv()

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()

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


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    environment = os.getenv("FLASK_ENV", "development").lower()
    secret_key = os.getenv("SECRET_KEY")
    app.config["SECRET_KEY"] = secret_key or "vaxicare-demo-secret"
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", "sqlite:///vaxicare.db")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = environment == "production"
    app.config["REMEMBER_COOKIE_HTTPONLY"] = True
    app.config["REMEMBER_COOKIE_SAMESITE"] = "Lax"
    app.config["REMEMBER_COOKIE_SECURE"] = environment == "production"
    app.config["PASSWORD_RESET_TTL_SECONDS"] = int(os.getenv("PASSWORD_RESET_TTL_SECONDS", "1800"))
    app.config["SMTP_HOST"] = os.getenv("SMTP_HOST")
    app.config["SMTP_PORT"] = int(os.getenv("SMTP_PORT", "587"))
    app.config["SMTP_USERNAME"] = os.getenv("SMTP_USERNAME")
    app.config["SMTP_PASSWORD"] = os.getenv("SMTP_PASSWORD")
    app.config["SMTP_FROM"] = os.getenv("SMTP_FROM")
    app.config["SMTP_USE_TLS"] = os.getenv("SMTP_USE_TLS", "true").lower() == "true"
    app.config["REMINDER_TIMEZONE"] = os.getenv("REMINDER_TIMEZONE", "Asia/Kolkata")
    app.config["REMINDER_DAYS_BEFORE"] = int(os.getenv("REMINDER_DAYS_BEFORE", "3"))
    app.config["REMINDER_OVERDUE_ENABLED"] = os.getenv("REMINDER_OVERDUE_ENABLED", "false").lower() == "true"
    app.config["REMINDER_OVERDUE_AFTER_DAYS"] = int(os.getenv("REMINDER_OVERDUE_AFTER_DAYS", "7"))
    app.config["SMS_MAX_ATTEMPTS"] = int(os.getenv("SMS_MAX_ATTEMPTS", "3"))
    app.config["SMS_RETRY_MINUTES"] = int(os.getenv("SMS_RETRY_MINUTES", "15"))
    app.config["SMS_FORCE_DEMO"] = os.getenv("SMS_FORCE_DEMO", "false").lower() == "true"
    app.config["SEED_DEMO_DATA"] = os.getenv(
        "SEED_DEMO_DATA",
        "false" if environment == "production" else "true",
    ).lower() == "true"

    if test_config:
        app.config.update(test_config)

    if (
        environment == "production"
        and not secret_key
        and not app.config.get("TESTING")
    ):
        raise RuntimeError("SECRET_KEY must be configured in production.")
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
        return {
            "t": lambda key, **kwargs: get_translation(key, session.get("language", "en"), **kwargs),
            "current_language": session.get("language", "en"),
            "current_year": datetime.now(UTC).year,
        }

    with app.app_context():
        from app.seed_data import seed_demo_data

        db.create_all()
        from app.migrations import upgrade_existing_database

        upgrade_existing_database()
        if app.config["SEED_DEMO_DATA"]:
            seed_demo_data()

    return app
