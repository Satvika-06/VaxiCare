from flask_wtf import FlaskForm
from wtforms import BooleanField, DateField, PasswordField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email, EqualTo, Length, Optional


class RegistrationForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(min=3, max=40)])
    email = StringField("Email", validators=[DataRequired(), Email()])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=6, max=60)])
    first_name = StringField("First Name", validators=[DataRequired(), Length(min=2, max=80)])
    last_name = StringField("Last Name", validators=[Optional(), Length(max=80)])
    mobile = StringField("Mobile Number", validators=[Optional(), Length(min=9, max=20)])
    sms_consent = BooleanField("I agree to receive vaccination reminders by SMS")
    submit = SubmitField("Register")


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
    password = PasswordField("New password", validators=[DataRequired(), Length(min=12, max=60)])
    confirm_password = PasswordField(
        "Confirm new password",
        validators=[DataRequired(), EqualTo("password", message="Passwords must match.")],
    )
    submit = SubmitField("Reset password")


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField("Current password", validators=[DataRequired()])
    password = PasswordField("New password", validators=[DataRequired(), Length(min=12, max=60)])
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
    submit = SubmitField("Create account")


class TestSmsForm(FlaskForm):
    recipient = StringField("Test recipient (E.164 format)", validators=[DataRequired(), Length(min=8, max=20)])
    submit = SubmitField("Send test SMS")
