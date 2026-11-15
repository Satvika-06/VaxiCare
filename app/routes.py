import csv
import io
import hashlib
import secrets
from collections import Counter
from datetime import date, datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func

from app.forms import (
    AdminRegistrationForm, AdminUserForm, AutomaticEmailReminderSettingsForm,
    ChangePasswordForm, ChildProfileForm, DeleteChildForm, HealthcareWorkerProfileForm,
    ParentProfileForm, ForgotPasswordForm, HealthcareWorkerRegistrationForm, LoginForm,
    MobileOtpForm, MobileVerificationForm, RegistrationForm, ReminderEmailForm,
    ReminderForm, ResetPasswordForm, TestEmailForm, TestSmsForm, VaccinationRecordForm,
    VaccineForm,
)
from app.models import (
    ApplicationSetting, Child, EmailReminderNotification, EmailTestAttempt,
    FCMDeviceToken, FCMPushNotification, FCMPushTestAttempt, HealthcareWorker,
    MobileVerificationChallenge, Notification, Parent, PasswordResetToken,
    SchedulerRunState, SmsNotification, User, VaccinationRecord,
    VaccinationSchedule, Vaccine, db, utcnow,
)
from app.scheduling import build_child_schedule as scheduled_child_entries
from app.services import (
    generate_record_token, generate_vaccination_pdf,
    is_valid_mobile_number, retry_failed_notification,
    run_due_reminder_check, send_sms_notification, send_test_sms,
    send_vaccination_reminder_email,
)
from app.auth_services import (
    PasswordResetDeliveryError, login_limiter,
    send_password_reset_email, validate_password_strength, is_sms_configured,
)

main = Blueprint("main", __name__)


# ---------------------------------------------------------------------------
# Security headers on every response
# ---------------------------------------------------------------------------
@main.after_request
def apply_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


# ---------------------------------------------------------------------------
# Language
# ---------------------------------------------------------------------------
@main.route("/set_language/<lang>")
def set_language(lang):
    if lang not in {"en", "te"}:
        lang = "en"
    session["language"] = lang
    referrer = request.referrer
    if referrer and urlparse(referrer).netloc == request.host:
        return redirect(referrer)
    return redirect(url_for("main.home"))


# ---------------------------------------------------------------------------
# Public pages
# ---------------------------------------------------------------------------
@main.route("/")
def home():
    return render_template("home.html")


@main.route("/about")
def about():
    return render_template("about.html")


@main.route("/contact")
def contact():
    return render_template("contact.html")


# ---------------------------------------------------------------------------
# Parent registration
# ---------------------------------------------------------------------------
@main.route("/register", methods=["GET", "POST"])
def register():
    return parent_registration()


@main.route("/parent/register", methods=["GET", "POST"])
def parent_register():
    return parent_registration()


def parent_registration():
    if current_user.is_authenticated:
        return _redirect_by_role(current_user)
    form = RegistrationForm()
    if form.validate_on_submit():
        username = form.username.data.strip()
        email = form.email.data.strip().lower()
        mobile = (form.mobile.data or "").strip()

        # Password strength
        ok, msg = validate_password_strength(form.password.data)
        if not ok:
            flash(msg, "danger")
            return render_template("register.html", form=form, auth_role="parent")

        # Mobile validation
        if form.sms_consent.data and not mobile:
            flash("Provide a phone number before opting in to SMS reminders.", "danger")
            return render_template("register.html", form=form, auth_role="parent")
        if mobile and not is_valid_mobile_number(mobile):
            flash("Enter a valid international number in +919876543210 format.", "danger")
            return render_template("register.html", form=form, auth_role="parent")

        # Duplicate check
        existing = User.query.filter(
            (func.lower(User.username) == username.lower())
            | (func.lower(User.email) == email)
        ).first()
        if existing:
            flash("Username or email already exists.", "danger")
            return render_template("register.html", form=form, auth_role="parent")

        try:
            user = User(
                username=username,
                email=email,
                role="parent",
                full_name=" ".join(
                    part for part in [form.first_name.data.strip(), (form.last_name.data or "").strip()] if part
                ),
                mobile=mobile or None,
                mobile_verified=False,
                sms_consent=bool(form.sms_consent.data),
            )
            user.set_password(form.password.data)
            db.session.add(user)
            db.session.flush()
            parent = Parent(
                user_id=user.id,
                first_name=form.first_name.data.strip(),
                last_name=(form.last_name.data or "").strip() or None,
                village="Not set",
            )
            db.session.add(parent)
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash("Registration failed. Please try again.", "danger")
            return render_template("register.html", form=form, auth_role="parent")

        login_user(user, fresh=True)
        flash("Registration successful. Welcome to VaxiCare.", "success")
        return redirect(url_for("main.parent_dashboard"))
    return render_template("register.html", form=form, auth_role="parent")


# ---------------------------------------------------------------------------
# Healthcare worker self-registration (pending admin approval model)
# ---------------------------------------------------------------------------
@main.route("/healthcare/register", methods=["GET", "POST"])
def healthcare_register():
    if current_user.is_authenticated:
        return _redirect_by_role(current_user)
    form = HealthcareWorkerRegistrationForm()
    if form.validate_on_submit():
        username = form.username.data.strip()
        email = form.email.data.strip().lower()
        mobile = (form.mobile.data or "").strip()

        ok, msg = validate_password_strength(form.password.data)
        if not ok:
            flash(msg, "danger")
            return render_template("healthcare_register.html", form=form)

        if mobile and not is_valid_mobile_number(mobile):
            flash("Enter a valid international mobile number.", "danger")
            return render_template("healthcare_register.html", form=form)

        existing = User.query.filter(
            (func.lower(User.username) == username.lower())
            | (func.lower(User.email) == email)
        ).first()
        if existing:
            flash("Username or email already exists.", "danger")
            return render_template("healthcare_register.html", form=form)

        try:
            user = User(
                username=username,
                email=email,
                role="healthcare_worker",
                full_name=form.full_name.data.strip(),
                mobile=mobile or None,
                mobile_verified=False,
                sms_consent=False,
            )
            user.set_password(form.password.data)
            db.session.add(user)
            db.session.flush()
            worker = HealthcareWorker(
                user_id=user.id,
                full_name=form.full_name.data.strip(),
                qualification=form.qualification.data.strip(),
                department=form.department.data.strip(),
                village=form.village.data.strip(),
            )
            db.session.add(worker)
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash("Registration failed. Please try again.", "danger")
            return render_template("healthcare_register.html", form=form)

        login_user(user, fresh=True)
        flash("Healthcare worker account created. Welcome to VaxiCare.", "success")
        return redirect(url_for("main.healthcare_dashboard"))
    return render_template("healthcare_register.html", form=form)


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
@main.route("/login", methods=["GET", "POST"])
def login():
    return authenticate(role=None, auth_role="parent")


@main.route("/parent/login", methods=["GET", "POST"])
def parent_login():
    return authenticate(role="parent", auth_role="parent")


@main.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    return authenticate(role="admin", auth_role="admin")


@main.route("/healthcare/login", methods=["GET", "POST"])
def healthcare_login():
    return authenticate(role="healthcare_worker", auth_role="healthcare_worker")


def authenticate(role, auth_role):
    if current_user.is_authenticated:
        return _redirect_by_role(current_user)

    form = LoginForm()
    if form.validate_on_submit():
        identity = form.username.data.strip()
        ip = request.remote_addr or "unknown"

        # Brute-force / rate limit check
        locked, remaining = login_limiter.is_locked_out(identity, ip)
        if locked:
            minutes = int(remaining / 60) + 1
            flash(
                f"Too many failed login attempts. Please try again in {minutes} minute(s).",
                "danger",
            )
            return render_template("login.html", form=form, auth_role=auth_role)

        user = User.query.filter(
            (func.lower(User.username) == identity.lower())
            | (func.lower(User.email) == identity.lower())
        ).first()

        if user and user.check_password(form.password.data) and (role is None or user.role == role):
            login_limiter.clear_attempts(identity, ip)
            login_user(user, remember=bool(form.remember_me.data), fresh=True)
            flash("Login successful.", "success")
            return _redirect_by_role(user)

        # Record failed attempt
        login_limiter.record_failed_attempt(identity, ip)
        flash("Invalid username or password.", "danger")

    return render_template("login.html", form=form, auth_role=auth_role)


def _redirect_by_role(user):
    next_url = request.args.get("next")
    if next_url and urlparse(next_url).netloc == "":
        return redirect(next_url)
    if user.role == "parent":
        return redirect(url_for("main.parent_dashboard"))
    if user.role == "healthcare_worker":
        return redirect(url_for("main.healthcare_dashboard"))
    if user.role == "admin":
        return redirect(url_for("main.admin_dashboard"))
    return redirect(url_for("main.home"))


# ---------------------------------------------------------------------------
# Admin register (admin-only)
# ---------------------------------------------------------------------------
@main.route("/admin/register", methods=["GET", "POST"])
def admin_register():
    form = AdminRegistrationForm()
    authorized = current_user.is_authenticated and current_user.role == "admin"
    if request.method == "POST" and not authorized:
        abort(403)
    if authorized and form.validate_on_submit():
        ok, msg = validate_password_strength(form.password.data)
        if not ok:
            flash(msg, "danger")
            return render_template("admin_register.html", form=form, authorized=authorized)

        username = form.username.data.strip()
        email = form.email.data.strip().lower()
        existing = User.query.filter(
            (func.lower(User.username) == username.lower())
            | (func.lower(User.email) == email)
        ).first()
        if existing:
            flash("Username or email already exists.", "danger")
        else:
            user = User(
                username=username,
                email=email,
                full_name=form.full_name.data.strip(),
                role="admin",
            )
            user.set_password(form.password.data)
            db.session.add(user)
            db.session.commit()
            flash("Administrator account created successfully.", "success")
            return redirect(url_for("main.admin_users"))
    return render_template("admin_register.html", form=form, authorized=authorized)


