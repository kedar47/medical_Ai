from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database.models import (
    Order,
    OrderItem,
    OrderLineItem,
    OrderSession,
)


class OrderRepository:
    def __init__(self, db: Session):
        self.db = db

    def add_session(self, session: OrderSession) -> OrderSession:
        self.db.add(session)
        return session

    def get_session(
        self,
        public_session_id: str,
        *,
        for_update: bool = False,
        with_items: bool = True,
    ) -> OrderSession | None:
        statement = select(OrderSession).where(
            OrderSession.session_id == public_session_id
        )
        if with_items:
            statement = statement.options(
                selectinload(OrderSession.items).selectinload(
                    OrderItem.medicine
                )
            )
        if for_update:
            statement = statement.with_for_update()
        return self.db.scalar(statement)

    def get_item(self, session_db_id: int, item_id: int) -> OrderItem | None:
        statement = select(OrderItem).where(
            OrderItem.id == item_id,
            OrderItem.session_id == session_db_id,
        )
        return self.db.scalar(statement)

    def find_item(
        self, session_db_id: int, medicine_id: str, unit: str
    ) -> OrderItem | None:
        statement = select(OrderItem).where(
            OrderItem.session_id == session_db_id,
            OrderItem.medicine_id == medicine_id,
            OrderItem.unit == unit,
        )
        return self.db.scalar(statement)

    def add_item(self, item: OrderItem) -> OrderItem:
        self.db.add(item)
        return item

    def delete_item(self, item: OrderItem) -> None:
        self.db.delete(item)

    def add_order(self, order: Order) -> Order:
        self.db.add(order)
        return order

    def add_order_line_item(self, line_item: OrderLineItem) -> OrderLineItem:
        self.db.add(line_item)
        return line_item

    def get_order_by_public_id(self, public_order_id: str) -> Order | None:
        statement = (
            select(Order)
            .where(Order.order_id == public_order_id)
            .options(
                selectinload(Order.line_items),
                selectinload(Order.session),
            )
        )
        return self.db.scalar(statement)

    def get_order_by_session_db_id(self, session_db_id: int) -> Order | None:
        statement = (
            select(Order)
            .where(Order.session_id == session_db_id)
            .options(
                selectinload(Order.line_items),
                selectinload(Order.session),
            )
        )
        return self.db.scalar(statement)
