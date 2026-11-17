import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.models import Order
from app.main import app

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")
POSTGRES_ONLY = pytest.mark.skipif(
    not TEST_DATABASE_URL.startswith("postgresql"),
    reason="PostgreSQL is required for row-lock concurrency testing",
)


@POSTGRES_ONLY
def test_concurrent_confirmations_create_one_order(
    client: TestClient, db_session: Session, seed_catalogue: None
) -> None:
    session = client.post("/order-sessions", json={}).json()
    session_id = session["session_id"]
    client.post(
        f"/order-sessions/{session_id}/items",
        json={"medicine_id": "MED-00013", "quantity": 2, "unit": "STRIP"},
    )
    token = client.get(
        f"/order-sessions/{session_id}/summary"
    ).json()["confirmation_token"]

    barrier = threading.Barrier(2)

    def worker() -> tuple[int, dict]:
        with TestClient(app) as thread_client:
            barrier.wait()
            response = thread_client.post(
                f"/order-sessions/{session_id}/confirm",
                json={
                    "confirmation_token": token,
                    "confirmation_source": "DEMO_API",
                    "explicit_confirmation": True,
                },
            )
            return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [executor.submit(worker) for _ in range(2)]
        outcomes = [result.result() for result in results]

    status_codes = sorted(status for status, _ in outcomes)
    assert status_codes == [200, 201]
    assert outcomes[0][1]["order_id"] == outcomes[1][1]["order_id"]
    assert db_session.scalar(select(func.count()).select_from(Order)) == 1