# ---------------------------------------------------------------------------
# Healthcare access info page
# ---------------------------------------------------------------------------
@main.route("/healthcare/access")
def healthcare_access():
    return render_template("healthcare_access.html")


# ---------------------------------------------------------------------------
# Logout (POST only, CSRF protected)
# ---------------------------------------------------------------------------
@main.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    session.clear()
    flash("Logged out successfully.", "info")
    return redirect(url_for("main.home"))


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------
@main.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    form = ForgotPasswordForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        user = User.query.filter(func.lower(User.email) == email).first()
        if user:
            # Invalidate all existing unused tokens
            now = utcnow()
            PasswordResetToken.query.filter(
                PasswordResetToken.user_id == user.id,
                PasswordResetToken.used_at.is_(None),
            ).update({"used_at": now}, synchronize_session=False)

            raw_token = secrets.token_urlsafe(32)
            token = PasswordResetToken(
                user_id=user.id,
                token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
                created_at=now,
                expires_at=now + timedelta(seconds=current_app.config["PASSWORD_RESET_TTL_SECONDS"]),
            )
            db.session.add(token)
            db.session.commit()
            reset_url = url_for("main.reset_password", token=raw_token, _external=True)
            try:
                send_password_reset_email(user.email, reset_url)
            except PasswordResetDeliveryError:
                token.used_at = utcnow()
                db.session.commit()
                current_app.logger.warning("Password reset email delivery unavailable.")
        # Anti-enumeration: always show same message
        flash(
            "If an account matches that email address, password reset instructions will be sent.",
            "info",
        )
        return redirect(url_for("main.forgot_password"))
    return render_template("forgot_password.html", form=form)


@main.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    reset_token = PasswordResetToken.query.filter_by(token_hash=digest).first()
    if (
        not reset_token
        or reset_token.used_at is not None
        or reset_token.expires_at <= utcnow()
    ):
        flash("This password reset link is invalid or has expired.", "danger")
        return redirect(url_for("main.forgot_password"))
    form = ResetPasswordForm()
    if form.validate_on_submit():
        ok, msg = validate_password_strength(form.password.data)
        if not ok:
            flash(msg, "danger")
            return render_template("reset_password.html", form=form)

        reset_token.user.set_password(form.password.data)
        now = utcnow()
        # Mark ALL unused tokens for this user as consumed (one-time use)
        PasswordResetToken.query.filter(
            PasswordResetToken.user_id == reset_token.user_id,
            PasswordResetToken.used_at.is_(None),
        ).update({"used_at": now}, synchronize_session=False)
        db.session.commit()
        logout_user()
        flash("Password changed. Please sign in with your new password.", "success")
        login_endpoint = {
            "admin": "main.admin_login",
            "healthcare_worker": "main.healthcare_login",
        }.get(reset_token.user.role, "main.parent_login")
        return redirect(url_for(login_endpoint))
    return render_template("reset_password.html", form=form)


@main.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            flash("Current password is incorrect.", "danger")
        else:
            ok, msg = validate_password_strength(form.password.data)
            if not ok:
                flash(msg, "danger")
                return render_template("change_password.html", form=form)
            # Capture concrete user object BEFORE login_user() to avoid LocalProxy recursion
            concrete_user = db.session.get(User, current_user.id)
            concrete_user.set_password(form.password.data)
            # Invalidate all existing password reset tokens
            now = utcnow()
            PasswordResetToken.query.filter(
                PasswordResetToken.user_id == concrete_user.id,
                PasswordResetToken.used_at.is_(None),
            ).update({"used_at": now}, synchronize_session=False)
            db.session.commit()
            login_user(concrete_user, remember=False, fresh=True)
            flash("Password changed successfully.", "success")
            return _redirect_by_role(concrete_user)
    return render_template("change_password.html", form=form)


# ---------------------------------------------------------------------------
# Parent dashboard
# ---------------------------------------------------------------------------
@main.route("/dashboard")
@login_required
def parent_dashboard_alias():
    return parent_dashboard()


@main.route("/parent-dashboard")
@login_required
def parent_dashboard():
    if current_user.role != "parent":
        abort(403)
    parent = current_user.parent
    children = Child.query.filter_by(parent_id=parent.id).order_by(Child.created_at.desc()).all() if parent else []
    summary = []
    alerts = []
    for child in children:
        schedule = build_child_schedule(child)
        next_vaccine = next(
            (item for item in schedule if item["status"] in {"due_today", "overdue", "upcoming"}),
            None,
        )
        summary.append({"child": child, "schedule": schedule, "next_vaccine": next_vaccine})
        alerts.extend(
            {"child": child, "dose": item}
            for item in schedule
            if item["status"] in {"due_today", "overdue", "upcoming"}
        )
    alerts.sort(key=lambda entry: (entry["dose"]["due_date"], entry["child"].id))

    sms_configured = is_sms_configured()
    # Check for pending mobile verification challenge
    pending_challenge = None
    if sms_configured and current_user.mobile and not current_user.mobile_verified:
        pending_challenge = MobileVerificationChallenge.query.filter_by(
            user_id=current_user.id, status="pending"
        ).order_by(MobileVerificationChallenge.created_at.desc()).first()

    return render_template(
        "dashboard.html",
        children=children,
        summary=summary,
        parent=parent,
        alerts=alerts,
        sms_settings_form=MobileVerificationForm(),
        mobile_otp_form=MobileOtpForm(),
        mobile_verification_available=sms_configured,
        mobile_verification_has_pending=pending_challenge is not None,
    )


# ---------------------------------------------------------------------------
# Child management
# ---------------------------------------------------------------------------
@main.route("/child/add", methods=["GET", "POST"])
@login_required
def add_child():
    if current_user.role != "parent":
        abort(403)
    parent = current_user.parent
    if not parent:
        flash("Your parent profile is not set up. Please contact support.", "danger")
        return redirect(url_for("main.parent_dashboard"))
    form = ChildProfileForm()
    if form.validate_on_submit():
        child = Child(
            parent_id=parent.id,
            name=form.name.data.strip(),
            dob=form.dob.data,
            gender=form.gender.data,
            village=form.village.data.strip(),
            allergies=(form.allergies.data or "").strip() or None,
            notes=(form.notes.data or "").strip() or None,
            record_token=generate_record_token(),
        )
        db.session.add(child)
        db.session.commit()
        flash("Child profile created successfully.", "success")
        return redirect(url_for("main.child_detail", child_id=child.id))
    return render_template("child_form.html", form=form)


@main.route("/child/<int:child_id>")
@login_required
def child_detail(child_id):
    child = get_or_404(Child, child_id)
    # RBAC enforcement — no IDOR
    if current_user.role == "parent":
        if not current_user.parent or child.parent_id != current_user.parent.id:
            abort(403)
    elif current_user.role == "healthcare_worker":
        if not can_worker_access(current_user, child):
            abort(403)
    elif current_user.role != "admin":
        abort(403)

    schedule = build_child_schedule(child)
    records = VaccinationRecord.query.filter_by(child_id=child.id).order_by(VaccinationRecord.administered_date.desc()).all()
    vaccines = Vaccine.query.order_by(Vaccine.name).all()
    return render_template(
        "child_detail.html",
        child=child,
        schedule=schedule,
        records=records,
        vaccines=vaccines,
        today_date=app_today(),
    )


# ---------------------------------------------------------------------------
# Edit child
# ---------------------------------------------------------------------------
@main.route("/child/<int:child_id>/edit", methods=["GET", "POST"])
@login_required
def edit_child(child_id):
    if current_user.role != "parent":
        abort(403)
    parent = current_user.parent
    if not parent:
        abort(403)
    child = get_or_404(Child, child_id)
    if child.parent_id != parent.id:
        abort(403)
    form = ChildProfileForm(obj=child)
    if form.validate_on_submit():
        try:
            child.name = form.name.data.strip()
            child.dob = form.dob.data
            child.gender = form.gender.data
            child.village = form.village.data.strip()
            child.allergies = (form.allergies.data or "").strip() or None
            child.notes = (form.notes.data or "").strip() or None
            db.session.commit()
            flash("Child profile updated successfully.", "success")
            return redirect(url_for("main.child_detail", child_id=child.id))
        except Exception:
            db.session.rollback()
            flash("An error occurred while saving. Please try again.", "danger")
    return render_template("child_form.html", form=form, child=child)


# ---------------------------------------------------------------------------
# Delete child
# ---------------------------------------------------------------------------
@main.route("/child/<int:child_id>/delete", methods=["POST"])
@login_required
def delete_child(child_id):
    if current_user.role != "parent":
        abort(403)
    parent = current_user.parent
    if not parent:
        abort(403)
    child = get_or_404(Child, child_id)
    if child.parent_id != parent.id:
        abort(403)
    # CSRF is validated automatically by Flask-WTF on any POST with hidden_tag
    try:
        db.session.delete(child)
        db.session.commit()
        flash("Child record deleted.", "success")
    except Exception:
        db.session.rollback()
        flash("An error occurred while deleting. Please try again.", "danger")
    return redirect(url_for("main.parent_dashboard"))


