from collections.abc import Generator

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.database.models import Medicine
from app.main import app


def make_medicine(
    medicine_id: str,
    medicine_name: str,
    description: str,
    source_serial_number: str = "1",
    source_row: int = 3,
    is_active: bool = True,
) -> Medicine:
    return Medicine(
        medicine_id=medicine_id,
        medicine_name=medicine_name,
        description=description,
        source_serial_number=source_serial_number,
        source_row=source_row,
        is_active=is_active,
    )


def seed_brufen_variants(db: Session) -> None:
    db.add_all(
        [
            make_medicine("MED-00013", "BRUFEN", "200MG 15'S", "5", 13),
            make_medicine("MED-00014", "BRUFEN", "400MG 15'S", "6", 14),
            make_medicine("MED-00015", "BRUFEN", "600MG 15'S", "7", 15),
            make_medicine("MED-00003", "CLOMIN TAB", "10'S", "1", 3),
        ]
    )
    db.commit()


def test_health_endpoint_reports_connection(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "connected"}


def test_health_endpoint_reports_database_failure(client: TestClient) -> None:
    class BrokenSession:
        def execute(self, *args, **kwargs):
            raise OperationalError("SELECT 1", {}, Exception("database down"))

    def broken_db() -> Generator[BrokenSession, None, None]:
        yield BrokenSession()

    app.dependency_overrides[get_db] = broken_db
    response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "error", "database": "unavailable"}
    assert "password" not in response.text.lower()


def test_database_connectivity(db_session: Session) -> None:
    assert db_session.execute(text("SELECT 1")).scalar() == 1


def test_catalogue_database_error_returns_generic_503(
    client: TestClient,
) -> None:
    class BrokenSession:
        def execute(self, *args, **kwargs):
            raise OperationalError(
                "SELECT medicines", {}, Exception("database down")
            )

        def scalar(self, *args, **kwargs):
            raise OperationalError(
                "SELECT medicines", {}, Exception("database down")
            )

    def broken_db() -> Generator[BrokenSession, None, None]:
        yield BrokenSession()

    app.dependency_overrides[get_db] = broken_db
    response = client.get("/medicines")

    assert response.status_code == 503
    assert response.json() == {"detail": "Database service unavailable"}
    assert "password" not in response.text.lower()


def test_medicine_count(client: TestClient, db_session: Session) -> None:
    seed_brufen_variants(db_session)

    response = client.get("/medicines/count")

    assert response.status_code == 200
    assert response.json() == {"total": 4}


def test_medicine_search_is_case_insensitive(
    client: TestClient, db_session: Session
) -> None:
    seed_brufen_variants(db_session)

    response = client.get("/medicines", params={"q": "brufen"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert {item["description"] for item in payload["items"]} == {
        "200MG 15'S",
        "400MG 15'S",
        "600MG 15'S",
    }


def test_medicine_search_matches_description(
    client: TestClient, db_session: Session
) -> None:
    seed_brufen_variants(db_session)

    response = client.get("/medicines", params={"q": "400MG"})

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["medicine_id"] == "MED-00014"


def test_exact_lookup_returns_all_variants(
    client: TestClient, db_session: Session
) -> None:
    seed_brufen_variants(db_session)

    response = client.get("/medicines/lookup/exact", params={"name": "  brufen  "})

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 3
    assert {item["description"] for item in payload} == {
        "200MG 15'S",
        "400MG 15'S",
        "600MG 15'S",
    }


def test_exact_lookup_preserves_variants_as_separate_rows(
    client: TestClient, db_session: Session
) -> None:
    seed_brufen_variants(db_session)

    response = client.get("/medicines/lookup/exact", params={"name": "BRUFEN"})
    payload = response.json()

    assert len(payload) == 3
    assert len({item["id"] for item in payload}) == 3
    assert len({item["medicine_id"] for item in payload}) == 3


def test_exact_lookup_unknown_name_returns_empty_list(
    client: TestClient, db_session: Session
) -> None:
    seed_brufen_variants(db_session)

    response = client.get("/medicines/lookup/exact", params={"name": "UNKNOWN"})

    assert response.status_code == 200
    assert response.json() == []


def test_get_medicine_by_id(client: TestClient, db_session: Session) -> None:
    seed_brufen_variants(db_session)

    response = client.get("/medicines/MED-00014")

    assert response.status_code == 200
    payload = response.json()
    assert payload["medicine_id"] == "MED-00014"
    assert payload["medicine_name"] == "BRUFEN"
    assert payload["description"] == "400MG 15'S"
    assert payload["source_serial_number"] == "6"
    assert payload["source_row"] == 14


def test_get_medicine_unknown_id_returns_404(
    client: TestClient, db_session: Session
) -> None:
    response = client.get("/medicines/DOES-NOT-EXIST")

    assert response.status_code == 404
    assert response.json() == {"detail": "Medicine not found"}


def test_invalid_pagination_parameters_return_422(
    client: TestClient, db_session: Session
) -> None:
    seed_brufen_variants(db_session)

    assert client.get("/medicines", params={"limit": 0}).status_code == 422
    assert client.get("/medicines", params={"limit": 101}).status_code == 422
    assert client.get("/medicines", params={"offset": -1}).status_code == 422
    assert client.get("/medicines", params={"limit": "abc"}).status_code == 422


def test_pagination_returns_expected_slice(
    client: TestClient, db_session: Session
) -> None:
    seed_brufen_variants(db_session)

    response = client.get("/medicines", params={"limit": 2, "offset": 1})

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 4
    assert payload["limit"] == 2
    assert payload["offset"] == 1
    assert len(payload["items"]) == 2


def test_inactive_medicines_are_not_returned(
    client: TestClient, db_session: Session
) -> None:
    db_session.add(
        make_medicine(
            "MED-99999",
            "INACTIVE MEDICINE",
            "TEST",
            is_active=False,
        )
    )
    db_session.commit()

    assert client.get("/medicines").json()["total"] == 0
    assert client.get("/medicines/count").json() == {"total": 0}
    assert client.get("/medicines/MED-99999").status_code == 404
