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

from app.forms import AdminRegistrationForm, AdminUserForm, ChangePasswordForm, ChildProfileForm, ForgotPasswordForm, LoginForm, MobileVerificationForm, RegistrationForm, ReminderForm, ResetPasswordForm, TestSmsForm, VaccinationRecordForm, VaccineForm
from app.models import Child, HealthcareWorker, Parent, PasswordResetToken, SmsNotification, User, VaccinationRecord, VaccinationSchedule, Vaccine, db, utcnow
from app.scheduling import build_child_schedule as scheduled_child_entries
from app.services import generate_record_token, generate_vaccination_pdf, is_valid_mobile_number, send_sms_notification, send_test_sms
from app.auth_services import PasswordResetDeliveryError, send_password_reset_email

main = Blueprint("main", __name__)


@main.route("/set_language/<lang>")
def set_language(lang):
    if lang not in {"en", "te"}:
        lang = "en"
    session["language"] = lang
    referrer = request.referrer
    if referrer and urlparse(referrer).netloc == request.host:
        return redirect(referrer)
    return redirect(url_for("main.home"))


@main.route("/")
def home():
    return render_template("home.html")


@main.route("/about")
def about():
    return render_template("about.html")


@main.route("/register", methods=["GET", "POST"])
def register():
    return parent_registration()


@main.route("/parent/register", methods=["GET", "POST"])
def parent_register():
    return parent_registration()


def parent_registration():
    form = RegistrationForm()
    if form.validate_on_submit():
        username = form.username.data.strip()
        email = form.email.data.strip().lower()
        mobile = form.mobile.data.strip() if form.mobile.data else ""
        if form.sms_consent.data and not mobile:
            flash("Provide a phone number before opting in to SMS reminders.", "danger")
            return render_template("register.html", form=form, auth_role="parent")
        if mobile and not is_valid_mobile_number(mobile):
            flash("Enter a valid international number in +15551234567 format.", "danger")
            return render_template("register.html", form=form, auth_role="parent")
        existing = User.query.filter(
            (func.lower(User.username) == username.lower())
            | (func.lower(User.email) == email)
        ).first()
        if existing:
            flash("Username or email already exists.", "danger")
            return render_template("register.html", form=form, auth_role="parent")
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
        parent = Parent(user_id=user.id, first_name=form.first_name.data, last_name=form.last_name.data, village="Not set")
        db.session.add(parent)
        db.session.commit()
        login_user(user)
        flash("Registration successful. Welcome to VaxiCare.", "success")
        return redirect(url_for("main.parent_dashboard"))
    return render_template("register.html", form=form, auth_role="parent")


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
    form = LoginForm()
    if form.validate_on_submit():
        identity = form.username.data.strip()
        user = User.query.filter(
            (func.lower(User.username) == identity.lower())
            | (func.lower(User.email) == identity.lower())
        ).first()
        if user and user.check_password(form.password.data) and (role is None or user.role == role):
            login_user(user, remember=bool(form.remember_me.data), fresh=True)
            flash("Login successful.", "success")
            if user.role == "parent":
                return redirect(url_for("main.parent_dashboard"))
            if user.role == "healthcare_worker":
                return redirect(url_for("main.healthcare_dashboard"))
            if user.role == "admin":
                return redirect(url_for("main.admin_dashboard"))
        flash("Invalid username or password.", "danger")
    return render_template("login.html", form=form, auth_role=auth_role)


@main.route("/admin/register", methods=["GET", "POST"])
def admin_register():
    form = AdminRegistrationForm()
    authorized = current_user.is_authenticated and current_user.role == "admin"
    if request.method == "POST" and not authorized:
        abort(403)
    if authorized and form.validate_on_submit():
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


@main.route("/healthcare/access")
def healthcare_access():
    return render_template("healthcare_access.html")


@main.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    form = ForgotPasswordForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        user = User.query.filter(func.lower(User.email) == email).first()
        if user:
            raw_token = secrets.token_urlsafe(32)
            now = utcnow()
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
            except PasswordResetDeliveryError as exc:
                token.used_at = utcnow()
                db.session.commit()
                current_app.logger.error("Password reset email delivery unavailable (%s).", type(exc).__name__)
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
        reset_token.user.set_password(form.password.data)
        now = utcnow()
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
            current_user.set_password(form.password.data)
            now = utcnow()
            PasswordResetToken.query.filter(
                PasswordResetToken.user_id == current_user.id,
                PasswordResetToken.used_at.is_(None),
            ).update({"used_at": now}, synchronize_session=False)
            db.session.commit()
            login_user(current_user, remember=False, fresh=True)
            flash("Password changed successfully.", "success")
            return redirect(url_for("main.home"))
    return render_template("change_password.html", form=form)


