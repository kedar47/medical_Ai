import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database.models import Medicine
from app.services.medicine_attributes import NOT_FOUND_TEXT
from tests.test_voice import FakeTTS, create_voice, turn, use_voice_adapters


@pytest.fixture()
def seed_safety_catalogue(db_session: Session) -> None:
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
                medicine_id="MED-00025",
                medicine_name="DOLO 650",
                description="TAB 650MG 15'S",
                source_serial_number="25",
                source_row=25,
            ),
            Medicine(
                medicine_id="MED-00026",
                medicine_name="CROCIN",
                description="TAB 500MG 15'S",
                source_serial_number="26",
                source_row=26,
            ),
            Medicine(
                medicine_id="MED-00027",
                medicine_name="PARACET",
                description="TAB 500MG 10'S",
                source_serial_number="27",
                source_row=27,
            ),
        ]
    )
    db_session.commit()


def _session(client: TestClient, session_factory) -> str:
    use_voice_adapters(session_factory, tts=FakeTTS())
    return create_voice(client)["voice_session_id"]


def test_brufen_200_matches_exact_catalogue_variant(
    client: TestClient,
    session_factory,
    seed_safety_catalogue: None,
) -> None:
    voice_session_id = _session(client, session_factory)

    result = turn(client, voice_session_id, "add brufen 200 two strips")

    assert result["order_summary"]["line_item_count"] == 1
    item = result["order_summary"]["items"][0]
    assert item["medicine_id"] == "MED-00013"
    assert item["medicine_name"] == "BRUFEN"
    assert "200MG" in item["description"]
    assert item["quantity"] == 2


def test_brufen_200_after_dolo_quantity_pending_does_not_add_dolo(
    client: TestClient,
    session_factory,
    seed_safety_catalogue: None,
) -> None:
    voice_session_id = _session(client, session_factory)
    turn(client, voice_session_id, "add dolo 650")

    switched = turn(client, voice_session_id, "brufen 200")

    assert switched["order_summary"] is None
    assert "DOLO 650" not in switched["assistant_text"]

    added = turn(client, voice_session_id, "two strips")

    assert added["order_summary"]["line_item_count"] == 1
    item = added["order_summary"]["items"][0]
    assert item["medicine_id"] == "MED-00013"
    assert "200MG" in item["description"]


def test_brufen_200_with_quantity_after_dolo_pending_switches(
    client: TestClient,
    session_factory,
    seed_safety_catalogue: None,
) -> None:
    voice_session_id = _session(client, session_factory)
    turn(client, voice_session_id, "add dolo 650")

    switched = turn(client, voice_session_id, "brufen 200 two strips")

    assert switched["order_summary"]["line_item_count"] == 1
    item = switched["order_summary"]["items"][0]
    assert item["medicine_id"] == "MED-00013"
    assert "200MG" in item["description"]
    assert "DOLO 650" not in switched["assistant_text"]


def test_brufen_650_does_not_select_dolo_from_candidate_pending(
    client: TestClient,
    session_factory,
    seed_safety_catalogue: None,
) -> None:
    voice_session_id = _session(client, session_factory)
    pending = turn(client, voice_session_id, "add paracetamol")
    assert {item["medicine_id"] for item in pending["candidates"]} >= {
        "MED-00025",
        "MED-00026",
        "MED-00027",
    }

    result = turn(client, voice_session_id, "brufen 650 two strips")

    assert result["order_summary"] is None
    assert "DOLO 650" not in result["assistant_text"]


def test_wrong_strength_for_exact_name_is_rejected(
    client: TestClient,
    session_factory,
    seed_safety_catalogue: None,
) -> None:
    voice_session_id = _session(client, session_factory)

    result = turn(client, voice_session_id, "add dolo 200 two strips")

    # Rejected: nothing added and no yes/no offer of the other dose; the
    # caller hears which strength the list has.
    assert result["order_summary"] is None
    assert result["assistant_text"] == "Strength 200 of DOLO 650 is not in our list. We have DOLO 650MG."
    follow_up = turn(client, voice_session_id, "yes")
    assert not (follow_up.get("order_summary") or {}).get("items")


def test_hindi_wrong_strength_uses_exact_not_found_message(
    client: TestClient,
    session_factory,
    seed_safety_catalogue: None,
) -> None:
    voice_session_id = _session(client, session_factory)

    result = turn(client, voice_session_id, "डोलो 200 दो पट्टी")

    assert result["order_summary"] is None
    assert result["language"] == "hi"
    assert result["assistant_text"] == "DOLO 650 की 200 strength हमारी लिस्ट में नहीं है। लिस्ट में DOLO 650MG है।"


def test_unknown_medicine_does_not_reuse_pending_dolo(
    client: TestClient,
    session_factory,
    seed_safety_catalogue: None,
) -> None:
    voice_session_id = _session(client, session_factory)
    turn(client, voice_session_id, "add dolo 650")

    result = turn(client, voice_session_id, "add zzz medicine two strips")

    assert result["order_summary"] is None
    assert result["assistant_text"] == NOT_FOUND_TEXT[result["language"]]


def test_ambiguous_brufen_strengths_require_choice(
    client: TestClient,
    session_factory,
    seed_safety_catalogue: None,
) -> None:
    voice_session_id = _session(client, session_factory)

    result = turn(client, voice_session_id, "add brufen 200 or 400 two strips")

    assert result["order_summary"] is None
    assert {item["medicine_id"] for item in result["candidates"]} >= {
        "MED-00013",
        "MED-00014",
    }
