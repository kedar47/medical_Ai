import argparse
import csv
from pathlib import Path

from openpyxl import load_workbook

EXPECTED_HEADERS = ("sno", "item", "descriptio")
SELECTED_SOURCE_ROWS = (
    3,
    4,
    5,
    6,
    7,
    9,
    10,
    11,
    12,
    13,
    14,
    15,
    16,
    17,
    19,
    20,
    21,
    22,
    23,
    24,
)
CSV_HEADERS = (
    "medicine_id",
    "medicine_name",
    "description",
    "source_serial_number",
    "source_row",
)


def _to_serial(value: object) -> str:
    if value is None:
        raise ValueError("source serial number is missing")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def build_demo_csv(source_path: Path, output_path: Path) -> None:
    workbook = load_workbook(source_path, read_only=True, data_only=True)
    worksheet = workbook.active
    rows = list(worksheet.iter_rows(values_only=True))

    header_index = None
    for index, row in enumerate(rows):
        values = tuple(
            str(value).strip().lower() if value is not None else ""
            for value in row[:3]
        )
        if values == EXPECTED_HEADERS:
            header_index = index
            break

    if header_index is None:
        raise ValueError(
            "Could not locate the expected header row: sno, item, descriptio"
        )

    records = []
    for source_row in SELECTED_SOURCE_ROWS:
        if source_row < header_index + 2 or source_row > len(rows):
            raise ValueError(f"Selected source row {source_row} is out of range")

        serial, item, description = rows[source_row - 1][:3]
        if serial is None or item is None or description is None:
            raise ValueError(
                f"Selected source row {source_row} does not contain a complete "
                "medicine record"
            )

        medicine_name = str(item)
        if not medicine_name.strip():
            raise ValueError(f"Selected source row {source_row} has no medicine name")

        records.append(
            {
                "medicine_id": f"MED-{source_row:05d}",
                "medicine_name": medicine_name,
                "description": str(description),
                "source_serial_number": _to_serial(serial),
                "source_row": source_row,
            }
        )

    if len(records) != 20:
        raise ValueError(f"Expected exactly 20 selected records, got {len(records)}")

    medicine_ids = [record["medicine_id"] for record in records]
    if len(set(medicine_ids)) != len(medicine_ids):
        raise ValueError("Selected records contain duplicate medicine IDs")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_HEADERS)
        writer.writeheader()
        writer.writerows(records)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate data/demo_medicines.csv from the client's Book1.xlsx workbook."
        )
    )
    parser.add_argument(
        "--source", type=Path, default=Path("Book1.xlsx"), help="Path to Book1.xlsx"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/demo_medicines.csv"),
        help="Output CSV path",
    )
    args = parser.parse_args(argv)

    build_demo_csv(args.source, args.output)
    print(f"Created {args.output} with 20 verified medicine records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
