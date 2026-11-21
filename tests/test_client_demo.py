"""Whole phone conversations against the client's demo list (data/client_demo_medicines.csv).

Texts are written the way Sarvam speech-to-text returns them: Hindi speech in
Devanagari, English speech in English letters.
"""
from pathlib import Path

import pytest

from app.database.models import Order
from app.services.import_service import import_medicines_from_csv

CSV = Path(__file__).resolve().parents[1] / "data" / "client_demo_medicines.csv"


@pytest.fixture
def client_catalogue(db_session):
    import_medicines_from_csv(db_session, CSV)


def session(client, language="hi"):
    return client.post("/voice/sessions", json={"language": language}).json()


def say(client, voice, text):
    response = client.post(
        f"/voice/sessions/{voice['voice_session_id']}/turns",
        json={"text": text, "speak": False},
    )
    assert response.status_code == 200, response.text
    return response.json()


def cart(result):
    summary = result.get("order_summary") or {}
    return [(i["medicine_id"], i["quantity"], i["unit"]) for i in summary.get("items", [])]


def test_catalogue_is_the_clients_list_only():
    import csv

    rows = list(csv.DictReader(CSV.open(encoding="utf-8")))
    names = {r["medicine_name"] for r in rows}
    assert len(names) == 26  # 20 picked + 6 from the client's own sample calls
    assert "CROCIN" not in names and "CETIRIZINE" not in names


# ---- the full call: name, medicine, read-back, confirm, saved ---------------

def test_full_call_with_name_is_saved_with_the_order(client, client_catalogue, db_session):
    voice = session(client)
    assert "अपना नाम" in voice["message"]
    thanked = say(client, voice, "मेरा नाम महेश्वरी है")
    assert thanked["assistant_text"].startswith("धन्यवाद महेश्वरी जी।")
    asked = say(client, voice, "ऑगमेंटिन")
    assert asked["assistant_text"] == "AUGMENTIN 375MG चाहिए, 625, DDS 30ML या DUO 30ML?"
    say(client, voice, "625")
    added = say(client, voice, "दो पत्ते")
    assert cart(added) == [("MED-103153", 2, "STRIP")]
    readback = say(client, voice, "नहीं")
    assert "AUGMENTIN 625 - 2 स्ट्रिप" in readback["assistant_text"]
    assert "कन्फर्म" in readback["assistant_text"]
    done = say(client, voice, "हाँ")
    assert done["status"] == "CONFIRMED"
    order = db_session.query(Order).one()
    assert order.customer_name == "महेश्वरी"


@pytest.mark.parametrize("answer,name", [
    ("महेश्वरी", "महेश्वरी"),
    ("जी, रमेश बोल रहा हूँ", "रमेश"),
    ("my name is ravi kumar", "Ravi Kumar"),
])
def test_name_answers(client, client_catalogue, answer, name):
    reply = say(client, session(client), answer)["assistant_text"]
    assert reply.startswith(f"धन्यवाद {name} जी।") or reply.startswith(f"Thank you, {name}.")


def test_name_is_optional_when_caller_orders_at_once(client, client_catalogue):
    voice = session(client)
    assert say(client, voice, "डोलो 650 दो पत्ते")["order_summary"]["items"][0]["medicine_id"] == "MED-104706"


def test_hello_asks_name_once_then_moves_on(client, client_catalogue):
    voice = session(client)
    assert "नाम" in say(client, voice, "हेलो")["assistant_text"]
    assert "कौन सी दवा" in say(client, voice, "हाँ जी")["assistant_text"]
    assert cart(say(client, voice, "सेरिडॉन एक पत्ता")) == [("MED-106998", 1, "STRIP")]


# ---- each demo medicine, said the Hindi way ----------------------------------

