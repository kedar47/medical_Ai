"""Make a medicine CSV the whole active catalogue.

`import_medicines` adds and updates rows but never switches any off. This
script switches on every medicine in the CSV and switches off all others
(nothing is deleted; past orders keep their medicines).

    python -m app.scripts.activate_only_csv data/client_demo_medicines.csv
"""
import argparse
import csv
from pathlib import Path

from sqlalchemy import update

from app.database.connection import SessionLocal
from app.database.models import Medicine


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("csv_path", type=Path)
    args = parser.parse_args()
    with args.csv_path.open(encoding="utf-8", newline="") as handle:
        ids = [row["medicine_id"] for row in csv.DictReader(handle)]
    if not ids:
        raise SystemExit("The CSV has no medicine_id rows; nothing changed.")
    with SessionLocal() as db:
        off = db.execute(
            update(Medicine).where(Medicine.medicine_id.not_in(ids)).values(is_active=False)
        ).rowcount
        db.execute(update(Medicine).where(Medicine.medicine_id.in_(ids)).values(is_active=True))
        db.commit()
        active = db.query(Medicine).filter(Medicine.is_active).count()
    print(f"Active: {active} (from {len(ids)} CSV rows). Switched off: {off}.")


if __name__ == "__main__":
    main()
