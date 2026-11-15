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
EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


def normalize_phone_number(mobile, default_country_code="+91"):
    """
    Normalizes a phone number to standard international E.164 format.
    - Preserves already valid +CountryCode numbers (cleaning internal spaces/dashes)
    - Adds default country code (+91) to 10-digit Indian numbers
    - Handles leading 0 (09876543210 -> +919876543210)
    - Handles numbers starting with 91 without '+' (919876543210 -> +919876543210)
    - Returns None if input is invalid or cannot be normalized to E.164
    """
    if not mobile or not isinstance(mobile, str):
        return None
    cleaned = re.sub(r"[\s\-\(\)\.]", "", mobile.strip())
    if not cleaned:
        return None
    if cleaned.startswith("+"):
        digits = cleaned[1:]
        if digits.isdigit() and 7 <= len(digits) <= 15:
            return cleaned
        return None
    if cleaned.startswith("00"):
        digits = cleaned[2:]
        if digits.isdigit() and 7 <= len(digits) <= 15:
            return f"+{digits}"
        return None
    if cleaned.startswith("0") and len(cleaned) == 11 and cleaned[1:].isdigit():
        return f"{default_country_code}{cleaned[1:]}"
    if len(cleaned) == 10 and cleaned.isdigit():
        return f"{default_country_code}{cleaned}"
    if len(cleaned) == 12 and cleaned.startswith("91") and cleaned.isdigit():
        return f"+{cleaned}"
    return None


def is_valid_mobile_number(mobile):
    if not mobile or not isinstance(mobile, str):
        return False
    norm = normalize_phone_number(mobile)
    return bool(norm and E164_PATTERN.fullmatch(norm))


def is_valid_email_address(email):
    return bool(email and isinstance(email, str) and EMAIL_PATTERN.match(email.strip()))


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


def is_sms_demo_mode_active():
    """Return True if SMS_FORCE_DEMO is enabled via config or .env."""
    try:
        from flask import current_app
        if current_app.config.get("TESTING"):
            return bool(current_app.config.get("SMS_FORCE_DEMO", False))
    except RuntimeError:
        pass

    try:
        from dotenv import dotenv_values
        raw = dotenv_values().get("SMS_FORCE_DEMO")
        if raw is not None:
            active = raw.strip().lower() in {"true", "1", "yes", "on"}
            try:
                from flask import current_app
                current_app.config["SMS_FORCE_DEMO"] = active
            except RuntimeError:
                pass
            return active
    except Exception:
        pass

    try:
        from flask import current_app
        val = current_app.config.get("SMS_FORCE_DEMO")
        if val is not None:
            return bool(val)
    except RuntimeError:
        pass

    return os.getenv("SMS_FORCE_DEMO", "false").strip().lower() in {"true", "1", "yes", "on"}