@pytest.mark.parametrize("heard,medicine_id", [
    ("पैंटोसिड दो पत्ते", "MED-105058"),
    ("डाइजीन दो पत्ते", "MED-100031"),
    ("एलेग्रा एम दो पत्ते", "MED-107122"),
    ("डिस्प्रिन दो पत्ते", "MED-107061"),
    ("सेरिडॉन दो पत्ते", "MED-106998"),
    ("नेबिस्टार दो पत्ते", "MED-105666"),
    ("डोलो 650 दो पत्ते", "MED-104706"),
    ("सेरोम 500 दो पत्ते", "MED-00016"),
    ("क्लोमिन दो पत्ते", "MED-00003"),
    ("ब्रूफेन 400 दो पत्ते", "MED-00014"),
    ("वैलेंटास 50 दो पत्ते", "MED-105837"),
    ("मोंटेयर 10 दो पत्ते", "MED-101597"),
])
def test_single_line_orders(client, client_catalogue, heard, medicine_id):
    assert cart(say(client, session(client), heard)) == [(medicine_id, 2, "STRIP")]


@pytest.mark.parametrize("heard,question", [
    ("वैलेंटास", "VALENTAS 100MG चाहिए, 25MG या 50MG?"),
    ("लिम्सी", "LIMCEE TAB चाहिए या ZINC TAB?"),
    ("ब्रूफेन", "BRUFEN 200MG चाहिए, 400MG या 600MG?"),
])
def test_variant_questions_read_naturally(client, client_catalogue, heard, question):
    assert say(client, session(client), heard)["assistant_text"] == question


def test_syrup_counted_in_bottles_even_when_caller_says_patte(client, client_catalogue):
    result = say(client, session(client), "बिकोसूल्स दो पत्ते")
    assert cart(result) == [("MED-106842", 2, "BOTTLE")]
    assert "2 बोतल" in result["assistant_text"]


def test_two_medicines_in_one_sentence(client, client_catalogue):
    result = say(client, session(client), "कॉम्बिफ्लेम दो पत्ते और डिस्प्रिन एक पत्ता")
    assert cart(result) == [("MED-107150", 2, "BOTTLE"), ("MED-107061", 1, "STRIP")]
    assert result["assistant_text"].count("जोड़ दी") == 2


def test_second_of_two_medicines_waits_for_its_variant(client, client_catalogue):
    voice = session(client)
    first = say(client, voice, "डोलो 650 दो पत्ते और वैलेंटास")
    assert cart(first) == [("MED-104706", 2, "STRIP")] or first["order_summary"] is None
    assert "VALENTAS 100MG चाहिए" in first["assistant_text"]
    say(client, voice, "25")
    assert ("MED-105836", 3, "STRIP") in cart(say(client, voice, "तीन"))


def test_unlisted_strength_offers_the_listed_pack(client, client_catalogue):
    voice = session(client)
    offered = say(client, voice, "पैंटोसिड 40")
    assert "PANTOCID" in offered["assistant_text"] and "हाँ या नहीं" in offered["assistant_text"]
    assert cart(say(client, voice, "दो")) == [("MED-105058", 2, "STRIP")]


def test_medicine_outside_the_list_is_not_found(client, client_catalogue):
    result = say(client, session(client), "क्रोसिन दो पत्ते")
    assert result["order_summary"] is None or not cart(result)
    assert "लिस्ट में नहीं" in result["assistant_text"]


def test_no_to_read_back_asks_what_to_change_and_confirm_still_works(client, client_catalogue):
    voice = session(client)
    say(client, voice, "डिस्प्रिन दो पत्ते")
    say(client, voice, "बस इतना ही")
    assert "क्या बदलना है" in say(client, voice, "नहीं")["assistant_text"]
    say(client, voice, "डिस्प्रिन तीन पत्ते कर दो")
    readback = say(client, voice, "बस")
    assert "कन्फर्म" in readback["assistant_text"]
    assert say(client, voice, "कन्फर्म")["status"] == "CONFIRMED"


def test_english_call(client, client_catalogue):
    voice = session(client, "en")
    say(client, voice, "My name is Ravi")
    added = say(client, voice, "Dolo 650 two strips")
    assert cart(added) == [("MED-104706", 2, "STRIP")]
    assert "confirm" in say(client, voice, "no that's all")["assistant_text"].lower()
    assert say(client, voice, "yes")["status"] == "CONFIRMED"


