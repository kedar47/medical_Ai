from datetime import datetime

from pydantic import BaseModel, ConfigDict


class MedicineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    medicine_id: str
    medicine_name: str
    description: str
    source_serial_number: str | None = None
    source_row: int | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class MedicineListResponse(BaseModel):
    items: list[MedicineRead]
    total: int
    limit: int
    offset: int


class MedicineCountResponse(BaseModel):
    total: int


class HealthResponse(BaseModel):
    status: str
    database: str