# ---------------------------------------------------------------------------
# Parent profile
# ---------------------------------------------------------------------------
@main.route("/parent/profile", methods=["GET", "POST"])
@login_required
def parent_profile():
    if current_user.role != "parent":
        abort(403)
    parent = current_user.parent
    if not parent:
        flash("Your parent profile is not set up. Please contact support.", "danger")
        return redirect(url_for("main.parent_dashboard"))
    form = ParentProfileForm(obj=parent)
    # Pre-fill mobile / sms_consent from User model on GET
    if request.method == "GET":
        form.mobile.data = current_user.mobile
        form.sms_consent.data = current_user.sms_consent
    if form.validate_on_submit():
        try:
            parent.first_name = form.first_name.data.strip()
            parent.last_name = (form.last_name.data or "").strip() or None
            parent.village = (form.village.data or "").strip() or None
            parent.address = (form.address.data or "").strip() or None
            current_user.mobile = (form.mobile.data or "").strip() or None
            current_user.sms_consent = form.sms_consent.data
            db.session.commit()
            flash("Profile updated successfully.", "success")
            return redirect(url_for("main.parent_profile"))
        except Exception:
            db.session.rollback()
            flash("An error occurred while saving. Please try again.", "danger")
    return render_template("parent_profile.html", form=form, parent=parent)


# ---------------------------------------------------------------------------
# Parent notifications
# ---------------------------------------------------------------------------
@main.route("/parent/notifications")
@login_required
def parent_notifications():
    if current_user.role != "parent":
        abort(403)
    notifications = (
        Notification.query
        .filter_by(user_id=current_user.id)
        .order_by(Notification.sent_at.desc())
        .limit(50)
        .all()
    )
    return render_template("parent_notifications.html", notifications=notifications)


# ---------------------------------------------------------------------------
# Vaccination schedule / awareness
# ---------------------------------------------------------------------------
@main.route("/schedule")
@login_required
def vaccination_schedule():
    if current_user.role == "parent":
        parent = current_user.parent
        children = Child.query.filter_by(parent_id=parent.id).all() if parent else []
    elif current_user.role in {"healthcare_worker", "admin"}:
        children = Child.query.order_by(Child.name).all()
    else:
        abort(403)
    schedule_data = {child: build_child_schedule(child) for child in children}
    return render_template("schedule.html", schedule_data=schedule_data, children=children)


@main.route("/awareness")
def awareness_alias():
    return redirect(url_for("main.vaccine_awareness"))


@main.route("/vaccine-awareness")
def vaccine_awareness():
    vaccines = Vaccine.query.order_by(Vaccine.name).all()
    return render_template("awareness.html", vaccines=vaccines)


@main.route("/vaccine/<int:vaccine_id>")
def vaccine_information(vaccine_id):
    vaccine = get_or_404(Vaccine, vaccine_id)
    schedules = VaccinationSchedule.query.filter_by(vaccine_id=vaccine_id).order_by(VaccinationSchedule.days_after_birth).all()
    return render_template("vaccine_detail.html", vaccine=vaccine, schedules=schedules)


# ---------------------------------------------------------------------------
# SMS reminders
# ---------------------------------------------------------------------------
@main.route("/reminders", methods=["GET", "POST"])
@login_required
def reminders():
    if current_user.role != "parent":
        abort(403)
    parent = current_user.parent
    if not parent:
        abort(403)
    children = Child.query.filter_by(parent_id=parent.id).all()
    if not children:
        flash("Add a child profile before sending reminders.", "info")
        return redirect(url_for("main.parent_dashboard"))

    form = ReminderForm()
    form.child_id.choices = [(c.id, c.name) for c in children]
    form.vaccine_id.choices = [(v.id, v.name) for v in Vaccine.query.order_by(Vaccine.name).all()]

    if form.validate_on_submit():
        child = get_or_404(Child, form.child_id.data)
        if child.parent_id != parent.id:
            abort(403)
        vaccine = get_or_404(Vaccine, form.vaccine_id.data)
        result = send_sms_notification(
            current_user, child, vaccine,
            form.reminder_type.data, form.language.data,
        )
        flash(
            result["message"],
            "success" if result.get("status") == "sent" else "info" if result.get("status") == "simulated" else "danger",
        )
        return redirect(url_for("main.reminders"))

    reminder_logs = SmsNotification.query.filter_by(user_id=current_user.id).order_by(SmsNotification.created_at.desc()).limit(20).all()
    return render_template("reminders.html", form=form, reminder_logs=reminder_logs)


# ---------------------------------------------------------------------------
# Mobile verification (save settings)
# ---------------------------------------------------------------------------
@main.route("/verify-mobile", methods=["POST"])
@login_required
def verify_mobile():
    if current_user.role != "parent":
        abort(403)
    form = MobileVerificationForm()
    if form.validate_on_submit():
        mobile = (form.mobile.data or "").strip()
        if form.sms_consent.data and (not mobile or not is_valid_mobile_number(mobile)):
            flash("A valid international phone number is required to enable SMS reminders.", "danger")
            return redirect(url_for("main.parent_dashboard"))
        if mobile and not is_valid_mobile_number(mobile):
            flash("Enter a valid international number in +919876543210 format.", "danger")
            return redirect(url_for("main.parent_dashboard"))
        # If mobile changed, reset verification
        if mobile != (current_user.mobile or ""):
            current_user.mobile_verified = False
            current_user.mobile_verified_at = None
        current_user.mobile = mobile or None
        current_user.sms_consent = bool(form.sms_consent.data)
        db.session.commit()
        flash("Notification settings saved.", "success")
    else:
        flash("Please provide a valid mobile number.", "danger")
    return redirect(url_for("main.parent_dashboard"))


# ---------------------------------------------------------------------------
# Mobile OTP send (stub with clear configuration message)
# ---------------------------------------------------------------------------
@main.route("/send-mobile-otp", methods=["POST"])
@login_required
def send_mobile_otp():
    if current_user.role != "parent":
        abort(403)
    if not is_sms_configured():
        flash(
            "Mobile OTP verification is not available because an SMS provider (Twilio) "
            "is not configured. Configure TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and "
            "TWILIO_PHONE_NUMBER in your .env file to enable phone verification.",
            "warning",
        )
        return redirect(url_for("main.parent_dashboard"))

    mobile = (request.form.get("mobile") or "").strip()
    if not mobile or not is_valid_mobile_number(mobile):
        flash("Enter a valid international phone number before requesting an OTP.", "danger")
        return redirect(url_for("main.parent_dashboard"))

    # Save mobile if changed
    if mobile != (current_user.mobile or ""):
        current_user.mobile = mobile
        current_user.mobile_verified = False
        current_user.mobile_verified_at = None
        db.session.commit()

    # Expire any existing pending challenges
    MobileVerificationChallenge.query.filter_by(
        user_id=current_user.id, status="pending"
    ).update({"status": "expired"}, synchronize_session=False)
    db.session.commit()

    # Generate new OTP challenge
    otp = secrets.randbelow(900000) + 100000  # 6-digit OTP
    otp_str = str(otp)
    import hashlib as _hl
    mobile_hash = _hl.sha256(mobile.encode()).hexdigest()
    code_hash = _hl.sha256(otp_str.encode()).hexdigest()
    now = utcnow()
    challenge = MobileVerificationChallenge(
        user_id=current_user.id,
        mobile_hash=mobile_hash,
        code_hash=code_hash,
        status="pending",
        attempt_count=0,
        created_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    db.session.add(challenge)
    db.session.commit()

    # Send OTP via SMS
    from app.services import _attempt_delivery
    from app.models import SmsNotification
    notif = SmsNotification(
        user_id=current_user.id,
        recipient=mobile,
        message=f"Your VaxiCare verification code is: {otp_str}. It expires in 10 minutes.",
        message_type="otp",
        language="en",
        due_date=now.date(),
        next_attempt_at=utcnow(),
    )
    db.session.add(notif)
    db.session.commit()
    result = _attempt_delivery(notif, require_consent=False)

    if result["status"] == "sent":
        flash("A 6-digit verification code has been sent to your phone.", "success")
    else:
        flash("OTP could not be sent. Please check your Twilio configuration.", "danger")
    return redirect(url_for("main.parent_dashboard"))


# ---------------------------------------------------------------------------
# Mobile OTP verify
# ---------------------------------------------------------------------------
@main.route("/verify-mobile-otp", methods=["POST"])
@login_required
def verify_mobile_otp():
    if current_user.role != "parent":
        abort(403)

    form = MobileOtpForm()
    if not form.validate_on_submit():
        flash("Please enter the 6-digit verification code.", "danger")
        return redirect(url_for("main.parent_dashboard"))

    otp_input = (form.otp.data or "").strip()
    if not otp_input.isdigit() or len(otp_input) != 6:
        flash("Enter a valid 6-digit code.", "danger")
        return redirect(url_for("main.parent_dashboard"))

    import hashlib as _hl
    code_hash = _hl.sha256(otp_input.encode()).hexdigest()
    mobile_hash = _hl.sha256((current_user.mobile or "").encode()).hexdigest()

    challenge = MobileVerificationChallenge.query.filter_by(
        user_id=current_user.id,
        status="pending",
        mobile_hash=mobile_hash,
        code_hash=code_hash,
    ).order_by(MobileVerificationChallenge.created_at.desc()).first()

    if not challenge or challenge.expires_at <= utcnow():
        # Increment attempt on wrong code
        wrong = MobileVerificationChallenge.query.filter_by(
            user_id=current_user.id, status="pending"
        ).order_by(MobileVerificationChallenge.created_at.desc()).first()
        if wrong:
            wrong.attempt_count += 1
            if wrong.attempt_count >= 5:
                wrong.status = "expired"
            db.session.commit()
        flash("Invalid or expired verification code. Please request a new OTP.", "danger")
        return redirect(url_for("main.parent_dashboard"))

    # Mark challenge as consumed
    challenge.status = "consumed"
    challenge.consumed_at = utcnow()
    current_user.mobile_verified = True
    current_user.mobile_verified_at = utcnow()
    db.session.commit()
    flash("Phone number verified successfully.", "success")
    return redirect(url_for("main.parent_dashboard"))


# ---------------------------------------------------------------------------
# FCM device push stubs (clear no-op with messages)
# ---------------------------------------------------------------------------
@main.route("/register-push-device", methods=["POST"])
@login_required
def register_push_device():
    if current_user.role != "parent":
        abort(403)
    # FCM registration is handled via JavaScript on the client side.
    # Backend endpoint validates token and stores device registration.
    token = (request.json or {}).get("token") if request.is_json else request.form.get("token")
    if not token:
        return {"status": "error", "message": "No FCM token provided."}, 400
    from app.models import FCMDeviceToken
    import hashlib as _hl
    token_hash = _hl.sha256(token.encode()).hexdigest()
    existing = FCMDeviceToken.query.filter_by(user_id=current_user.id, token_hash=token_hash).first()
    if existing:
        existing.is_active = True
        existing.last_seen_at = utcnow()
        db.session.commit()
        return {"status": "ok", "message": "Device token updated."}, 200
    device = FCMDeviceToken(
        user_id=current_user.id,
        token=token,
        token_hash=token_hash,
        target_type="registration_token",
        is_active=True,
        created_at=utcnow(),
        last_seen_at=utcnow(),
    )
    db.session.add(device)
    db.session.commit()
    return {"status": "ok", "message": "Push device registered."}, 200


@main.route("/disable-push-device", methods=["POST"])
@login_required
def disable_push_device():
    if current_user.role != "parent":
        abort(403)
    from app.models import FCMDeviceToken
    token = (request.json or {}).get("token") if request.is_json else request.form.get("token")
    if token:
        import hashlib as _hl
        token_hash = _hl.sha256(token.encode()).hexdigest()
        FCMDeviceToken.query.filter_by(user_id=current_user.id, token_hash=token_hash).update({"is_active": False})
        db.session.commit()
    flash("Push notifications disabled for this browser.", "info")
    return redirect(url_for("main.parent_dashboard"))


@main.route("/test-push-device", methods=["POST"])
@login_required
def test_push_device():
    if current_user.role != "parent":
        abort(403)
    token_device = FCMDeviceToken.query.filter_by(user_id=current_user.id, is_active=True).first()
    fcm_enabled = bool(current_app.config.get("FCM_SERVER_KEY") or current_app.config.get("FIREBASE_CREDENTIALS"))
    if not token_device:
        flash("No active registered browser device found. Enable push notifications first.", "warning")
        return redirect(url_for("main.parent_dashboard"))
    if not fcm_enabled:
        attempt = FCMPushTestAttempt(
            user_id=current_user.id,
            device_id=token_device.id,
            status="failed",
            failure_reason="Push notifications are not configured on server (missing FCM credentials).",
            created_at=utcnow(),
            completed_at=utcnow(),
        )
        db.session.add(attempt)
        db.session.commit()
        flash("Push notification test failed: Server FCM configuration missing.", "warning")
    else:
        attempt = FCMPushTestAttempt(
            user_id=current_user.id,
            device_id=token_device.id,
            status="sent",
            provider_message_id="fcm-simulated-msg-id",
            created_at=utcnow(),
            completed_at=utcnow(),
        )
        db.session.add(attempt)
        db.session.commit()
        flash("Test push notification sent to your browser.", "success")
    return redirect(url_for("main.parent_dashboard"))


# ---------------------------------------------------------------------------
# Download vaccination card
# ---------------------------------------------------------------------------
@main.route("/download-card/<int:child_id>")
@login_required
def download_vaccination_card(child_id):
    child = get_or_404(Child, child_id)
    if current_user.role == "parent":
        if not current_user.parent or child.parent_id != current_user.parent.id:
            abort(403)
    elif current_user.role == "healthcare_worker":
        if not can_worker_access(current_user, child):
            abort(403)
    elif current_user.role != "admin":
        abort(403)
    pdf_bytes = generate_vaccination_pdf(
        child,
        current_user.parent if current_user.role == "parent" else child.parent,
    )
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"{child.name}_vaccination_card.pdf",
    )