def _attempt_delivery(notification, require_consent=True):
    import socket
    user = notification.user
    if notification.message_type == "test":
        require_consent = False

    if require_consent and (not user or not user.sms_consent):
        return _record_result(notification, "skipped", "SMS consent is not enabled.")
    if not is_valid_mobile_number(notification.recipient):
        return _record_result(notification, "skipped", "Recipient phone number is missing or invalid.")

    normalized = normalize_phone_number(notification.recipient)
    if normalized and normalized != notification.recipient:
        notification.recipient = normalized[:20]

    force_demo = is_sms_demo_mode_active()
    is_testing = current_app.config.get("TESTING", False)
    is_test_message = notification.message_type == "test"

    environment = (current_app.config.get("ENV") or os.getenv("FLASK_ENV", "development")).lower()
    if environment == "production" and force_demo and not is_testing:
        return _record_result(
            notification,
            "failed",
            "Configuration Error: SMS Demo Mode is prohibited in production.",
        )

    if force_demo:
        return _record_result(
            notification,
            "simulated",
            "SMS simulated successfully — no message was sent.",
            provider_message_id=None,
        )

    sid = (current_app.config.get("TWILIO_ACCOUNT_SID") or os.getenv("TWILIO_ACCOUNT_SID") or "").strip()
    token = (current_app.config.get("TWILIO_AUTH_TOKEN") or os.getenv("TWILIO_AUTH_TOKEN") or "").strip()
    from_number = (current_app.config.get("TWILIO_PHONE_NUMBER") or os.getenv("TWILIO_PHONE_NUMBER") or "").strip()
    if not (sid and token and from_number):
        try:
            from dotenv import dotenv_values
            ev = dotenv_values()
            sid = sid or (ev.get("TWILIO_ACCOUNT_SID") or "").strip()
            token = token or (ev.get("TWILIO_AUTH_TOKEN") or "").strip()
            from_number = from_number or (ev.get("TWILIO_PHONE_NUMBER") or "").strip()
        except Exception:
            pass
    if sid:
        current_app.config["TWILIO_ACCOUNT_SID"] = sid
    if token:
        current_app.config["TWILIO_AUTH_TOKEN"] = token
    if from_number:
        current_app.config["TWILIO_PHONE_NUMBER"] = from_number

    if not (sid and token and from_number):
        return _record_result(notification, "simulated", "Demo mode: SMS provider credentials are not active.")

    if force_demo and (is_testing or not is_test_message):
        return _record_result(notification, "simulated", "Demo mode: SMS provider credentials are not active.")

    if notification.attempt_count >= current_app.config.get("SMS_MAX_ATTEMPTS", 3):
        return _record_result(notification, "failed", "SMS retry limit reached.")

    try:
        notification.attempt_count += 1
        db.session.commit()
        client = Client(sid, token)
        sent = client.messages.create(
            body=notification.message,
            from_=from_number,
            to=notification.recipient,
        )
        if not sent or not getattr(sent, "sid", None):
            raise RuntimeError("SMS provider did not return an acceptance ID.")
        _record_result(notification, "sent", provider_message_id=sent.sid)
        return {
            "status": "sent",
            "message": f"SMS accepted by provider for {notification.recipient} (SID: {sent.sid}).",
            "sid": sent.sid,
        }
    except TwilioRestException as exc:
        code = getattr(exc, "code", None)
        status_code = getattr(exc, "status", None)
        current_app.logger.warning("Twilio API error occurred (code=%s, status=%s)", code, status_code)

        if code in {20003} or status_code == 401:
            err_msg = "Twilio authentication failed: Invalid Account SID or Auth Token."
            retryable = False
        elif code in {21211, 21614, 21612}:
            err_msg = f"Invalid recipient phone number '{notification.recipient}': Check phone format."
            retryable = False
        elif code in {21608, 21606}:
            err_msg = f"Twilio unverified number restriction: '{notification.recipient}' must be verified in your Twilio Console."
            retryable = False
        elif code in {21610}:
            err_msg = "Recipient phone number has unsubscribed from SMS notifications."
            retryable = False
        elif code in {20008, 21617, 21408}:
            err_msg = "Twilio account balance insufficient or international SMS permission disabled."
            retryable = False
        elif code in {21659}:
            err_msg = "Twilio regulatory restriction: A2P 10DLC or carrier registration required."
            retryable = False
        elif code in {572006}:
            err_msg = (
                "Twilio Trial Restriction (Error 572006): Free trial accounts cannot send custom SMS messages. "
                "Upgrade your Twilio account in the Twilio Console to remove trial restrictions."
            )
            retryable = False
        elif status_code in {408, 429} or (status_code and status_code >= 500):
            err_msg = "Twilio service temporarily unavailable; retry scheduled."
            retryable = True
        else:
            raw_msg = (getattr(exc, "msg", "") or "").strip()
            if raw_msg.startswith("Unable to create record:"):
                raw_msg = raw_msg[len("Unable to create record:"):].strip()
            if raw_msg and len(raw_msg) < 150:
                err_msg = f"SMS provider rejected the request: {raw_msg} (Twilio Error {code or status_code})."
            else:
                err_msg = f"SMS provider rejected the request (Twilio Error {code or status_code or 'unknown'})."
            retryable = False

        if is_test_message:
            retryable = False

        return _handle_provider_failure(notification, retryable, custom_message=err_msg)
    except (socket.timeout, TimeoutError):
        current_app.logger.warning("Twilio connection timed out")
        retryable = not is_test_message
        return _handle_provider_failure(notification, retryable, custom_message="Twilio connection timed out.")
    except (ConnectionError, OSError):
        current_app.logger.warning("Network failure connecting to Twilio")
        retryable = not is_test_message
        return _handle_provider_failure(notification, retryable, custom_message="Unable to connect to Twilio SMS gateway. Check network connectivity.")
    except Exception as exc:
        current_app.logger.error("Unexpected error during SMS dispatch: %s", type(exc).__name__)
        return _handle_provider_failure(notification, retryable=False, custom_message="Unexpected error occurred while dispatching SMS.")


def _handle_provider_failure(notification, retryable, custom_message=None):
    max_attempts = current_app.config.get("SMS_MAX_ATTEMPTS", 3)
    default_msg = custom_message or "SMS provider rejected the request."
    if retryable and notification.attempt_count < max_attempts:
        notification.status = "pending"
        notification.failure_reason = default_msg
        notification.next_attempt_at = utcnow() + timedelta(
            minutes=current_app.config.get("SMS_RETRY_MINUTES", 15)
        )
        db.session.commit()
        return {"status": "pending", "message": notification.failure_reason}
    _record_result(notification, "failed", failure_reason=default_msg)
    return {"status": "failed", "message": default_msg}


