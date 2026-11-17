from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.models import Medicine, Order, OrderItem, OrderSession


def create_session(
    client: TestClient,
    *,
    phone: str | None = "+919876543210",
    name: str | None = "Asha",
) -> dict:
    response = client.post(
        "/order-sessions",
        json={"customer_phone": phone, "customer_name": name},
    )
    assert response.status_code == 201
    return response.json()


def add_item(
    client: TestClient,
    session_id: str,
    *,
    medicine_id: str = "MED-00013",
    quantity: int = 2,
    unit: str = "STRIP",
) -> dict:
    response = client.post(
        f"/order-sessions/{session_id}/items",
        json={
            "medicine_id": medicine_id,
            "quantity": quantity,
            "unit": unit,
        },
    )
    assert response.status_code == 201
    return response.json()


def summary(client: TestClient, session_id: str) -> dict:
    response = client.get(f"/order-sessions/{session_id}/summary")
    assert response.status_code == 200
    return response.json()


def confirm(
    client: TestClient,
    session_id: str,
    token: str,
    *,
    explicit: bool = True,
) -> object:
    return client.post(
        f"/order-sessions/{session_id}/confirm",
        json={
            "confirmation_token": token,
            "confirmation_source": "DEMO_API",
            "explicit_confirmation": explicit,
        },
    )


def test_create_session_persists_active_empty_draft(
    client: TestClient, seed_catalogue: None
) -> None:
    payload = create_session(
        client, phone="  +919876543210  ", name="  Asha  "
    )

    assert payload["session_id"].startswith("session_")
    assert payload["customer_phone"] == "+919876543210"
    assert payload["customer_name"] == "Asha"
    assert payload["status"] == "ACTIVE"
    assert payload["items"] == []
    assert payload["confirmed_at"] is None