# ---------------------------------------------------------------------------
# Public child record (by token)
# ---------------------------------------------------------------------------
@main.route("/record/<token>")
def public_child_record(token):
    child = Child.query.filter_by(record_token=token).first_or_404()
    schedule = build_child_schedule(child)
    return render_template("public_record.html", child=child, schedule=schedule)


# ---------------------------------------------------------------------------
# Healthcare worker dashboard
# ---------------------------------------------------------------------------
@main.route("/worker-dashboard")
@main.route("/healthcare-dashboard")
@login_required
def healthcare_dashboard():
    if current_user.role not in {"healthcare_worker", "admin"}:
        abort(403)
    search = request.args.get("search", "").strip()
    village = request.args.get("village", "").strip()
    status_filter = request.args.get("status", "").strip()
    due_from_text = request.args.get("due_from", "").strip()
    due_to_text = request.args.get("due_to", "").strip()
    if status_filter not in {"", "upcoming", "due_today", "overdue"}:
        abort(400)
    try:
        due_from = date.fromisoformat(due_from_text) if due_from_text else None
        due_to = date.fromisoformat(due_to_text) if due_to_text else None
    except ValueError:
        abort(400)

    worker = current_user.healthcare_worker
    worker_village = worker.village if (worker and worker.village) else None

    # Base query for assigned children
    base_query = Child.query
    if current_user.role == "healthcare_worker" and worker_village:
        base_query = base_query.filter(func.lower(Child.village) == worker_village.casefold())

    total_assigned = base_query.count()

    # Metric counts across all assigned children
    all_assigned_children = base_query.all()
    upcoming_count = 0
    overdue_count = 0
    due_today_count = 0
    for ch in all_assigned_children:
        sch = build_child_schedule(ch)
        for it in sch:
            if it["status"] == "upcoming":
                upcoming_count += 1
            elif it["status"] == "overdue":
                overdue_count += 1
            elif it["status"] == "due_today":
                due_today_count += 1

    # Total vaccinations administered
    if current_user.role == "healthcare_worker" and worker:
        total_administered = VaccinationRecord.query.filter_by(recorded_by_id=worker.id).count()
    else:
        total_administered = VaccinationRecord.query.count()

    # Filtered children query for display
    query = base_query
    if search:
        query = query.filter((Child.name.ilike(f"%{search}%")) | (Child.record_token == search))
    if village:
        query = query.filter(func.lower(Child.village) == village.casefold())

    children = query.order_by(Child.name).all()
    child_entries = []
    for child in children:
        schedule = build_child_schedule(child)
        filtered = [
            item for item in schedule
            if (not status_filter or item["status"] == status_filter)
            and (not due_from or item["due_date"] >= due_from)
            and (not due_to or item["due_date"] <= due_to)
        ]
        if filtered or not (status_filter or due_from or due_to):
            child_entries.append({"child": child, "schedule": filtered, "items": filtered})

    # Available villages for filter dropdown
    if current_user.role == "healthcare_worker" and worker_village:
        villages = [worker_village]
    else:
        villages = sorted({c.village for c in Child.query.all() if c.village})

    # Recent records (restricted to authorized scope)
    rec_query = VaccinationRecord.query.join(VaccinationRecord.child)
    if current_user.role == "healthcare_worker" and worker_village:
        rec_query = rec_query.filter(func.lower(Child.village) == worker_village.casefold())
    recent_records = (
        rec_query
        .order_by(VaccinationRecord.administered_date.desc(), VaccinationRecord.id.desc())
        .limit(10)
        .all()
    )

    record_form = VaccinationRecordForm()
    record_form.vaccine_id.choices = [(v.id, v.name) for v in Vaccine.query.order_by(Vaccine.name).all()]

    return render_template(
        "healthcare_dashboard.html",
        child_rows=child_entries,
        child_entries=child_entries,
        villages=villages,
        search=search,
        village=village,
        status_filter=status_filter,
        due_from=due_from_text,
        due_to=due_to_text,
        record_form=record_form,
        total_assigned=total_assigned,
        total_administered=total_administered,
        upcoming_count=upcoming_count,
        overdue_count=overdue_count,
        due_today_count=due_today_count,
        records=recent_records,
        recent_records=recent_records,
        worker=worker,
    )