def _queue_sms(user, child, vaccine, reminder_type, language, due_date, schedule_id=None, recipient_override=None):
    raw_recipient = (recipient_override if recipient_override is not None else (user.mobile if user else "")).strip()
    recipient = normalize_phone_number(raw_recipient) or raw_recipient
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
            else "VaxiCare test SMS. Your Twilio SMS integration is configured and active."
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
        result["message"] = "SMS simulated successfully — no message was sent."
    return result


def send_test_sms(user, recipient):
    one_hour_ago = utcnow() - timedelta(hours=1)
    recent_count = SmsNotification.query.filter(
        SmsNotification.message_type == "test",
        SmsNotification.created_at >= one_hour_ago,
    ).count()
    if recent_count >= 10:
        return {
            "status": "failed",
            "message": "Rate limit exceeded: maximum 10 test SMS attempts per hour.",
        }

    normalized = normalize_phone_number(recipient)
    if not normalized or not is_valid_mobile_number(normalized):
        return {
            "status": "failed",
            "message": "Invalid recipient phone number. Please use international E.164 format (e.g. +919121478435).",
        }

    notification = _queue_sms(user, None, None, "test", "en", None, recipient_override=normalized)
    if isinstance(notification, dict):
        return notification
    result = _attempt_delivery(notification, require_consent=False)
    if result["status"] == "simulated":
        result["message"] = "SMS simulated successfully — no message was sent."
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



def _send_smtp_email(recipient, subject, text_content, html_content=None):
    """
    Core SMTP delivery function using Gmail SMTP / standard SMTP.
    - Validates recipient syntax.
    - Connects to MAIL_SERVER / SMTP_HOST with TLS.
    - Authenticates using MAIL_USERNAME / MAIL_PASSWORD (cleaning whitespace from App Passwords).
    - Ensures success is reported only when the SMTP server accepts the message (250 OK).
    - Protects credentials from leaking in logs or error return messages.
    """
    import logging
    import smtplib
    import socket
    from email.message import EmailMessage

    logger = logging.getLogger(__name__)

    if not is_valid_email_address(recipient):
        return {
            "status": "failed",
            "message": f"Invalid recipient email address format: '{recipient}'.",
            "smtp_response": "501 Syntax error",
        }

    mail_enabled = current_app.config.get("MAIL_ENABLED", True)
    if not mail_enabled:
        return {
            "status": "simulated",
            "message": "Email delivery is disabled by configuration (MAIL_ENABLED is false).",
            "smtp_response": None,
        }

    host = current_app.config.get("MAIL_SERVER") or current_app.config.get("SMTP_HOST")
    sender = current_app.config.get("MAIL_DEFAULT_SENDER") or current_app.config.get("SMTP_FROM")
    if not host or not sender:
        return {
            "status": "simulated",
            "message": "SMTP not configured (MAIL_SERVER or MAIL_DEFAULT_SENDER missing); email logged in demo mode.",
            "smtp_response": None,
        }

    port = int(current_app.config.get("MAIL_PORT") or current_app.config.get("SMTP_PORT") or 587)
    username = current_app.config.get("MAIL_USERNAME") or current_app.config.get("SMTP_USERNAME")
    password = current_app.config.get("MAIL_PASSWORD") or current_app.config.get("SMTP_PASSWORD") or ""
    use_tls = current_app.config.get("MAIL_USE_TLS", current_app.config.get("SMTP_USE_TLS", True))

    if password and ("gmail" in (host or "").lower() or len(password.replace(" ", "")) == 16):
        password = password.replace(" ", "").strip()

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = recipient.strip()
    message.set_content(text_content)
    if html_content:
        message.add_alternative(html_content, subtype="html")

    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=15) as smtp:
                if username:
                    smtp.login(username, password)
                refused = smtp.send_message(message)
                if refused:
                    return {
                        "status": "failed",
                        "message": f"SMTP recipient delivery refused for {recipient}.",
                        "smtp_response": "550 Recipient refused",
                    }
                return {
                    "status": "sent",
                    "message": f"Email successfully accepted by SMTP server for {recipient}.",
                    "smtp_response": "250 OK",
                }
        else:
            with smtplib.SMTP(host, port, timeout=15) as smtp:
                smtp.ehlo()
                if use_tls:
                    smtp.starttls()
                    smtp.ehlo()
                if username:
                    smtp.login(username, password)
                refused = smtp.send_message(message)
                if refused:
                    return {
                        "status": "failed",
                        "message": f"SMTP recipient delivery refused for {recipient}.",
                        "smtp_response": "550 Recipient refused",
                    }
                return {
                    "status": "sent",
                    "message": f"Email successfully accepted by SMTP server for {recipient}.",
                    "smtp_response": "250 OK",
                }
    except smtplib.SMTPAuthenticationError:
        logger.warning("SMTP authentication failure on server %s for sender %s", host, sender)
        return {
            "status": "failed",
            "message": "Gmail SMTP authentication failed: Invalid username or App Password. Check your 16-character Google App Password.",
            "smtp_response": "535 Authentication failed",
        }
    except smtplib.SMTPRecipientsRefused:
        logger.warning("SMTP recipient refused for %s", recipient)
        return {
            "status": "failed",
            "message": f"Recipient address was refused by Gmail SMTP server: {recipient}.",
            "smtp_response": "550 Recipient refused",
        }
    except smtplib.SMTPSenderRefused:
        logger.warning("SMTP sender refused for %s", sender)
        return {
            "status": "failed",
            "message": "Sender address was refused by Gmail SMTP server.",
            "smtp_response": "553 Sender refused",
        }
    except smtplib.SMTPDataError as exc:
        logger.warning("SMTP data error during transmission")
        return {
            "status": "failed",
            "message": "Gmail SMTP server rejected email content.",
            "smtp_response": f"554 Data error: {getattr(exc, 'smtp_code', 554)}",
        }
    except (socket.timeout, TimeoutError):
        logger.warning("SMTP connection timeout to %s:%s", host, port)
        return {
            "status": "failed",
            "message": f"Connection to Gmail SMTP server timed out ({host}:{port}).",
            "smtp_response": "Timeout",
        }
    except (smtplib.SMTPConnectError, ConnectionRefusedError, socket.gaierror, OSError):
        logger.warning("SMTP connection error connecting to %s:%s", host, port)
        return {
            "status": "failed",
            "message": f"Unable to connect to Gmail SMTP server ({host}:{port}). Please verify network connectivity.",
            "smtp_response": "Connection failed",
        }
    except smtplib.SMTPException:
        logger.warning("SMTP exception during delivery")
        return {
            "status": "failed",
            "message": "SMTP transmission error occurred during delivery.",
            "smtp_response": "SMTPException",
        }
    except Exception:
        logger.error("Unexpected error in email dispatch")
        return {
            "status": "failed",
            "message": "An unexpected error occurred while sending email.",
            "smtp_response": "Internal error",
        }


