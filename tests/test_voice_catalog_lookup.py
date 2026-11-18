import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database.models import Medicine
from app.services.voice.types import SynthesizedAudio
from tests.test_voice import create_voice, turn, use_voice_adapters


class FakeTTS:
    def synthesize(self, text: str, *, language: str) -> SynthesizedAudio:
        return SynthesizedAudio(
            data=b"RIFFfake-wav", sample_rate=24000, voice="fake_voice"
        )


@pytest.fixture()
def seed_spoken_catalogue(db_session: Session) -> None:
    db_session.add_all(
        [
            Medicine(
                medicine_id="MED-0002",
                medicine_name="paracet",
                description="FRESH TAB 1*S",
                source_serial_number="22",
                source_row=22,
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
        ]
    )
    db_session.commit()


@pytest.mark.parametrize(
    "spoken",
    ["add two strips of dolo 650", "add two strips of DOLO 650MG"],
)
def test_voice_matches_spoken_dolo_650(
    client: TestClient,
    session_factory,
    seed_spoken_catalogue: None,
    spoken: str,
) -> None:
    use_voice_adapters(session_factory, tts=FakeTTS())
    session = create_voice(client)

    result = turn(client, session["voice_session_id"], spoken)

    assert result["order_summary"]["line_item_count"] == 1
    item = result["order_summary"]["items"][0]
    assert item["medicine_id"] == "MED-00025"
    assert item["medicine_name"] == "DOLO 650"
    assert item["quantity"] == 2
    assert item["unit"] == "STRIP"


def test_voice_matches_spoken_crocin(
    client: TestClient,
    session_factory,
    seed_spoken_catalogue: None,
) -> None:
    use_voice_adapters(session_factory, tts=FakeTTS())
    session = create_voice(client)

    result = turn(
        client, session["voice_session_id"], "add one strip of crocin"
    )

    assert result["order_summary"]["line_item_count"] == 1
    item = result["order_summary"]["items"][0]
    assert item["medicine_id"] == "MED-00026"
    assert item["medicine_name"] == "CROCIN"


def test_generic_paracetamol_requires_brand_choice(
    client: TestClient,
    session_factory,
    seed_spoken_catalogue: None,
) -> None:
    use_voice_adapters(session_factory, tts=FakeTTS())
    session = create_voice(client)

    first = turn(client, session["voice_session_id"], "add paracetamol")

    assert first["order_summary"] is None
    assert {item["medicine_id"] for item in first["candidates"]} == {
        "MED-0002",
        "MED-00025",
        "MED-00026",
    }

    selected = turn(client, session["voice_session_id"], "500 two strips")

    assert selected["order_summary"]["line_item_count"] == 1
    assert selected["order_summary"]["items"][0]["medicine_id"] == "MED-00026"


def test_noisy_wrong_strength_is_not_auto_added(
    client: TestClient,
    session_factory,
    seed_spoken_catalogue: None,
) -> None:
    use_voice_adapters(session_factory, tts=FakeTTS())
    session = create_voice(client)

    result = turn(client, session["voice_session_id"], "COJOT DOLO 625MG")

    assert result["order_summary"] is None
    assert "MED-00025" in {
        item["medicine_id"] for item in result["candidates"]
    }
    assert "DOLO 650" in result["assistant_text"]


def test_invalid_quantity_turn_keeps_call_alive(
    client: TestClient,
    session_factory,
    seed_spoken_catalogue: None,
) -> None:
    use_voice_adapters(session_factory, tts=FakeTTS())
    session = create_voice(client, language="en")

    first = turn(client, session["voice_session_id"], "add dolo 650")
    assert first["order_summary"] is None

    invalid = turn(
        client, session["voice_session_id"], "brufin 200, pundraya"
    )
    assert invalid["order_summary"] is None
    assert "100" in invalid["assistant_text"]

    recovered = turn(client, session["voice_session_id"], "four strips")
    assert recovered["order_summary"]["line_item_count"] == 1
    item = recovered["order_summary"]["items"][0]
    assert item["medicine_id"] == "MED-00025"
    assert item["quantity"] == 4
