from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.database.models import Medicine


def normalize_name(value: str) -> str:
    return " ".join(value.strip().split()).casefold()


class MedicineRepository:
    def __init__(self, db: Session):
        self.db = db

    def add(self, medicine: Medicine) -> Medicine:
        self.db.add(medicine)
        return medicine

    def get_by_medicine_id(
        self, medicine_id: str, *, active_only: bool = True
    ) -> Medicine | None:
        statement = select(Medicine).where(Medicine.medicine_id == medicine_id)
        if active_only:
            statement = statement.where(Medicine.is_active.is_(True))
        return self.db.scalar(statement)

    def list_all_active(self) -> list[Medicine]:
        statement = (
            select(Medicine)
            .where(Medicine.is_active.is_(True))
            .order_by(Medicine.medicine_name.asc(), Medicine.medicine_id.asc())
        )
        return list(self.db.scalars(statement).all())

    def count(self) -> int:
        statement = (
            select(func.count())
            .select_from(Medicine)
            .where(Medicine.is_active.is_(True))
        )
        return int(self.db.scalar(statement) or 0)

    def list_medicines(
        self, q: str | None, limit: int, offset: int
    ) -> tuple[list[Medicine], int]:
        filters = [Medicine.is_active.is_(True)]
        search_term = q.strip() if q else ""
        if search_term:
            pattern = f"%{search_term.casefold()}%"
            filters.append(
                or_(
                    func.lower(Medicine.medicine_name).like(pattern),
                    func.lower(Medicine.description).like(pattern),
                )
            )

        total_statement = (
            select(func.count()).select_from(Medicine).where(*filters)
        )
        total = int(self.db.scalar(total_statement) or 0)

        statement = (
            select(Medicine)
            .where(*filters)
            .order_by(Medicine.medicine_name.asc(), Medicine.medicine_id.asc())
            .offset(offset)
            .limit(limit)
        )
        items = list(self.db.scalars(statement).all())
        return items, total

    def exact_by_name(self, name: str) -> list[Medicine]:
        normalized = normalize_name(name)
        if not normalized:
            return []

        statement = select(Medicine).where(Medicine.is_active.is_(True))
        candidates = list(self.db.scalars(statement).all())
        matches = [
            medicine
            for medicine in candidates
            if normalize_name(medicine.medicine_name) == normalized
        ]
        return sorted(matches, key=lambda medicine: medicine.medicine_id)