def send_vaccination_reminder_email(recipient, child_name, vaccine_name, dose_label, due_date, reminder_type, is_test=False):
    due = due_date.strftime("%d %b %Y") if isinstance(due_date, (date, datetime)) else str(due_date)
    test_prefix = "[TEST] " if is_test else ""
    subject = f"{test_prefix}VaxiCare Immunization Reminder: {child_name} - {vaccine_name} ({dose_label})"

    text_body = (
        f"{test_prefix}Dear Parent,\n\n"
        f"This is a reminder from VaxiCare regarding immunization for {child_name}.\n"
        f"Vaccine: {vaccine_name}\n"
        f"Dose: {dose_label}\n"
        f"Due Date: {due}\n"
        f"Reminder Category: {reminder_type}\n\n"
        "Please visit your primary healthcare centre or consult your community health worker.\n\n"
        "Regards,\nVaxiCare Health Tracking Team"
    )

    badge_html = '<span style="display:inline-block;padding:4px 10px;font-size:12px;font-weight:600;border-radius:6px;background-color:#f0fdfa;color:#0f766e;border:1px solid #ccfbf1;margin-bottom:16px;">TEST NOTIFICATION</span>' if is_test else ''
    status_label = reminder_type.replace('_', ' ').capitalize()
    html_body = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background-color:#f8fafc;margin:0;padding:24px;color:#1e293b;">
  <div style="max-width:560px;margin:0 auto;background:#ffffff;border-radius:12px;border:1px solid #e2e8f0;overflow:hidden;box-shadow:0 4px 6px -1px rgba(0,0,0,0.05);">
    <div style="background-color:#0f766e;color:#ffffff;padding:24px 28px;">
      <h1 style="margin:0;font-size:20px;font-weight:700;">VaxiCare Immunization Alert</h1>
      <p style="margin:4px 0 0;opacity:0.85;font-size:13px;">Universal Immunization Program Monitoring</p>
    </div>
    <div style="padding:28px;font-size:15px;line-height:1.6;">
      {badge_html}
      <p>Dear Parent,</p>
      <p>This is an automated reminder regarding the upcoming vaccination milestone for <strong>{child_name}</strong>.</p>
      <table style="width:100%;border-collapse:collapse;margin:20px 0;background-color:#f8fafc;border-radius:8px;">
        <tr><td style="padding:10px 16px;border-bottom:1px solid #e2e8f0;color:#64748b;font-weight:600;width:38%;">Child Name</td><td style="padding:10px 16px;border-bottom:1px solid #e2e8f0;font-weight:600;color:#0f172a;">{child_name}</td></tr>
        <tr><td style="padding:10px 16px;border-bottom:1px solid #e2e8f0;color:#64748b;font-weight:600;">Vaccine</td><td style="padding:10px 16px;border-bottom:1px solid #e2e8f0;font-weight:600;color:#0f172a;">{vaccine_name}</td></tr>
        <tr><td style="padding:10px 16px;border-bottom:1px solid #e2e8f0;color:#64748b;font-weight:600;">Dose</td><td style="padding:10px 16px;border-bottom:1px solid #e2e8f0;font-weight:600;color:#0f172a;">{dose_label}</td></tr>
        <tr><td style="padding:10px 16px;border-bottom:1px solid #e2e8f0;color:#64748b;font-weight:600;">Due Date</td><td style="padding:10px 16px;border-bottom:1px solid #e2e8f0;font-weight:600;color:#0f172a;">{due}</td></tr>
        <tr><td style="padding:10px 16px;border-bottom:1px solid #e2e8f0;color:#64748b;font-weight:600;">Status</td><td style="padding:10px 16px;border-bottom:1px solid #e2e8f0;font-weight:600;color:#0f172a;">{status_label}</td></tr>
      </table>
      <p>Please visit your designated Primary Health Centre (PHC) or consult your community healthcare worker (ASHA / ANM) to ensure timely immunization.</p>
      <p style="margin-top:24px;color:#64748b;font-size:13px;">Warm regards,<br><strong>VaxiCare Child Health Tracking System</strong></p>
    </div>
    <div style="background-color:#f8fafc;border-top:1px solid #e2e8f0;padding:16px 28px;font-size:12px;color:#64748b;text-align:center;">
      This is an automated healthcare advisory from VaxiCare. Please do not reply directly to this email.
    </div>
  </div>
