from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.schemas.order import (
    OrderConfirmationRequest,
    OrderConfirmationResponse,
    OrderItemAdd,
    OrderItemUpdate,
    OrderRead,
    OrderSessionCreate,
    OrderSessionRead,
    OrderSummaryRead,
)
from app.services.order_service import OrderService

router = APIRouter(tags=["orders"])


@router.post(
    "/order-sessions",
    response_model=OrderSessionRead,
    status_code=201,
)
def create_order_session(
    payload: OrderSessionCreate,
    db: Session = Depends(get_db),
) -> OrderSessionRead:
    return OrderService(db).create_session(payload)


@router.get(
    "/order-sessions/{session_id}",
    response_model=OrderSessionRead,
)
def get_order_session(
    session_id: str,
    db: Session = Depends(get_db),
) -> OrderSessionRead:
    return OrderService(db).get_session(session_id)


@router.post(
    "/order-sessions/{session_id}/cancel",
    response_model=OrderSessionRead,
)
def cancel_order_session(
    session_id: str,
    db: Session = Depends(get_db),
) -> OrderSessionRead:
    return OrderService(db).cancel_session(session_id)


@router.post(
    "/order-sessions/{session_id}/items",
    response_model=OrderSessionRead,
    status_code=201,
)
def add_order_item(
    session_id: str,
    payload: OrderItemAdd,
    db: Session = Depends(get_db),
) -> OrderSessionRead:
    return OrderService(db).add_item(session_id, payload)


@router.patch(
    "/order-sessions/{session_id}/items/{item_id}",
    response_model=OrderSessionRead,
)
def update_order_item(
    session_id: str,
    item_id: int,
    payload: OrderItemUpdate,
    db: Session = Depends(get_db),
) -> OrderSessionRead:
    return OrderService(db).update_item(session_id, item_id, payload)


@router.delete(
    "/order-sessions/{session_id}/items/{item_id}",
    response_model=OrderSessionRead,
)
def delete_order_item(
    session_id: str,
    item_id: int,
    db: Session = Depends(get_db),
) -> OrderSessionRead:
    return OrderService(db).delete_item(session_id, item_id)


@router.get(
    "/order-sessions/{session_id}/summary",
    response_model=OrderSummaryRead,
)
def get_order_summary(
    session_id: str,
    db: Session = Depends(get_db),
) -> OrderSummaryRead:
    return OrderService(db).get_summary(session_id)


@router.post(
    "/order-sessions/{session_id}/confirm",
    response_model=OrderConfirmationResponse,
)
def confirm_order(
    session_id: str,
    payload: OrderConfirmationRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> OrderConfirmationResponse:
    order, idempotent_retry = OrderService(db).confirm_order(
        session_id, payload
    )
    response.status_code = 200 if idempotent_retry else 201
    return OrderConfirmationResponse(
        **order.model_dump(), idempotent_retry=idempotent_retry
    )


@router.get("/orders/{order_id}", response_model=OrderRead)
def get_order(
    order_id: str,
    db: Session = Depends(get_db),
) -> OrderRead:
    return OrderService(db).get_order(order_id)
