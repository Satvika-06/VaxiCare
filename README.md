# VaxiCare – Smart Child Immunization and Community Health Tracking System

## Project abstract
VaxiCare is a bilingual community health application for tracking child immunization schedules, managing vaccinations, sending reminders, and supporting frontline health workers and administrators. The system helps families and local health teams monitor due, overdue, and completed vaccines while keeping digital health records accessible and secure.

## Problem statement
Many families and local healthcare teams struggle to track child immunization milestones, especially in community settings where records may be incomplete or poorly coordinated. A digital yet simple tracking system is needed to support reminders, improve follow-up, and reduce missed vaccine doses.

## Project objectives
- Support parent registration and child health profile management
- Track immunization schedules using child date of birth
- Highlight due-today, upcoming, and overdue vaccinations
- Provide healthcare worker and admin monitoring dashboards
- Record vaccine administration securely and accurately
- Generate QR-secured public child records and downloadable vaccination cards
- Support bilingual communication in English and Telugu
- Provide safe and demo-friendly SMS reminder workflows

## System architecture
- Frontend: HTML5, CSS3, Bootstrap 5, JavaScript
- Backend: Python Flask
- Database: SQLite with SQLAlchemy ORM
- Security: Flask-Login, Flask-WTF CSRF, password hashing, role-based routes
- PDF generation: ReportLab
- QR generation: qrcode
- SMS: Twilio integration with demo fallback
- Deployment: Gunicorn with Render configuration

## Technologies used
- Python 3.x
- Flask 3.0.3
- Flask-SQLAlchemy
- Flask-Login
- Flask-WTF
- WTForms
- SQLite
- ReportLab
- qrcode
- Pillow
- Twilio
- python-dotenv
- Gunicorn
- pytest

## Database design
The application uses SQLite and SQLAlchemy models for:
- User
- Parent
- Child
- HealthcareWorker
- Vaccine
- VaccinationSchedule
- VaccinationRecord
- ReminderLog
- Notification
- SmsNotification
- EmailReminderNotification

## Features and modules
### Parent module
- Registration and login
- Child profile creation and management
- Vaccination schedule view
- Vaccine record history and status tracking
- Reminder history and SMS demo mode
- PDF vaccine card download
- QR code generation and public record access

### Healthcare worker module
- Secure login
- Search registered children by name or village
- View patient vaccination records
- Record administered vaccines
- Identify due and overdue cases

### Admin module
- Dashboard summary
- Parent, child, and vaccination statistics
- Vaccine management
- Reminder log review
- Manual vaccination email reminders to a selected parent's registered address
- User and healthcare worker oversight

### Additional features
- English and Telugu switcher
- Responsive healthcare UI
- Security checks and role-based access control
- Contact and help pages
- Vaccine awareness page

## Installation instructions
1. Open a PowerShell terminal in the project folder.
2. Create a virtual environment:
   ```powershell
   cd "C:\Users\T JYOTHIRMAI\OneDrive\Pictures\Documents\Desktop\VaxiCare"
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```
3. Install dependencies:
   ```powershell
   python -m pip install --upgrade pip
   python -m pip install -r requirements.txt
   ```
4. Copy the environment template:
   ```powershell
   copy .env.example .env
   ```
5. Update `.env` with your settings.
6. Initialize the database:
   ```powershell
   python init_db.py
   ```
7. Start the application:
   ```powershell
   python run.py
   ```
8. Open the app at: http://localhost:5000/
9. In a second terminal, start the automatic reminder scheduler:
   ```powershell
   python run_reminders.py
   ```

## Windows setup commands
```powershell
cd "C:\Users\T JYOTHIRMAI\OneDrive\Pictures\Documents\Desktop\VaxiCare"
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
copy .env.example .env
python init_db.py
python run.py
```
For automatic reminders, keep this running in a separate terminal:
```powershell
python run_reminders.py
```

## Environment configuration
Use the `.env` file with these values:

```env
SECRET_KEY=replace-with-a-long-random-secret
FLASK_ENV=development
APP_NAME=VaxiCare
DATABASE_URL=sqlite:///vaxicare.db
SEED_DEMO_DATA=true
PUBLIC_BASE_URL=http://localhost:5000
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_PHONE_NUMBER=
SMS_ENABLED=false
SMS_FORCE_DEMO=false
MAIL_DEFAULT_SENDER=info@vaxicare.local
SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM=info@vaxicare.local
SMTP_USE_TLS=true
PASSWORD_RESET_TTL_SECONDS=1800
REMINDER_TIMEZONE=Asia/Kolkata
REMINDER_DAYS_BEFORE=3
REMINDER_OVERDUE_ENABLED=false
REMINDER_OVERDUE_AFTER_DAYS=7
EMAIL_REMINDER_OVERDUE_AFTER_DAYS=1
EMAIL_REMINDERS_ENABLED=false
REMINDER_TEST_EMAIL=
REMINDER_CHECK_INTERVAL_SECONDS=60
SMS_MAX_ATTEMPTS=3
SMS_RETRY_MINUTES=15
FCM_ENABLED=false
FIREBASE_CREDENTIALS_PATH=firebase-credentials/serviceAccountKey.json
FIREBASE_VAPID_KEY=
FIREBASE_API_KEY=
FIREBASE_AUTH_DOMAIN=
FIREBASE_PROJECT_ID=
FIREBASE_STORAGE_BUCKET=
FIREBASE_MESSAGING_SENDER_ID=
FIREBASE_APP_ID=
```
See `.env.example` for the complete template. Set `SEED_DEMO_DATA=false` in production.

Password recovery uses expiring, single-use links and stores only a hash of each reset token. Configure `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, and `SMTP_USE_TLS` to deliver reset emails; until SMTP is configured, the recovery page keeps its generic response but no email can be delivered. `PASSWORD_RESET_TTL_SECONDS` controls link expiry (default: 1800 seconds). Admin accounts may be created only by an authenticated administrator; healthcare worker accounts must be provisioned by an administrator.

For Gmail, use `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, and `SMTP_USE_TLS=true`, with the account address in `SMTP_USERNAME` and `SMTP_FROM` and a Google App Password in `SMTP_PASSWORD` (not the normal Gmail password). Keep these values only in the ignored local `.env` or your deployment secret store; never put credentials in `.env.example`. An administrator can submit the **Send test email** form on the dashboard to send a real test message. The form allows at most three attempts per administrator per hour, with at least five minutes between attempts; failed sends count. The admin dashboard displays the current usage and next safe retry time (UTC). The form reports the SMTP server's acceptance response; no email is sent automatically by startup or tests.

Automatic vaccination reminder emails are disabled by default (`EMAIL_REMINDERS_ENABLED=false`). An administrator can enable or disable them and configure the upcoming lead time, overdue delay, and scheduler interval using the **Automatic vaccination email reminders** control on the admin dashboard. These settings are stored in the shared database and take effect on the scheduler's next cycle. Enabling allows real emails to be sent to registered parent addresses; changing a setting does not send an email immediately. The scheduler uses `REMINDER_TIMEZONE` (default `Asia/Kolkata`), checks on `REMINDER_CHECK_INTERVAL_SECONDS` (default 60; allowed range 10–86400), sends upcoming reminders the configured number of days ahead (default 3), due-today reminders on the due date, and overdue reminders after the configured delay (default 1 day). Each child/dose/type/date event is recorded once in `email_reminder_notifications`, separately from SMS history. Records retain recipient, attempt time, status, SMTP acceptance time, and failure reason. Failed reminders are not reported as delivered.

Run the scheduler as a separate process/service with `python run_reminders.py`; the Flask development server, debug reloader, and web workers do not start scheduler loops. A renewable lease in the shared database prevents multiple scheduler processes using that database from processing reminders concurrently. The admin dashboard reports whether the scheduler is running, its last result/error, its next check time, and recent failed deliveries. Scheduler, runtime-setting, and history tables are created additively by the existing `db.create_all()` startup path; existing vaccination data is not changed.

Administrators can also send one explicit reminder from the **Vaccination Email Notifications** section on the admin dashboard. Select the parent, that parent's child, vaccine, scheduled dose, and reminder type; the page displays the registered recipient and asks for confirmation before submitting. The route is admin-only and CSRF-protected, validates that the selections match and the dose is incomplete, and sends to the parent's registered email only. This manual action works independently of the automatic scheduler flag; nothing is sent by merely opening the page. SMTP acceptance, failures, duplicate attempts, parent, child, dose, and recipient are shown in the email reminder history. The dashboard uses the existing `email_reminder_notifications` table; no new migration is needed for the UI route. If that table is absent in an older deployment, the existing `db.create_all()` startup path creates it without dropping existing records.

For safe manual reminder testing, set `REMINDER_TEST_EMAIL` to the email address of a dedicated test parent account, register that parent, and add a test child. Keep `EMAIL_REMINDERS_ENABLED=false`. Run `python run_reminders.py --list-test-reminder-targets` to list child and dose IDs, then explicitly trigger each test type (these commands send real test messages only to the configured test email):