</body>
</html>"""

    return _send_smtp_email(recipient, subject, text_body, html_body)


def send_test_email(recipient):
    """
    Sends a test verification email via SMTP to verify Gmail credentials.
    """
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
    subject = "[TEST] VaxiCare SMTP Delivery Verification"
    text_body = (
        "Hello,\n\n"
        "This is a test notification from your VaxiCare Vaccination Tracking System.\n"
        "Your Gmail SMTP email configuration is active and working correctly!\n\n"
        f"Recipient: {recipient}\n"
        f"Timestamp: {now_str}\n"
        "Status: 250 OK (SMTP Handshake & Message Accepted)\n\n"
        "If you received this message, automatic parent vaccination reminders and manual "
        "clinical notifications will be delivered reliably.\n\n"
        "Regards,\nVaxiCare Development & Health Administration Team"
    )

    html_body = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background-color:#f8fafc;margin:0;padding:24px;color:#1e293b;">
  <div style="max-width:560px;margin:0 auto;background:#ffffff;border-radius:12px;border:1px solid #e2e8f0;overflow:hidden;box-shadow:0 4px 6px -1px rgba(0,0,0,0.05);">
    <div style="background-color:#0f766e;color:#ffffff;padding:24px 28px;">
      <h1 style="margin:0;font-size:20px;font-weight:700;">VaxiCare SMTP Verification</h1>
      <p style="margin:4px 0 0;opacity:0.85;font-size:13px;">Email Gateway Diagnostic Test</p>
    </div>
    <div style="padding:28px;font-size:15px;line-height:1.6;">
      <span style="display:inline-block;padding:6px 12px;font-size:13px;font-weight:700;border-radius:6px;background-color:#ecfdf5;color:#047857;border:1px solid #a7f3d0;margin-bottom:16px;">&#10003; SMTP Delivery Active</span>
      <p>Hello,</p>
      <p>This is a test notification confirming that your VaxiCare <strong>Gmail SMTP</strong> email integration is properly configured and functional.</p>
      <div style="background-color:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:16px;margin:18px 0;font-size:13px;">
        <div style="margin-bottom:6px;"><strong>Recipient:</strong> {recipient}</div>
        <div style="margin-bottom:6px;"><strong>Status:</strong> 250 OK (Message Accepted by Gmail SMTP)</div>
        <div><strong>Timestamp:</strong> {now_str}</div>
      </div>
      <p>With this connection confirmed, both automatic milestone reminders and manual clinical notifications will be delivered to parents without interruption.</p>
      <p style="margin-top:24px;color:#64748b;font-size:13px;">Regards,<br><strong>VaxiCare Development & Health Administration Team</strong></p>
    </div>
    <div style="background-color:#f8fafc;border-top:1px solid #e2e8f0;padding:16px 28px;font-size:12px;color:#64748b;text-align:center;">
      VaxiCare Digital Immunization Platform · Admin Diagnostic Email
    </div>
  </div>
</body>
</html>"""

    return _send_smtp_email(recipient, subject, text_body, html_body)



