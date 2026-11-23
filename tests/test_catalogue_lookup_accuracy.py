"""Lookups against the live 26-record catalogue (data/demo_medicines.csv)."""
from pathlib import Path

import pytest

from app.services.import_service import import_medicines_from_csv
from app.services.matching_service import phonetic_key

CSV = Path(__file__).resolve().parents[1] / "data" / "demo_medicines.csv"
NOT_FOUND = "लिस्ट में नहीं"


@pytest.fixture
def catalogue(db_session):
    import_medicines_from_csv(db_session, CSV)


def say(client, text):
    voice = client.post("/voice/sessions", json={"language": "hi"}).json()
    response = client.post(f"/voice/sessions/{voice['voice_session_id']}/turns",
                           json={"text": text, "speak": False})
    assert response.status_code == 200, response.text
    return response.json()


def ids(result):
    return [i["medicine_id"] for i in (result.get("order_summary") or {}).get("items", [])]


def test_phonetic_key_ignores_script_and_spelling():
    assert phonetic_key("सेट्रिज़िन") == phonetic_key("cetirizine")
    assert phonetic_key("क्रेमाफिन") == phonetic_key("cremaffin")


@pytest.mark.parametrize("heard,medicine_id", [
    ("डोलो 650 दो पत्ते", "MED-00025"),
    ("क्रोसिन दो पत्ते", "MED-00026"),
    ("कोलोस्पा दो पत्ते", "MED-00017"),
    ("सेरोम 500 दो पत्ते", "MED-00016"),
    ("ब्रूफेन 400 दो पत्ते", "MED-00014"),
])
def test_hindi_names_select_the_catalogue_id(client, catalogue, heard, medicine_id):
    assert ids(say(client, heard)) == [medicine_id]


@pytest.mark.parametrize("heard,name", [
    ("सेट्रिज़िन दो पत्ते", "CETIRIZINE"),
    ("cetrizine 2 strips", "CETIRIZINE"),
    ("crosin 2 strips", "CROCIN"),
    ("क्रीमा एनो एक", "CREMA"),
])
def test_misspelt_names_are_asked_not_rejected(client, catalogue, heard, name):
    result = say(client, heard)
    assert NOT_FOUND not in result["assistant_text"]
    assert name in result["assistant_text"].upper()
    assert ids(result) == []


@pytest.mark.parametrize("heard", ["क्रेमाफिन एक बोतल", "ब्रूफेन दो पत्ते", "arachitol 2 strips"])
def test_several_variants_are_asked(client, catalogue, heard):
    result = say(client, heard)
    assert ids(result) == [] and NOT_FOUND not in result["assistant_text"]


@pytest.mark.parametrize("heard", ["पैंटोसिड दो पत्ते", "वैलेंटास 50 दो पत्ते", "zyxqv 2 strips"])
def test_absent_medicine_is_not_substituted(client, catalogue, heard):
    result = say(client, heard)
    assert ids(result) == []


def test_unlisted_strength_is_not_changed(client, catalogue):
    assert "MED-00025" not in ids(say(client, "डोलो 500 दो पत्ते"))