@main.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("Logged out successfully.", "info")
    return redirect(url_for("main.home"))


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
    return render_template(
        "dashboard.html",
        children=children,
        summary=summary,
        parent=parent,
        alerts=alerts,
        sms_settings_form=MobileVerificationForm(),
    )


@main.route("/child/add", methods=["GET", "POST"])
@login_required
def add_child():
    if current_user.role != "parent":
        abort(403)
    form = ChildProfileForm()
    if form.validate_on_submit():
        if current_user.parent is None:
            flash("Parent profile not found.", "danger")
            return redirect(url_for("main.parent_dashboard"))
        child = Child(
            parent_id=current_user.parent.id,
            name=form.name.data.strip(),
            dob=form.dob.data,
            gender=form.gender.data,
            village=form.village.data.strip(),
            allergies=form.allergies.data.strip() if form.allergies.data else "None",
            notes=form.notes.data.strip() if form.notes.data else "",
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
    if current_user.role == "parent" and (not current_user.parent or child.parent_id != current_user.parent.id):
        abort(403)
    if current_user.role not in {"parent", "healthcare_worker", "admin"}:
        abort(403)
    if current_user.role == "healthcare_worker" and not can_worker_access(current_user, child):
        abort(403)
    schedule = build_child_schedule(child)
    records = VaccinationRecord.query.filter_by(child_id=child.id).order_by(VaccinationRecord.administered_date.desc()).all()
    vaccines = Vaccine.query.order_by(Vaccine.name).all()
    return render_template("child_detail.html", child=child, schedule=schedule, records=records, vaccines=vaccines, today_date=app_today().isoformat())


@main.route("/schedule")
def vaccination_schedule():
    if current_user.is_authenticated and current_user.role == "parent":
        parent = current_user.parent
        children = Child.query.filter_by(parent_id=parent.id).all() if parent else []
        child_schedules = [(child, build_child_schedule(child)) for child in children]
        return render_template("schedule.html", children=children, child_schedules=child_schedules)
    if current_user.is_authenticated and current_user.role in {"healthcare_worker", "admin"}:
        children = Child.query.order_by(Child.created_at.desc()).all()
        if current_user.role == "healthcare_worker" and current_user.healthcare_worker and current_user.healthcare_worker.village:
            children = [child for child in children if can_worker_access(current_user, child)]
        return render_template(
            "schedule.html",
            children=children,
            child_schedules=[(child, build_child_schedule(child)) for child in children],
        )
    schedule_catalog = VaccinationSchedule.query.join(VaccinationSchedule.vaccine).order_by(
        VaccinationSchedule.age_days.asc()
    ).all()
    return render_template("schedule.html", schedule_catalog=schedule_catalog, public_view=True)


@main.route("/awareness")
def awareness_alias():
    return vaccine_awareness()


@main.route("/vaccine-awareness")
def vaccine_awareness():
    search = request.args.get("search", "").strip()
    query = Vaccine.query
    if search:
        query = query.filter((Vaccine.name.ilike(f"%{search}%")) | (Vaccine.disease.ilike(f"%{search}%")))
    vaccines = query.order_by(Vaccine.name).all()
    return render_template("awareness.html", vaccines=vaccines, search=search)


@main.route("/vaccine/<int:vaccine_id>")
def vaccine_information(vaccine_id):
    vaccine = get_or_404(Vaccine, vaccine_id)
    schedule_entries = VaccinationSchedule.query.filter_by(vaccine_id=vaccine.id).order_by(
        VaccinationSchedule.age_days.asc()
    ).all()
    if session.get("language") == "te":
        disease_display = {
            "Tuberculosis": "క్షయవ్యాధి",
            "Hepatitis B": "హెపటైటిస్ బి",
            "Polio": "పోలియో",
            "Diphtheria, Pertussis, Tetanus": "డిఫ్తీరియా, పర్టుసిస్ మరియు టెటనస్",
            "Diphtheria, Pertussis, Tetanus, Hib, Hepatitis B": "డిఫ్తీరియా, పర్టుసిస్, టెటనస్, Hib మరియు హెపటైటిస్ బి",
            "Measles and Rubella": "తట్టు మరియు రుబెల్లా",
        }.get(vaccine.disease, vaccine.disease)
        guidance = {
            "importance": "ఈ టీకా ఏ వ్యాధుల నుంచి రక్షణకు సహాయపడుతుందో పై వివరాల్లో చూడండి. వ్యక్తిగత వైద్య సూచన కోసం అర్హత కలిగిన ఆరోగ్య నిపుణుడిని సంప్రదించండి.",
            "side_effects": "ఈ టీకాకు సంబంధించిన సాధారణ ప్రతిచర్యలు ఇక్కడ ధృవీకరించబడలేదు. ఈ టీకాకు ప్రత్యేకమైన సమాచారం కోసం ఆరోగ్య నిపుణుడిని అడగండి.",
            "contact": "తీవ్రమైన ప్రతిచర్య, శ్వాస తీసుకోవడంలో ఇబ్బంది లేదా ముఖం/గొంతు వాపు ఉంటే వెంటనే వైద్య సహాయం పొందండి.",
            "care": "టీకా ముందు అలర్జీలు లేదా గత ప్రతిచర్యల గురించి ఆరోగ్య నిపుణుడికి తెలియజేయండి. క్లినిక్ సూచనలను పాటించండి.",
            "review": "ఆరోగ్య నిపుణుడి సమీక్ష అవసరం",
        }
    else:
        disease_display = vaccine.disease
        guidance = {
            "importance": "The vaccine helps protect against the diseases listed above. Ask a qualified healthcare professional for advice for an individual child.",
            "side_effects": "VaxiCare has not verified vaccine-specific expected reactions for this entry. Ask a healthcare professional for product-specific information.",
            "contact": "Seek prompt medical help for a severe reaction, difficulty breathing, swelling of the face or throat, or any symptoms that concern you.",
            "care": "Before vaccination, tell the healthcare professional about allergies or previous reactions and follow the clinic's instructions.",
            "review": "Needs healthcare-professional review",
        }
    return render_template(
        "vaccine_detail.html",
        vaccine=vaccine,
        schedule_entries=schedule_entries,
        guidance=guidance,
        disease_display=disease_display,
    )


@main.route("/reminders", methods=["GET", "POST"])
@login_required
def reminders():
    if current_user.role != "parent":
        abort(403)
    form = ReminderForm()
    children = Child.query.filter_by(parent_id=current_user.parent.id).all() if current_user.parent else []
    form.child_id.choices = [(child.id, child.name) for child in children]
    form.vaccine_id.choices = [(v.id, v.name) for v in Vaccine.query.order_by(Vaccine.name).all()]

    if form.validate_on_submit():
        child = db.session.get(Child, form.child_id.data)
        vaccine = db.session.get(Vaccine, form.vaccine_id.data)
        if not child or not vaccine:
            flash("Child or vaccine not found.", "danger")
            return redirect(url_for("main.reminders"))
        if not current_user.parent or child.parent_id != current_user.parent.id:
            abort(403)
        requested_type = form.reminder_type.data
        matching = [
            item
            for item in build_child_schedule(child)
            if item["vaccine"].id == vaccine.id and item["status"] == requested_type
        ]
        if not matching:
            flash("There is no matching vaccine dose for that reminder status.", "warning")
            return redirect(url_for("main.reminders"))
        item = matching[0]
        result = send_sms_notification(
            current_user,
            child,
            vaccine,
            requested_type,
            form.language.data,
            scheduled_for=item["due_date"],
            schedule_id=item["schedule"].id,
        )
        flash(result["message"], "success" if result["status"] == "sent" else "info" if result["status"] in {"simulated", "duplicate"} else "warning")
        return redirect(url_for("main.reminders"))

    reminder_logs = SmsNotification.query.filter_by(user_id=current_user.id).order_by(SmsNotification.created_at.desc()).limit(20).all()
    return render_template("reminders.html", form=form, reminder_logs=reminder_logs)


@main.route("/verify-mobile", methods=["POST"])
@login_required
def verify_mobile():
    if current_user.role != "parent":
        abort(403)
    form = MobileVerificationForm()
    if form.validate_on_submit():
        mobile = form.mobile.data.strip() if form.mobile.data else ""
        if form.sms_consent.data and (not mobile or not is_valid_mobile_number(mobile)):
            flash("A valid international phone number is required to enable SMS reminders.", "danger")
            return redirect(url_for("main.parent_dashboard"))
        if mobile and not is_valid_mobile_number(mobile):
            flash("Enter a valid international number in +15551234567 format.", "danger")
            return redirect(url_for("main.parent_dashboard"))
        current_user.mobile = mobile or None
        current_user.mobile_verified = False
        current_user.sms_consent = bool(form.sms_consent.data)
        db.session.commit()
        flash("Notification settings saved. Phone ownership is not verified by this form.", "success")
    else:
        flash("Please provide a valid mobile number.", "danger")
    return redirect(url_for("main.parent_dashboard"))


@main.route("/download-card/<int:child_id>")
@login_required
def download_vaccination_card(child_id):
    child = get_or_404(Child, child_id)
    if current_user.role == "parent" and (not current_user.parent or child.parent_id != current_user.parent.id):
        abort(403)
    if current_user.role not in {"parent", "healthcare_worker", "admin"}:
        abort(403)
    if current_user.role == "healthcare_worker" and not can_worker_access(current_user, child):
        abort(403)
    pdf_bytes = generate_vaccination_pdf(child, current_user.parent if current_user.role == "parent" else child.parent)
    return send_file(io.BytesIO(pdf_bytes), mimetype="application/pdf", as_attachment=True, download_name=f"{child.name}_vaccination_card.pdf")


@main.route("/record/<token>")
def public_child_record(token):
    child = Child.query.filter_by(record_token=token).first_or_404()
    schedule = build_child_schedule(child)
    return render_template("public_record.html", child=child, schedule=schedule)


@main.route("/healthcare-dashboard")
@login_required
def healthcare_dashboard():
    if current_user.role not in {"healthcare_worker", "admin"}:
        abort(403)
    search = request.args.get("search", "")
    village = request.args.get("village", "")
    status_filter = request.args.get("status", "")
    due_from_text = request.args.get("due_from", "")
    due_to_text = request.args.get("due_to", "")
    if status_filter not in {"", "upcoming", "due_today", "overdue"}:
        abort(400)
    try:
        due_from = date.fromisoformat(due_from_text) if due_from_text else None
        due_to = date.fromisoformat(due_to_text) if due_to_text else None
    except ValueError:
        abort(400)
    if due_from and due_to and due_from > due_to:
        abort(400)
    query = Child.query
    if search:
        query = query.filter(Child.name.ilike(f"%{search}%"))
    if village:
        query = query.filter(Child.village.ilike(f"%{village}%"))
    candidates = query.order_by(Child.created_at.desc()).all()
    if current_user.role == "healthcare_worker":
        candidates = [child for child in candidates if can_worker_access(current_user, child)]
    child_rows = []
    for child in candidates:
        items = [item for item in build_child_schedule(child) if item["status"] != "completed"]
        if status_filter:
            items = [item for item in items if item["status"] == status_filter]
        if due_from:
            items = [item for item in items if item["due_date"] >= due_from]
        if due_to:
            items = [item for item in items if item["due_date"] <= due_to]
        if not (status_filter or due_from or due_to):
            items = [
                item for item in items
                if item["status"] in {"due_today", "overdue"}
                or 0 <= (item["due_date"] - app_today()).days <= 30
            ]
        if items:
            child_rows.append({"child": child, "items": items})
    records = VaccinationRecord.query.order_by(VaccinationRecord.administered_date.desc()).limit(10).all()
    return render_template(
        "healthcare_dashboard.html",
        child_rows=child_rows,
        records=records,
        search=search,
        village=village,
        status_filter=status_filter,
        due_from=due_from_text,
        due_to=due_to_text,
    )


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
        next_dose = next(
            (
                item for item in build_child_schedule(child)
                if item["vaccine"].id == vaccine.id and item["status"] != "completed"
            ),
            None,
        )
        record = VaccinationRecord(
            child_id=child.id,
            vaccine_id=vaccine.id,
            schedule_id=next_dose["schedule"].id if next_dose else None,
            recorded_by_id=current_user.healthcare_worker.id if current_user.role == "healthcare_worker" and current_user.healthcare_worker else None,
            administered_date=form.administered_date.data,
            dose_status="received",
            notes=form.notes.data or "",
        )
        db.session.add(record)
        db.session.commit()
        flash("Vaccination recorded successfully.", "success")
        return redirect(url_for("main.child_detail", child_id=child.id))
    flash("Please provide a valid vaccine entry.", "danger")
    return redirect(url_for("main.healthcare_dashboard"))


@main.route("/admin-dashboard")
@login_required
def admin_dashboard():
    if current_user.role != "admin":
        abort(403)
    parent_count = User.query.filter_by(role="parent").count()
    worker_count = User.query.filter_by(role="healthcare_worker").count()
    admin_count = User.query.filter_by(role="admin").count()
    child_count = Child.query.count()
    record_count = VaccinationRecord.query.count()
    vaccine_count = Vaccine.query.count()
    notifications = SmsNotification.query.order_by(SmsNotification.created_at.desc()).limit(12).all()
    notification_counts = Counter(
        dict(
            db.session.query(SmsNotification.status, func.count(SmsNotification.id))
            .group_by(SmsNotification.status)
            .all()
        )
    )
    schedule_count = VaccinationSchedule.query.count()
    return render_template(
        "admin_dashboard.html",
        parent_count=parent_count,
        worker_count=worker_count,
        admin_count=admin_count,
        child_count=child_count,
        record_count=record_count,
        vaccine_count=vaccine_count,
        schedule_count=schedule_count,
        notification_counts=notification_counts,
        reminder_logs=notifications,
        user_form=AdminUserForm(),
        test_sms_form=TestSmsForm(),
        vaccine_form=VaccineForm(),
    )


@main.route("/admin/notifications")
@login_required
def admin_notifications():
    if current_user.role != "admin":
        abort(403)
    status_filter = request.args.get("status", "")
    allowed_statuses = {"sent", "failed", "pending", "simulated", "skipped"}
    if status_filter and status_filter not in allowed_statuses:
        abort(400)
    query = SmsNotification.query
    if status_filter:
        query = query.filter_by(status=status_filter)
    notifications = query.order_by(SmsNotification.created_at.desc()).limit(200).all()
    return render_template("admin_notifications.html", notifications=notifications, status_filter=status_filter)


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
    flash(result["message"], "success" if result["status"] == "sent" else "info" if result["status"] == "simulated" else "danger")
    return redirect(url_for("main.admin_dashboard"))


@main.route("/admin/users", methods=["GET", "POST"])
@login_required
def admin_users():
    if current_user.role != "admin":
        abort(403)
    form = AdminUserForm()
    if form.validate_on_submit():
        username = form.username.data.strip()
        email = form.email.data.strip().lower()
        if User.query.filter((User.username == username) | (User.email == email)).first():
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
                    )
                )
            elif user.role == "healthcare_worker":
                full_name = " ".join(
                    part for part in [(form.first_name.data or "").strip(), (form.last_name.data or "").strip()] if part
                ) or username
                db.session.add(HealthcareWorker(user_id=user.id, full_name=full_name))
            db.session.commit()
            flash(f"{user.role.replace('_', ' ').title()} account created.", "success")
            return redirect(url_for("main.admin_users"))
    users = User.query.order_by(User.created_at.desc()).limit(100).all()
    return render_template("admin_users.html", form=form, users=users)


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
            source=(form.source.data or "").strip()
            or "Government of India UIP National Immunization Schedule",
            source_checked_on=(form.source_checked_on.data or "").strip() or "2026-11-04",
        )
        db.session.add(vaccine)
        db.session.commit()
        flash("Vaccine added successfully.", "success")
    else:
        flash("Please complete the vaccine details correctly.", "danger")
    return redirect(url_for("main.admin_dashboard"))


@main.route("/contact")
def contact():
    return render_template("contact.html")


@main.route("/health-record/<int:child_id>")
@login_required
def health_record(child_id):
    if current_user.role not in {"healthcare_worker", "admin"}:
        abort(403)
    child = get_or_404(Child, child_id)
    if current_user.role == "healthcare_worker" and not can_worker_access(current_user, child):
        abort(403)
    vaccines = Vaccine.query.order_by(Vaccine.name).all()
    return render_template("child_detail.html", child=child, schedule=build_child_schedule(child), records=VaccinationRecord.query.filter_by(child_id=child.id).all(), vaccines=vaccines, today_date=app_today().isoformat())


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
