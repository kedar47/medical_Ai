from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database.models import (
    Order,
    OrderItem,
    OrderSession,
    VoiceSession,
)


class VoiceRepository:
    def __init__(self, db: Session):
        self.db = db

    def add_session(self, voice_session: VoiceSession) -> VoiceSession:
        self.db.add(voice_session)
        return voice_session

    def get_session(
        self,
        public_voice_session_id: str,
        *,
        for_update: bool = False,
    ) -> VoiceSession | None:
        statement = (
            select(VoiceSession)
            .where(VoiceSession.voice_session_id == public_voice_session_id)
            .options(
                selectinload(VoiceSession.order_session)
                .selectinload(OrderSession.items)
                .selectinload(OrderItem.medicine),
                selectinload(VoiceSession.order_session).selectinload(
                    OrderSession.order
                ).selectinload(Order.line_items),
            )
        )
        if for_update:
            statement = statement.with_for_update()
        return self.db.scalar(statement)

    def get_by_order_session_db_id(
        self, order_session_db_id: int
    ) -> VoiceSession | None:
        statement = select(VoiceSession).where(
            VoiceSession.order_session_id == order_session_db_id
        )
        return self.db.scalar(statement)
