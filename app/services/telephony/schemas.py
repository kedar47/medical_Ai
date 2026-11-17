from pydantic import BaseModel, Field, field_validator

from app.schemas.order import PHONE_PATTERN


class MockIncoming(BaseModel):
    call_reference: str = Field(pattern=r"^[\w-]{1,128}$")
    customer_phone: str | None = Field(default=None, max_length=32)


class OutboundCallCreate(BaseModel):
    customer_phone: str
    language: str = Field(default="hi", pattern="^(hi|en)$")

    @field_validator("customer_phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        phone = value.strip()
        if not PHONE_PATTERN.fullmatch(phone):
            raise ValueError("customer_phone must use E.164 format, for example +919876543210")
        return phone
