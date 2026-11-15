import io
import os
import re
import uuid
from datetime import date, datetime, timedelta

import qrcode
from flask import current_app
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy.exc import IntegrityError
from twilio.rest import Client

from twilio.base.exceptions import TwilioRestException

from app import db
from app.models import SmsNotification, utcnow


def generate_record_token():
    return uuid.uuid4().hex


E164_PATTERN = re.compile(r"^\+[1-9][0-9]{7,14}$")


def is_valid_mobile_number(mobile):
    return bool(mobile and E164_PATTERN.fullmatch(mobile.strip()))


def build_sms_message(language, child_name, vaccine_name, due_date, reminder_type):
    due = due_date.strftime("%d %b %Y") if isinstance(due_date, date) else str(due_date)
    if language == "te":
        messages = {
            "upcoming": f"VaxiCare: {child_name}కు {vaccine_name} టీకా {due}న ఇవ్వాల్సి ఉంది. వైద్య సిబ్బందిని సంప్రదించండి.",
            "due_today": f"VaxiCare: {child_name}కు {vaccine_name} టీకా ఈ రోజు ఇవ్వాల్సి ఉంది ({due}). వైద్య సిబ్బందిని సంప్రదించండి.",
            "overdue": f"VaxiCare: {child_name}కు {vaccine_name} టీకా {due}న ఇవ్వాల్సి ఉంది. వైద్య సిబ్బందిని సంప్రదించండి.",
        }
        return messages.get(reminder_type, f"VaxiCare: {child_name}కు {vaccine_name} టీకా {due}న ఇవ్వాల్సి ఉంది.")

    messages = {
        "upcoming": f"VaxiCare reminder: {child_name} is due for {vaccine_name} on {due}. Please contact your healthcare provider for vaccination guidance.",
        "due_today": f"VaxiCare reminder: {child_name} is due for {vaccine_name} on {due}. Please contact your healthcare provider for vaccination guidance.",
        "overdue": f"VaxiCare reminder: {child_name} was due for {vaccine_name} on {due}. Please contact your healthcare provider for vaccination guidance.",
    }
    return messages.get(reminder_type, f"VaxiCare reminder: {child_name} is due for {vaccine_name} on {due}.")


def _record_result(notification, status, failure_reason=None, provider_message_id=None):
    notification.status = status
    notification.failure_reason = failure_reason
    notification.provider_message_id = provider_message_id
    notification.next_attempt_at = None
    db.session.commit()
    return {
        "status": status,
        "message": failure_reason or f"SMS status: {status}.",
        "sid": provider_message_id,
    }


def _attempt_delivery(notification, require_consent=True):
    user = notification.user
    if require_consent and (not user or not user.sms_consent):
        return _record_result(notification, "skipped", "SMS consent is not enabled.")
    if not is_valid_mobile_number(notification.recipient):
        return _record_result(notification, "skipped", "Recipient phone number is missing or invalid.")

    force_demo = current_app.config.get("SMS_FORCE_DEMO", False)
    sid = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    from_number = os.getenv("TWILIO_PHONE_NUMBER")
    if force_demo or not (sid and token and from_number):
        return _record_result(notification, "simulated", "Demo mode: SMS provider credentials are not active.")
    if notification.attempt_count >= current_app.config.get("SMS_MAX_ATTEMPTS", 3):
        return _record_result(notification, "failed", "SMS retry limit reached.")

    try:
        notification.attempt_count += 1
        db.session.commit()
        sent = Client(sid, token).messages.create(
            body=notification.message,
            from_=from_number,
            to=notification.recipient,
        )
        if not sent.sid:
            raise RuntimeError("SMS provider did not return an acceptance ID.")
        _record_result(notification, "sent", provider_message_id=sent.sid)
        return {"status": "sent", "message": "SMS accepted by the provider.", "sid": sent.sid}
    except TwilioRestException as exc:
        retryable = exc.status is None or exc.status in {408, 429} or exc.status >= 500
        return _handle_provider_failure(notification, retryable)
    except Exception as exc:
        current_app.logger.error("SMS provider request failed (%s).", type(exc).__name__)
        return _handle_provider_failure(notification, retryable=True)