```powershell
python run_reminders.py --test-child-id <CHILD_ID> --test-schedule-id <SCHEDULE_ID> --test-reminder-type upcoming
python run_reminders.py --test-child-id <CHILD_ID> --test-schedule-id <SCHEDULE_ID> --test-reminder-type due_today
python run_reminders.py --test-child-id <CHILD_ID> --test-schedule-id <SCHEDULE_ID> --test-reminder-type overdue
```

The CLI test trigger requires the selected child to belong to the parent whose email matches `REMINDER_TEST_EMAIL`; it cannot accept an arbitrary recipient. Each CLI message is labeled as a test and recorded on the admin dashboard. This separate test flow is distinct from the admin-dashboard manual reminder, which sends to the selected parent's actual registered address. Automated tests mock email delivery. Do not enable scheduled emails until you are ready for eligible real parents to receive reminders.

## Firebase browser push notifications
FCM is disabled by default (`FCM_ENABLED=false`) and is not initialized in automated tests. To configure it, obtain the Web App configuration and Web Push certificate key from your own Firebase project, then enter those values in the ignored root `.env` file using the `FIREBASE_*` names in `.env.example`. Do not use sample values. Keep the Firebase service-account JSON outside source control, set `FIREBASE_CREDENTIALS_PATH` to its path relative to the project root (or an absolute path), and never paste its contents into source files, logs, or support messages. `.gitignore` excludes both the expected credential directory and service-account JSON filenames.

Obtain `FIREBASE_API_KEY`, `FIREBASE_AUTH_DOMAIN`, `FIREBASE_PROJECT_ID`, `FIREBASE_STORAGE_BUCKET`, `FIREBASE_MESSAGING_SENDER_ID`, and `FIREBASE_APP_ID` from Firebase Console **Project settings → General → Your apps → Web app → SDK setup and configuration**. Obtain `FIREBASE_VAPID_KEY` from **Project settings → Cloud Messaging → Web configuration → Web Push certificates**. Enable the **FCM Registration API** for the same project in Google Cloud Console. The Firebase service-account JSON is separate from these Web values; it is already kept in the ignored local credential directory. This app's local `.env` points to the existing file. FCM Web requires HTTPS (localhost is permitted for development).

After all project values and the private service-account file are in place, an operator may explicitly set `FCM_ENABLED=true` in the deployment secret store and restart the web and reminder-scheduler processes. Startup checks that the Web configuration, VAPID public key, and service-account file are present; if they are missing, browser push remains unavailable and the app does not send anything. Parents can opt in from **Vaccination push alerts** on their dashboard, and can disable the current browser there. Registration and deactivation are authenticated, parent-scoped, and CSRF-protected; each browser is associated with its Firebase Installation ID (FID). The browser uses the current Firebase `register()` / `onRegistered()` APIs, and the Firebase Admin SDK 7.5+ targets that FID, avoiding deprecated registration-token APIs. FCM records are stored in additive `fcm_device_tokens` and `fcm_push_notifications` tables; legacy registration-token rows are retained but are not used as FIDs. Administrators can view FCM delivery history and failures on the notification-history page without seeing device identifiers. The parent dashboard also offers a rate-limited **Send test to this browser** action; it targets only the signed-in parent’s registered browser and records its outcome. To test it safely after configuration and explicit approval, set `FCM_ENABLED=true` locally, restart only the Flask web process, and keep `python run_reminders.py` stopped so no scheduled pushes can go to other parents. Sign in as the parent in your own browser, enable notifications, then use the test action. Set `FCM_ENABLED=false` and restart Flask after the test. The existing separate reminder worker sends upcoming, due-today, and overdue push reminders using the same schedule calculations; newly scheduled doses are included in the upcoming category rather than triggering a separate immediate message. Automated tests mock Firebase delivery and never contact Firebase.

## Database initialization
```powershell
python init_db.py
```
This creates SQLite tables and loads demo seed data.

## Demo login credentials
In development only, demo data includes:
- Parent: `parent1` / `Parent@123`
- Healthcare worker: `healthworker` / `Worker@123`
- Admin: `admin` / `Admin@123`

Demo accounts are not seeded when `FLASK_ENV=production`. Never enable demo seeding for a production database. Create a production administrator from the deployment environment with:
```powershell
python create_admin.py admin.name admin@example.com
```
The script prompts for a password and does not expose it in command-line arguments. Additional administrator accounts can be created from the admin user-management page.

## Testing instructions
```powershell
cd "C:\Users\T JYOTHIRMAI\OneDrive\Pictures\Documents\Desktop\VaxiCare"
.\.venv\Scripts\Activate.ps1
python -m pytest -q
```

## Twilio configuration
Keep automatic SMS disabled with `SMS_ENABLED=false`. The admin test form accepts only the phone number configured in `VAXICARE_TEST_SMS_NUMBER`. It permits at most one test attempt every five minutes and three attempts per hour; simulated and failed test attempts count and are retained in SMS notification history.