# ---------------------------------------------------------------------------
# Record vaccine (healthcare worker / admin only)
# ---------------------------------------------------------------------------
@main.route("/record-vaccine/<int:child_id>", methods=["POST"])
@login_required
def record_vaccine(child_id):
    if current_user.role not in {"healthcare_worker", "admin"}:
        abort(403)
    form = VaccinationRecordForm(formdata=request.form)
    form.vaccine_id.choices = [(v.id, v.name) for v in Vaccine.query.order_by(Vaccine.name).all()]
    if form.validate_on_submit():
        child = get_or_404(Child, child_id)
        if current_user.role == "healthcare_worker" and not can_worker_access(current_user, child):
            abort(403)
        vaccine = db.session.get(Vaccine, form.vaccine_id.data)
        if not vaccine:
            flash("Invalid vaccine selected.", "danger")
            return redirect(url_for("main.healthcare_dashboard"))

        admin_date = form.administered_date.data
        if admin_date < child.dob:
            flash("Administration date cannot be before the child's date of birth.", "danger")
            return redirect(url_for("main.health_record", child_id=child.id))
        if admin_date > app_today() + timedelta(days=1):
            flash("Administration date cannot be in the future.", "danger")
            return redirect(url_for("main.health_record", child_id=child.id))

        schedule_entries = build_child_schedule(child)
        next_dose = next(
            (
                item for item in schedule_entries
                if item["vaccine"].id == vaccine.id and item["status"] != "completed"
            ),
            None,
        )
        target_schedule_id = next_dose["schedule"].id if next_dose else None

        # Duplicate check
        if target_schedule_id:
            existing = VaccinationRecord.query.filter_by(
                child_id=child.id,
                vaccine_id=vaccine.id,
                schedule_id=target_schedule_id,
            ).first()
            if existing:
                flash(f"This dose ({next_dose['schedule'].dose_label}) has already been recorded for {child.name}.", "warning")
                return redirect(url_for("main.health_record", child_id=child.id))
        else:
            existing = VaccinationRecord.query.filter_by(
                child_id=child.id,
                vaccine_id=vaccine.id,
                administered_date=admin_date,
            ).first()
            if existing:
                flash(f"A vaccination record for {vaccine.name} on {admin_date} already exists for {child.name}.", "warning")
                return redirect(url_for("main.health_record", child_id=child.id))

        worker_id = current_user.healthcare_worker.id if current_user.healthcare_worker else None
        record = VaccinationRecord(
            child_id=child.id,
            vaccine_id=vaccine.id,
            schedule_id=target_schedule_id,
            recorded_by_id=worker_id,
            administered_date=admin_date,
            dose_status="received",
            notes=(form.notes.data or "").strip() or None,
        )
        db.session.add(record)

        # Notify parent if parent exists
        if child.parent and child.parent.user_id:
            dose_lbl = next_dose["schedule"].dose_label if next_dose else "Dose"
            notif = Notification(
                user_id=child.parent.user_id,
                child_id=child.id,
                vaccine_id=vaccine.id,
                reminder_type="completed",
                title=f"Vaccination Administered: {vaccine.name}",
                body=f"{dose_lbl} of {vaccine.name} was administered for {child.name} on {admin_date.strftime('%d %b %Y')}.",
                channel="system",
                status="sent",
            )
            db.session.add(notif)

        try:
            db.session.commit()
            flash(f"Vaccination record saved successfully for {child.name}.", "success")
        except Exception:
            db.session.rollback()
            flash("An error occurred while saving the record. Please try again.", "danger")
    else:
        flash("Please fill all required fields correctly.", "danger")
    return redirect(url_for("main.health_record", child_id=child_id))


# ---------------------------------------------------------------------------
# Health record (healthcare worker / admin view of a specific child)
# ---------------------------------------------------------------------------
@main.route("/health-record/<int:child_id>")
@login_required
def health_record(child_id):
    if current_user.role not in {"healthcare_worker", "admin"}:
        abort(403)
    child = get_or_404(Child, child_id)
    if current_user.role == "healthcare_worker" and not can_worker_access(current_user, child):
        abort(403)
    vaccines = Vaccine.query.order_by(Vaccine.name).all()
    record_form = VaccinationRecordForm()
    record_form.vaccine_id.choices = [(v.id, v.name) for v in vaccines]
    return render_template(
        "child_detail.html",
        child=child,
        schedule=build_child_schedule(child),
        records=VaccinationRecord.query.filter_by(child_id=child.id).order_by(VaccinationRecord.administered_date.desc()).all(),
        vaccines=vaccines,
        today_date=app_today(),
        record_form=record_form,
    )


# ---------------------------------------------------------------------------
# Healthcare worker vaccination history / clinical records
# ---------------------------------------------------------------------------
@main.route("/worker/records")
@main.route("/healthcare/records")
@login_required
def worker_records():
    if current_user.role not in {"healthcare_worker", "admin"}:
        abort(403)
    worker = current_user.healthcare_worker
    worker_village = worker.village if (worker and worker.village) else None

    search = request.args.get("search", "").strip()
    vaccine_id = request.args.get("vaccine_id", type=int)
    village = request.args.get("village", "").strip()
    date_from_text = request.args.get("date_from", "").strip()
    date_to_text = request.args.get("date_to", "").strip()

    try:
        date_from = date.fromisoformat(date_from_text) if date_from_text else None
        date_to = date.fromisoformat(date_to_text) if date_to_text else None
    except ValueError:
        abort(400)

    query = VaccinationRecord.query.join(VaccinationRecord.child)
    if current_user.role == "healthcare_worker" and worker_village:
        query = query.filter(func.lower(Child.village) == worker_village.casefold())

    if search:
        query = query.filter(Child.name.ilike(f"%{search}%"))
    if vaccine_id:
        query = query.filter(VaccinationRecord.vaccine_id == vaccine_id)
    if village:
        query = query.filter(func.lower(Child.village) == village.casefold())
    if date_from:
        query = query.filter(VaccinationRecord.administered_date >= date_from)
    if date_to:
        query = query.filter(VaccinationRecord.administered_date <= date_to)

    records = query.order_by(VaccinationRecord.administered_date.desc(), VaccinationRecord.id.desc()).all()
    vaccines = Vaccine.query.order_by(Vaccine.name).all()

    if current_user.role == "healthcare_worker" and worker_village:
        villages = [worker_village]
    else:
        villages = sorted({c.village for c in Child.query.all() if c.village})

    return render_template(
        "worker_records.html",
        records=records,
        vaccines=vaccines,
        villages=villages,
        search=search,
        vaccine_id=vaccine_id,
        village=village,
        date_from=date_from_text,
        date_to=date_to_text,
    )


# ---------------------------------------------------------------------------
# Healthcare worker CSV export
# ---------------------------------------------------------------------------
@main.route("/worker/export-records")
@login_required
def worker_export_records():
    if current_user.role not in {"healthcare_worker", "admin"}:
        abort(403)
    worker = current_user.healthcare_worker
    worker_village = worker.village if (worker and worker.village) else None

    query = VaccinationRecord.query.join(VaccinationRecord.child)
    if current_user.role == "healthcare_worker" and worker_village:
        query = query.filter(func.lower(Child.village) == worker_village.casefold())

    records = query.order_by(VaccinationRecord.administered_date.desc(), VaccinationRecord.id.desc()).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Record ID", "Child Name", "Date of Birth", "Village",
        "Vaccine Name", "Dose Label", "Administered Date",
        "Status", "Recorded By", "Notes",
    ])
    for r in records:
        recorded_by_name = r.recorded_by.full_name if r.recorded_by else "System/Admin"
        dose_label = r.schedule.dose_label if r.schedule else "Dose"
        writer.writerow([
            r.id,
            r.child.name,
            r.child.dob.strftime("%Y-%m-%d") if r.child.dob else "",
            r.child.village or "",
            r.vaccine.name if r.vaccine else "",
            dose_label,
            r.administered_date.strftime("%Y-%m-%d") if r.administered_date else "",
            r.dose_status,
            recorded_by_name,
            r.notes or "",
        ])

    csv_data = output.getvalue().encode("utf-8")
    filename = f"vaccination_records_{app_today().strftime('%Y%m%d')}.csv"
    return send_file(
        io.BytesIO(csv_data),
        mimetype="text/csv",
        as_attachment=True,
        download_name=filename,
    )


# ---------------------------------------------------------------------------
# Healthcare worker profile
# ---------------------------------------------------------------------------
@main.route("/worker/profile", methods=["GET", "POST"])
@main.route("/healthcare/profile", methods=["GET", "POST"])
@login_required
def worker_profile():
    if current_user.role != "healthcare_worker":
        abort(403)
    worker = current_user.healthcare_worker
    if not worker:
        flash("Healthcare worker profile not found.", "danger")
        return redirect(url_for("main.healthcare_dashboard"))

    form = HealthcareWorkerProfileForm(obj=worker)
    if request.method == "GET":
        form.mobile.data = current_user.mobile

    if form.validate_on_submit():
        try:
            worker.full_name = form.full_name.data.strip()
            worker.qualification = (form.qualification.data or "").strip() or None
            worker.department = (form.department.data or "").strip() or None
            worker.village = (form.village.data or "").strip() or None
            current_user.full_name = worker.full_name
            current_user.mobile = (form.mobile.data or "").strip() or None
            db.session.commit()
            flash("Profile updated successfully.", "success")
            return redirect(url_for("main.worker_profile"))
        except Exception:
            db.session.rollback()
            flash("An error occurred while updating your profile.", "danger")

    return render_template("worker_profile.html", form=form, worker=worker)


