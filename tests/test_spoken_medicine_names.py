from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database.models import Medicine
from app.services.medicine_attributes import spoken_name
from tests.test_voice import FakeTTS, create_voice, turn, use_voice_adapters


def _seed_catalogue(db_session: Session) -> None:
    db_session.add_all(
        [
            Medicine(
                medicine_id="MED-00013",
                medicine_name="BRUFEN",
                description="200MG TAB 15'S",
                source_serial_number="5",
                source_row=13,
            ),
            Medicine(
                medicine_id="MED-00014",
                medicine_name="BRUFEN",
                description="400MG TAB 15'S",
                source_serial_number="6",
                source_row=14,
            ),
            Medicine(
                medicine_id="MED-00025",
                medicine_name="DOLO 650",
                description="TAB 650MG 15'S",
                source_serial_number="17",
                source_row=25,
            ),
            Medicine(
                medicine_id="MED-00026",
                medicine_name="CROCIN",
                description="TAB 500MG 15'S",
                source_serial_number="18",
                source_row=26,
            ),
            Medicine(
                medicine_id="MED-00011",
                medicine_name="ARACHITOL",
                description="INJ 6L BOX",
                source_serial_number="3",
                source_row=11,
            ),
        ]
    )
    db_session.commit()


def _session(client: TestClient, session_factory) -> str:
    use_voice_adapters(session_factory, tts=FakeTTS())
    return create_voice(client)["voice_session_id"]


def test_spoken_name_removes_packaging_and_internal_ids() -> None:
    assert spoken_name("BRUFEN", "200MG TAB 15'S") == "BRUFEN 200MG"
    assert spoken_name("DOLO 650", "TAB 650MG 15'S") == "DOLO 650MG"
    assert spoken_name("ARACHITOL", "INJ 6L BOX") == "ARACHITOL INJ"
    assert spoken_name("MED-00014", "BRUFEN 400MG 15'S") == "BRUFEN 400MG"


def test_spoken_name_keeps_meaningful_variants() -> None:
    assert spoken_name("BRUFEN", "200MG 15'S") == "BRUFEN 200MG"
    assert spoken_name("BRUFEN", "400MG 15'S") == "BRUFEN 400MG"
    assert "INJ" in spoken_name("ARACHITOL", "INJ 6L BOX")
    assert "MIXED FRUIT" in spoken_name(
        "CREMAFFIN", "MIXED FRUIT SYP 225ML"
    )


def test_spoken_name_distinguishes_colliding_short_names() -> None:
    first = ("CREMAFFIN", "MINT/PLAIN SYP 225ML")
    second = ("CREMAFFIN", "MIXED FRUIT SYP 225ML")
    first_label = spoken_name(*first, alternatives=[first, second])
    second_label = spoken_name(*second, alternatives=[first, second])
    assert first_label != second_label
    assert "MINT" in first_label
    assert "MIXED FRUIT" in second_label


def test_voice_add_quantity_prompt_summary_and_confirmation_do_not_speak_packaging(
    client: TestClient,
    session_factory,
    db_session: Session,
) -> None:
    _seed_catalogue(db_session)
    voice_session_id = _session(client, session_factory)

    prompted = turn(client, voice_session_id, "add brufen 200")
    assert "BRUFEN 200MG" in prompted["assistant_text"]
    assert "15'S" not in prompted["assistant_text"]

    added = turn(client, voice_session_id, "two strips")
    assert "BRUFEN 200MG" in added["assistant_text"]
    assert "15'S" not in added["assistant_text"]
    assert "MED-00013" not in added["assistant_text"]

    summary = turn(client, voice_session_id, "show summary")
    assert "BRUFEN 200MG" in summary["assistant_text"]
    assert "15'S" not in summary["assistant_text"]
    assert "TAB" not in summary["assistant_text"]

    reviewed = turn(client, voice_session_id, "yes confirm")
    confirmed = turn(client, voice_session_id, "yes confirm")
    assert confirmed["order"] is not None
    assert "15'S" not in reviewed["assistant_text"]
    assert "15'S" not in confirmed["assistant_text"]


def test_clarification_and_remove_do_not_speak_packaging(
    client: TestClient,
    session_factory,
    db_session: Session,
) -> None:
    _seed_catalogue(db_session)
    voice_session_id = _session(client, session_factory)

    clarification = turn(client, voice_session_id, "add paracetamol")
    assert clarification["candidates"]
    assert "DOLO 650MG" in clarification["assistant_text"]
    assert "15'S" not in clarification["assistant_text"]
    assert "MED-" not in clarification["assistant_text"]

    added = turn(client, voice_session_id, "dolo 650 two strips")
    removed = turn(client, voice_session_id, "remove dolo 650")
    assert removed["order_summary"]["line_item_count"] == 0
    assert "DOLO 650MG" in removed["assistant_text"]
    assert "15'S" not in removed["assistant_text"]
    assert "MED-" not in removed["assistant_text"]
    assert added["order_summary"]["line_item_count"] == 1
