"""Recover abandoned logs only after maximum call lifetime plus five minutes.

Run after restoring database access or after a process crash. This never creates
orders. A saved order is preserved; an unfinished draft is cancelled.
"""
from datetime import timedelta
from sqlalchemy import select
from app.core.config import get_settings
from app.database.connection import SessionLocal
from app.database.models import CallLog, utc_now
from app.services.telephony.call_manager import CallManager


def main():
    cutoff = utc_now() - timedelta(seconds=get_settings().telephony_max_call_seconds + 300)
    with SessionLocal() as db:
        ids = list(db.scalars(select(CallLog.call_id).where(CallLog.ended_at.is_(None), CallLog.started_at < cutoff)))
    for call_id in ids:
        with SessionLocal() as db:
            CallManager(db).finish(call_id, "stale_call_recovery", "PROCESS_RESTART")
    print(f"Recovered {len(ids)} stale call logs")


if __name__ == "__main__":
    main()