def run_due_reminder_check(today=None):
    from app.models import (
        ApplicationSetting, Child, EmailReminderNotification,
        SchedulerRunState, SmsNotification, Vaccine,
        VaccinationSchedule, utcnow, db,
    )
    from app.scheduling import build_child_schedule

    today = today or date.today()

    setting_enabled = ApplicationSetting.query.filter_by(key="automatic_email_reminders_enabled").first()
    email_enabled = setting_enabled.value.lower() in {"true", "1", "yes", "on"} if setting_enabled else False

    setting_days = ApplicationSetting.query.filter_by(key="email_reminder_days_before").first()
    try:
        days_before = int(setting_days.value) if setting_days else 3
    except (ValueError, TypeError):
        days_before = 3

    created_email_count = 0
    created_sms_count = 0
    skipped_count = 0

    children = Child.query.all()
    for child in children:
        parent = child.parent
        if not parent or not parent.user:
            continue
        user = parent.user

        schedule_entries = build_child_schedule(child, today=today)
        for entry in schedule_entries:
            sched = entry["schedule"]
            vaccine = entry["vaccine"]
            due_date = entry["due_date"]
            status = entry["status"]

            reminder_type = None
            if status == "due_today":
                reminder_type = "due_today"
            elif status == "overdue":
                reminder_type = "overdue"
            elif status == "upcoming" and (due_date - today).days <= days_before:
                reminder_type = "upcoming"

            if not reminder_type:
                continue

            # 1. Automatic email reminder
            if email_enabled and user.email:
                existing_email = EmailReminderNotification.query.filter_by(
                    child_id=child.id,
                    schedule_id=sched.id,
                    reminder_type=reminder_type,
                    due_date=due_date,
                    is_test=False,
                ).first()
                if not existing_email:
                    email_result = send_vaccination_reminder_email(
                        recipient=user.email,
                        child_name=child.name,
                        vaccine_name=vaccine.name,
                        dose_label=sched.dose_label,
                        due_date=due_date,
                        reminder_type=reminder_type,
                        is_test=False,
                    )
                    email_record = EmailReminderNotification(
                        user_id=user.id,
                        child_id=child.id,
                        vaccine_id=vaccine.id,
                        schedule_id=sched.id,
                        recipient=user.email,
                        due_date=due_date,
                        reminder_type=reminder_type,
                        status=email_result.get("status", "sent"),
                        failure_reason=email_result.get("message") if email_result.get("status") in {"failed", "simulated"} else None,
                        provider_response=email_result.get("smtp_response"),
                        is_test=False,
                        sent_at=utcnow() if email_result.get("status") == "sent" else None,
                    )
                    db.session.add(email_record)
                    try:
                        db.session.commit()
                        created_email_count += 1
                    except Exception:
                        db.session.rollback()
                        skipped_count += 1
                else:
                    skipped_count += 1

            # 2. Automatic SMS reminder
            if user.sms_consent and user.mobile:
                sms_res = send_sms_notification(
                    user=user,
                    child=child,
                    vaccine=vaccine,
                    reminder_type=reminder_type,
                    language="en",
                    scheduled_for=due_date,
                    schedule_id=sched.id,
                )
                if isinstance(sms_res, dict):
                    if sms_res.get("status") in {"sent", "simulated"}:
                        created_sms_count += 1
                    elif sms_res.get("status") == "duplicate":
                        skipped_count += 1

            # 3. In-App Notification (Parent Dashboard)
            from app.models import Notification, FCMDeviceToken, FCMPushNotification
            existing_inapp = Notification.query.filter_by(
                user_id=user.id,
                child_id=child.id,
                vaccine_id=vaccine.id,
                reminder_type=reminder_type,
            ).first()
            if not existing_inapp:
                inapp_notif = Notification(
                    user_id=user.id,
                    child_id=child.id,
                    vaccine_id=vaccine.id,
                    reminder_type=reminder_type,
                    title=f"Vaccine Reminder: {vaccine.name}",
                    body=f"{sched.dose_label} of {vaccine.name} for {child.name} is {reminder_type.replace('_', ' ')} (Due: {due_date.strftime('%d %b %Y')}).",
                    channel="system",
                    status="sent",
                    sent_at=utcnow(),
                )
                db.session.add(inapp_notif)
                try:
                    db.session.commit()
                except Exception:
                    db.session.rollback()

            # 4. FCM Push Notifications for active parent devices
            fcm_tokens = FCMDeviceToken.query.filter_by(user_id=user.id, is_active=True).all()
            for dev_tok in fcm_tokens:
                existing_fcm = FCMPushNotification.query.filter_by(
                    device_token_id=dev_tok.id,
                    child_id=child.id,
                    schedule_id=sched.id,
                    reminder_type=reminder_type,
                    due_date=due_date,
                ).first()
                if not existing_fcm:
                    send_fcm_push_notification(
                        user=user,
                        device_token=dev_tok,
                        child=child,
                        vaccine=vaccine,
                        schedule=sched,
                        reminder_type=reminder_type,
                        due_date=due_date,
                    )

            # 5. Optional Android SIM SMS Gateway
            if current_app.config.get("ANDROID_SMS_GATEWAY_ENABLED") and user.sms_consent and user.mobile:
                from app.models import AndroidGatewayMessage
                existing_gw = AndroidGatewayMessage.query.filter_by(
                    user_id=user.id,
                    child_id=child.id,
                    recipient_phone=user.mobile,
                ).first()
                if not existing_gw:
                    sms_body = build_sms_message("en", child.name, vaccine.name, due_date, reminder_type)
                    queue_android_gateway_message(
                        user=user,
                        recipient_phone=user.mobile,
                        message_text=sms_body,
                        child=child,
                    )

    state = SchedulerRunState.query.filter_by(name="vaccination_reminders").first()
    if not state:
        state = SchedulerRunState(name="vaccination_reminders")
        db.session.add(state)
    state.last_started_at = utcnow()
    state.last_completed_at = utcnow()
    state.status = "idle"
    state.last_result = f"Processed {len(children)} children: generated {created_email_count} emails, {created_sms_count} SMS, {skipped_count} skipped/duplicates."
    db.session.commit()

    return {
        "status": "success",
        "email_count": created_email_count,
        "sms_count": created_sms_count,
        "skipped_count": skipped_count,
        "message": state.last_result,
    }