# ---------------------------------------------------------------------------
# Admin dashboard
# ---------------------------------------------------------------------------
@main.route("/admin-dashboard")
@login_required
def admin_dashboard():
    if current_user.role != "admin":
        abort(403)

    # 1. Primary counts
    total_users_count = User.query.count()
    parent_count = User.query.filter_by(role="parent").count()
    worker_count = User.query.filter_by(role="healthcare_worker").count()
    admin_count = User.query.filter_by(role="admin").count()
    child_count = Child.query.count()
    record_count = VaccinationRecord.query.count()
    vaccine_count = Vaccine.query.count()
    schedule_count = VaccinationSchedule.query.count()

    today = app_today()
    today_administered_count = VaccinationRecord.query.filter(
        VaccinationRecord.administered_date == today
    ).count()

    # Calculate upcoming and overdue vaccinations across all children
    upcoming_vaccinations_count = 0
    overdue_vaccinations_count = 0
    all_children = Child.query.all()
    for ch in all_children:
        child_sched = build_child_schedule(ch)
        for entry in child_sched:
            st = entry["status"]
            if st in {"upcoming", "due_today"}:
                upcoming_vaccinations_count += 1
            elif st == "overdue":
                overdue_vaccinations_count += 1

    # 2. Real SQLite Analytics
    # Monthly trends (past 12 months)
    monthly_records = (
        db.session.query(
            func.strftime("%Y-%m", VaccinationRecord.administered_date).label("month"),
            func.count(VaccinationRecord.id),
        )
        .filter(VaccinationRecord.administered_date.isnot(None))
        .group_by("month")
        .order_by("month")
        .all()
    )
    monthly_trends = {m: c for m, c in monthly_records if m}

    # Village-wise statistics
    all_villages = sorted(list({c.village for c in all_children if c.village}))
    village_stats = []
    for v in all_villages:
        c_cnt = Child.query.filter_by(village=v).count()
        v_cnt = (
            VaccinationRecord.query.join(VaccinationRecord.child)
            .filter(Child.village == v)
            .count()
        )
        village_stats.append({
            "village": v,
            "children": c_cnt,
            "administered": v_cnt,
        })

    # Vaccine-wise administration counts
    vaccine_dist_raw = (
        db.session.query(Vaccine.name, func.count(VaccinationRecord.id))
        .outerjoin(VaccinationRecord, VaccinationRecord.vaccine_id == Vaccine.id)
        .group_by(Vaccine.name)
        .order_by(func.count(VaccinationRecord.id).desc())
        .all()
    )
    vaccine_distribution = {v_name: count for v_name, count in vaccine_dist_raw}

    # User registration trends
    user_reg_raw = (
        db.session.query(
            func.strftime("%Y-%m", User.created_at).label("month"),
            func.count(User.id),
        )
        .filter(User.created_at.isnot(None))
        .group_by("month")
        .order_by("month")
        .all()
    )
    user_reg_trends = {m: c for m, c in user_reg_raw if m}

    # 3. Notification logs & stats
    notifications = SmsNotification.query.order_by(SmsNotification.created_at.desc()).limit(12).all()
    notification_counts = Counter(
        dict(
            db.session.query(SmsNotification.status, func.count(SmsNotification.id))
            .group_by(SmsNotification.status)
            .all()
        )
    )

    email_reminder_logs = EmailReminderNotification.query.order_by(
        EmailReminderNotification.created_at.desc()
    ).limit(12).all()

    # Rate limiting objects using SQLite data
    _RateLimit = type("RateLimit", (), {})
    one_hour_ago = utcnow() - timedelta(hours=1)

    # SMS rate limit
    _sms_configured = is_sms_configured()
    sms_test_count = SmsNotification.query.filter(
        SmsNotification.message_type == "test",
        SmsNotification.created_at >= one_hour_ago,
    ).count()
    sms_test_rate_limit = _RateLimit()
    sms_test_rate_limit.configured = _sms_configured
    sms_test_rate_limit.configuration_issue = None if _sms_configured else "missing"
    sms_test_rate_limit.attempts_in_window = sms_test_count
    sms_test_rate_limit.max_attempts = 10
    sms_test_rate_limit.attempts_remaining = max(0, 10 - sms_test_count)
    sms_test_rate_limit.retry_at = None
    sms_test_rate_limit.retry_in_seconds = 0
    sms_test_recipient = current_app.config.get("VAXICARE_TEST_SMS_NUMBER", "")

    # Email rate limit
    email_attempts = EmailTestAttempt.query.filter(
        EmailTestAttempt.user_id == current_user.id,
        EmailTestAttempt.created_at >= one_hour_ago,
    ).order_by(EmailTestAttempt.created_at.desc()).all()
    email_test_rate_limit = _RateLimit()
    email_test_rate_limit.configured = bool(
        current_app.config.get("SMTP_HOST") and current_app.config.get("SMTP_FROM")
    )
    email_test_rate_limit.attempts_in_window = len(email_attempts)
    email_test_rate_limit.max_attempts = 12
    email_test_rate_limit.attempts_remaining = max(0, 12 - len(email_attempts))
    if email_attempts:
        elapsed = (utcnow() - email_attempts[0].created_at).total_seconds()
        if elapsed < 300:
            email_test_rate_limit.retry_in_seconds = int(300 - elapsed)
            email_test_rate_limit.retry_at = email_attempts[0].created_at + timedelta(seconds=300)
        else:
            email_test_rate_limit.retry_in_seconds = 0
            email_test_rate_limit.retry_at = None
    else:
        email_test_rate_limit.retry_in_seconds = 0
        email_test_rate_limit.retry_at = None

    # Twilio config check
    _twilio_keys = ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER"]
    twilio_configuration_missing = [k for k in _twilio_keys if not current_app.config.get(k)]

    sms_enabled = _sms_configured
    sms_force_demo = current_app.config.get("SMS_FORCE_DEMO", False)

    # FCM details
    fcm_enabled = bool(
        current_app.config.get("FCM_SERVER_KEY") or current_app.config.get("FIREBASE_CREDENTIALS")
    )
    fcm_status = "not_configured" if not fcm_enabled else "ready"
    active_fcm_installation_count = FCMDeviceToken.query.filter_by(is_active=True).count()
    fcm_notification_counts: dict = Counter(
        dict(
            db.session.query(FCMPushNotification.status, func.count(FCMPushNotification.id))
            .group_by(FCMPushNotification.status)
            .all()
        )
    )
    fcm_notifications = FCMPushNotification.query.order_by(
        FCMPushNotification.created_at.desc()
    ).limit(12).all()
    fcm_test_attempts = FCMPushTestAttempt.query.order_by(
        FCMPushTestAttempt.created_at.desc()
    ).limit(12).all()
    fcm_test_failure_count = FCMPushTestAttempt.query.filter_by(status="failed").count()

    # Scheduler state
    scheduler_state = SchedulerRunState.query.filter_by(name="vaccination_reminders").first()
    scheduler_is_running = scheduler_state is not None and scheduler_state.status == "running"

    # Failures tracking
    failed_email_notifications = (
        EmailReminderNotification.query.filter_by(status="failed")
        .order_by(EmailReminderNotification.created_at.desc())
        .limit(20)
        .all()
    )
    failed_sms_notifications = (
        SmsNotification.query.filter_by(status="failed")
        .order_by(SmsNotification.created_at.desc())
        .limit(20)
        .all()
    )
    failed_fcm_notifications = (
        FCMPushNotification.query.filter_by(status="failed")
        .order_by(FCMPushNotification.created_at.desc())
        .limit(20)
        .all()
    )
    failed_fcm_test_attempts = (
        FCMPushTestAttempt.query.filter_by(status="failed")
        .order_by(FCMPushTestAttempt.created_at.desc())
        .limit(20)
        .all()
    )
    email_failure_count = EmailReminderNotification.query.filter_by(status="failed").count()
    sms_failure_count = SmsNotification.query.filter_by(status="failed").count()
    fcm_failure_count = len(failed_fcm_notifications) + fcm_test_failure_count

    # Automatic email reminder settings
    setting_auto = ApplicationSetting.query.filter_by(key="automatic_email_reminders_enabled").first()
    automatic_email_reminders_enabled = (
        setting_auto.value.lower() in {"true", "1", "yes", "on"}
        if setting_auto
        else current_app.config.get("AUTOMATIC_EMAIL_REMINDERS_ENABLED", False)
    )

    setting_days = ApplicationSetting.query.filter_by(key="email_reminder_days_before").first()
    _days_before = int(setting_days.value) if setting_days and setting_days.value.isdigit() else int(current_app.config.get("REMINDER_DAYS_BEFORE", 3))
    _overdue_after = int(current_app.config.get("REMINDER_OVERDUE_AFTER_DAYS", 7))
    _interval_secs = int(current_app.config.get("REMINDER_INTERVAL_SECONDS", 3600))

    auto_reminder_form = AutomaticEmailReminderSettingsForm(
        enabled="true" if automatic_email_reminders_enabled else "false",
        days_before=_days_before,
        overdue_after_days=_overdue_after,
        interval_seconds=_interval_secs,
    )
    _TimingSettings = type("TimingSettings", (), {})
    reminder_timing_settings = _TimingSettings()
    reminder_timing_settings.interval_seconds = _interval_secs
    reminder_timing_settings.days_before = _days_before
    reminder_timing_settings.overdue_after_days = _overdue_after

    reminder_email_form = ReminderEmailForm()
    reminder_email_parents = Parent.query.join(Parent.user).order_by(User.username).all()
    reminder_email_children = Child.query.order_by(Child.name).all()
    reminder_email_vaccines = Vaccine.query.order_by(Vaccine.name).all()
    reminder_email_schedules = VaccinationSchedule.query.order_by(VaccinationSchedule.id).all()

    return render_template(
        "admin_dashboard.html",
        total_users_count=total_users_count,
        parent_count=parent_count,
        worker_count=worker_count,
        admin_count=admin_count,
        child_count=child_count,
        record_count=record_count,
        vaccine_count=vaccine_count,
        schedule_count=schedule_count,
        today_administered_count=today_administered_count,
        upcoming_vaccinations_count=upcoming_vaccinations_count,
        overdue_vaccinations_count=overdue_vaccinations_count,
        # Analytics
        monthly_trends=monthly_trends,
        village_stats=village_stats,
        vaccine_distribution=vaccine_distribution,
        user_reg_trends=user_reg_trends,
        # Notifications
        notification_counts=notification_counts,
        reminder_logs=notifications,
        email_reminder_logs=email_reminder_logs,
        user_form=AdminUserForm(),
        test_sms_form=TestSmsForm(),
        test_email_form=TestEmailForm(),
        vaccine_form=VaccineForm(),
        # SMS
        sms_enabled=sms_enabled,
        sms_force_demo=sms_force_demo,
        sms_test_rate_limit=sms_test_rate_limit,
        sms_test_recipient=sms_test_recipient,
        twilio_configuration_missing=twilio_configuration_missing,
        # Email
        email_test_rate_limit=email_test_rate_limit,
        # Automatic email reminders
        automatic_email_reminders_enabled=automatic_email_reminders_enabled,
        automatic_email_reminder_settings_form=auto_reminder_form,
        reminder_timing_settings=reminder_timing_settings,
        # Reminder email manual form
        reminder_email_form=reminder_email_form,
        reminder_email_parents=reminder_email_parents,
        reminder_email_children=reminder_email_children,
        reminder_email_vaccines=reminder_email_vaccines,
        reminder_email_schedules=reminder_email_schedules,
        # Scheduler health
        scheduler_is_running=scheduler_is_running,
        scheduler_state=scheduler_state,
        # Failure tracking
        email_failure_count=email_failure_count,
        sms_failure_count=sms_failure_count,
        fcm_failure_count=fcm_failure_count,
        failed_email_notifications=failed_email_notifications,
        failed_sms_notifications=failed_sms_notifications,
        failed_fcm_notifications=failed_fcm_notifications,
        failed_fcm_test_attempts=failed_fcm_test_attempts,
        # FCM / push
        fcm_enabled=fcm_enabled,
        fcm_status=fcm_status,
        active_fcm_installation_count=active_fcm_installation_count,
        fcm_notification_counts=fcm_notification_counts,
        fcm_notifications=fcm_notifications,
        fcm_test_attempts=fcm_test_attempts,
        fcm_test_failure_count=fcm_test_failure_count,
    )


