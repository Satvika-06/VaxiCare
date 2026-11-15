"""
VaxiCare Phase 8 Complete Upgrade Test Suite
Verifies:
- Error pages (403, 404, 500)
- Service Worker route (/firebase-messaging-sw.js)
- Firebase Cloud Messaging (Web Push) registration, deactivation & simulated test dispatch
- Scheduler FCM push integration & duplicate prevention
- Android SIM Gateway device registration, authentication, queue retrieval, status reporting & revocation
- Unified Admin Notification Center multi-channel audit view
- Security and role authorization boundaries
"""

import hashlib
import json
import os
import secrets
import tempfile
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

import pytest

from app import create_app, db
from app.models import (
    AndroidGatewayDevice, AndroidGatewayMessage, Child, FCMDeviceToken,
    FCMPushNotification, FCMPushTestAttempt, HealthcareWorker, Notification,
    Parent, SmsNotification, User, VaccinationRecord, VaccinationSchedule,
    Vaccine, utcnow,
)
from app.services import (
    queue_android_gateway_message, run_due_reminder_check,
    send_fcm_push_notification, send_test_fcm_notification,
)


@pytest.fixture
def upgrade_app():
    db_fd, db_path = tempfile.mkstemp(suffix="_test_phase8.db")
    os.close(db_fd)

    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
        "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": "phase8-test-secret-key-123",
        "SEED_DEMO_DATA": False,
        "FCM_ENABLED": False,
        "ANDROID_SMS_GATEWAY_ENABLED": True,
    })

    with app.app_context():
        db.create_all()

        # Seed standard UIP vaccines
        v_bcg = Vaccine(
            name="BCG",
            disease="Tuberculosis",
            description="Bacillus Calmette-Guerin",
            source="MoHFW UIP",
            source_checked_on="2026-01-01",
        )
        v_penta = Vaccine(
            name="Pentavalent",
            disease="Diphtheria, Pertussis, Tetanus, Hepatitis B, Hib",
            description="Pentavalent vaccine",
            source="MoHFW UIP",
            source_checked_on="2026-01-01",
        )
        db.session.add_all([v_bcg, v_penta])
        db.session.commit()

        # Seed Users
        # 1. Admin
        admin_u = User(username="admin_p8", email="admin_p8@vaxicare.local", role="admin")
        admin_u.set_password("AdminSecure#2026")
        db.session.add(admin_u)

        # 2. Worker
        worker_u = User(username="worker_p8", email="worker_p8@vaxicare.local", role="healthcare_worker")
        worker_u.set_password("WorkerSecure#2026")
        db.session.add(worker_u)
        db.session.commit()

        hw = HealthcareWorker(user_id=worker_u.id, full_name="Nurse Sita", village="Kalyan")
        db.session.add(hw)

        # 3. Parent
        parent_u = User(username="parent_p8", email="parent_p8@vaxicare.local", role="parent", mobile="+919876543210", sms_consent=True)
        parent_u.set_password("ParentSecure#2026")
        db.session.add(parent_u)
        db.session.commit()

        parent_prof = Parent(user_id=parent_u.id, first_name="Kavita", last_name="Devi", village="Kalyan")
        db.session.add(parent_prof)
        db.session.commit()

        # 4. Child
        child = Child(
            parent_id=parent_prof.id,
            name="Ananya Devi",
            dob=date.today() - timedelta(days=45),  # ~6 weeks old (Penta 1 due)
            gender="female",
            village="Kalyan",
            record_token="token-ananya-phase8",
        )
        db.session.add(child)
        db.session.commit()

        # Schedule catalog rows
        s1 = VaccinationSchedule(
            vaccine_id=v_bcg.id,
            dose_label="At Birth",
            age_days=0,
            description="BCG dose at birth",
            source_note="MoHFW UIP",
            source_checked_on="2026-01-01",
        )
        s2 = VaccinationSchedule(
            vaccine_id=v_penta.id,
            dose_label="Dose 1",
            age_days=42,
            description="Pentavalent 1 at 6 weeks",
            source_note="MoHFW UIP",
            source_checked_on="2026-01-01",
        )
        db.session.add_all([s1, s2])
        db.session.commit()

    yield app, app.test_client(), db_path

    with app.app_context():
        db.session.remove()
        db.drop_all()
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except OSError:
            pass


def login(client, username, password):
    return client.post("/login", data={"username": username, "password": password}, follow_redirects=True)


# ===========================================================================
# 1. Error Handlers & Service Worker Route
# ===========================================================================

