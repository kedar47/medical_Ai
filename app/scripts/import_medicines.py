import argparse
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.database.connection import SessionLocal, init_db
from app.services.import_service import (
    ImportValidationError,
    import_medicines_from_csv,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import medicines from a validated catalogue CSV."
    )
    parser.add_argument("csv_path", type=Path, help="Path to the medicine CSV file")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level)

    try:
        init_db()
        with SessionLocal() as db:
            result = import_medicines_from_csv(db, args.csv_path)
    except FileNotFoundError as exc:
        print(f"Import failed: {exc}")
        return 1
    except ImportValidationError as exc:
        print(f"Import failed: {exc}")
        return 1
    except SQLAlchemyError as exc:
        print(f"Import failed: database error ({exc.__class__.__name__})")
        return 1

    print("Import completed")
    print(f"Total records: {result.total}")
    print(f"Inserted: {result.inserted}")
    print(f"Updated: {result.updated}")
    print(f"Skipped: {result.skipped}")
    print(f"Failed: {result.failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
