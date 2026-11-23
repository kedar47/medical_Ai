"""The real call of 2026-11-23 (call_5ddde559...) against the live 26-record catalogue."""
from pathlib import Path

import pytest

from app.database.models import Order
from app.services.import_service import import_medicines_from_csv
from app.services.voice.quantity_correction import is_correction_request, parse_quantity

CSV = Path(__file__).resolve().parents[1] / "data" / "demo_medicines.csv"
FOUR = "मुझे डोलो 650 के चार पत्ते, क्रोसिन 500 के तीन पत्ते, कोम्बी फ्लेम के दो पत्ते और ब्रोफिन 200 के पाँच पत्ते चाहिए।"


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


def four_items(client, voice):
    say(client, voice, FOUR)
    return say(client, voice, "हाँ")  # COMBIFLAM, heard in two parts, is asked yes/no


@pytest.mark.parametrize("text,expected", [
    ("सीरोम 500 के चार पत्ते ऐड कर दो।", False),
    ("एनाफोर्टन सिरप की तीन बोतल ऐड कर दो।", False),
    ("डोलो 650 की चार की जगह सात पत्ते कर दो।", True),
    ("dolo 4 se 7 kar do", True),
    ("crocin ki quantity badha do", True),
])
def test_add_is_not_a_correction(text, expected):
    assert is_correction_request(text) is expected


def test_old_and_new_quantity():
    assert parse_quantity("चार की जगह सात पत्ते कर दो") == (7, "STRIP")
    assert parse_quantity("4 se 7 kar do") == (7, None)


def test_four_medicines_in_one_speech(client, voice):
    asked = say(client, voice, FOUR)
    assert "COMBIFLAM TAB की 2 स्ट्रिप चाहिए? हाँ या नहीं" in asked["assistant_text"]
    assert "CORFLAM" not in asked["assistant_text"]
    say(client, voice, "हाँ")
    done = say(client, voice, "हाँ")
    assert cart(done) == [("MED-00025", 4, "STRIP"), ("MED-00026", 3, "STRIP"),
                          ("MED-00028", 2, "STRIP"), ("MED-00013", 5, "STRIP")]


def test_yes_and_more_medicine_continues(client, voice):
    four_items(client, voice)
    more = say(client, voice, "हाँ, और और दवाई भी चाहिए।")
    assert more["status"] == "ACTIVE" and "सही हो तो कन्फर्म" not in more["assistant_text"]
    assert "और कौन सी दवा चाहिए" in more["assistant_text"]
    assert len(cart(more)) == 4  # "हाँ" also answered the BRUFEN question


def test_anafortan_syrup_and_cerom_are_added_from_the_draft(client, voice):
    four_items(client, voice)
    say(client, voice, "हाँ")
    syrup = say(client, voice, "और मेडिसिन ऐड कर दो, अब मुझे एनाफोर्टन सिरप की तीन बोतल भी जोड़ दो।")
    assert ("MED-00009", 3, "BOTTLE") in cart(syrup)
    asked = say(client, voice, "सीरोम 500 के चार पत्ते ऐड कर दो।")["assistant_text"]
    assert asked == "क्या आपको CEROM 500 की 4 स्ट्रिप चाहिए? हाँ या नहीं बोलें।"
    assert "CREMA" not in asked
    assert ("MED-00016", 4, "STRIP") in cart(say(client, voice, "हाँ"))


def test_strength_that_contradicts_a_near_name_is_not_offered(client, voice):
    reply = say(client, voice, "इरोम ₹500 के पांच पत्ते भी ऐड कर दो।")["assistant_text"]
    assert "CREMA" not in reply and reply.startswith("क्या आपको CEROM 500")


def test_box_pack_is_not_counted_in_bottles(client, voice):
    say(client, voice, "मुझे आर्चिटोल की दो बोतलें भी चाहिए।")
    asked = say(client, voice, "Architol Nano Soberfree वाला चाहिए।")
    assert asked["assistant_text"] == "ARACHITOL NANO SUGAR FREE डिब्बे में आती है, बोतल में नहीं। कितने डिब्बे चाहिए?"
    assert asked["order_summary"] is None
    assert cart(say(client, voice, "दो डिब्बे")) == [("MED-00012", 2, "BOX")]