def send_fcm_push_notification(user, device_token, child, vaccine, schedule, reminder_type, due_date):
    from app.models import FCMPushNotification, db, utcnow

    title = f"Vaccination Reminder: {vaccine.name}"
    body = f"{schedule.dose_label} of {vaccine.name} for {child.name} is {reminder_type.replace('_', ' ')}."
    deep_link = f"/child/{child.id}"

    fcm_enabled = current_app.config.get("FCM_ENABLED", False)
    cred_path = current_app.config.get("FIREBASE_SERVICE_ACCOUNT_PATH")

    if not fcm_enabled or not cred_path or not os.path.exists(cred_path):
        record = FCMPushNotification(
            user_id=user.id,
            device_token_id=device_token.id,
            child_id=child.id,
            vaccine_id=vaccine.id,
            schedule_id=schedule.id,
            due_date=due_date,
            reminder_type=reminder_type,
            status="simulated" if not fcm_enabled else "failed",
            failure_reason="Firebase credentials not configured on server." if not (cred_path and os.path.exists(cred_path)) else "FCM is disabled.",
            created_at=utcnow(),
            completed_at=utcnow(),
        )
        db.session.add(record)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
        return {"status": record.status, "message": record.failure_reason}

    try:
        import firebase_admin
        from firebase_admin import credentials, messaging

        if not firebase_admin._apps:
            cred = credentials.Certificate(cred_path)
            project_id = current_app.config.get("FIREBASE_PROJECT_ID")
            firebase_admin.initialize_app(cred, {"projectId": project_id} if project_id else None)

        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data={
                "child_id": str(child.id),
                "vaccine_id": str(vaccine.id),
                "url": deep_link,
                "click_action": deep_link,
            },
            token=device_token.token,
        )
        response_id = messaging.send(message)

        record = FCMPushNotification(
            user_id=user.id,
            device_token_id=device_token.id,
            child_id=child.id,
            vaccine_id=vaccine.id,
            schedule_id=schedule.id,
            due_date=due_date,
            reminder_type=reminder_type,
            status="sent",
            provider_message_id=str(response_id),
            created_at=utcnow(),
            completed_at=utcnow(),
        )
        db.session.add(record)
        db.session.commit()
        return {"status": "sent", "message_id": response_id}

    except Exception as exc:
        err_str = str(exc)
        if "Unregistered" in err_str or "invalid" in err_str.lower() or "not registered" in err_str.lower():
            device_token.is_active = False
            failure_reason = "Device token is expired or unregistered."
        else:
            failure_reason = f"FCM delivery error: {err_str[:180]}"

        record = FCMPushNotification(
            user_id=user.id,
            device_token_id=device_token.id,
            child_id=child.id,
            vaccine_id=vaccine.id,
            schedule_id=schedule.id,
            due_date=due_date,
            reminder_type=reminder_type,
            status="failed",
            failure_reason=failure_reason,
            created_at=utcnow(),
            completed_at=utcnow(),
        )
        db.session.add(record)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
        return {"status": "failed", "message": failure_reason}


