from datetime import timedelta
import pytest
from sqlalchemy import select
from app.core.config import get_settings
from app.database.models import OrderSession, utc_now
from app.services.telephony.call_manager import CallManager

AUTH = {"Authorization": "Bearer dashboard-test"}


@pytest.fixture(autouse=True)
def dashboard_settings(monkeypatch):
    monkeypatch.setattr(get_settings(), "dashboard_enabled", True)
    monkeypatch.setattr(get_settings(), "dashboard_token", "dashboard-test")


@pytest.mark.parametrize("path", ["stats", "orders", "sessions", "calls", "calls/missing"])
def test_operator_access_required(client, path):
    assert client.get(f"/dashboard/{path}").status_code == 401
    assert client.get(f"/dashboard/{path}", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_disabled_access(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "dashboard_enabled", False)
    assert client.get("/dashboard/stats", headers=AUTH).status_code == 503


def test_stats_empty_and_real_catalogue(client, seed_catalogue):
    result = client.get("/dashboard/stats", headers=AUTH).json()
    assert result == {"medicines": 6, "orders": 0, "active_sessions": 0, "calls": 0, "active_calls": 0}


def test_sessions_pagination_and_masking(client, db_session):
    first = client.post("/order-sessions", json={"customer_phone": "+919876543210", "customer_name": "Operator test"}).json()
    second = client.post("/order-sessions", json={}).json()
    row = db_session.scalar(select(OrderSession).where(OrderSession.session_id == first["session_id"]))
    row.expires_at = utc_now() - timedelta(seconds=1)
    db_session.commit()
    assert client.get("/dashboard/stats", headers=AUTH).json()["active_sessions"] == 1
    data = client.get("/dashboard/sessions?limit=1", headers=AUTH).json()
    assert data["total"] == 2 and len(data["items"]) == 1
    expired = client.get("/dashboard/sessions?status=EXPIRED", headers=AUTH).json()
    assert expired["total"] == 1 and expired["items"][0]["status"] == "EXPIRED"
    assert expired["items"][0]["customer_phone"].endswith("3210")
    assert "+919876543210" not in str(expired)
    assert client.get("/dashboard/sessions?limit=101", headers=AUTH).status_code == 422
    assert client.get("/dashboard/sessions?offset=-1", headers=AUTH).status_code == 422


def test_real_order_listing(client, seed_catalogue):
    session = client.post("/order-sessions", json={"customer_phone": "+919876543210"}).json()["session_id"]
    client.post(f"/order-sessions/{session}/items", json={"medicine_id": "MED-00003", "quantity": 2, "unit": "STRIP"})
    summary = client.get(f"/order-sessions/{session}/summary").json()
    order = client.post(f"/order-sessions/{session}/confirm", json={"confirmation_token": summary["confirmation_token"], "explicit_confirmation": True}).json()
    data = client.get("/dashboard/orders?limit=1", headers=AUTH).json()
    assert data["total"] == 1 and data["items"][0]["order_id"] == order["order_id"]
    assert data["items"][0]["total_line_items"] == 1
    assert "+919876543210" not in str(data)
    assert client.get("/dashboard/stats", headers=AUTH).json()["orders"] == 1


def test_call_history_filter_and_voice_mapping(client, db_session):
    call = CallManager(db_session).incoming("mock", "dashboard-call", "+919876543210")
    data = client.get("/dashboard/calls?active=true", headers=AUTH).json()
    assert data["total"] == 1 and data["items"][0]["provider"] == "mock"
    assert client.get(f"/dashboard/calls/{call.call_id}", headers=AUTH).json()["customer_phone"].endswith("3210")
    assert client.get("/dashboard/calls/missing", headers=AUTH).status_code == 404
    sessions = client.get("/dashboard/sessions", headers=AUTH).json()
    assert sessions["items"][0]["voice_session_id"] == call.voice_session_id
    CallManager(db_session).finish(call.call_id, "test_end")
    assert client.get("/dashboard/calls?active=true", headers=AUTH).json()["total"] == 0
    assert client.get("/dashboard/calls?status=DISCONNECTED", headers=AUTH).json()["total"] == 1