def test_dolo_four_to_seven_keeps_id_and_unit(client, voice):
    four_items(client, voice)
    say(client, voice, "हाँ")
    updated = say(client, voice, "डोलो 650 की चार की जगह सात पत्ते कर दो।")
    assert ("MED-00025", 7, "STRIP") in cart(updated) and len(cart(updated)) == 4


def test_unclear_correction_asks_which_item(client, voice):
    four_items(client, voice)
    say(client, voice, "हाँ")
    asked = say(client, voice, "इसकी चार की जगह सात पत्ते कर दो।")["assistant_text"]
    assert asked.startswith("किस दवा की मात्रा बदलनी है:") and "DOLO 650" in asked


def test_adding_during_final_readback_returns_to_editing(client, voice, db_session):
    say(client, voice, "डोलो 650 दो पत्ते")
    assert "सही हो तो कन्फर्म" in say(client, voice, "बस")["assistant_text"]
    added = say(client, voice, "क्रोसिन 500 के तीन पत्ते भी चाहिए")
    assert ("MED-00026", 3, "STRIP") in cart(added)
    readback = say(client, voice, "कन्फर्म करो")
    assert readback["status"] == "ACTIVE" and "CROCIN" in readback["assistant_text"]
    assert db_session.query(Order).count() == 0


def test_unavailable_medicine_skip_keeps_the_order(client, voice):
    say(client, voice, "डोलो 650 दो पत्ते")
    assert "नहीं मिली" in say(client, voice, "मुझे जायलोमैट 900 के दो पत्ते भी चाहिए हैं।")["assistant_text"]
    kept = say(client, voice, "ठीक है, जाइलो मैन नहीं चाहिए, उसे छोड़ दो, बाकी ऑर्डर रखो।")
    assert cart(kept) == [("MED-00025", 2, "STRIP")] and "लंबित" not in kept["assistant_text"]


def test_interrupted_question_is_never_dropped_silently(client, voice):
    say(client, voice, "डोलो 650 दो पत्ते, सीरोम 500 के चार पत्ते")
    switched = say(client, voice, "क्रोसिन 500 के तीन पत्ते भी चाहिए")["assistant_text"]
    assert "CEROM 500 तय नहीं हुई थी, उसे छोड़ दिया।" in switched


@pytest.mark.parametrize("tentative", ["कन्फर्म कर दूं।", "कन्फर्म करूँ?", "ऑर्डर कन्फर्म कर दूंगा", "CONFIRM KARU"])
def test_tentative_confirm_never_saves(client, voice, db_session, tentative):
    say(client, voice, "डोलो 650 दो पत्ते")
    say(client, voice, "बस")
    reply = say(client, voice, tentative)
    assert reply["status"] == "ACTIVE" and "हाँ, कन्फर्म करो" in reply["assistant_text"]
    assert db_session.query(Order).count() == 0


def test_no_after_readback_never_saves(client, voice, db_session):
    say(client, voice, "डोलो 650 दो पत्ते")
    say(client, voice, "बस")
    assert say(client, voice, "नहीं")["status"] == "ACTIVE"
    assert db_session.query(Order).count() == 0


def test_explicit_approval_saves_exactly_once(client, voice, db_session):
    four_items(client, voice)
    say(client, voice, "हाँ")
    say(client, voice, "कन्फर्म कर दूं।")
    assert say(client, voice, "हाँ, पूरा ऑर्डर कन्फर्म करो")["status"] == "CONFIRMED"
    again = say(client, voice, "कन्फर्म करो")
    assert "पहले ही" in again["assistant_text"]
    order = db_session.query(Order).one()
    assert sorted((line.medicine_id, line.quantity, line.unit) for line in order.line_items) == sorted([
        ("MED-00025", 4, "STRIP"), ("MED-00026", 3, "STRIP"), ("MED-00028", 2, "STRIP"), ("MED-00013", 5, "STRIP")])


def test_readback_uses_hindi_units_and_no_ids(client, voice):
    four_items(client, voice)
    say(client, voice, "हाँ")
    text = say(client, voice, "बस")["assistant_text"]
    assert "स्ट्रिप" in text and "MED-" not in text and "15'S" not in text


# ---- real Exotel call 2026-11-23 12:57 UTC (Exotel SID e844…abn) -------------