def test_session_validation_rejects_bad_phone_and_blank_name(
    client: TestClient,
) -> None:
    assert (
        client.post(
            "/order-sessions", json={"customer_phone": "12345"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/order-sessions", json={"customer_name": "   "}
        ).status_code
        == 422
    )


def test_add_item_uses_catalogue_snapshot_and_accumulates_same_unit(
    client: TestClient, seed_catalogue: None
) -> None:
    session = create_session(client)
    first = add_item(client, session["session_id"], quantity=2)
    second = add_item(client, session["session_id"], quantity=3)

    assert len(first["items"]) == 1
    assert len(second["items"]) == 1
    item = second["items"][0]
    assert item["medicine_id"] == "MED-00013"
    assert item["medicine_name"] == "BRUFEN"
    assert item["description"] == "200MG 15'S"
    assert item["quantity"] == 5
    assert item["unit"] == "STRIP"


def test_same_medicine_in_different_units_creates_separate_lines(
    client: TestClient, seed_catalogue: None
) -> None:
    session = create_session(client)
    add_item(client, session["session_id"], unit="STRIP")
    payload = add_item(client, session["session_id"], unit="BOX")

    assert len(payload["items"]) == 2
    assert {item["unit"] for item in payload["items"]} == {"STRIP", "BOX"}


def test_add_unknown_and_inactive_medicine_returns_404(
    client: TestClient, seed_catalogue: None
) -> None:
    session = create_session(client)

    missing = client.post(
        f"/order-sessions/{session['session_id']}/items",
        json={"medicine_id": "UNKNOWN", "quantity": 1, "unit": "STRIP"},
    )
    inactive = client.post(
        f"/order-sessions/{session['session_id']}/items",
        json={
            "medicine_id": "MED-99999",
            "quantity": 1,
            "unit": "STRIP",
        },
    )

    assert missing.status_code == 404
    assert inactive.status_code == 404


def test_add_item_quantity_validation(client: TestClient, seed_catalogue: None) -> None:
    session = create_session(client)
    url = f"/order-sessions/{session['session_id']}/items"

    for quantity in (0, -1, 1.5, "2"):
        response = client.post(
            url,
            json={
                "medicine_id": "MED-00013",
                "quantity": quantity,
                "unit": "STRIP",
            },
        )
        assert response.status_code == 422

    too_many = client.post(
        url,
        json={
            "medicine_id": "MED-00013",
            "quantity": 101,
            "unit": "STRIP",
        },
    )
    assert too_many.status_code == 400


def test_invalid_unit_is_rejected(client: TestClient, seed_catalogue: None) -> None:
    session = create_session(client)

    response = client.post(
        f"/order-sessions/{session['session_id']}/items",
        json={
            "medicine_id": "MED-00013",
            "quantity": 1,
            "unit": "SPOON",
        },
    )

    assert response.status_code == 422


def test_update_item_quantity_and_unit(
    client: TestClient, seed_catalogue: None
) -> None:
    session = create_session(client)
    item = add_item(client, session["session_id"])["items"][0]

    response = client.patch(
        f"/order-sessions/{session['session_id']}/items/{item['id']}",
        json={"quantity": 7, "unit": "BOX"},
    )

    assert response.status_code == 200
    updated = response.json()["items"][0]
    assert updated["quantity"] == 7
    assert updated["unit"] == "BOX"


def test_update_item_rejects_duplicate_medicine_unit(
    client: TestClient, seed_catalogue: None
) -> None:
    session = create_session(client)
    add_item(client, session["session_id"], unit="STRIP")
    second = add_item(client, session["session_id"], unit="BOX")["items"][1]

    response = client.patch(
        f"/order-sessions/{session['session_id']}/items/{second['id']}",
        json={"unit": "STRIP"},
    )

    assert response.status_code == 409


def test_update_item_requires_a_change_and_existing_item(
    client: TestClient, seed_catalogue: None
) -> None:
    session = create_session(client)
    item = add_item(client, session["session_id"])["items"][0]
    url = f"/order-sessions/{session['session_id']}/items/{item['id']}"

    assert client.patch(url, json={}).status_code == 422
    assert (
        client.patch(
            f"/order-sessions/{session['session_id']}/items/99999",
            json={"quantity": 2},
        ).status_code
        == 404
    )


def test_delete_item_removes_it_and_unknown_item_returns_404(
    client: TestClient, seed_catalogue: None
) -> None:
    session = create_session(client)
    item = add_item(client, session["session_id"])["items"][0]
    url = f"/order-sessions/{session['session_id']}/items/{item['id']}"

    deleted = client.delete(url)

    assert deleted.status_code == 200
    assert deleted.json()["items"] == []
    assert client.delete(url).status_code == 404


def test_empty_summary_cannot_be_confirmed(
    client: TestClient, seed_catalogue: None
) -> None:
    session = create_session(client)
    payload = summary(client, session["session_id"])

    assert payload["line_item_count"] == 0
    assert payload["can_confirm"] is False
    assert payload["confirmation_token"] is None
    assert payload["summary_digest"] is None
    assert "empty" in payload["message"].lower()

    response = confirm(client, session["session_id"], "x" * 32)
    assert response.status_code == 400


def test_summary_issues_token_then_confirm_creates_single_order(
    client: TestClient, db_session: Session, seed_catalogue: None
) -> None:
    session = create_session(client)
    add_item(client, session["session_id"], medicine_id="MED-00013", quantity=2)
    add_item(client, session["session_id"], medicine_id="MED-00003", quantity=1)
    draft = summary(client, session["session_id"])

    assert draft["can_confirm"] is True
    assert draft["line_item_count"] == 2
    assert draft["confirmation_token"]
    assert len(draft["summary_digest"]) == 64

    response = confirm(client, session["session_id"], draft["confirmation_token"])

    assert response.status_code == 201
    order = response.json()
    assert order["order_id"].startswith("order_")
    assert order["session_id"] == session["session_id"]
    assert order["status"] == "PENDING_PHARMACY_REVIEW"
    assert order["confirmation_source"] == "DEMO_API"
    assert order["total_line_items"] == 2
    assert order["idempotent_retry"] is False
    assert {line["medicine_name"] for line in order["line_items"]} == {
        "BRUFEN",
        "CLOMIN TAB",
    }

    fetched = client.get(f"/orders/{order['order_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["order_id"] == order["order_id"]

    session_payload = client.get(
        f"/order-sessions/{session['session_id']}"
    ).json()
    assert session_payload["status"] == "CONFIRMED"
    assert session_payload["confirmed_at"] is not None

    order_count = db_session.scalar(
        select(func.count()).select_from(Order)
    )
    assert order_count == 1


def test_repeat_confirmation_with_same_token_is_idempotent(
    client: TestClient, db_session: Session, seed_catalogue: None
) -> None:
    session = create_session(client)
    add_item(client, session["session_id"])
    token = summary(client, session["session_id"])["confirmation_token"]

    first = confirm(client, session["session_id"], token)
    second = confirm(client, session["session_id"], token)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["idempotent_retry"] is True
    assert second.json()["order_id"] == first.json()["order_id"]
    order_count = db_session.scalar(
        select(func.count()).select_from(Order)
    )
    assert order_count == 1


def test_confirmation_requires_explicit_true(
    client: TestClient, db_session: Session, seed_catalogue: None
) -> None:
    session = create_session(client)
    add_item(client, session["session_id"])
    token = summary(client, session["session_id"])["confirmation_token"]

    response = confirm(client, session["session_id"], token, explicit=False)

    assert response.status_code == 400
    assert db_session.scalar(select(func.count()).select_from(Order)) == 0
    assert (
        client.get(f"/order-sessions/{session['session_id']}").json()["status"]
        == "ACTIVE"
    )


def test_invalid_confirmation_token_returns_409(
    client: TestClient, seed_catalogue: None
) -> None:
    session = create_session(client)
    add_item(client, session["session_id"])
    summary(client, session["session_id"])

    response = confirm(client, session["session_id"], "z" * 32)

    assert response.status_code == 409
    assert "token" in response.json()["detail"].lower()


def test_adding_item_invalidates_summary_token(
    client: TestClient, seed_catalogue: None
) -> None:
    session = create_session(client)
    add_item(client, session["session_id"])
    token = summary(client, session["session_id"])["confirmation_token"]

    add_item(client, session["session_id"], medicine_id="MED-00003")
    response = confirm(client, session["session_id"], token)

    assert response.status_code == 409
    assert (
        client.get(f"/order-sessions/{session['session_id']}").json()["status"]
        == "ACTIVE"
    )


def test_updating_item_invalidates_summary_token(
    client: TestClient, seed_catalogue: None
) -> None:
    session = create_session(client)
    item = add_item(client, session["session_id"])["items"][0]
    token = summary(client, session["session_id"])["confirmation_token"]

    client.patch(
        f"/order-sessions/{session['session_id']}/items/{item['id']}",
        json={"quantity": 4},
    )
    stale = confirm(client, session["session_id"], token)

    assert stale.status_code == 409

    fresh_token = summary(client, session["session_id"])[
        "confirmation_token"
    ]
    assert confirm(client, session["session_id"], fresh_token).status_code == 201


def test_deleting_item_invalidates_summary_token(
    client: TestClient, seed_catalogue: None
) -> None:
    session = create_session(client)
    first = add_item(client, session["session_id"])["items"][0]
    add_item(client, session["session_id"], medicine_id="MED-00003")
    token = summary(client, session["session_id"])["confirmation_token"]

    client.delete(
        f"/order-sessions/{session['session_id']}/items/{first['id']}"
    )
    response = confirm(client, session["session_id"], token)

    assert response.status_code == 409


def test_digest_change_without_token_change_is_rejected(
    client: TestClient, db_session: Session, seed_catalogue: None
) -> None:
    session = create_session(client)
    add_item(client, session["session_id"])
    token = summary(client, session["session_id"])["confirmation_token"]

    order_session = db_session.scalar(
        select(OrderSession).where(
            OrderSession.session_id == session["session_id"]
        )
    )
    item = db_session.scalar(
        select(OrderItem).where(OrderItem.session_id == order_session.id)
    )
    item.quantity += 1
    db_session.commit()

    response = confirm(client, session["session_id"], token)

    assert response.status_code == 409
    assert "draft changed" in response.json()["detail"].lower()


def test_inactive_catalogue_item_at_confirmation_is_rejected(
    client: TestClient, db_session: Session, seed_catalogue: None
) -> None:
    session = create_session(client)
    add_item(client, session["session_id"])
    token = summary(client, session["session_id"])["confirmation_token"]

    medicine = db_session.scalar(
        select(Medicine).where(Medicine.medicine_id == "MED-00013")
    )
    medicine.is_active = False
    db_session.commit()

    response = confirm(client, session["session_id"], token)

    assert response.status_code == 409
    assert db_session.scalar(select(func.count()).select_from(Order)) == 0


def test_cancel_session_blocks_mutation_and_confirmation(
    client: TestClient, seed_catalogue: None
) -> None:
    session = create_session(client)
    add_item(client, session["session_id"])
    token = summary(client, session["session_id"])["confirmation_token"]

    cancelled = client.post(
        f"/order-sessions/{session['session_id']}/cancel"
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"

    again = client.post(f"/order-sessions/{session['session_id']}/cancel")
    assert again.status_code == 200
    assert again.json()["status"] == "CANCELLED"

    add = client.post(
        f"/order-sessions/{session['session_id']}/items",
        json={"medicine_id": "MED-00003", "quantity": 1, "unit": "STRIP"},
    )
    assert add.status_code == 409

    summary_payload = summary(client, session["session_id"])
    assert summary_payload["can_confirm"] is False
    assert summary_payload["confirmation_token"] is None
    assert confirm(client, session["session_id"], token).status_code == 409


def test_expired_session_blocks_mutation_and_confirmation(
    client: TestClient, db_session: Session, seed_catalogue: None
) -> None:
    session = create_session(client)
    add_item(client, session["session_id"])
    token = summary(client, session["session_id"])["confirmation_token"]

    order_session = db_session.scalar(
        select(OrderSession).where(
            OrderSession.session_id == session["session_id"]
        )
    )
    order_session.expires_at = datetime.now(timezone.utc) - timedelta(
        minutes=1
    )
    db_session.commit()

    fetched = client.get(f"/order-sessions/{session['session_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "EXPIRED"

    summary_payload = summary(client, session["session_id"])
    assert summary_payload["status"] == "EXPIRED"
    assert summary_payload["can_confirm"] is False
    assert summary_payload["confirmation_token"] is None

    assert confirm(client, session["session_id"], token).status_code == 409
    add = client.post(
        f"/order-sessions/{session['session_id']}/items",
        json={"medicine_id": "MED-00003", "quantity": 1, "unit": "STRIP"},
    )
    assert add.status_code == 409


def test_order_preserves_catalogue_snapshot(
    client: TestClient, db_session: Session, seed_catalogue: None
) -> None:
    session = create_session(client)
    add_item(client, session["session_id"])
    token = summary(client, session["session_id"])["confirmation_token"]
    order_id = confirm(client, session["session_id"], token).json()["order_id"]

    medicine = db_session.scalar(
        select(Medicine).where(Medicine.medicine_id == "MED-00013")
    )
    medicine.medicine_name = "BRUFEN RENAMED"
    medicine.description = "CHANGED"
    db_session.commit()

    line = client.get(f"/orders/{order_id}").json()["line_items"][0]
    assert line["medicine_name"] == "BRUFEN"
    assert line["medicine_description"] == "200MG 15'S"


def test_one_order_per_session_is_enforced_by_unique_constraint(
    client: TestClient, db_session: Session, seed_catalogue: None
) -> None:
    session = create_session(client)
    add_item(client, session["session_id"])
    token = summary(client, session["session_id"])["confirmation_token"]
    confirm(client, session["session_id"], token)

    order_session = db_session.scalar(
        select(OrderSession).where(
            OrderSession.session_id == session["session_id"]
        )
    )
    now = datetime.now(timezone.utc)
    db_session.add(
        Order(
            order_id="order_duplicate",
            session_id=order_session.id,
            status="PENDING_PHARMACY_REVIEW",
            confirmed_at=now,
            created_at=now,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_missing_resources_return_404(
    client: TestClient, seed_catalogue: None
) -> None:
    assert client.get("/order-sessions/missing").status_code == 404
    assert client.get("/order-sessions/missing/summary").status_code == 404
    assert client.post("/order-sessions/missing/cancel").status_code == 404
    assert client.get("/orders/missing").status_code == 404
