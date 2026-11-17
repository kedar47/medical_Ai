"""Local operator read APIs. Bearer token required; no public admin exposure."""
import hmac
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from app.core.config import get_settings
from app.database.connection import get_db
from app.database.models import CallLog, CallTurn, Medicine, Order, OrderSession, utc_now
from app.services.order_service import OrderService
from app.services.telephony.call_manager import CallManager
from app.services.telephony.schemas import OutboundCallCreate
from app.api.telephony_routes import serialize_call


def operator(request: Request):
    s = get_settings()
    expected = f"Bearer {s.dashboard_token}"
    if not s.dashboard_enabled or not s.dashboard_token:
        raise HTTPException(503, "Local dashboard access is not configured")
    if not hmac.compare_digest(request.headers.get("authorization", "").encode(), expected.encode()):
        raise HTTPException(401, "Operator authentication required")


router = APIRouter(prefix="/dashboard", tags=["local dashboard"], dependencies=[Depends(operator)])
ACTIVE_CALLS = ("RINGING", "CONNECTED", "ACTIVE", "PROCESSING", "CONFIRMING")


def masked(phone):
    return f"•••• {phone[-4:]}" if phone else None


def session_data(row):
    from app.services.voice_service import _as_utc
    return {"session_id": row.session_id, "customer_name": row.customer_name,
            "customer_phone": masked(row.customer_phone),
            "status": "EXPIRED" if row.status == "ACTIVE" and _as_utc(row.expires_at) <= utc_now() else row.status,
            "created_at": row.created_at, "expires_at": row.expires_at, "item_count": len(row.items),
            "voice_session_id": row.voice_session.voice_session_id if row.voice_session else None}


@router.get("/stats")
def stats(db=Depends(get_db)):
    from datetime import timedelta
    cutoff = utc_now() - timedelta(seconds=get_settings().telephony_max_call_seconds)
    def count(model, *filters):
        return db.scalar(select(func.count()).select_from(model).where(*filters))
    return {"medicines": count(Medicine, Medicine.is_active.is_(True)), "orders": count(Order),
            "active_sessions": count(OrderSession, OrderSession.status == "ACTIVE", OrderSession.expires_at > utc_now()),
            "calls": count(CallLog), "active_calls": count(CallLog, CallLog.status.in_(ACTIVE_CALLS), CallLog.ended_at.is_(None), CallLog.started_at > cutoff)}


def page(db, model, statement, limit, offset, serialize):
    total = db.scalar(select(func.count()).select_from(statement.order_by(None).subquery()))
    rows = db.scalars(statement.limit(limit).offset(offset)).all()
    return {"items": [serialize(row) for row in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/sessions")
def sessions(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
             status: str | None = Query(None, max_length=32), db=Depends(get_db)):
    query = select(OrderSession).order_by(OrderSession.created_at.desc(), OrderSession.id.desc())
    if status == "ACTIVE":
        query = query.where(OrderSession.status == status, OrderSession.expires_at > utc_now())
    elif status == "EXPIRED":
        from sqlalchemy import or_, and_
        query = query.where(or_(OrderSession.status == "EXPIRED", and_(OrderSession.status == "ACTIVE", OrderSession.expires_at <= utc_now())))
    elif status:
        query = query.where(OrderSession.status == status)
    return page(db, OrderSession, query, limit, offset, session_data)


@router.get("/orders")
def orders(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0), db=Depends(get_db)):
    def serialize(row):
        data = OrderService(db)._to_order_read(row).model_dump()
        data["customer_phone"] = masked(data["customer_phone"])
        return data
    return page(db, Order, select(Order).order_by(Order.created_at.desc(), Order.id.desc()), limit, offset, serialize)


def call_data(row):
    return {**serialize_call(row), "customer_phone": masked(row.customer_phone)}


@router.get("/calls")
def calls(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
          status: str | None = Query(None, max_length=32), active: bool = False, db=Depends(get_db)):
    query = select(CallLog).order_by(CallLog.started_at.desc(), CallLog.id.desc())
    if active:
        from datetime import timedelta
        query = query.where(CallLog.status.in_(ACTIVE_CALLS), CallLog.ended_at.is_(None),
                            CallLog.started_at > utc_now() - timedelta(seconds=get_settings().telephony_max_call_seconds))
    if status:
        query = query.where(CallLog.status == status)
    return page(db, CallLog, query, limit, offset, call_data)


@router.post("/calls/outbound")
def place_outbound_call(payload: OutboundCallCreate, db=Depends(get_db)):
    return CallManager(db).start_outbound(payload.customer_phone, payload.language)


@router.get("/calls/{call_id}")
def call_detail(call_id: str, db=Depends(get_db)):
    row = db.scalar(select(CallLog).where(CallLog.call_id == call_id))
    if not row:
        raise HTTPException(404, "Call not found")
    result = call_data(row)
    turns = db.scalars(
        select(CallTurn).where(CallTurn.call_id == call_id).order_by(CallTurn.sequence)
    )
    result["transcripts"] = [
        entry
        for turn in turns
        for entry in (
            {
                "sequence": turn.sequence * 2 - 1,
                "speaker": "CALLER",
                "text": turn.transcript,
                "created_at": turn.created_at,
            },
            {
                "sequence": turn.sequence * 2,
                "speaker": "AGENT",
                "text": turn.assistant_text,
                "created_at": turn.created_at,
            },
        )
    ]
    return result
