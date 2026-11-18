import csv
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.models import Medicine
from app.services.import_service import (
    DuplicateMedicineIdError,
    ImportValidationError,
    MissingHeadersError,
    import_medicines_from_csv,
    read_medicine_csv,
)

CSV_HEADERS = [
    "medicine_id",
    "medicine_name",
    "description",
    "source_serial_number",
    "source_row",
]
ROOT = Path(__file__).resolve().parents[1]
DEMO_CSV = ROOT / "data" / "demo_medicines.csv"


def write_csv(path: Path, rows: list[dict[str, object]], headers=None) -> Path:
    fieldnames = headers or CSV_HEADERS
    with path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


def sample_rows() -> list[dict[str, object]]:
    return [
        {
            "medicine_id": "MED-00013",
            "medicine_name": "BRUFEN",
            "description": "200MG 15'S",
            "source_serial_number": "5",
            "source_row": 13,
        },
        {
            "medicine_id": "MED-00014",
            "medicine_name": "BRUFEN",
            "description": "400MG 15'S",
            "source_serial_number": "6",
            "source_row": 14,
        },
    ]


def test_valid_csv_import(tmp_path: Path, db_session: Session) -> None:
    csv_path = write_csv(tmp_path / "valid.csv", sample_rows())

    result = import_medicines_from_csv(db_session, csv_path)

    assert result.total == 2
    assert result.inserted == 2
    assert result.updated == 0
    assert result.skipped == 0
    assert result.failed == 0
    assert db_session.scalar(select(func.count()).select_from(Medicine)) == 2


def test_missing_csv_file_raises(db_session: Session, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        import_medicines_from_csv(db_session, tmp_path / "missing.csv")


def test_invalid_csv_headers_raise(tmp_path: Path, db_session: Session) -> None:
    csv_path = write_csv(
        tmp_path / "bad_headers.csv",
        [{"medicine_id": "MED-1", "medicine_name": "TEST"}],
        headers=["medicine_id", "medicine_name"],
    )

    with pytest.raises(MissingHeadersError):
        import_medicines_from_csv(db_session, csv_path)

    assert db_session.scalar(select(func.count()).select_from(Medicine)) == 0


def test_duplicate_medicine_ids_are_rejected(
    tmp_path: Path, db_session: Session
) -> None:
    rows = sample_rows()
    rows[1]["medicine_id"] = rows[0]["medicine_id"]
    csv_path = write_csv(tmp_path / "duplicates.csv", rows)

    with pytest.raises(DuplicateMedicineIdError):
        import_medicines_from_csv(db_session, csv_path)

    assert db_session.scalar(select(func.count()).select_from(Medicine)) == 0


def test_empty_medicine_name_is_rejected(
    tmp_path: Path, db_session: Session
) -> None:
    rows = sample_rows()
    rows[0]["medicine_name"] = "   "
    csv_path = write_csv(tmp_path / "empty_name.csv", rows)

    with pytest.raises(ImportValidationError, match="medicine_name is required"):
        import_medicines_from_csv(db_session, csv_path)

    assert db_session.scalar(select(func.count()).select_from(Medicine)) == 0


def test_empty_medicine_id_is_rejected(
    tmp_path: Path, db_session: Session
) -> None:
    rows = sample_rows()
    rows[0]["medicine_id"] = ""
    csv_path = write_csv(tmp_path / "empty_id.csv", rows)

    with pytest.raises(ImportValidationError, match="medicine_id is required"):
        import_medicines_from_csv(db_session, csv_path)

    assert db_session.scalar(select(func.count()).select_from(Medicine)) == 0


def test_invalid_batch_is_not_partially_imported(
    tmp_path: Path, db_session: Session
) -> None:
    rows = sample_rows()
    rows[1]["medicine_name"] = ""
    csv_path = write_csv(tmp_path / "partial.csv", rows)

    with pytest.raises(ImportValidationError):
        import_medicines_from_csv(db_session, csv_path)

    assert db_session.scalar(select(func.count()).select_from(Medicine)) == 0


def test_import_is_idempotent(tmp_path: Path, db_session: Session) -> None:
    csv_path = write_csv(tmp_path / "idempotent.csv", sample_rows())

    first = import_medicines_from_csv(db_session, csv_path)
    second = import_medicines_from_csv(db_session, csv_path)

    assert first.inserted == 2
    assert second.inserted == 0
    assert second.updated == 0
    assert second.skipped == 2
    assert db_session.scalar(select(func.count()).select_from(Medicine)) == 2


def test_reimport_updates_changed_records(
    tmp_path: Path, db_session: Session
) -> None:
    csv_path = write_csv(tmp_path / "update.csv", sample_rows())
    import_medicines_from_csv(db_session, csv_path)

    rows = sample_rows()
    rows[0]["description"] = "200MG 20'S"
    updated_csv = write_csv(tmp_path / "update_changed.csv", rows)
    result = import_medicines_from_csv(db_session, updated_csv)

    assert result.updated == 1
    assert result.skipped == 1
    medicine = db_session.scalar(
        select(Medicine).where(Medicine.medicine_id == "MED-00013")
    )
    assert medicine is not None
    assert medicine.description == "200MG 20'S"


def test_demo_csv_contains_exactly_26_unique_records() -> None:
    assert DEMO_CSV.is_file(), "data/demo_medicines.csv is missing"
    records = read_medicine_csv(DEMO_CSV)

    assert len(records) == 26
    assert len({record.medicine_id for record in records}) == 26
    assert all(record.medicine_name.strip() for record in records)
    assert all(record.source_row is not None for record in records)


def test_demo_csv_import_and_variant_preservation(db_session: Session) -> None:
    result = import_medicines_from_csv(db_session, DEMO_CSV)

    assert result.total == 26
    assert result.inserted == 26
    assert db_session.scalar(select(func.count()).select_from(Medicine)) == 26

    variants = list(
        db_session.scalars(
            select(Medicine).where(Medicine.medicine_name == "BRUFEN")
        )
    )
    assert len(variants) == 3
    assert {variant.description for variant in variants} == {
        "200MG                             15'S",
        "400MG                             15'S",
        "600MG                             15'S",
    }
    assert all(variant.source_row is not None for variant in variants)