def test_service_worker_route(upgrade_app):
    app, client, _ = upgrade_app
    res = client.get("/firebase-messaging-sw.js")
    assert res.status_code == 200
    assert "javascript" in res.content_type
    assert res.headers.get("Service-Worker-Allowed") == "/"
    assert b"addEventListener" in res.data


def test_custom_404_page(upgrade_app):
    app, client, _ = upgrade_app
    res = client.get("/this-route-definitely-does-not-exist-404")
    assert res.status_code == 404
    assert b"Page Not Found" in res.data or b"404" in res.data


def test_custom_403_page(upgrade_app):
    app, client, _ = upgrade_app
    login(client, "parent_p8", "ParentSecure#2026")
    # Parent accessing admin endpoint
    res = client.get("/admin-dashboard")
    assert res.status_code == 403
    assert b"Access Denied" in res.data or b"403" in res.data


# ===========================================================================
# 2. Firebase Cloud Messaging (Web Push) Endpoints
# ===========================================================================

def test_fcm_device_registration_and_update(upgrade_app):
    app, client, _ = upgrade_app
    login(client, "parent_p8", "ParentSecure#2026")

    # Register token via JSON
    test_token = "mock-fcm-device-token-abc-123456789"
    res = client.post("/register-push-device", json={"token": test_token})
    assert res.status_code == 200
    assert res.json.get("status") == "ok"

    with app.app_context():
        u = User.query.filter_by(username="parent_p8").first()
        dev = FCMDeviceToken.query.filter_by(user_id=u.id).first()
        assert dev is not None
        assert dev.token == test_token
        assert dev.is_active is True

    # Re-registering the same token updates it without duplicate error
    res_repeat = client.post("/register-push-device", json={"token": test_token})
    assert res_repeat.status_code == 200
    assert res_repeat.json.get("status") == "ok"


def test_fcm_device_deactivation(upgrade_app):
    app, client, _ = upgrade_app
    login(client, "parent_p8", "ParentSecure#2026")

    test_token = "mock-fcm-token-to-disable"
    client.post("/register-push-device", json={"token": test_token})

    # Disable push device
    res_disable = client.post("/disable-push-device", json={"token": test_token})
    assert res_disable.status_code == 200

    with app.app_context():
        u = User.query.filter_by(username="parent_p8").first()
        token_hash = hashlib.sha256(test_token.encode()).hexdigest()
        dev = FCMDeviceToken.query.filter_by(user_id=u.id, token_hash=token_hash).first()
        assert dev is not None
        assert dev.is_active is False


def test_fcm_test_push_simulated(upgrade_app):
    app, client, _ = upgrade_app
    login(client, "parent_p8", "ParentSecure#2026")

    test_token = "mock-fcm-test-token"
    client.post("/register-push-device", json={"token": test_token})

    # Trigger test push (credentials unconfigured -> simulated demo mode)
    res_test = client.post("/test-push-device", json={})
    assert res_test.status_code == 200
    data = res_test.json
    assert data.get("status") in {"simulated", "sent"}

    with app.app_context():
        u = User.query.filter_by(username="parent_p8").first()
        attempts = FCMPushTestAttempt.query.filter_by(user_id=u.id).all()
        assert len(attempts) >= 1


# ===========================================================================
# 3. Scheduler Push Notification Integration
# ===========================================================================

def test_scheduler_fcm_integration_and_idempotency(upgrade_app):
    app, client, _ = upgrade_app

    with app.app_context():
        u = User.query.filter_by(username="parent_p8").first()
        # Add active device token
        token_str = "fcm-scheduler-test-token"
        dev = FCMDeviceToken(
            user_id=u.id,
            token=token_str,
            token_hash=hashlib.sha256(token_str.encode()).hexdigest(),
            target_type="web",
            is_active=True,
            created_at=utcnow(),
            last_seen_at=utcnow(),
        )
        db.session.add(dev)
        db.session.commit()

        # Run due reminder check
        res = run_due_reminder_check(today=date.today())
        assert res.get("status") == "success"

        # Verify FCMPushNotification was created
        fcm_notifs = FCMPushNotification.query.filter_by(user_id=u.id).all()
        assert len(fcm_notifs) >= 1
        assert fcm_notifs[0].status in {"simulated", "sent"}

        initial_count = len(fcm_notifs)

        # Run a second time immediately - verify duplicate push not created
        res2 = run_due_reminder_check(today=date.today())
        assert res2.get("status") == "success"
        fcm_notifs_after = FCMPushNotification.query.filter_by(user_id=u.id).all()
        assert len(fcm_notifs_after) == initial_count


