from typing import Literal

from pydantic import BaseModel, Field, field_validator


class MedicineMatchRequest(BaseModel):
    text: str = Field(min_length=1, max_length=255)

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("text must not be blank")
        return stripped


class MedicineMatchCandidate(BaseModel):
    medicine_id: str
    medicine_name: str
    description: str
    match_type: Literal["EXACT_NAME", "ALIAS", "FUZZY_SUGGESTION"]


class MedicineMatchResponse(BaseModel):
    status: Literal["EXACT_MATCH", "CLARIFICATION_REQUIRED", "NOT_FOUND"]
    requested_text: str
    candidates: list[MedicineMatchCandidate]
    message: str