def _handle_provider_failure(notification, retryable):
    max_attempts = current_app.config.get("SMS_MAX_ATTEMPTS", 3)
    if retryable and notification.attempt_count < max_attempts:
        notification.status = "pending"
        notification.failure_reason = "Temporary SMS provider failure; retry scheduled."
        notification.next_attempt_at = utcnow() + timedelta(
            minutes=current_app.config.get("SMS_RETRY_MINUTES", 15)
        )
        db.session.commit()
        return {"status": "pending", "message": notification.failure_reason}
    _record_result(notification, "failed", "SMS provider rejected the request.")
    return {"status": "failed", "message": "SMS provider rejected the request."}


def _queue_sms(user, child, vaccine, reminder_type, language, due_date, schedule_id=None, recipient_override=None):
    recipient = (recipient_override if recipient_override is not None else (user.mobile if user else "")).strip()
    notification = SmsNotification(
        user_id=user.id if user else None,
        child_id=child.id if child else None,
        vaccine_id=vaccine.id if vaccine else None,
        schedule_id=schedule_id,
        recipient=recipient[:20],
        due_date=due_date,
        message_type=reminder_type,
        language=language if language in {"en", "te"} else "en",
        next_attempt_at=utcnow()
        + timedelta(minutes=current_app.config.get("SMS_RETRY_MINUTES", 15)),
        message=(
            build_sms_message(language, child.name, vaccine.name, due_date, reminder_type)
            if child and vaccine and due_date
            else "VaxiCare test SMS. Your SMS configuration is working."
        ),
    )
    existing = None
    if child and vaccine and due_date:
        existing = SmsNotification.query.filter_by(
            recipient=notification.recipient,
            child_id=child.id,
            vaccine_id=vaccine.id,
            message_type=reminder_type,
            due_date=due_date,
        ).first()
    if existing:
        return {"status": "duplicate", "message": "This reminder was already logged."}
    db.session.add(notification)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return {"status": "duplicate", "message": "This reminder was already logged."}
    return notification


def send_sms_notification(
    user,
    child,
    vaccine,
    reminder_type,
    language="en",
    scheduled_for=None,
    db=None,
    schedule_id=None,
):
    notification = _queue_sms(
        user,
        child,
        vaccine,
        reminder_type,
        language,
        scheduled_for or date.today(),
        schedule_id,
    )
    if isinstance(notification, dict):
        return notification
    result = _attempt_delivery(notification)
    if result["status"] == "simulated":
        result["message"] = "Demo mode: a simulated SMS was logged; no real SMS was sent."
    return result


def send_test_sms(user, recipient):
    notification = _queue_sms(user, None, None, "test", "en", None, recipient_override=recipient)
    if isinstance(notification, dict):
        return notification
    result = _attempt_delivery(notification, require_consent=False)
    if result["status"] == "simulated":
        result["message"] = "Demo mode: a simulated test SMS was logged; no real SMS was sent."
    return result


def retry_due_notifications(now=None):
    now = now or utcnow()
    notifications = SmsNotification.query.filter(
        SmsNotification.status == "pending",
        SmsNotification.next_attempt_at.isnot(None),
        SmsNotification.next_attempt_at <= now,
    ).all()
    results = []
    for notification in notifications:
        result = _attempt_delivery(notification)
        results.append(result["status"])
    return results


