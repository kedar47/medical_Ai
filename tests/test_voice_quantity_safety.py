import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database.models import OrderLineItem
from tests.test_voice import FakeTTS, create_voice, turn, use_voice_adapters


@pytest.fixture(autouse=True)
def voice_adapters(session_factory):
    use_voice_adapters(session_factory, tts=FakeTTS())


@pytest.mark.parametrize(
    "spoken,expected",
    [
        ("add one strip of brufen 400", 1),
        ("add 2 strips of brufen 400", 2),
        ("add teen strips of brufen 400", 3),
        ("add 4 strips of brufen 400", 4),
        ("add paanch strips of brufen 400", 5),
        ("add six strips of brufen 400", 6),
        ("add saat strips of brufen 400", 7),
        ("add 8 strips of brufen 400", 8),
        ("add nau strips of brufen 400", 9),
        ("add das strips of brufen 400", 10),
    ],
)
def test_quantities_one_to_ten(
    client: TestClient,
    seed_catalogue: None,
    spoken: str,
    expected: int,
) -> None:
    session = create_voice(client, language="en")

    result = turn(client, session["voice_session_id"], spoken)

    item = result["order_summary"]["items"][0]
    assert item["medicine_id"] == "MED-00014"
    assert item["quantity"] == expected
    assert item["unit"] == "STRIP"


@pytest.mark.parametrize(
    "spoken",
    [
        "add five strips of brufen 400",
        "add 5 strips of brufen 400",
        "add paanch strips of brufen 400",
        "ब्रूफेन 400 की पांच स्ट्रिप जोड़ो",
    ],
)
def test_five_is_recognized_in_all_supported_forms(
    client: TestClient,
    seed_catalogue: None,
    spoken: str,
) -> None:
    session = create_voice(client, language="en")

    result = turn(client, session["voice_session_id"], spoken)

    item = result["order_summary"]["items"][0]
    assert item["quantity"] == 5
    assert item["unit"] == "STRIP"


def test_ambiguous_initial_quantity_asks_for_repeat(
    client: TestClient,
    seed_catalogue: None,
) -> None:
    session = create_voice(client, language="en")

    result = turn(
        client, session["voice_session_id"], "add four or five strips of brufen 400"
    )

    assert result["order_summary"] is None
    assert "quantity" in result["assistant_text"].lower()


def test_ambiguous_pending_answer_does_not_become_four(
    client: TestClient,
    seed_catalogue: None,
) -> None:
    session = create_voice(client, language="en")
    turn(client, session["voice_session_id"], "add brufen 400")

    ambiguous = turn(client, session["voice_session_id"], "4-8")

    assert ambiguous["order_summary"] is None
    assert "quantity" in ambiguous["assistant_text"].lower()

    recovered = turn(client, session["voice_session_id"], "five strips")

    item = recovered["order_summary"]["items"][0]
    assert item["medicine_id"] == "MED-00014"
    assert item["quantity"] == 5
    assert item["unit"] == "STRIP"


def test_ambiguous_correction_keeps_original_quantity(
    client: TestClient,
    seed_catalogue: None,
) -> None:
    session = create_voice(client, language="en")
    turn(client, session["voice_session_id"], "add two strips of brufen 400")

    ambiguous = turn(
        client,
        session["voice_session_id"],
        "Brufen 400 ki quantity 3 ya 4 strips kar do",
    )

    assert ambiguous["intent"] == "update_item"
    assert ambiguous["order_summary"]["items"][0]["quantity"] == 2
    assert "quantity" in ambiguous["assistant_text"].lower()

    corrected = turn(
        client,
        session["voice_session_id"],
        "Brufen 400 ki quantity five strips kar do",
    )

    assert corrected["order_summary"]["items"][0]["quantity"] == 5


def test_summary_reads_back_name_and_quantity_before_explicit_approval(
    client: TestClient,
    seed_catalogue: None,
    db_session,
) -> None:
    session = create_voice(client, language="en")
    turn(client, session["voice_session_id"], "add five strips of brufen 400")

    review = turn(client, session["voice_session_id"], "confirm")

    assert review["intent"] == "show_summary"
    assert review["order"] is None
    text = review["assistant_text"].lower()
    assert "brufen" in text
    assert "5 strip" in text
    assert "confirm" in text

    confirmed = turn(client, session["voice_session_id"], "confirm")

    assert confirmed["order"] is not None
    db_session.expire_all()
    line_item = db_session.scalar(select(OrderLineItem))
    assert line_item is not None
    assert line_item.medicine_id == "MED-00014"
    assert line_item.quantity == 5
    assert line_item.unit == "STRIP"
