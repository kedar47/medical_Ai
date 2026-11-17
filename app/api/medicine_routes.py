from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.schemas.medicine import (
    MedicineCountResponse,
    MedicineListResponse,
    MedicineRead,
)
from app.services.medicine_service import MedicineService

router = APIRouter(prefix="/medicines", tags=["medicines"])


@router.get("", response_model=MedicineListResponse)
def list_medicines(
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> MedicineListResponse:
    items, total = MedicineService(db).list_medicines(q=q, limit=limit, offset=offset)
    return MedicineListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/count", response_model=MedicineCountResponse)
def count_medicines(db: Session = Depends(get_db)) -> MedicineCountResponse:
    return MedicineCountResponse(total=MedicineService(db).count_medicines())


@router.get("/lookup/exact", response_model=list[MedicineRead])
def exact_medicine_lookup(
    name: str = Query(..., min_length=1, max_length=255),
    db: Session = Depends(get_db),
) -> list[MedicineRead]:
    return MedicineService(db).exact_lookup(name=name)


@router.get("/{medicine_id}", response_model=MedicineRead)
def get_medicine(
    medicine_id: str, db: Session = Depends(get_db)
) -> MedicineRead:
    medicine = MedicineService(db).get_medicine(medicine_id=medicine_id)
    if medicine is None:
        raise HTTPException(status_code=404, detail="Medicine not found")
    return medicine