def send_test_fcm_notification(user, device_token):
    from app.models import FCMPushTestAttempt, db, utcnow

    title = "VaxiCare Test Push Notification"
    body = "Your browser is successfully connected to VaxiCare vaccination push alerts."
    deep_link = "/parent-dashboard"

    fcm_enabled = current_app.config.get("FCM_ENABLED", False)
    cred_path = current_app.config.get("FIREBASE_SERVICE_ACCOUNT_PATH")

    if not fcm_enabled or not cred_path or not os.path.exists(cred_path):
        attempt = FCMPushTestAttempt(
            user_id=user.id,
            device_id=device_token.id,
            status="simulated",
            failure_reason="Firebase credentials not configured; test simulated.",
            created_at=utcnow(),
            completed_at=utcnow(),
        )
        db.session.add(attempt)
        db.session.commit()
        return {"status": "simulated", "message": "Demo mode: FCM credentials not active."}

    try:
        import firebase_admin
        from firebase_admin import credentials, messaging

        if not firebase_admin._apps:
            cred = credentials.Certificate(cred_path)
            project_id = current_app.config.get("FIREBASE_PROJECT_ID")
            firebase_admin.initialize_app(cred, {"projectId": project_id} if project_id else None)

        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data={"url": deep_link},
            token=device_token.token,
        )
        response_id = messaging.send(message)
        attempt = FCMPushTestAttempt(
            user_id=user.id,
            device_id=device_token.id,
            status="sent",
            provider_message_id=str(response_id),
            created_at=utcnow(),
            completed_at=utcnow(),
        )
        db.session.add(attempt)
        db.session.commit()
        return {"status": "sent", "message_id": response_id}
    except Exception as exc:
        attempt = FCMPushTestAttempt(
            user_id=user.id,
            device_id=device_token.id,
            status="failed",
            failure_reason=f"FCM error: {str(exc)[:180]}",
            created_at=utcnow(),
            completed_at=utcnow(),
        )
        db.session.add(attempt)
        db.session.commit()
        return {"status": "failed", "message": str(exc)}


def queue_android_gateway_message(user, recipient_phone, message_text, child=None):
    from app.models import AndroidGatewayMessage, db, utcnow
    import uuid as _uuid

    msg_uuid = str(_uuid.uuid4())
    msg = AndroidGatewayMessage(
        message_uuid=msg_uuid,
        user_id=user.id,
        child_id=child.id if child else None,
        recipient_phone=recipient_phone,
        message_text=message_text,
        status="queued",
        created_at=utcnow(),
    )
    db.session.add(msg)
    try:
        db.session.commit()
        return {"status": "queued", "uuid": msg_uuid}
    except Exception as e:
        db.session.rollback()
        return {"status": "error", "message": str(e)}


def retry_failed_notification(channel, notification_id):
    from app.models import SmsNotification, EmailReminderNotification, FCMPushNotification, AndroidGatewayMessage, db, utcnow

    if channel == "sms":
        notification = db.session.get(SmsNotification, notification_id)
        if not notification:
            return {"status": "error", "message": "SMS notification not found."}
        if notification.status not in {"failed", "pending", "simulated", "skipped"}:
            return {"status": "error", "message": f"Cannot retry notification with status '{notification.status}'."}
        require_consent = bool(notification.user_id) and notification.message_type != "test"
        res = _attempt_delivery(notification, require_consent=require_consent)
        return res
    elif channel == "email":
        notification = db.session.get(EmailReminderNotification, notification_id)
        if not notification:
            return {"status": "error", "message": "Email notification not found."}
        if notification.status not in {"failed", "simulated"}:
            return {"status": "error", "message": f"Cannot retry notification with status '{notification.status}'."}
        child_name = notification.child.name if notification.child else "Child"
        vaccine_name = notification.vaccine.name if notification.vaccine else "Vaccine"
        dose_label = notification.schedule.dose_label if notification.schedule else "Dose"
        res = send_vaccination_reminder_email(
            recipient=notification.recipient,
            child_name=child_name,
            vaccine_name=vaccine_name,
            dose_label=dose_label,
            due_date=notification.due_date,
            reminder_type=notification.reminder_type,
            is_test=notification.is_test,
        )
        notification.status = res.get("status", "sent")
        notification.failure_reason = res.get("message") if res.get("status") in {"failed", "simulated"} else None
        notification.provider_response = res.get("smtp_response")
        if res.get("status") == "sent":
            notification.sent_at = utcnow()
        db.session.commit()
        return res
    elif channel in {"push", "fcm"}:
        notification = db.session.get(FCMPushNotification, notification_id)
        if not notification:
            return {"status": "error", "message": "Push notification not found."}
        if notification.status not in {"failed", "simulated"}:
            return {"status": "error", "message": f"Cannot retry notification with status '{notification.status}'."}
        dev = notification.device_token
        if not dev or not dev.is_active:
            return {"status": "error", "message": "Associated device token is no longer active."}
        res = send_fcm_push_notification(
            user=notification.user,
            device_token=dev,
            child=notification.child,
            vaccine=notification.vaccine,
            schedule=notification.schedule,
            reminder_type=notification.reminder_type,
            due_date=notification.due_date,
        )
        return res
    elif channel == "android_gateway":
        msg = db.session.get(AndroidGatewayMessage, notification_id)
        if not msg:
            return {"status": "error", "message": "Gateway message not found."}
        msg.status = "queued"
        msg.assigned_at = None
        msg.completed_at = None
        db.session.commit()
        return {"status": "queued", "message": "Message returned to pending queue."}
    else:
        return {"status": "error", "message": "Invalid notification channel."}
