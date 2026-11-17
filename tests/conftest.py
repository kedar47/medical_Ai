import os
from collections.abc import Generator

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.connection import get_db
from app.database.models import (
    Base,
    CallLog,
    CallTurn,
    Medicine,
    Order,
    OrderItem,
    OrderLineItem,
    OrderSession,
    VoiceSession,
)
from app.main import app

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

if TEST_DATABASE_URL:
    test_engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True, future=True)
else:
    test_engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )

TestingSessionLocal = sessionmaker(
    bind=test_engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    class_=Session,
)


@pytest.fixture(scope="session", autouse=True)
def create_schema() -> Generator[None, None, None]:
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture(autouse=True)
def clean_database() -> Generator[None, None, None]:
    with TestingSessionLocal() as session:
        session.execute(delete(CallTurn))
        session.execute(delete(CallLog))
        session.execute(delete(VoiceSession))
        session.execute(delete(OrderLineItem))
        session.execute(delete(Order))
        session.execute(delete(OrderItem))
        session.execute(delete(OrderSession))
        session.execute(delete(Medicine))
        session.commit()
    yield


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    with TestingSessionLocal() as session:
        yield session


@pytest.fixture()
def session_factory():
    return TestingSessionLocal


@pytest.fixture()
def seed_catalogue(db_session: Session) -> None:
    db_session.add_all(
        [
            Medicine(
                medicine_id="MED-00013",
                medicine_name="BRUFEN",
                description="200MG 15'S",
                source_serial_number="5",
                source_row=13,
            ),
            Medicine(
                medicine_id="MED-00014",
                medicine_name="BRUFEN",
                description="400MG 15'S",
                source_serial_number="6",
                source_row=14,
            ),
            Medicine(
                medicine_id="MED-00015",
                medicine_name="BRUFEN",
                description="600MG 15'S",
                source_serial_number="7",
                source_row=15,
            ),
            Medicine(
                medicine_id="MED-00003",
                medicine_name="CLOMIN TAB",
                description="10'S",
                source_serial_number="1",
                source_row=3,
            ),
            Medicine(
                medicine_id="MED-00001",
                medicine_name="CORFLAM",
                description="10'S",
                source_serial_number="9",
                source_row=9,
            ),
            Medicine(
                medicine_id="MED-00002",
                medicine_name="CORFLAM",
                description="20'S",
                source_serial_number="10",
                source_row=10,
            ),
            Medicine(
                medicine_id="MED-99999",
                medicine_name="OLD MEDICINE",
                description="INACTIVE",
                source_serial_number="99",
                source_row=99,
                is_active=False,
            ),
        ]
    )
    db_session.commit()


def override_get_db() -> Generator[Session, None, None]:
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    app.dependency_overrides[get_db] = override_get_db
    test_client = TestClient(app)
    try:
        yield test_client
    finally:
        test_client.close()
        app.dependency_overrides.clear()
