"""The live call of 2026-11-23 10:38 (call_7bfb0fa5...), against the live
26-record catalogue: loops on clarification and "पहले लंबित बदलाव पूरा करें"."""
from pathlib import Path

import pytest

from app.database.models import Order
from app.services.import_service import import_medicines_from_csv
from app.services.voice.intent import extract_quantity_unit
from app.schemas.order import OrderUnit

CSV = Path(__file__).resolve().parents[1] / "data" / "demo_medicines.csv"
LIST = ("मेरा नाम हर्ष है, मुझे एनाफोर्टन सिरप 3 बोतल चाहिए, आर्चीटोल टैबलेट 5 स्ट्रिप चाहिए, "
        "ब्रोफेन टैबलेट 5 स्ट्रिप चाहिए, टेरोम 4 पत्ते चाहिए, फैक्टरजन 5 पत्ते चाहिए।")


@pytest.fixture
def voice(client, db_session):
    import_medicines_from_csv(db_session, CSV)
    return client.post("/voice/sessions", json={"language": "hi"}).json()


def say(client, voice, text):
    response = client.post(f"/voice/sessions/{voice['voice_session_id']}/turns",
                           json={"text": text, "speak": False})
    assert response.status_code == 200, response.text
    return response.json()


def cart(result):
    return [(i["medicine_id"], i["quantity"], i["unit"])
            for i in (result.get("order_summary") or {}).get("items", [])]


def test_unit_after_the_number_counts():
    assert extract_quantity_unit("आर्चीटोल टैबलेट 5 स्ट्रिप चाहिए")[:2] == (5, OrderUnit.STRIP)


def test_the_live_call_completes(client, voice, db_session):
    first = say(client, voice, LIST)
    assert "ANAFORTAN SYRUP 30ML की 3 बोतल जोड़ दी" in first["assistant_text"]
    assert first["assistant_text"].endswith("ARACHITOL INJ चाहिए या NANO SUGAR FREE?")
    assert "medicine ID" not in first["assistant_text"]
    assert "NANO SUGAR FREE" in say(client, voice, "हा.")["assistant_text"]
    boxed = say(client, voice, "नैनो शुगर फ्री")["assistant_text"]
    assert boxed == "ARACHITOL NANO SUGAR FREE डिब्बे में आती है, स्ट्रिप में नहीं। कितने डिब्बे चाहिए?"
    assert "BRUFEN 200MG चाहिए, 400MG या 600MG?" in say(client, voice, "पाँच डिब्बे")["assistant_text"]
    assert "CEROM 500" in say(client, voice, "400")["assistant_text"]
    added = say(client, voice, "हाँ")
    assert "फैक्टरजन हमारी लिस्ट में नहीं मिली" in added["assistant_text"]
    assert sorted(cart(added)) == sorted([("MED-00009", 3, "BOTTLE"), ("MED-00012", 5, "BOX"),
                                          ("MED-00014", 5, "STRIP"), ("MED-00016", 4, "STRIP")])
    readback = say(client, voice, "ऑर्डर कन्फर्म कर दो।")
    assert readback["status"] == "ACTIVE" and "सही हो तो कन्फर्म कहें" in readback["assistant_text"]
    assert say(client, voice, "कन्फर्म")["status"] == "CONFIRMED"
    order = db_session.query(Order).one()
    assert order.customer_name == "हर्ष"


def test_repeated_unclear_answer_says_what_is_pending(client, voice):
    say(client, voice, "आर्चीटोल दो पत्ते")
    say(client, voice, "हा.")
    again = say(client, voice, "हा.")["assistant_text"]
    assert "ARACHITOL अभी तय नहीं हुई" in again and "'नहीं'" in again
    assert "नहीं जोड़ी" in say(client, voice, "नहीं")["assistant_text"]


def test_failed_correction_does_not_block_confirm(client, voice):
    say(client, voice, "डोलो 650 दो पत्ते")
    say(client, voice, "नहीं, चार चाहिए।")
    answers = [say(client, voice, "ऑर्डर कन्फर्म कर दो।")["assistant_text"] for _ in range(2)]
    assert all("लंबित" not in a for a in answers)
    assert "सही हो तो कन्फर्म कहें" in answers[0]
    assert say(client, voice, "कन्फर्म")["status"] == "CONFIRMED"


def test_confirm_with_open_question_names_the_dropped_item(client, voice):
    say(client, voice, "डोलो 650 दो पत्ते, ब्रूफेन दो पत्ते")
    assert "BRUFEN" in say(client, voice, "हाँ")["assistant_text"]  # question repeated, not confirmed
    readback = say(client, voice, "ऑर्डर कन्फर्म कर दो")
    assert "BRUFEN तय नहीं हुई, इसलिए नहीं जोड़ी" in readback["assistant_text"]
    assert readback["status"] == "ACTIVE"


def test_yes_to_how_many_repeats_the_question(client, voice):
    say(client, voice, "ब्रूफेन 400")
    assert say(client, voice, "हाँ")["assistant_text"] == "BRUFEN 400MG की कितनी स्ट्रिप चाहिए?"


def test_mixed_hindi_english_list(client, voice):
    result = say(client, voice, "Dolo 650 ke do patte aur Colospa ek strip")
    assert cart(result) == [("MED-00025", 2, "STRIP"), ("MED-00017", 1, "STRIP")]


def test_no_inside_a_list_skips_only_that_item(client, voice):
    say(client, voice, LIST)
    say(client, voice, "नैनो शुगर फ्री")
    say(client, voice, "पाँच डिब्बे")
    say(client, voice, "400")
    skipped = say(client, voice, "नहीं")["assistant_text"]
    assert skipped.startswith("ठीक है, वह दवा नहीं जोड़ी।") and "नहीं हमारी लिस्ट" not in skipped
    assert "फैक्टरजन हमारी लिस्ट में नहीं मिली" in skipped