# ===========================================================================
# 4. Optional Android SIM SMS Gateway
# ===========================================================================

def test_android_gateway_device_registration_and_auth(upgrade_app):
    app, client, _ = upgrade_app
    login(client, "admin_p8", "AdminSecure#2026")

    # Admin registers companion phone
    res_reg = client.post("/admin/gateway-devices/register", data={
        "device_name": "Kalyan PHC Staff Phone",
        "device_identifier": "phc-android-device-001"
    }, follow_redirects=True)
    assert res_reg.status_code == 200

    # Retrieve generated API key from database
    with app.app_context():
        dev = AndroidGatewayDevice.query.filter_by(device_identifier="phc-android-device-001").first()
        assert dev is not None
        assert dev.is_active is True

    # Test unauthorized access without key
    client.post("/logout", follow_redirects=True)
    res_unauth = client.get("/api/gateway/pending")
    assert res_unauth.status_code == 401


def test_android_gateway_queue_retrieval_and_status_update(upgrade_app):
    app, client, _ = upgrade_app

    # Create device with known API key
    raw_api_key = "secret-gateway-api-key-999"
    key_hash = hashlib.sha256(raw_api_key.encode()).hexdigest()

    with app.app_context():
        dev = AndroidGatewayDevice(
            device_name="Test PHC Device",
            device_identifier="test-device-uuid-123",
            api_key_hash=key_hash,
            is_active=True,
            created_at=utcnow(),
        )
        db.session.add(dev)

        u = User.query.filter_by(username="parent_p8").first()
        child = Child.query.first()

        # Queue a gateway message
        res_q = queue_android_gateway_message(
            user=u,
            recipient_phone="+919876543210",
            message_text="VaxiCare Gateway Test SMS",
            child=child,
        )
        assert res_q.get("status") == "queued"
        msg_uuid = res_q.get("uuid")

    # Companion Android device retrieves pending messages
    headers = {"X-Gateway-Key": raw_api_key}
    res_pending = client.get("/api/gateway/pending", headers=headers)
    assert res_pending.status_code == 200
    data = res_pending.json
    assert data.get("status") == "ok"
    assert data.get("count") >= 1
    uuids = [m["uuid"] for m in data["messages"]]
    assert msg_uuid in uuids

    # Companion Android device reports delivery status
    res_status = client.post("/api/gateway/status", headers=headers, json={
        "uuid": msg_uuid,
        "status": "sent",
        "delivery_confirmed": True
    })
    assert res_status.status_code == 200
    assert res_status.json.get("status") == "ok"

    with app.app_context():
        msg = AndroidGatewayMessage.query.filter_by(message_uuid=msg_uuid).first()
        assert msg is not None
        assert msg.status == "sent"
        assert msg.carrier_delivery_confirmed is True


def test_android_gateway_heartbeat(upgrade_app):
    app, client, _ = upgrade_app
    raw_api_key = "heartbeat-test-key-555"
    key_hash = hashlib.sha256(raw_api_key.encode()).hexdigest()

    with app.app_context():
        dev = AndroidGatewayDevice(
            device_name="Heartbeat Device",
            device_identifier="heartbeat-dev-555",
            api_key_hash=key_hash,
            is_active=True,
            created_at=utcnow(),
        )
        db.session.add(dev)
        db.session.commit()

    headers = {"X-Gateway-Key": raw_api_key}
    res = client.post("/api/gateway/heartbeat", headers=headers)
    assert res.status_code == 200
    assert res.json.get("status") == "ok"

    with app.app_context():
        updated_dev = AndroidGatewayDevice.query.filter_by(device_identifier="heartbeat-dev-555").first()
        assert updated_dev.last_ping_at is not None


# ===========================================================================
# 5. Unified Admin Notification Center
# ===========================================================================

def test_admin_notification_center_multi_channel_tabs(upgrade_app):
    app, client, _ = upgrade_app
    login(client, "admin_p8", "AdminSecure#2026")

    # Check overall notification center loads
    res_all = client.get("/admin/notifications")
    assert res_all.status_code == 200
    assert b"Notification Center" in res_all.data or b"Unified Notification" in res_all.data

    # Check each individual channel tab
    for ch in ["sms", "email", "push", "inapp", "android_gateway"]:
        res_ch = client.get(f"/admin/notifications?channel={ch}")
        assert res_ch.status_code == 200