# ---- the client's own sample calls (Sarvam transcripts of audio2/audio3) ----

def test_shop_name_is_a_name(client, client_catalogue):
    reply = say(client, session(client), "शर्मा मेडिकल खरसौत कला")["assistant_text"]
    assert reply.startswith("धन्यवाद शर्मा मेडिकल खरसौत कला जी।")


def test_client_sample_list_valentas_and_nebistar(client, client_catalogue):
    voice = session(client)
    say(client, voice, "शर्मा मेडिकल")
    first = say(client, voice, "वेलेंटा फिफ्टी के 50 पत्ते।")
    assert cart(first) == [("MED-105837", 50, "STRIP")]
    listed = say(client, voice, (
        "वेलेंटास 100 के 20 पत्ते, वेलेंटास 200 के 20 पत्ते, नेबी स्टार ढाई के 50 पत्ते, "
        "नेबी स्टार 5 के 50 पत्ते, नेबी स्टार टी के 30 पत्ते।"
    ))
    assert "VALENTAS 200 हमारी लिस्ट में नहीं है। VALENTAS 100MG चाहिए, 25MG या 50MG?" in listed["assistant_text"]
    rest = say(client, voice, "25")
    assert "NEBISTAR 2.5 हमारी लिस्ट में नहीं है" in rest["assistant_text"]
    assert cart(rest) == [
        ("MED-105837", 50, "STRIP"), ("MED-105835", 20, "STRIP"), ("MED-105836", 20, "STRIP"),
        ("MED-105671", 50, "STRIP"), ("MED-105673", 30, "STRIP"),
    ]
    say(client, voice, "नहीं")
    assert say(client, voice, "हाँ")["status"] == "CONFIRMED"


def test_client_sample_duphalac_and_syrup_choice(client, client_catalogue):
    voice = session(client)
    two = say(client, voice, "ड्यूफालेक 150 एमएल की चार बोतल, ड्यूफालेक 250 एमएल की चार बोतल")
    assert cart(two) == [("MED-100034", 4, "BOTTLE"), ("MED-100035", 4, "BOTTLE")]
    asked = say(client, voice, "चिटानिल टीएम 25 के तीन पत्ते, मैक्स्ट्रा पी सिरप दो बोतल")
    assert asked["assistant_text"].endswith("MAXTRA P DS SYRUP चाहिए या SYRUP?")
    assert "CETANIL TM 25 की 3 स्ट्रिप जोड़ दी" in asked["assistant_text"]
    final = cart(say(client, voice, "दूसरा"))
    assert ("MED-102087", 3, "STRIP") in final and ("MED-102604", 2, "BOTTLE") in final


@pytest.mark.parametrize("heard,expected", [
    ("ड्यूफाल एक 450 एमएल की एक बॉटल", [("MED-100036", 1, "BOTTLE")]),
    ("ब्रूफेन 201 पत्ता", [("MED-00013", 1, "STRIP")]),
    ("कैलपोल सिरप 250 एक बोतल", [("MED-103167", 1, "BOTTLE")]),
    ("Livo Set सिरप एक बोतल", [("MED-105891", 1, "BOTTLE")]),
    ("Limsi Zinc के दो पत्ते", [("MED-106649", 2, "STRIP")]),
    ("कॉम्बी फ्लेम सिरप दो बोतल", [("MED-107150", 2, "BOTTLE")]),
    ("एलेग्रा एम दुपत्ते", [("MED-107122", 2, "STRIP")]),
    ("नेबी स्टार 5 के 50 पत्ते", [("MED-105671", 50, "STRIP")]),
])
def test_sarvam_ways_of_writing(client, client_catalogue, heard, expected):
    assert cart(say(client, session(client), heard)) == expected


def test_near_name_is_asked_yes_no(client, client_catalogue):
    voice = session(client)
    asked = say(client, voice, "Pantha said, three strips please.")
    assert asked["assistant_text"] == "Do you want 3 strips of PANTOCID TAB? Please say yes or no."
    assert cart(say(client, voice, "yes")) == [("MED-105058", 3, "STRIP")]
