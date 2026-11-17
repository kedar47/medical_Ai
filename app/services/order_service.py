import hashlib
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.enums import OrderStatus, OrderUnit, SessionStatus
from app.core.exceptions import OrderError
from app.database.models import (
    Order,
    OrderItem,
    OrderLineItem,
    OrderSession,
    utc_now,
)
from app.repositories.medicine_repository import MedicineRepository
from app.repositories.order_repository import OrderRepository
from app.schemas.order import (
    OrderConfirmationRequest,
    OrderItemAdd,
    OrderItemRead,
    OrderItemUpdate,
    OrderLineItemRead,
    OrderRead,
    OrderSessionCreate,
    OrderSessionRead,
    OrderSummaryRead,
)

ACTIVE_STATUS = SessionStatus.ACTIVE.value
CONFIRMED_STATUS = SessionStatus.CONFIRMED.value
CANCELLED_STATUS = SessionStatus.CANCELLED.value
EXPIRED_STATUS = SessionStatus.EXPIRED.value
ORDER_STATUS = OrderStatus.PENDING_PHARMACY_REVIEW.value
VALID_UNITS = {unit.value for unit in OrderUnit}


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _unit_value(unit: OrderUnit | str) -> str:
    if isinstance(unit, OrderUnit):
        return unit.value
    return OrderUnit(unit).value