def test_admin_notification_center_access_control(upgrade_app):
    app, client, _ = upgrade_app

    # Anonymous -> Redirect to login
    res_anon = client.get("/admin/notifications")
    assert res_anon.status_code in {302, 401}

    # Parent -> 403 Forbidden
    login(client, "parent_p8", "ParentSecure#2026")
    res_parent = client.get("/admin/notifications")
    assert res_parent.status_code == 403
    client.post("/logout", follow_redirects=True)

    # Healthcare Worker -> 403 Forbidden
    login(client, "worker_p8", "WorkerSecure#2026")
    res_worker = client.get("/admin/notifications")
    assert res_worker.status_code == 403


# ===========================================================================
# 6. Landing Page & Truthful Messaging
# ===========================================================================

def test_landing_page_truthful_content(upgrade_app):
    app, client, _ = upgrade_app
    res = client.get("/")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    # Truthful healthcare messaging
    assert "Your Child's Vaccination Companion" in html
    assert "Universal Immunization Programme" in html
    assert "How VaxiCare Works" in html
    assert "Register as a Parent" in html


# ===========================================================================
# 7. Render Deployment & Health Endpoint
# ===========================================================================

def test_health_check_endpoint(upgrade_app):
    _, client, _ = upgrade_app
    res = client.get("/health")
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "healthy"
    assert data["service"] == "vaxicare"
    assert data["database"] == "connected"
    assert "timestamp" in data


def test_pwa_manifest_available(upgrade_app):
    _, client, _ = upgrade_app
    res = client.get("/static/manifest.json")
    assert res.status_code == 200
    manifest = json.loads(res.data)
    assert manifest["name"] == "VaxiCare Immunization Tracker"
    assert manifest["short_name"] == "VaxiCare"
    assert manifest["display"] == "standalone"
    assert len(manifest["icons"]) >= 2


# ===========================================================================
# 8. In-App Notifications: Mark as Read & Device Revocation
# ===========================================================================

def test_inapp_notification_mark_read_and_read_all(upgrade_app):
    app, client, _ = upgrade_app

    with app.app_context():
        parent_user = User.query.filter_by(username="parent_p8").first()
        parent_id = parent_user.id
        n1 = Notification(
            user_id=parent_id,
            reminder_type="upcoming",
            title="Reminder 1",
            body="First dose due",
            status="sent",
            channel="system",
            is_read=False,
        )
        n2 = Notification(
            user_id=parent_id,
            reminder_type="due_today",
            title="Reminder 2",
            body="Second dose due",
            status="sent",
            channel="system",
            is_read=False,
        )
        db.session.add_all([n1, n2])
        db.session.commit()
        n1_id = n1.id
        n2_id = n2.id

    login(client, "parent_p8", "ParentSecure#2026")

    # Mark single notification as read
    res_single = client.post(f"/parent/notifications/{n1_id}/read", headers={"Accept": "application/json"})
    assert res_single.status_code == 200
    with app.app_context():
        assert db.session.get(Notification, n1_id).is_read is True
        assert db.session.get(Notification, n2_id).is_read is False

    # Mark all notifications as read
    res_all = client.post("/parent/notifications/read-all", headers={"Accept": "application/json"})
    assert res_all.status_code == 200
    with app.app_context():
        assert db.session.get(Notification, n2_id).is_read is True


def test_register_push_device_with_metadata_and_revoke(upgrade_app):
    app, client, _ = upgrade_app
    login(client, "parent_p8", "ParentSecure#2026")

    # Register device with device_name and permission_status
    token = "test-token-meta-xyz-987"
    res = client.post(
        "/register-push-device",
        json={
            "token": token,
            "device_name": "Chrome on Android",
            "permission_status": "granted",
        },
    )
    assert res.status_code == 200

    dev_id = None
    with app.app_context():
        dev = FCMDeviceToken.query.filter_by(token=token).first()
        assert dev is not None
        assert dev.device_name == "Chrome on Android"
        assert dev.permission_status == "granted"
        assert dev.is_active is True
        dev_id = dev.id

    # Revoke device via parent endpoint
    res_revoke = client.post(f"/parent/devices/{dev_id}/revoke", headers={"Accept": "application/json"})
    assert res_revoke.status_code == 200
    with app.app_context():
        dev = db.session.get(FCMDeviceToken, dev_id)
        assert dev.is_active is False


def test_parent_dashboard_renders_with_notification_context(upgrade_app):
    app, client, _ = upgrade_app
    app.config["FCM_ENABLED"] = True
    login(client, "parent_p8", "ParentSecure#2026")

    res = client.get("/parent-dashboard")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "Recent In-App Notifications" in html
    assert "Vaccination Push Alerts" in html
    assert "Active devices:" in html
