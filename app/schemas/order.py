import re
from datetime import datetime

from pydantic import (
    BaseModel,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

from app.core.enums import OrderStatus, OrderUnit, SessionStatus

PHONE_PATTERN = re.compile(r"^\+[1-9]\d{7,14}$")


class OrderSessionCreate(BaseModel):
    customer_phone: str | None = None
    customer_name: str | None = Field(default=None, max_length=255)

    @field_validator("customer_phone")
    @classmethod
    def validate_phone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        if not PHONE_PATTERN.fullmatch(stripped):
            raise ValueError(
                "customer_phone must use E.164 format, for example +919876543210"
            )
        return stripped

    @field_validator("customer_name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("customer_name must not be blank")
        return stripped


class OrderItemAdd(BaseModel):
    medicine_id: str = Field(min_length=1, max_length=64)
    quantity: StrictInt = Field(gt=0)
    unit: OrderUnit


class OrderItemUpdate(BaseModel):
    quantity: StrictInt | None = Field(default=None, gt=0)
    unit: OrderUnit | None = None

    @model_validator(mode="after")
    def require_change(self) -> "OrderItemUpdate":
        if self.quantity is None and self.unit is None:
            raise ValueError("At least one of quantity or unit is required")
        return self


class OrderItemRead(BaseModel):
    id: int
    medicine_id: str
    medicine_name: str
    description: str
    quantity: int
    unit: OrderUnit
    created_at: datetime
    updated_at: datetime


class OrderSessionRead(BaseModel):
    session_id: str
    customer_phone: str | None
    customer_name: str | None
    status: SessionStatus
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    confirmed_at: datetime | None
    items: list[OrderItemRead]


class OrderSummaryRead(BaseModel):
    session_id: str
    customer_phone: str | None
    customer_name: str | None
    status: SessionStatus
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    confirmed_at: datetime | None
    items: list[OrderItemRead]
    line_item_count: int
    can_confirm: bool
    confirmation_token: str | None
    summary_digest: str | None
    confirmation_issued_at: datetime | None
    message: str


class OrderConfirmationRequest(BaseModel):
    confirmation_token: str = Field(min_length=16, max_length=256)
    confirmation_source: str = Field(default="DEMO_API", min_length=1, max_length=32)
    explicit_confirmation: bool


class OrderLineItemRead(BaseModel):
    id: int
    medicine_id: str
    medicine_name: str
    medicine_description: str
    quantity: int
    unit: OrderUnit


class OrderRead(BaseModel):
    order_id: str
    session_id: str
    customer_phone: str | None
    customer_name: str | None
    status: OrderStatus
    confirmed_at: datetime
    created_at: datetime
    call_reference_id: str | None
    confirmation_source: str | None
    line_items: list[OrderLineItemRead]
    total_line_items: int


class OrderConfirmationResponse(OrderRead):
    idempotent_retry: bool = False