LIVE_1257 = "मेरा नाम हरियोग है, मुझे डोलो 650 के 4 पत्ते, क्रूसिन 500 के 3 पत्ते, कोम्बी फ्लेम के 2 पत्ते और ब्रोफिन 200 के 4 पत्ते चाहिए।"


def live_1257_draft(client, voice):
    say(client, voice, LIVE_1257)
    for _ in range(3):
        say(client, voice, "हाँ।")


def test_continue_and_new_item_in_one_sentence(client, voice):
    live_1257_draft(client, voice)
    reply = say(client, voice, "हाँ और दवाई भी चाहिए। सीरोम 500 के चार पत्ते ऐड कर दो।")
    assert reply["assistant_text"] == "क्या आपको CEROM 500 की 4 स्ट्रिप चाहिए? हाँ या नहीं बोलें।"


def test_correction_after_readback_never_confirms(client, voice, db_session):
    live_1257_draft(client, voice)
    assert "सही हो तो कन्फर्म" in say(client, voice, "हाँ।")["assistant_text"]
    fixed = say(client, voice, "हाँ हाँ दोनों 650 की चार की जगह सात तक।")
    assert fixed["status"] == "ACTIVE" and db_session.query(Order).count() == 0
    assert ("MED-00025", 7, "STRIP") in cart(fixed)
    readback = say(client, voice, "बस")["assistant_text"]
    assert "DOLO 650MG - 7 स्ट्रिप" in readback
    assert say(client, voice, "हाँ, कन्फर्म करो")["status"] == "CONFIRMED"
    line = next(l for l in db_session.query(Order).one().line_items if l.medicine_id == "MED-00025")
    assert line.quantity == 7


# ---- real Exotel call 2026-11-23 13:11 UTC (Exotel SID ba10…abn) -------------

def test_clear_confirm_beside_a_tentative_word_saves(client, voice, db_session):
    say(client, voice, "डोलो 650 दो पत्ते")
    say(client, voice, "बस")
    assert say(client, voice, "हाँ कन्फर्म करूँ, कन्फर्म कर दो, कन्फर्म कर दो।")["status"] == "CONFIRMED"
    assert db_session.query(Order).count() == 1


def test_name_sentence_before_the_list_is_saved(client, voice, db_session):
    say(client, voice, "मेरा नाम लवन है। मुझे डोलो 650 के 4 पत्ते, क्रोसिन 500 के 3 पत्ते चाहिए।")
    say(client, voice, "बस")
    say(client, voice, "हाँ, कन्फर्म करो")
    assert db_session.query(Order).one().customer_name == "लवन"


# ---- real Exotel call 2026-11-23 13:25 UTC (Exotel SID 06e5…abn) -------------

def test_replace_strength_removes_the_old_item(client, voice):
    say(client, voice, "डोलो 650 चार पत्ते, ब्रूफेन 200 दो पत्ते")
    swapped = say(client, voice, "ब्रोफिन 200 की जगह ब्रोफिन 400 दो पत्ते ऐड कर दो।")
    assert swapped["assistant_text"].startswith("BRUFEN 200MG हटा दी।")
    final = cart(say(client, voice, "हाँ")) if "हाँ या नहीं" in swapped["assistant_text"] else cart(swapped)
    assert ("MED-00014", 2, "STRIP") in final and all(i[0] != "MED-00013" for i in final)


def test_and_some_medicine_plus_item_adds_it(client, voice):
    say(client, voice, "डोलो 650 चार पत्ते")
    reply = say(client, voice, "हाँ हाँ मुझे और कोई दवा चाहिए एनाफोर्टन सिरप की तीन बोतल भी जोड़ दो")
    assert reply["status"] == "ACTIVE" and "सही हो तो कन्फर्म" not in reply["assistant_text"]
    assert "ANAFORTAN SYRUP" in reply["assistant_text"]


# ---- real Exotel call 2026-11-23 13:38 UTC (Exotel SID a979…abn) -------------

def test_list_without_commas_is_split(client, voice):
    reply = say(client, voice, "हाँ मुझे और दवा चाहिए सीरोम 500 के 5 पत्ते डोलो 650 के 4 पत्ते ब्रूफेन 200 के 5 पत्ते ऐड कर दो")
    assert reply["assistant_text"] == "क्या आपको CEROM 500 की 5 स्ट्रिप चाहिए? हाँ या नहीं बोलें।"
    after = say(client, voice, "हाँ")
    assert ("MED-00016", 5, "STRIP") in cart(after) and ("MED-00025", 4, "STRIP") in cart(after)


