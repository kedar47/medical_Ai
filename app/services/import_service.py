import csv
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.database.models import Medicine
from app.repositories.medicine_repository import MedicineRepository

REQUIRED_HEADERS = (
    "medicine_id",
    "medicine_name",
    "description",
    "source_serial_number",
    "source_row",
)


class ImportValidationError(ValueError):
    pass


class MissingHeadersError(ImportValidationError):
    pass


class DuplicateMedicineIdError(ImportValidationError):
    pass


@dataclass(frozen=True)
class CsvMedicineRecord:
    medicine_id: str
    medicine_name: str
    description: str
    source_serial_number: str | None
    source_row: int | None


@dataclass
class ImportResult:
    total: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0


def _parse_source_row(value: str, line_number: int) -> int | None:
    if not value:
        return None
    try:
        source_row = int(value)
    except ValueError as exc:
        raise ImportValidationError(
            f"line {line_number}: source_row must be an integer"
        ) from exc
    if source_row < 1:
        raise ImportValidationError(
            f"line {line_number}: source_row must be a positive integer"
        )
    return source_row


def read_medicine_csv(csv_path: Path) -> list[CsvMedicineRecord]:
    if not csv_path.is_file():
        raise FileNotFoundError(f"Medicine CSV not found: {csv_path}")

    records: list[CsvMedicineRecord] = []
    seen_ids: dict[str, int] = {}
    duplicate_ids: set[str] = set()
    errors: list[str] = []

    with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        if reader.fieldnames is None:
            raise MissingHeadersError("CSV has no header row")

        headers = [header.strip() for header in reader.fieldnames if header]
        missing_headers = [
            header for header in REQUIRED_HEADERS if header not in headers
        ]
        if missing_headers:
            raise MissingHeadersError(
                "Missing required CSV headers: " + ", ".join(missing_headers)
            )

        for line_number, raw_row in enumerate(reader, start=2):
            row = {
                (key or "").strip(): (value if value is not None else "")
                for key, value in raw_row.items()
            }
            medicine_id = row.get("medicine_id", "").strip()
            medicine_name = row.get("medicine_name", "")
            description = row.get("description", "")
            source_serial_number = (
                row.get("source_serial_number", "").strip() or None
            )
            source_row_text = row.get("source_row", "").strip()

            if not medicine_id:
                errors.append(f"line {line_number}: medicine_id is required")
            if not medicine_name.strip():
                errors.append(f"line {line_number}: medicine_name is required")

            try:
                source_row = _parse_source_row(source_row_text, line_number)
            except ImportValidationError as exc:
                errors.append(str(exc))
                source_row = None

            if medicine_id:
                if medicine_id in seen_ids:
                    duplicate_ids.add(medicine_id)
                else:
                    seen_ids[medicine_id] = line_number

            records.append(
                CsvMedicineRecord(
                    medicine_id=medicine_id,
                    medicine_name=medicine_name,
                    description=description,
                    source_serial_number=source_serial_number,
                    source_row=source_row,
                )
            )

    if duplicate_ids:
        raise DuplicateMedicineIdError(
            "Duplicate medicine IDs in CSV: " + ", ".join(sorted(duplicate_ids))
        )

    if errors:
        raise ImportValidationError("; ".join(errors))

    return records


def import_medicines_from_csv(
    db: Session, csv_path: Path | str
) -> ImportResult:
    records = read_medicine_csv(Path(csv_path))
    repository = MedicineRepository(db)
    result = ImportResult(total=len(records))

    try:
        for record in records:
            existing = repository.get_by_medicine_id(
                record.medicine_id, active_only=False
            )
            if existing is None:
                repository.add(
                    Medicine(
                        medicine_id=record.medicine_id,
                        medicine_name=record.medicine_name,
                        description=record.description,
                        source_serial_number=record.source_serial_number,
                        source_row=record.source_row,
                        is_active=True,
                    )
                )
                result.inserted += 1
                continue

            changed = (
                existing.medicine_name != record.medicine_name
                or existing.description != record.description
                or existing.source_serial_number != record.source_serial_number
                or existing.source_row != record.source_row
            )
            if changed:
                existing.medicine_name = record.medicine_name
                existing.description = record.description
                existing.source_serial_number = record.source_serial_number
                existing.source_row = record.source_row
                result.updated += 1
            else:
                result.skipped += 1

        db.commit()
    except Exception:
        db.rollback()
        raise

    return result