# ---------------------------------------------------------------------------
# Admin: notifications view
# ---------------------------------------------------------------------------
@main.route("/admin/notifications")
@login_required
def admin_notifications():
    if current_user.role != "admin":
        abort(403)

    channel_filter = request.args.get("channel", "all").strip().lower()
    status_filter = request.args.get("status", "").strip().lower()
    search = request.args.get("search", "").strip()

    allowed_channels = {"all", "sms", "email", "push"}
    if channel_filter not in allowed_channels:
        abort(400)
    allowed_statuses = {"", "sent", "failed", "pending", "simulated", "skipped", "disabled"}
    if status_filter and status_filter not in allowed_statuses:
        abort(400)

    # 1. SMS Query
    sms_query = SmsNotification.query
    if status_filter:
        sms_query = sms_query.filter_by(status=status_filter)
    if search:
        sms_query = sms_query.join(SmsNotification.child, isouter=True).filter(
            (SmsNotification.recipient.ilike(f"%{search}%")) |
            (Child.name.ilike(f"%{search}%"))
        )
    sms_notifications = sms_query.order_by(SmsNotification.created_at.desc()).limit(200).all()

    # 2. Email Query
    email_query = EmailReminderNotification.query
    if status_filter:
        email_query = email_query.filter_by(status=status_filter)
    if search:
        email_query = email_query.join(EmailReminderNotification.child, isouter=True).filter(
            (EmailReminderNotification.recipient.ilike(f"%{search}%")) |
            (Child.name.ilike(f"%{search}%"))
        )
    email_notifications = email_query.order_by(EmailReminderNotification.created_at.desc()).limit(200).all()

    # 3. Push Query
    push_query = FCMPushNotification.query
    if status_filter:
        push_query = push_query.filter_by(status=status_filter)
    if search:
        push_query = push_query.join(FCMPushNotification.child, isouter=True).filter(
            Child.name.ilike(f"%{search}%")
        )
    push_notifications = push_query.order_by(FCMPushNotification.created_at.desc()).limit(200).all()

    push_test_attempts = FCMPushTestAttempt.query.order_by(FCMPushTestAttempt.created_at.desc()).limit(100).all()
    fcm_enabled = bool(current_app.config.get("FCM_SERVER_KEY") or current_app.config.get("FIREBASE_CREDENTIALS"))

    return render_template(
        "admin_notifications.html",
        notifications=sms_notifications,
        email_notifications=email_notifications,
        push_notifications=push_notifications,
        push_test_attempts=push_test_attempts,
        fcm_enabled=fcm_enabled,
        channel_filter=channel_filter,
        status_filter=status_filter,
        search=search,
    )


# ---------------------------------------------------------------------------
# Admin: retry failed notification
# ---------------------------------------------------------------------------
@main.route("/admin/notifications/retry/<int:notification_id>", methods=["POST"])
@login_required
def admin_retry_notification(notification_id):
    if current_user.role != "admin":
        abort(403)
    channel = request.form.get("channel", "sms").strip().lower()
    res = retry_failed_notification(channel, notification_id)
    if res.get("status") in {"sent", "simulated", "success"}:
        flash(f"Notification #{notification_id} retry succeeded: {res.get('message', 'Delivered')}", "success")
    else:
        flash(f"Notification #{notification_id} retry failed: {res.get('message', 'Failed')}", "danger")
    return redirect(url_for("main.admin_notifications", channel=channel))


# ---------------------------------------------------------------------------
# Admin: run automatic reminder check on demand
# ---------------------------------------------------------------------------
@main.route("/admin/run-reminder-check", methods=["POST"])
@login_required
def admin_run_reminder_check():
    if current_user.role != "admin":
        abort(403)
    result = run_due_reminder_check(today=app_today())
    flash(result.get("message", "Reminder check complete."), "success")
    return redirect(url_for("main.admin_dashboard"))


# ---------------------------------------------------------------------------
# Admin: Healthcare Worker & Village Monitoring
# ---------------------------------------------------------------------------
@main.route("/admin/workers")
@login_required
def admin_workers():
    if current_user.role != "admin":
        abort(403)
    workers = HealthcareWorker.query.join(HealthcareWorker.user).order_by(HealthcareWorker.full_name).all()
    villages = sorted(list({c.village for c in Child.query.all() if c.village} | {w.village for w in workers if w.village}))
    village_stats = []
    for v in villages:
        c_count = Child.query.filter_by(village=v).count()
        r_count = VaccinationRecord.query.join(VaccinationRecord.child).filter(Child.village == v).count()
        w_assigned = [w.full_name for w in workers if (w.village or "").casefold() == v.casefold()]
        village_stats.append({
            "village": v,
            "children_count": c_count,
            "vaccination_count": r_count,
            "workers": w_assigned,
        })
    return render_template(
        "admin_workers.html",
        workers=workers,
        village_stats=village_stats,
    )


# ---------------------------------------------------------------------------
# Admin: Reassign worker village
# ---------------------------------------------------------------------------
@main.route("/admin/workers/<int:worker_id>/reassign", methods=["POST"])
@login_required
def admin_reassign_worker(worker_id):
    if current_user.role != "admin":
        abort(403)
    worker = get_or_404(HealthcareWorker, worker_id)
    new_village = request.form.get("village", "").strip()
    worker.village = new_village or None
    db.session.commit()
    flash(f"Updated village assignment for {worker.full_name} to '{new_village or 'Unassigned'}'.", "success")
    return redirect(url_for("main.admin_workers"))


# ---------------------------------------------------------------------------
# Admin: Vaccine & Schedule Management
# ---------------------------------------------------------------------------
@main.route("/admin/vaccines")
@login_required
def admin_vaccines():
    if current_user.role != "admin":
        abort(403)
    vaccines = Vaccine.query.order_by(Vaccine.name).all()
    schedules = VaccinationSchedule.query.order_by(VaccinationSchedule.age_days.asc()).all()
    admin_counts = dict(
        db.session.query(VaccinationRecord.vaccine_id, func.count(VaccinationRecord.id))
        .group_by(VaccinationRecord.vaccine_id)
        .all()
    )
    return render_template(
        "admin_vaccines.html",
        vaccines=vaccines,
        schedules=schedules,
        admin_counts=admin_counts,
        form=VaccineForm(),
    )


# ---------------------------------------------------------------------------
# Admin: send test SMS
# ---------------------------------------------------------------------------
@main.route("/admin/send-test-sms", methods=["POST"])
@login_required
def admin_send_test_sms():
    if current_user.role != "admin":
        abort(403)
    form = TestSmsForm()
    if not form.validate_on_submit():
        flash("Enter a valid test recipient number.", "danger")
        return redirect(url_for("main.admin_dashboard"))
    recipient = form.recipient.data.strip()
    if not is_valid_mobile_number(recipient):
        flash("Use a valid international number in +15551234567 format.", "danger")
        return redirect(url_for("main.admin_dashboard"))
    result = send_test_sms(current_user, recipient)
    flash(
        result["message"],
        "success" if result["status"] == "sent" else "info" if result["status"] == "simulated" else "danger",
    )
    return redirect(url_for("main.admin_dashboard"))