```env
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_PHONE_NUMBER=
SMS_ENABLED=false
VAXICARE_TEST_SMS_NUMBER=+15551234567
SMS_FORCE_DEMO=true
```

Set credentials only in the ignored local `.env` or your deployment secret store; never commit them or print them. Put your own phone number in `VAXICARE_TEST_SMS_NUMBER` using E.164 format, such as `+15551234567`; all other test recipients are rejected server-side. The dashboard displays that configured test number and the remaining quota. Keep `SMS_FORCE_DEMO=true` for simulated checks. Only set `SMS_FORCE_DEMO=false` when you explicitly intend an admin test submission to contact Twilio. Parents must enter a valid international-format number and explicitly opt in from Notification settings. Scheduled SMS stays disabled until `SMS_ENABLED=true`, and it also respects parent consent. `REMINDER_DAYS_BEFORE`, `REMINDER_OVERDUE_ENABLED`, `REMINDER_OVERDUE_AFTER_DAYS`, and `REMINDER_TIMEZONE` configure SMS timing; email timing is configurable in the admin dashboard.

Parents can request a phone-verification code from Notification settings only when Twilio is configured and `SMS_FORCE_DEMO=false`; this explicit OTP action is independent of `SMS_ENABLED`, which continues to control automatic reminder delivery. The six-digit code expires after five minutes, is stored only as a keyed hash, and is limited to five verification attempts. Resends are limited to one per minute and three per hour. Challenges store keyed phone identifiers rather than copies of old phone numbers. Saving notification preferences never verifies a number; changing the number clears its verification status. OTP values are not written to notification history, application logs, or dashboard messages. If Twilio is unavailable or demo mode is active, the page reports that real verification is unavailable and does not simulate success. Existing reminder delivery continues to use its prior SMS-consent and valid-number checks.

When automatic SMS is disabled, scheduled sends do not contact Twilio or consume a reminder record, but an explicitly submitted admin test can still send when demo mode is off. With `SMS_FORCE_DEMO=true`, the app records a simulated message and does not call Twilio. Missing Twilio settings produce a failed test result listing only missing variable names. A `sent` status means Twilio accepted the request; it does not confirm delivery to a handset. Admin test submissions are not automatically retried. Automated tests use mocked providers and never send real SMS.

Temporary provider failures are retried up to `SMS_MAX_ATTEMPTS`, spaced by `SMS_RETRY_MINUTES`. Notification records prevent duplicate sends for the same recipient, child, vaccine, reminder type, and due date. Existing reminder history is preserved and imported into the SMS history table on startup; the schema upgrade adds fields/tables without dropping existing records.

## Production deployment
1. Deploy from a production WSGI server rather than Flask's development server. The web start command is:

```yaml
gunicorn --workers 2 --bind 0.0.0.0:$PORT run:app
```

2. Set `FLASK_ENV=production`, a strong secret `SECRET_KEY`, `SEED_DEMO_DATA=false`, and a durable `DATABASE_URL` in the hosting provider's environment/secrets UI. Use the same `DATABASE_URL` for the web service and scheduler worker. Do not run `init_db.py` against an existing production database.
3. Configure SMTP values in the provider secret store. Keep `EMAIL_REMINDERS_ENABLED=false` and `SMS_ENABLED=false` until each channel is explicitly approved. Do not store credentials in source control.
4. Run `python run_reminders.py` as a dedicated background worker/service using the same code and database configuration. Do not start it from the Flask app factory or a Gunicorn worker. The database lease protects against accidental duplicate worker processes, but each worker must use the same shared database.
5. Keep the web and worker processes supervised by the hosting provider, systemd, or another process manager so they restart after failures.

The included `render.yaml` defines only the Gunicorn web service and requires `DATABASE_URL` to be supplied in Render; configure a durable shared database before deployment. Add the separate background worker in the hosting dashboard using `python run_reminders.py` and the same environment/database. Separate local SQLite files do not share notification history or scheduler leases. The app loads the project-root `.env` for local development; production secrets should be injected by the platform. App startup creates new tables additively and disables demo seeding by default in production.

## Limitations and future enhancements
- Real SMS delivery requires valid Twilio credentials.
- Vaccine-specific side-effect details are deliberately marked for healthcare-professional review until verified against authoritative product guidance.
- The schedule is informational and should always be reviewed by a qualified medical professional.
- Future improvements could include stronger analytics, multilingual doctor notes, and deeper reporting.

## Important note
The immunization schedule is based on the Government of India UIP National Immunization Schedule and is intended for informational use only. It must be confirmed by an authorized healthcare professional before clinical action is taken.