class OrderService:
    def __init__(self, db: Session):
        self.db = db
        self.settings = get_settings()
        self.repository = OrderRepository(db)
        self.medicine_repository = MedicineRepository(db)

    def create_session(self, payload: OrderSessionCreate) -> OrderSessionRead:
        now = utc_now()
        session = OrderSession(
            session_id=f"session_{uuid.uuid4().hex}",
            customer_phone=payload.customer_phone,
            customer_name=payload.customer_name,
            status=ACTIVE_STATUS,
            expires_at=now
            + timedelta(minutes=self.settings.order_session_ttl_minutes),
        )
        self.repository.add_session(session)
        self.db.commit()
        return self._serialize_session(session)

    def get_session(self, public_session_id: str) -> OrderSessionRead:
        session = self.repository.get_session(public_session_id)
        if session is None:
            raise OrderError(404, "Order session not found.")
        self._expire_if_needed(session)
        self.db.commit()
        return self._serialize_session(session)

    def cancel_session(self, public_session_id: str) -> OrderSessionRead:
        session = self.repository.get_session(
            public_session_id, for_update=True
        )
        if session is None:
            raise OrderError(404, "Order session not found.")
        if self._expire_if_needed(session):
            self.db.commit()
            raise OrderError(409, "Order session has expired.")
        if session.status == CANCELLED_STATUS:
            self.db.commit()
            return self._serialize_session(session)
        if session.status != ACTIVE_STATUS:
            self.db.rollback()
            raise OrderError(
                409,
                f"Order session cannot be cancelled while {session.status}.",
            )
        session.status = CANCELLED_STATUS
        self._clear_confirmation(session)
        self.db.commit()
        return self._serialize_session(session)

    def add_item(
        self, public_session_id: str, payload: OrderItemAdd
    ) -> OrderSessionRead:
        session = self.repository.get_session(
            public_session_id, for_update=True
        )
        if session is None:
            raise OrderError(404, "Order session not found.")
        self._require_active(session)

        medicine = self.medicine_repository.get_by_medicine_id(
            payload.medicine_id
        )
        if medicine is None or not medicine.is_active:
            self.db.rollback()
            raise OrderError(404, "Medicine not found in approved catalogue.")

        unit = _unit_value(payload.unit)
        self._validate_quantity(payload.quantity)
        existing = self.repository.find_item(
            session.id, medicine.medicine_id, unit
        )
        if existing is not None:
            combined_quantity = existing.quantity + payload.quantity
            self._validate_quantity(combined_quantity)
            existing.quantity = combined_quantity
        else:
            item = OrderItem(
                medicine=medicine,
                medicine_id=medicine.medicine_id,
                quantity=payload.quantity,
                unit=unit,
            )
            session.items.append(item)
            self.repository.add_item(item)

        self._clear_confirmation(session)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise OrderError(
                409, "Order item conflicted with another request; retry."
            ) from exc
        except SQLAlchemyError:
            self.db.rollback()
            raise

        return self._serialize_session(session)

    def update_item(
        self,
        public_session_id: str,
        item_id: int,
        payload: OrderItemUpdate,
    ) -> OrderSessionRead:
        session = self.repository.get_session(
            public_session_id, for_update=True
        )
        if session is None:
            raise OrderError(404, "Order session not found.")
        self._require_active(session)

        item = self.repository.get_item(session.id, item_id)
        if item is None:
            self.db.rollback()
            raise OrderError(404, "Order item not found.")

        quantity = (
            payload.quantity if payload.quantity is not None else item.quantity
        )
        unit = (
            _unit_value(payload.unit)
            if payload.unit is not None
            else item.unit
        )
        self._validate_quantity(quantity)
        if unit not in VALID_UNITS:
            self.db.rollback()
            raise OrderError(400, "Invalid order unit.")

        if quantity == item.quantity and unit == item.unit:
            self.db.commit()
            return self._serialize_session(session)

        duplicate = self.repository.find_item(
            session.id, item.medicine_id, unit
        )
        if duplicate is not None and duplicate.id != item.id:
            self.db.rollback()
            raise OrderError(
                409,
                "The session already contains this medicine in the selected "
                "unit. Update or remove that item instead.",
            )

        item.quantity = quantity
        item.unit = unit
        self._clear_confirmation(session)
        self.db.commit()
        return self._serialize_session(session)

    def delete_item(
        self, public_session_id: str, item_id: int
    ) -> OrderSessionRead:
        session = self.repository.get_session(
            public_session_id, for_update=True
        )
        if session is None:
            raise OrderError(404, "Order session not found.")
        self._require_active(session)

        item = self.repository.get_item(session.id, item_id)
        if item is None:
            self.db.rollback()
            raise OrderError(404, "Order item not found.")

        session.items.remove(item)
        self.repository.delete_item(item)
        self._clear_confirmation(session)
        self.db.commit()
        return self._serialize_session(session)

    def get_summary(self, public_session_id: str) -> OrderSummaryRead:
        session = self.repository.get_session(
            public_session_id, for_update=True
        )
        if session is None:
            raise OrderError(404, "Order session not found.")

        expired = self._expire_if_needed(session)
        confirmation_token = None
        summary_digest = None
        can_confirm = (
            not expired
            and session.status == ACTIVE_STATUS
            and len(session.items) > 0
        )

        if can_confirm:
            confirmation_token = secrets.token_urlsafe(32)
            summary_digest = self._summary_digest(session)
            session.confirmation_token_hash = _token_hash(
                confirmation_token
            )
            session.confirmation_digest = summary_digest
            session.confirmation_issued_at = utc_now()

        self.db.commit()
        return self._serialize_summary(
            session,
            can_confirm=can_confirm,
            confirmation_token=confirmation_token,
            summary_digest=summary_digest,
        )

    def confirm_order(
        self,
        public_session_id: str,
        payload: OrderConfirmationRequest,
    ) -> tuple[OrderRead, bool]:
        if not payload.explicit_confirmation:
            raise OrderError(
                400,
                "explicit_confirmation must be true to place an order.",
            )

        token = payload.confirmation_token.strip()
        source = payload.confirmation_source.strip()
        if not token or not source:
            raise OrderError(
                400,
                "confirmation_token and confirmation_source are required.",
            )

        session = self.repository.get_session(
            public_session_id, for_update=True
        )
        if session is None:
            raise OrderError(404, "Order session not found.")

        if self._expire_if_needed(session):
            self.db.commit()
            raise OrderError(409, "Order session has expired.")

        if session.status == CONFIRMED_STATUS:
            existing = self.repository.get_order_by_session_db_id(
                session.id
            )
            if existing is None:
                self.db.rollback()
                raise OrderError(
                    409, "Order session is confirmed but has no order."
                )
            if (
                self._token_matches(session, token)
                and self._digest_matches(session)
            ):
                self.db.commit()
                return self._to_order_read(existing), True
            self.db.rollback()
            raise OrderError(
                409, "Order session is already confirmed."
            )

        if session.status != ACTIVE_STATUS:
            self.db.rollback()
            raise OrderError(
                409,
                f"Order session cannot be confirmed while {session.status}.",
            )

        if not session.items:
            self.db.rollback()
            raise OrderError(400, "Order draft is empty.")

        if not self._token_matches(session, token):
            self.db.rollback()
            raise OrderError(
                409,
                "Confirmation token is invalid or stale. Generate a new "
                "summary before confirming.",
            )
        if not self._digest_matches(session):
            self.db.rollback()
            raise OrderError(
                409,
                "Order draft changed after the summary was generated. "
                "Generate a new summary before confirming.",
            )

        for item in session.items:
            if item.medicine is None or not item.medicine.is_active:
                self.db.rollback()
                raise OrderError(
                    409,
                    "A medicine in the order is no longer available in the "
                    "approved catalogue.",
                )
            self._validate_quantity(item.quantity)
            if item.unit not in VALID_UNITS:
                self.db.rollback()
                raise OrderError(400, "Invalid order unit.")

        now = utc_now()
        order = Order(
            order_id=f"order_{uuid.uuid4().hex}",
            session=session,
            customer_phone=session.customer_phone,
            customer_name=session.customer_name,
            status=ORDER_STATUS,
            confirmed_at=now,
            confirmation_source=source,
        )
        for item in session.items:
            order.line_items.append(
                OrderLineItem(
                    medicine_id=item.medicine_id,
                    medicine_name=item.medicine.medicine_name,
                    medicine_description=item.medicine.description,
                    quantity=item.quantity,
                    unit=item.unit,
                )
            )

        session.status = CONFIRMED_STATUS
        session.confirmed_at = now
        self.repository.add_order(order)

        session_db_id = session.id
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            current_session = self.repository.get_session(
                public_session_id
            )
            existing = self.repository.get_order_by_session_db_id(
                session_db_id
            )
            if (
                current_session is not None
                and existing is not None
                and current_session.status == CONFIRMED_STATUS
                and self._token_matches(current_session, token)
                and self._digest_matches(current_session)
            ):
                return self._to_order_read(existing), True
            raise OrderError(
                409, "Order was already confirmed by another request."
            ) from exc
        except SQLAlchemyError:
            self.db.rollback()
            raise

        return self._to_order_read(order), False

    def get_order(self, public_order_id: str) -> OrderRead:
        order = self.repository.get_order_by_public_id(public_order_id)
        if order is None:
            raise OrderError(404, "Order not found.")
        return self._to_order_read(order)

    def _expire_if_needed(self, session: OrderSession) -> bool:
        if (
            session.status == ACTIVE_STATUS
            and _as_utc(session.expires_at) <= utc_now()
        ):
            session.status = EXPIRED_STATUS
            self._clear_confirmation(session)
            return True
        return False

    def _require_active(self, session: OrderSession) -> None:
        if self._expire_if_needed(session):
            self.db.commit()
            raise OrderError(409, "Order session has expired.")
        if session.status != ACTIVE_STATUS:
            self.db.rollback()
            raise OrderError(
                409, f"Order session is not active ({session.status})."
            )

    def _validate_quantity(self, quantity: int) -> None:
        if quantity <= 0:
            raise OrderError(400, "Quantity must be greater than zero.")
        if quantity > self.settings.max_order_item_quantity:
            raise OrderError(
                400,
                "Quantity cannot exceed "
                f"{self.settings.max_order_item_quantity} per line item.",
            )

    def _clear_confirmation(self, session: OrderSession) -> None:
        session.confirmation_token_hash = None
        session.confirmation_digest = None
        session.confirmation_issued_at = None

    def _token_matches(self, session: OrderSession, token: str) -> bool:
        if not session.confirmation_token_hash:
            return False
        return secrets.compare_digest(
            session.confirmation_token_hash, _token_hash(token)
        )

    def _digest_matches(self, session: OrderSession) -> bool:
        if not session.confirmation_digest:
            return False
        return secrets.compare_digest(
            session.confirmation_digest, self._summary_digest(session)
        )

    def _summary_digest(self, session: OrderSession) -> str:
        payload = {
            "session_id": session.session_id,
            "items": [
                {
                    "item_id": item.id,
                    "medicine_id": item.medicine_id,
                    "medicine_name": (
                        item.medicine.medicine_name
                        if item.medicine is not None
                        else ""
                    ),
                    "medicine_description": (
                        item.medicine.description
                        if item.medicine is not None
                        else ""
                    ),
                    "quantity": item.quantity,
                    "unit": item.unit,
                }
                for item in sorted(session.items, key=lambda item: item.id or 0)
            ],
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _serialize_item(self, item: OrderItem) -> OrderItemRead:
        medicine_name = (
            item.medicine.medicine_name if item.medicine is not None else ""
        )
        description = (
            item.medicine.description if item.medicine is not None else ""
        )
        return OrderItemRead(
            id=item.id,
            medicine_id=item.medicine_id,
            medicine_name=medicine_name,
            description=description,
            quantity=item.quantity,
            unit=item.unit,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )

    def _serialize_session(
        self, session: OrderSession
    ) -> OrderSessionRead:
        return OrderSessionRead(
            session_id=session.session_id,
            customer_phone=session.customer_phone,
            customer_name=session.customer_name,
            status=session.status,
            created_at=session.created_at,
            updated_at=session.updated_at,
            expires_at=session.expires_at,
            confirmed_at=session.confirmed_at,
            items=[
                self._serialize_item(item)
                for item in sorted(
                    session.items, key=lambda item: item.id or 0
                )
            ],
        )

    def _serialize_summary(
        self,
        session: OrderSession,
        *,
        can_confirm: bool,
        confirmation_token: str | None,
        summary_digest: str | None,
    ) -> OrderSummaryRead:
        return OrderSummaryRead(
            session_id=session.session_id,
            customer_phone=session.customer_phone,
            customer_name=session.customer_name,
            status=session.status,
            created_at=session.created_at,
            updated_at=session.updated_at,
            expires_at=session.expires_at,
            confirmed_at=session.confirmed_at,
            items=[
                self._serialize_item(item)
                for item in sorted(
                    session.items, key=lambda item: item.id or 0
                )
            ],
            line_item_count=len(session.items),
            can_confirm=can_confirm,
            confirmation_token=confirmation_token,
            summary_digest=summary_digest,
            confirmation_issued_at=session.confirmation_issued_at,
            message=self._summary_message(session, can_confirm),
        )

    def _summary_message(
        self, session: OrderSession, can_confirm: bool
    ) -> str:
        if can_confirm:
            return (
                "Order summary is ready. Confirm only after explicit customer "
                "approval."
            )
        if session.status == ACTIVE_STATUS:
            return "Order draft is empty; add at least one medicine."
        if session.status == CONFIRMED_STATUS:
            return "Order session is already confirmed."
        if session.status == CANCELLED_STATUS:
            return "Order session is cancelled and cannot be confirmed."
        return "Order session has expired and cannot be confirmed."

    def _to_order_read(self, order: Order) -> OrderRead:
        return OrderRead(
            order_id=order.order_id,
            session_id=order.session.session_id,
            customer_phone=order.customer_phone,
            customer_name=order.customer_name,
            status=order.status,
            confirmed_at=order.confirmed_at,
            created_at=order.created_at,
            call_reference_id=order.call_reference_id,
            confirmation_source=order.confirmation_source,
            line_items=[
                OrderLineItemRead(
                    id=line_item.id,
                    medicine_id=line_item.medicine_id,
                    medicine_name=line_item.medicine_name,
                    medicine_description=line_item.medicine_description,
                    quantity=line_item.quantity,
                    unit=line_item.unit,
                )
                for line_item in sorted(
                    order.line_items, key=lambda line_item: line_item.id or 0
                )
            ],
            total_line_items=len(order.line_items),
        )