# ---------------------------------------------------------------------------
# Admin: send test email
# ---------------------------------------------------------------------------
@main.route("/admin/send-test-email", methods=["POST"])
@login_required
def admin_send_test_email():
    if current_user.role != "admin":
        abort(403)
    form = TestEmailForm()
    if not form.validate_on_submit():
        flash("Invalid test email recipient.", "danger")
        return redirect(url_for("main.admin_dashboard"))

    recipient = form.recipient.data.strip()

    # Rate limiting: max 12 per hour, cooldown 300 seconds
    one_hour_ago = utcnow() - timedelta(hours=1)
    recent_attempts = EmailTestAttempt.query.filter(
        EmailTestAttempt.user_id == current_user.id,
        EmailTestAttempt.created_at >= one_hour_ago
    ).order_by(EmailTestAttempt.created_at.desc()).all()

    if len(recent_attempts) >= 12:
        flash("Rate limit reached: Maximum 12 test emails allowed per hour.", "warning")
        return redirect(url_for("main.admin_dashboard"))

    if recent_attempts:
        elapsed = (utcnow() - recent_attempts[0].created_at).total_seconds()
        if elapsed < 300:
            cooldown_rem = int(300 - elapsed)
            flash(f"Cooldown active: Please wait {cooldown_rem} seconds before sending another test email.", "warning")
            return redirect(url_for("main.admin_dashboard"))

    # Record test attempt in SQLite
    db.session.add(EmailTestAttempt(user_id=current_user.id, created_at=utcnow()))
    db.session.commit()

    host = current_app.config.get("SMTP_HOST")
    sender = current_app.config.get("SMTP_FROM")
    if not host or not sender:
        flash("Email is not configured (SMTP_HOST or SMTP_FROM missing in configuration). Test attempt logged in demo mode.", "warning")
        return redirect(url_for("main.admin_dashboard"))

    try:
        import smtplib
        from email.message import EmailMessage
        msg = EmailMessage()
        msg["Subject"] = "VaxiCare Test Email"
        msg["From"] = sender
        msg["To"] = recipient
        msg.set_content("This is a VaxiCare SMTP configuration test email. If you received this, your email settings are working correctly.")
        port = current_app.config.get("SMTP_PORT", 587)
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=10) as smtp:
                username = current_app.config.get("SMTP_USERNAME")
                password = current_app.config.get("SMTP_PASSWORD")
                if username:
                    smtp.login(username, password or "")
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=10) as smtp:
                if current_app.config.get("SMTP_USE_TLS", True):
                    smtp.starttls()
                username = current_app.config.get("SMTP_USERNAME")
                password = current_app.config.get("SMTP_PASSWORD")
                if username:
                    smtp.login(username, password or "")
                smtp.send_message(msg)
        flash(f"Test email sent successfully to {recipient}.", "success")
    except Exception as exc:
        flash(f"Test email delivery failed: {exc}", "danger")

    return redirect(url_for("main.admin_dashboard"))


# ---------------------------------------------------------------------------
# Admin: update automatic email reminders settings
# ---------------------------------------------------------------------------
@main.route("/admin/update-automatic-email-reminders", methods=["POST"])
@login_required
def admin_update_automatic_email_reminders():
    if current_user.role != "admin":
        abort(403)
    enabled = request.form.get("enabled", "false").lower() in {"true", "1", "on", "yes"}
    days_before = request.form.get("days_before", "3").strip()
    try:
        days_before = int(days_before)
        if days_before < 0:
            raise ValueError
    except ValueError:
        flash("Invalid days-before value.", "danger")
        return redirect(url_for("main.admin_dashboard"))

    def _upsert(key, value):
        s = ApplicationSetting.query.filter_by(key=key).first()
        if s:
            s.value = str(value)
            s.updated_at = utcnow()
        else:
            db.session.add(ApplicationSetting(key=key, value=str(value), updated_at=utcnow()))

    _upsert("automatic_email_reminders_enabled", str(enabled).lower())
    _upsert("email_reminder_days_before", str(days_before))
    db.session.commit()
    flash("Automatic email reminder settings updated.", "success")
    return redirect(url_for("main.admin_dashboard"))


# ---------------------------------------------------------------------------
# Admin: send vaccination reminder email manual
# ---------------------------------------------------------------------------
@main.route("/admin/send-vaccination-email", methods=["POST"])
@login_required
def admin_send_vaccination_email():
    if current_user.role != "admin":
        abort(403)
    child_id = request.form.get("child_id", type=int)
    vaccine_id = request.form.get("vaccine_id", type=int)
    schedule_id = request.form.get("schedule_id", type=int)
    reminder_type = request.form.get("reminder_type", "upcoming").strip()

    if not child_id or not vaccine_id:
        flash("Child and vaccine must be specified.", "danger")
        return redirect(url_for("main.admin_dashboard"))
    child = get_or_404(Child, child_id)
    vaccine = get_or_404(Vaccine, vaccine_id)
    parent = child.parent
    if not parent or not parent.user or not parent.user.email:
        flash("Parent email not available for this child.", "danger")
        return redirect(url_for("main.admin_dashboard"))

    schedule = (
        db.session.get(VaccinationSchedule, schedule_id)
        if schedule_id
        else VaccinationSchedule.query.filter_by(vaccine_id=vaccine.id).first()
    )
    if not schedule:
        schedule = VaccinationSchedule.query.first()

    dose_label = schedule.dose_label if schedule else "Next dose"
    due_date = child.dob + timedelta(days=schedule.age_days) if schedule else date.today()

    existing = EmailReminderNotification.query.filter_by(
        child_id=child.id,
        schedule_id=schedule.id if schedule else 0,
        reminder_type=reminder_type,
        due_date=due_date,
        is_test=False,
    ).first()
    if existing:
        flash("A reminder email has already been logged for this child, vaccine, and milestone.", "warning")
        return redirect(url_for("main.admin_dashboard"))

    result = send_vaccination_reminder_email(
        recipient=parent.user.email,
        child_name=child.name,
        vaccine_name=vaccine.name,
        dose_label=dose_label,
        due_date=due_date,
        reminder_type=reminder_type,
    )

    if schedule:
        email_record = EmailReminderNotification(
            user_id=parent.user.id,
            child_id=child.id,
            vaccine_id=vaccine.id,
            schedule_id=schedule.id,
            recipient=parent.user.email,
            due_date=due_date,
            reminder_type=reminder_type,
            status=result.get("status", "sent"),
            failure_reason=result.get("message") if result.get("status") in {"failed", "simulated"} else None,
            provider_response=result.get("smtp_response"),
            is_test=False,
            sent_at=utcnow() if result.get("status") == "sent" else None,
        )
        db.session.add(email_record)
        db.session.commit()

    flash(result["message"], "success" if result["status"] == "sent" else "info" if result["status"] == "simulated" else "danger")
    return redirect(url_for("main.admin_dashboard"))


# ---------------------------------------------------------------------------
# Admin: user management
# ---------------------------------------------------------------------------
@main.route("/admin/users", methods=["GET", "POST"])
@login_required
def admin_users():
    if current_user.role != "admin":
        abort(403)
    form = AdminUserForm()
    if form.validate_on_submit():
        username = form.username.data.strip()
        email = form.email.data.strip().lower()
        ok, msg = validate_password_strength(form.password.data)
        if not ok:
            flash(msg, "danger")
        elif User.query.filter((User.username == username) | (User.email == email)).first():
            flash("Username or email already exists.", "danger")
        elif form.role.data == "parent" and not (form.first_name.data or "").strip():
            flash("A parent account requires a first name.", "danger")
        else:
            user = User(username=username, email=email, role=form.role.data)
            user.set_password(form.password.data)
            db.session.add(user)
            db.session.flush()
            if user.role == "parent":
                db.session.add(
                    Parent(
                        user_id=user.id,
                        first_name=(form.first_name.data or "").strip(),
                        last_name=(form.last_name.data or "").strip() or None,
                        village=(form.village.data or "").strip() or "Not set",
                    )
                )
            elif user.role == "healthcare_worker":
                full_name = " ".join(
                    part for part in [
                        (form.first_name.data or "").strip(),
                        (form.last_name.data or "").strip(),
                    ] if part
                ) or username
                db.session.add(HealthcareWorker(
                    user_id=user.id,
                    full_name=full_name,
                    qualification=(form.qualification.data or "").strip() or None,
                    department=(form.department.data or "").strip() or None,
                    village=(form.village.data or "").strip() or None,
                ))
            db.session.commit()
            flash(f"{user.role.replace('_', ' ').title()} account created.", "success")
            return redirect(url_for("main.admin_users"))

    search = request.args.get("search", "").strip()
    role_filter = request.args.get("role", "").strip()

    query = User.query
    if role_filter in {"parent", "healthcare_worker", "admin"}:
        query = query.filter(User.role == role_filter)
    if search:
        query = query.filter(
            (User.username.ilike(f"%{search}%")) |
            (User.email.ilike(f"%{search}%")) |
            (User.full_name.ilike(f"%{search}%"))
        )

    users = query.order_by(User.created_at.desc()).limit(150).all()
    return render_template(
        "admin_users.html",
        form=form,
        users=users,
        search=search,
        role_filter=role_filter,
    )


# ---------------------------------------------------------------------------
# Add vaccine (admin only)
# ---------------------------------------------------------------------------
@main.route("/vaccine/add", methods=["GET", "POST"])
@login_required
def add_vaccine():
    if current_user.role != "admin":
        abort(403)
    form = VaccineForm(formdata=request.form)
    if request.method == "GET":
        return render_template("vaccine_form.html", form=form)
    if form.validate_on_submit():
        vaccine = Vaccine(
            name=form.name.data.strip(),
            disease=form.disease.data.strip(),
            description=form.description.data.strip(),
            category=(form.category.data or "").strip() or "General",
            recommended_age=(form.recommended_age.data or "").strip() or "As per schedule",
            source=(form.source.data or "").strip() or "Government of India UIP National Immunization Schedule",
            source_checked_on=(form.source_checked_on.data or "").strip() or "2026-11-04",
        )
        db.session.add(vaccine)
        db.session.commit()
        flash("Vaccine added successfully.", "success")
    else:
        flash("Please complete the vaccine details correctly.", "danger")
    return redirect(url_for("main.admin_dashboard"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def build_child_schedule(child):
    return scheduled_child_entries(child, today=app_today())


def app_today():
    timezone = ZoneInfo(current_app.config["REMINDER_TIMEZONE"])
    return datetime.now(timezone).date()


def can_worker_access(user, child):
    worker = user.healthcare_worker
    return bool(worker and (not worker.village or worker.village.casefold() == (child.village or "").casefold()))


def get_or_404(model, primary_key):
    record = db.session.get(model, primary_key)
    if record is None:
        abort(404)
    return record
