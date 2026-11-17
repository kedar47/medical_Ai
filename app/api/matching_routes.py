from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.schemas.matching import MedicineMatchRequest, MedicineMatchResponse
from app.services.matching_service import MedicineMatchingService

router = APIRouter(prefix="/medicine-matching", tags=["medicine matching"])


@router.post("/search", response_model=MedicineMatchResponse)
def search_medicine(
    payload: MedicineMatchRequest,
    db: Session = Depends(get_db),
) -> MedicineMatchResponse:
    return MedicineMatchingService(db).search(payload.text)
