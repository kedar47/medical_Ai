from sqlalchemy.orm import Session

from app.database.models import Medicine
from app.repositories.medicine_repository import MedicineRepository


class MedicineService:
    def __init__(self, db: Session):
        self.repository = MedicineRepository(db)

    def get_medicine(self, medicine_id: str) -> Medicine | None:
        return self.repository.get_by_medicine_id(medicine_id)

    def list_medicines(
        self, q: str | None, limit: int, offset: int
    ) -> tuple[list[Medicine], int]:
        return self.repository.list_medicines(q=q, limit=limit, offset=offset)

    def count_medicines(self) -> int:
        return self.repository.count()

    def exact_lookup(self, name: str) -> list[Medicine]:
        return self.repository.exact_by_name(name)
