from flask_wtf import FlaskForm
from wtforms import BooleanField, DateField, IntegerField, PasswordField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email, EqualTo, Length, NumberRange, Optional


class RegistrationForm(FlaskForm):
    first_name = StringField("First Name", validators=[DataRequired(), Length(min=2, max=80)])
    last_name = StringField("Last Name", validators=[Optional(), Length(max=80)])
    username = StringField("Username", validators=[DataRequired(), Length(min=3, max=40)])
    email = StringField("Email", validators=[DataRequired(), Email()])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=8, max=60)])
    confirm_password = PasswordField(
        "Confirm Password",
        validators=[DataRequired(), EqualTo("password", message="Passwords must match.")],
    )
    mobile = StringField("Mobile Number", validators=[Optional(), Length(min=8, max=20)])
    sms_consent = BooleanField("I agree to receive vaccination reminders by SMS")
    submit = SubmitField("Register")


class HealthcareWorkerRegistrationForm(FlaskForm):
    full_name = StringField("Full Name", validators=[DataRequired(), Length(min=2, max=120)])
    username = StringField("Username", validators=[DataRequired(), Length(min=3, max=40)])
    email = StringField("Email", validators=[DataRequired(), Email()])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=8, max=60)])
    confirm_password = PasswordField(
        "Confirm Password",
        validators=[DataRequired(), EqualTo("password", message="Passwords must match.")],
    )
    qualification = StringField("Qualification (e.g. MBBS, GNM, ANM)", validators=[DataRequired(), Length(min=2, max=80)])
    department = StringField("Department (e.g. Immunization, Pediatrics)", validators=[DataRequired(), Length(min=2, max=80)])
    village = StringField("Assigned Village / Primary Health Centre", validators=[DataRequired(), Length(min=2, max=80)])
    mobile = StringField("Mobile Number", validators=[Optional(), Length(min=8, max=20)])
    submit = SubmitField("Register as Healthcare Worker")


class LoginForm(FlaskForm):
    username = StringField("Username or email", validators=[DataRequired()])
    password = PasswordField("Password", validators=[DataRequired()])
    remember_me = BooleanField("Remember me")
    submit = SubmitField("Login")


class AdminRegistrationForm(FlaskForm):
    full_name = StringField("Full name", validators=[DataRequired(), Length(min=2, max=120)])
    username = StringField("Username", validators=[DataRequired(), Length(min=3, max=40)])
    email = StringField("Email", validators=[DataRequired(), Email()])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=12, max=60)])
    confirm_password = PasswordField(
        "Confirm password",
        validators=[DataRequired(), EqualTo("password", message="Passwords must match.")],
    )
    submit = SubmitField("Create administrator account")


class ForgotPasswordForm(FlaskForm):
    email = StringField("Email address", validators=[DataRequired(), Email()])
    submit = SubmitField("Send reset instructions")


class ResetPasswordForm(FlaskForm):
    password = PasswordField("New password", validators=[DataRequired(), Length(min=8, max=60)])
    confirm_password = PasswordField(
        "Confirm new password",
        validators=[DataRequired(), EqualTo("password", message="Passwords must match.")],
    )
    submit = SubmitField("Reset password")


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField("Current password", validators=[DataRequired()])
    password = PasswordField("New password", validators=[DataRequired(), Length(min=8, max=60)])
    confirm_password = PasswordField(
        "Confirm new password",
        validators=[DataRequired(), EqualTo("password", message="Passwords must match.")],
    )
    submit = SubmitField("Change password")


class ChildProfileForm(FlaskForm):
    name = StringField("Child Name", validators=[DataRequired(), Length(min=2, max=150)])
    dob = DateField("Date of Birth", validators=[DataRequired()])
    gender = SelectField("Gender", choices=[("Male", "Male"), ("Female", "Female"), ("Other", "Other")], validators=[DataRequired()])
    village = StringField("Village", validators=[DataRequired(), Length(min=2, max=80)])
    allergies = TextAreaField("Allergies (optional)", validators=[Optional()])
    notes = TextAreaField("Notes (optional)", validators=[Optional()])
    submit = SubmitField("Save Child")


class MobileVerificationForm(FlaskForm):
    mobile = StringField("Mobile Number", validators=[Optional(), Length(min=8, max=20)])
    sms_consent = BooleanField("I agree to receive vaccination reminders by SMS")
    submit = SubmitField("Save notification settings")


class MobileOtpForm(FlaskForm):
    otp = StringField("6-Digit Code", validators=[DataRequired(), Length(min=6, max=6)])
    submit = SubmitField("Verify phone")


class VaccinationRecordForm(FlaskForm):
    vaccine_id = SelectField("Vaccine", coerce=int, validators=[DataRequired()])
    administered_date = DateField("Date Given", validators=[DataRequired()])
    notes = TextAreaField("Notes", validators=[Optional()])
    submit = SubmitField("Record Vaccine")