def generate_child_qr_code(child):
    base_url = os.getenv("PUBLIC_BASE_URL", "http://localhost:5000")
    payload = f"{base_url}/record/{child.record_token}"
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def generate_vaccination_pdf(child, parent=None):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, title=f"{child.name} Vaccination Card")
    styles = getSampleStyleSheet()
    elements = []
    elements.append(Paragraph("VaxiCare Digital Vaccination Card", styles["Title"]))
    elements.append(Spacer(1, 20))
    info = [
        ("Child Name", child.name),
        ("Date of Birth", child.dob.strftime("%d-%b-%Y") if child.dob else "-"),
        ("Village", child.village or "-"),
        ("Parent", f"{parent.first_name} {parent.last_name}" if parent else "-"),
    ]
    table = Table(info, colWidths=[180, 300])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f766e")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 1, colors.grey),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    elements.append(table)

    from app.models import VaccinationRecord

    records = VaccinationRecord.query.filter_by(child_id=child.id).order_by(VaccinationRecord.administered_date.asc()).all()
    if records:
        elements.append(Spacer(1, 18))
        elements.append(Paragraph("Completed Vaccinations", styles["Heading2"]))
        rows = [["Vaccine", "Date Given", "Dose"]]
        for rec in records:
            rows.append([rec.vaccine.name, rec.administered_date.strftime("%d-%b-%Y"), rec.dose_status])
        vaccine_table = Table(rows, colWidths=[200, 150, 130])
        vaccine_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#14b8a6")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("GRID", (0, 0), (-1, -1), 1, colors.grey),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ]
            )
        )
        elements.append(vaccine_table)

    elements.append(Spacer(1, 18))
    elements.append(Paragraph("Informational use only. Confirm with an authorized healthcare professional.", styles["BodyText"]))
    doc.build(elements)
    pdf = buffer.getvalue()
    buffer.close()
    return pdf



def send_vaccination_reminder_email(recipient, child_name, vaccine_name, dose_label, due_date, reminder_type, is_test=False):
    import smtplib
    from email.message import EmailMessage

    host = current_app.config.get("SMTP_HOST")
    sender = current_app.config.get("SMTP_FROM") or current_app.config.get("MAIL_DEFAULT_SENDER", "info@vaxicare.local")

    due = due_date.strftime("%d %b %Y") if isinstance(due_date, (date, datetime)) else str(due_date)
    test_prefix = "[TEST] " if is_test else ""
    subject = f"{test_prefix}VaxiCare Immunization Reminder: {child_name} - {vaccine_name} ({dose_label})"

    body = (
        f"{test_prefix}Dear Parent,\n\n"
        f"This is a reminder from VaxiCare regarding immunization for {child_name}.\n"
        f"Vaccine: {vaccine_name}\n"
        f"Dose: {dose_label}\n"
        f"Due Date: {due}\n"
        f"Reminder Category: {reminder_type}\n\n"
        "Please visit your primary healthcare centre or consult your community health worker.\n\n"
        "Regards,\nVaxiCare Health Tracking Team"
    )

    if not host or not sender:
        return {"status": "simulated", "message": "SMTP not configured; reminder email logged in demo mode."}

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = recipient
    message.set_content(body)

    try:
        port = current_app.config.get("SMTP_PORT", 587)
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=15) as smtp:
                username = current_app.config.get("SMTP_USERNAME")
                password = current_app.config.get("SMTP_PASSWORD")
                if username:
                    smtp.login(username, password or "")
                smtp.send_message(message)
                return {"status": "sent", "message": "Email accepted by SMTP server.", "smtp_response": "250 OK"}
        else:
            with smtplib.SMTP(host, port, timeout=15) as smtp:
                if current_app.config.get("SMTP_USE_TLS", True):
                    smtp.starttls()
                username = current_app.config.get("SMTP_USERNAME")
                password = current_app.config.get("SMTP_PASSWORD")
                if username:
                    smtp.login(username, password or "")
                smtp.send_message(message)
                return {"status": "sent", "message": "Email accepted by SMTP server.", "smtp_response": "250 OK"}
    except Exception as exc:
        return {"status": "failed", "message": f"SMTP delivery failed: {exc}"}