def test_aur_chahiye_with_an_item_adds_it(client, voice):
    say(client, voice, "डोलो 650 चार पत्ते")
    reply = say(client, voice, "हाँ और चाहिए, एनाफोर्टन सिरप की 3 बोतल भी है, जोड़ दो।")
    assert "ANAFORTAN SYRUP" in reply["assistant_text"] and "सही हो तो कन्फर्म" not in reply["assistant_text"]


def test_unknown_word_with_a_unique_strength_is_asked(client, voice):
    reply = say(client, voice, "दोनों 650 के 4 पत्ते")
    assert "DOLO 650" in reply["assistant_text"] and reply["order_summary"] is None


def test_unknown_word_with_an_unlisted_strength_is_not_offered(client, voice):
    assert "नहीं मिली" in say(client, voice, "जायलोमेड 900 के दो पत्ते ऐड कर दो।")["assistant_text"]


def test_repeating_the_same_item_does_not_double_it(client, voice):
    say(client, voice, "डोलो 650 के 4 पत्ते")
    again = say(client, voice, "डोलो 650 के 4 पत्ते")
    assert "पहले से ऑर्डर में है" in again["assistant_text"]
    assert cart(say(client, voice, "बस")) == [("MED-00025", 4, "STRIP")]


def test_repeat_inside_a_list_is_said_plainly(client, voice):
    say(client, voice, "डोलो 650 के 4 पत्ते")
    reply = say(client, voice, "डोलो 650 के 4 पत्ते, क्रोसिन 500 के 3 पत्ते")["assistant_text"]
    assert "DOLO 650MG की 4 स्ट्रिप पहले से ऑर्डर में है" in reply and "लिस्ट में नहीं मिली" not in reply


# ---- real Exotel call 2026-11-23 13:38 UTC: misheard names ------------------

@pytest.mark.parametrize("heard,name", [
    ("प्रोफिन 200 के 5 पत्ते", "BRUFEN 200MG"),
    ("एनाफोटन सिरप की तीन बोतल", "ANAFORTAN SYRUP"),
    ("एनाफोटल सिरप के तीन बोतल ऐड कर दी", "ANAFORTAN SYRUP"),
    ("एनाफर्टन सिरप की 3 बोतल", "ANAFORTAN SYRUP"),
    ("सीरम 500 के 5 पत्ते", "CEROM 500"),
    ("तेरोम पाँच सौ के चार पत्ते", "CEROM 500"),
    ("ई-रोम 500 के पांच पत्ते", "CEROM 500"),
    ("टेरॉम 500 की चार पत्री", "CEROM 500"),
])
def test_misheard_names_are_asked_yes_or_no(client, voice, heard, name):
    reply = say(client, voice, heard)
    assert reply["assistant_text"].startswith(f"क्या आपको {name}") and reply["order_summary"] is None


def test_first_sound_rule_needs_the_catalogue_number(client, voice):
    assert "CEROM" not in say(client, voice, "टेरॉम के चार पत्ते")["assistant_text"]


def test_readback_names_medicines_that_were_not_added(client, voice, db_session):
    say(client, voice, "डोलो 650 के चार पत्ते")
    say(client, voice, "जायलोमेड 900 के दो पत्ते ऐड कर दो")
    readback = say(client, voice, "बस")["assistant_text"]
    assert readback.startswith("ये दवाइयाँ ऑर्डर में नहीं जुड़ीं: जायलोमेड।")
    assert db_session.query(Order).count() == 0


def test_medicine_added_later_is_not_listed_as_missing(client, voice):
    say(client, voice, "डोलो 650 के चार पत्ते")
    say(client, voice, "एनाफोटल")  # not clear
    say(client, voice, "एनाफोर्टन सिरप की तीन बोतल")
    readback = say(client, voice, "बस")["assistant_text"]
    assert "नहीं जुड़ीं" not in readback and "ANAFORTAN SYRUP" in readback


def test_second_miss_offers_a_way_out(client, voice):
    say(client, voice, "क्सयज़ 2 पत्ते")
    again = say(client, voice, "क्सयज़ 2 पत्ते")["assistant_text"]
    assert "धीरे-धीरे" in again and "छोड़ दो" in again