class ReminderForm(FlaskForm):
    child_id = SelectField("Child", coerce=int, validators=[DataRequired()])
    vaccine_id = SelectField("Vaccine", coerce=int, validators=[DataRequired()])
    reminder_type = SelectField("Reminder Type", choices=[("upcoming", "Upcoming"), ("due_today", "Due Today"), ("overdue", "Overdue")], validators=[DataRequired()])
    language = SelectField("Language", choices=[("en", "English"), ("te", "Telugu")], validators=[DataRequired()])
    submit = SubmitField("Send Reminder")


class VaccineForm(FlaskForm):
    name = StringField("Vaccine Name", validators=[DataRequired(), Length(min=2, max=150)])
    disease = StringField("Disease Targeted", validators=[DataRequired(), Length(min=2, max=120)])
    description = TextAreaField("Description", validators=[DataRequired()])
    category = StringField("Category", validators=[Optional()])
    recommended_age = StringField("Recommended Age", validators=[Optional()])
    source = StringField("Source", validators=[Optional()])
    source_checked_on = StringField("Reference Checked On", validators=[Optional()])
    submit = SubmitField("Add Vaccine")


class AdminUserForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(min=3, max=40)])
    email = StringField("Email", validators=[DataRequired(), Email()])
    password = PasswordField("Temporary password", validators=[DataRequired(), Length(min=8, max=60)])
    role = SelectField(
        "Role",
        choices=[("parent", "Parent"), ("healthcare_worker", "Healthcare worker"), ("admin", "Administrator")],
        validators=[DataRequired()],
    )
    first_name = StringField("First name", validators=[Optional(), Length(max=80)])
    last_name = StringField("Last name", validators=[Optional(), Length(max=80)])
    qualification = StringField("Qualification", validators=[Optional(), Length(max=80)])
    department = StringField("Department", validators=[Optional(), Length(max=80)])
    village = StringField("Village", validators=[Optional(), Length(max=80)])
    submit = SubmitField("Create account")


class TestSmsForm(FlaskForm):
    recipient = StringField("Test recipient (E.164 format)", validators=[DataRequired(), Length(min=8, max=20)])
    submit = SubmitField("Send test SMS")


class AutomaticEmailReminderSettingsForm(FlaskForm):
    """Settings form for automatic email reminders (admin panel)."""
    enabled = SelectField(
        "Automatic email reminders",
        choices=[("true", "Enabled"), ("false", "Disabled")],
        validators=[DataRequired()],
    )
    days_before = IntegerField(
        "Days before due date to send reminder",
        validators=[DataRequired(), NumberRange(min=0, max=365)],
        default=3,
    )
    overdue_after_days = IntegerField(
        "Days after due date to flag as overdue",
        validators=[DataRequired(), NumberRange(min=1, max=365)],
        default=7,
    )
    interval_seconds = IntegerField(
        "Scheduler check interval (seconds)",
        validators=[DataRequired(), NumberRange(min=10, max=86400)],
        default=3600,
    )
    submit = SubmitField("Save settings")


class TestEmailForm(FlaskForm):
    """Send a one-off test email to verify SMTP settings."""
    recipient = StringField("Test recipient email", validators=[DataRequired(), Email()])
    submit = SubmitField("Send test email")


class ParentProfileForm(FlaskForm):
    first_name = StringField("First Name", validators=[DataRequired(), Length(min=2, max=80)])
    last_name = StringField("Last Name", validators=[Optional(), Length(max=80)])
    village = StringField("Village", validators=[Optional(), Length(max=80)])
    address = StringField("Address", validators=[Optional(), Length(max=200)])
    mobile = StringField("Mobile Number", validators=[Optional(), Length(min=8, max=20)])
    sms_consent = BooleanField("I agree to receive vaccination reminders by SMS")
    submit = SubmitField("Save Profile")


class HealthcareWorkerProfileForm(FlaskForm):
    full_name = StringField("Full Name", validators=[DataRequired(), Length(min=2, max=120)])
    qualification = StringField("Qualification", validators=[Optional(), Length(max=120)])
    department = StringField("Department", validators=[Optional(), Length(max=80)])
    village = StringField("Assigned Village / PHC", validators=[Optional(), Length(max=80)])
    mobile = StringField("Mobile Number", validators=[Optional(), Length(min=8, max=20)])
    submit = SubmitField("Save profile")


class DeleteChildForm(FlaskForm):
    """CSRF-protected confirmation form for child deletion."""
    submit = SubmitField("Delete")


class ReminderEmailForm(FlaskForm):
    """Manual vaccination reminder email form (admin panel)."""
    parent_id = SelectField("Parent", coerce=int, validators=[DataRequired()])
    child_id = SelectField("Child", coerce=int, validators=[DataRequired()])
    vaccine_id = SelectField("Vaccine", coerce=int, validators=[DataRequired()])
    schedule_id = SelectField("Scheduled dose", coerce=int, validators=[DataRequired()])
    reminder_type = SelectField(
        "Reminder type",
        choices=[("upcoming", "Upcoming"), ("due_today", "Due today"), ("overdue", "Overdue")],
        validators=[DataRequired()],
    )
    submit = SubmitField("Send vaccination email")
