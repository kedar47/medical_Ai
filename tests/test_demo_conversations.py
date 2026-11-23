"""Whole conversations against the real demo catalogue (data/demo_medicines.csv).

Expected behaviour for the client demo:
- a medicine name alone is enough; one variant goes straight to "how many?",
- several variants get one natural question ("BRUFEN 200MG चाहिए, 400MG या
  600MG?") answered by strength, number, flavour, form or पहला/दूसरा,
- "नहीं" drops the item being chosen, unclear answers never loop forever,
- a number heard on its own ("650.") is matched to the catalogue.
"""
from pathlib import Path

import pytest

from app.services.import_service import import_medicines_from_csv

CSV = Path(__file__).resolve().parents[1] / "data" / "demo_medicines.csv"


@pytest.fixture
def demo_catalogue(db_session):
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


# ---- name only -------------------------------------------------------------

@pytest.mark.parametrize("answer", ["400", "400 mg", "चार सौ", "char sau", "car so", "दूसरा", "400 वाला", "BRUFEN 400"])
def test_name_only_asks_strength_naturally_then_how_many(client, demo_catalogue, answer):
    voice = session(client)
    asked = say(client, voice, "ब्रूफेन")
    assert asked["assistant_text"] == "BRUFEN 200MG चाहिए, 400MG या 600MG?"
    picked = say(client, voice, answer)
    assert picked["assistant_text"] == "BRUFEN 400MG की कितनी स्ट्रिप चाहिए?"
    added = say(client, voice, "दो")
    assert cart(added) == [("MED-00014", 2, "STRIP")]
    assert "और कोई दवा चाहिए" in added["assistant_text"]


def test_name_and_quantity_then_strength_adds_at_once(client, demo_catalogue):
    voice = session(client)
    say(client, voice, "brufen ke do patte")
    assert cart(say(client, voice, "600")) == [("MED-00015", 2, "STRIP")]


@pytest.mark.parametrize("name", ["dolo", "डोलो", "DOLO 650", "dolo650"])
def test_single_variant_name_goes_straight_to_how_many(client, demo_catalogue, name):
    voice = session(client)
    asked = say(client, voice, name)
    assert asked["assistant_text"] == "DOLO 650MG की कितनी स्ट्रिप चाहिए?"
    assert cart(say(client, voice, "3")) == [("MED-00025", 3, "STRIP")]


@pytest.mark.parametrize("answer,medicine", [("mixed fruit", "MED-00022"), ("plus", "MED-00023"), ("mint", "MED-00021"), ("पहला", "MED-00021")])
def test_syrup_flavours(client, demo_catalogue, answer, medicine):
    voice = session(client)
    asked = say(client, voice, "cremaffin")
    assert asked["assistant_text"] == "CREMAFFIN MINT PLAIN चाहिए, MIXED FRUIT या PLUS?"
    picked = say(client, voice, answer)
    assert "की कितनी बोतल चाहिए?" in picked["assistant_text"]
    assert cart(say(client, voice, "1")) == [(medicine, 1, "BOTTLE")]


@pytest.mark.parametrize("answer,medicine", [("गोली वाली", "MED-00007"), ("tablet", "MED-00007"), ("syrup", "MED-00006"), ("60 ml", "MED-00006")])
def test_tablet_or_liquid(client, demo_catalogue, answer, medicine):
    voice = session(client)
    asked = say(client, voice, "corflam")
    assert asked["assistant_text"] == "CORFLAM SUSPENSION 60ML चाहिए या TAB?"
    picked = say(client, voice, answer)
    assert picked["order_summary"] is None and "कितनी" in picked["assistant_text"]
    assert cart(say(client, voice, "2"))[0][0] == medicine


def test_form_word_that_picks_a_variant_is_not_the_unit(client, demo_catalogue):
    voice = session(client)
    say(client, voice, "corflam")
    picked = say(client, voice, "गोली वाली")
    assert picked["assistant_text"] == "CORFLAM TAB की कितनी स्ट्रिप चाहिए?"
    assert cart(say(client, voice, "2")) == [("MED-00007", 2, "STRIP")]
    voice = session(client)
    say(client, voice, "corflam")
    assert cart(say(client, voice, "tab wali 10 goli")) == [("MED-00007", 10, "TABLET")]


def test_injection_choice_and_unit(client, demo_catalogue):
    voice = session(client)
    assert say(client, voice, "arachitol")["assistant_text"] == "ARACHITOL INJ चाहिए या NANO SUGAR FREE?"
    picked = say(client, voice, "injection")
    assert "ARACHITOL INJ" in picked["assistant_text"] and "मात्रा" in picked["assistant_text"]
    assert cart(say(client, voice, "2 box")) == [("MED-00011", 2, "BOX")]


def test_generic_paracetamol_offers_the_brands(client, demo_catalogue):
    voice = session(client)
    asked = say(client, voice, "paracetamol")
    assert "DOLO 650MG" in asked["assistant_text"] and "CROCIN 500MG" in asked["assistant_text"]
    picked = say(client, voice, "crocin")
    assert picked["assistant_text"] == "CROCIN 500MG की कितनी स्ट्रिप चाहिए?"


# ---- no, repeats, retries -------------------------------------------------

@pytest.mark.parametrize("negative", ["नहीं", "nahi", "no", "नहीं चाहिए", "NO!"])
def test_no_drops_the_item_being_chosen(client, demo_catalogue, negative):
    voice = session(client)
    say(client, voice, "ब्रूफेन")
    dropped = say(client, voice, negative)
    assert dropped["assistant_text"] == "ठीक है, वह दवा नहीं जोड़ी। कौन सी दवा चाहिए?"
    # The next answer is a fresh request, not a variant choice.
    assert say(client, voice, "dolo")["assistant_text"] == "DOLO 650MG की कितनी स्ट्रिप चाहिए?"


def test_no_during_quantity_drops_only_that_item(client, demo_catalogue):
    voice = session(client)
    say(client, voice, "dolo")
    say(client, voice, "2")
    say(client, voice, "crocin")
    dropped = say(client, voice, "नहीं")
    assert "नहीं जोड़ी" in dropped["assistant_text"]
    summary = say(client, voice, "order batao")
    assert [i["medicine_id"] for i in summary["order_summary"]["items"]] == ["MED-00025"]


def test_repeating_only_the_name_asks_again_with_a_hint(client, demo_catalogue):
    voice = session(client)
    say(client, voice, "ब्रूफेन")
    again = say(client, voice, "brufen")
    assert again["order_summary"] is None
    assert again["assistant_text"].startswith("BRUFEN 200MG चाहिए, 400MG या 600MG?")
    assert "पहला" in again["assistant_text"]


def test_unclear_answers_stop_after_three_tries(client, demo_catalogue):
    voice = session(client)
    say(client, voice, "ब्रूफेन")
    replies = [say(client, voice, junk)["assistant_text"] for junk in ["NG", "KU", "NIRU"]]
    assert "दवा का नाम दोबारा बताएं" in replies[-1]
    assert say(client, voice, "dolo")["assistant_text"] == "DOLO 650MG की कितनी स्ट्रिप चाहिए?"


# ---- only a number heard -------------------------------------------------

@pytest.mark.parametrize("heard,prompt", [
    ("650.", "DOLO 650MG की कितनी स्ट्रिप चाहिए?"),
    ("600.", "BRUFEN 600MG की कितनी स्ट्रिप चाहिए?"),
    ("छह सौ पचास", "DOLO 650MG की कितनी स्ट्रिप चाहिए?"),
    ("325 mg", "DISPRIN 325MG की कितनी स्ट्रिप चाहिए?"),
])
def test_strength_heard_alone_offers_the_matching_medicine(client, demo_catalogue, heard, prompt):
    assert say(client, session(client), heard)["assistant_text"] == prompt


def test_strength_shared_by_two_medicines_asks_which(client, demo_catalogue):
    voice = session(client)
    asked = say(client, voice, "500.")
    assert "CEROM 500" in asked["assistant_text"] and "CROCIN 500MG" in asked["assistant_text"]
    assert say(client, voice, "crocin")["assistant_text"] == "CROCIN 500MG की कितनी स्ट्रिप चाहिए?"


@pytest.mark.parametrize("heard", ["550.", "2", "5% 5%"])
def test_number_without_a_match_asks_for_the_name(client, demo_catalogue, heard):
    result = say(client, session(client), heard)
    assert result["order_summary"] is None and not result["candidates"]


def test_other_medicines_strength_during_quantity_switches(client, demo_catalogue):
    voice = session(client)
    say(client, voice, "paracet")
    switched = say(client, voice, "650.")
    assert switched["assistant_text"] == "DOLO 650MG की कितनी स्ट्रिप चाहिए?"
    assert cart(say(client, voice, "2")) == [("MED-00025", 2, "STRIP")]


def test_own_strength_during_quantity_is_not_a_count(client, demo_catalogue):
    voice = session(client)
    say(client, voice, "dolo")
    again = say(client, voice, "650.")
    assert "DOLO 650MG की कितनी स्ट्रिप चाहिए?" in again["assistant_text"]
    assert "1 से 100" not in again["assistant_text"]


def test_unclear_quantity_answers_get_clearer_then_start_over(client, demo_catalogue):
    voice = session(client)
    say(client, voice, "dolo")
    first = say(client, voice, "NG")["assistant_text"]
    second = say(client, voice, "DOCH")["assistant_text"]
    third = say(client, voice, "PIPE POTCH")["assistant_text"]
    assert first.startswith("माफ़ कीजिए") and "बस संख्या बोलें" in first
    assert "नहीं बोलें" in second and second == third
    gave_up = say(client, voice, "MMMM")["assistant_text"]
    assert "दवा का नाम दोबारा बताएं" in gave_up


def test_clear_number_after_unclear_answers_still_adds(client, demo_catalogue):
    voice = session(client)
    say(client, voice, "dolo")
    say(client, voice, "NG")
    say(client, voice, "DOCH")
    assert cart(say(client, voice, "दो")) == [("MED-00025", 2, "STRIP")]


# ---- two names heard -------------------------------------------------------

def test_two_names_then_unclear_twice_starts_afresh(client, demo_catalogue):
    voice = session(client)
    first = say(client, voice, "CREMAFFIN, DOLO 650")
    assert first["assistant_text"].startswith("कौन सी दवा चाहिए: CREMAFFIN या DOLO 650?")
    assert say(client, voice, "NG")["assistant_text"].startswith("कौन सी दवा चाहिए: CREMAFFIN या DOLO 650?")
    third = say(client, voice, "BASTA THANK YOU GERAMIN")
    assert "CREMAFFIN या DOLO 650" not in third["assistant_text"]


def test_two_names_then_a_strength_starts_afresh(client, demo_catalogue):
    voice = session(client)
    say(client, voice, "CREMAFFIN, DOLO 650")
    assert say(client, voice, "600.")["assistant_text"] == "BRUFEN 600MG की कितनी स्ट्रिप चाहिए?"


# ---- complete orders -------------------------------------------------------

def test_complete_hindi_order_with_two_medicines(client, demo_catalogue):
    voice = session(client)
    say(client, voice, "dolo")
    say(client, voice, "2")
    say(client, voice, "ब्रूफेन")
    say(client, voice, "चार सौ")
    say(client, voice, "एक")
    summary = say(client, voice, "order confirm")
    assert "DOLO 650MG" in summary["assistant_text"] and "BRUFEN 400MG" in summary["assistant_text"]
    confirmed = say(client, voice, "हाँ")
    assert confirmed["order"] is not None
    assert sorted(i["medicine_id"] for i in confirmed["order"]["line_items"]) == ["MED-00014", "MED-00025"]


def test_complete_english_order(client, demo_catalogue):
    voice = session(client, "en")
    asked = say(client, voice, "add brufen")
    assert asked["assistant_text"] == "Do you want BRUFEN 200MG, 400MG or 600MG?"
    picked = say(client, voice, "second")
    assert picked["assistant_text"] == "How many strips of BRUFEN 400MG? Please say the quantity."
    added = say(client, voice, "two")
    assert cart(added) == [("MED-00014", 2, "STRIP")]
    assert "Anything else" in added["assistant_text"]
    say(client, voice, "confirm")
    assert say(client, voice, "yes")["order"] is not None


# ---- live call 2026-11-20 19:42 IST, replayed ------------------------------

LIVE_CALL_19_42 = [
    "600.", "DOLO 650 BRUFIN CEROM 480", "brufan 400 mg chahiye umli", "5.5.", "5% 5%", "500.",
    "paracet ecorretion paracet cermin", "ARACHITOL M", "5 STRIP END KALDI", "650.",
    "NO! PARA 60 CETIRIZINE", "650.", "paracet", "PLEMENT CEROM IN",
    "CREMAFFIN CREMAFFIN CREMAFFIN CREMAFFIN DOLO 650", "550.", "Cromoson HEAP POTUSHRIP",
    "BASTA THANK YOU GERAMIN", "600.",
]


def test_strength_of_another_medicine_during_variant_choice_switches(client, demo_catalogue):
    voice = session(client)
    say(client, voice, "arachitol")
    kept = say(client, voice, "5 STRIP END KALDI")
    assert kept["assistant_text"].startswith("ARACHITOL INJ चाहिए या NANO SUGAR FREE?")
    switched = say(client, voice, "650.")
    assert switched["assistant_text"] == "DOLO 650MG की कितनी स्ट्रिप चाहिए?"


def test_other_strength_with_count_during_quantity_switches_and_adds(client, demo_catalogue):
    voice = session(client)
    say(client, voice, "brufen 200")
    assert cart(say(client, voice, "char sau ke do patte")) == [("MED-00014", 2, "STRIP")]


def test_count_said_during_variant_choice_is_kept(client, demo_catalogue):
    voice = session(client)
    say(client, voice, "ब्रूफेन")
    say(client, voice, "5 strip")
    assert cart(say(client, voice, "400")) == [("MED-00014", 5, "STRIP")]


def test_cerom_in_english_letters(client, demo_catalogue):
    assert say(client, session(client), "cerom")["assistant_text"] == "CEROM 500 की कितनी स्ट्रिप चाहिए?"


def test_live_call_never_repeats_one_question_three_times(client, demo_catalogue):
    voice = session(client)
    replies = [say(client, voice, text)["assistant_text"] for text in LIVE_CALL_19_42]
    run = longest = 1
    for previous, current in zip(replies, replies[1:]):
        run = run + 1 if current.split("?")[0] == previous.split("?")[0] else 1
        longest = max(longest, run)
    assert longest <= 2, replies


# ---- live calls 2026-11-20 evening ---------------------------------------

@pytest.mark.parametrize("heard", ["200 K 2 PAKTA CHAHIYAY", "200 के 2 पत्ते चाहिए", "दो सौ के दो पत्ते चाहिए"])
def test_strength_and_count_without_a_name_asks_yes_or_no(client, demo_catalogue, heard):
    voice = session(client)
    asked = say(client, voice, heard)
    assert asked["assistant_text"] == "क्या आपको BRUFEN 200MG की 2 स्ट्रिप चाहिए? हाँ या नहीं बोलें।"
    assert asked["order_summary"] is None
    assert cart(say(client, voice, "हाँ")) == [("MED-00013", 2, "STRIP")]


def test_strength_guess_no_drops_it(client, demo_catalogue):
    voice = session(client)
    say(client, voice, "200 K 2 PAKTA CHAHIYAY")
    assert "नहीं जोड़ी" in say(client, voice, "नहीं")["assistant_text"]
    assert say(client, voice, "dolo")["assistant_text"] == "DOLO 650MG की कितनी स्ट्रिप चाहिए?"


def test_strength_guess_answered_with_another_medicine(client, demo_catalogue):
    voice = session(client)
    say(client, voice, "200 K 2 PAKTA CHAHIYAY")
    assert say(client, voice, "brufen 400")["assistant_text"] == "BRUFEN 400MG की कितनी स्ट्रिप चाहिए?"


def test_strength_guess_unclear_answer_repeats_the_question(client, demo_catalogue):
    voice = session(client)
    say(client, voice, "200 K 2 PAKTA CHAHIYAY")
    again = say(client, voice, "NG")
    assert again["assistant_text"] == "क्या आपको BRUFEN 200MG की 2 स्ट्रिप चाहिए? हाँ या नहीं बोलें।"
    assert cart(say(client, voice, "haan ji")) == [("MED-00013", 2, "STRIP")]


def test_strength_and_count_shared_by_two_medicines_asks_which_and_keeps_count(client, demo_catalogue):
    voice = session(client)
    asked = say(client, voice, "500 के 2 पत्ते चाहिए")
    assert "CEROM 500" in asked["assistant_text"] and "CROCIN 500MG" in asked["assistant_text"]
    assert cart(say(client, voice, "crocin")) == [("MED-00026", 2, "STRIP")]


def test_count_with_small_number_only_is_not_a_strength_guess(client, demo_catalogue):
    result = say(client, session(client), "2 पत्ते चाहिए")
    assert result["order_summary"] is None and "STRENGTH" not in result["assistant_text"]
    assert "हाँ या नहीं" not in result["assistant_text"]


@pytest.mark.parametrize("heard", ["brufan 200 KDOPATTE CHAHIYE", "brufen 200 dopatte chahiye", "brufen 200 ke 2patte"])
def test_merged_count_and_strip_word(client, demo_catalogue, heard):
    assert cart(say(client, session(client), heard)) == [("MED-00013", 2, "STRIP")]


def test_merged_count_as_quantity_answer(client, demo_catalogue):
    voice = session(client)
    say(client, voice, "dolo")
    assert cart(say(client, voice, "KDOPATTE")) == [("MED-00025", 2, "STRIP")]


def test_two_names_but_one_clear_strength_picks_it(client, demo_catalogue):
    voice = session(client)
    picked = say(client, voice, "brufan 200 MG CEROM")
    assert picked["assistant_text"] == "BRUFEN 200MG की कितनी स्ट्रिप चाहिए?"
    assert cart(say(client, voice, "2 STRIP")) == [("MED-00013", 2, "STRIP")]


def test_two_names_with_clashing_strengths_still_asks(client, demo_catalogue):
    asked = say(client, session(client), "DOLO 650 BRUFIN CEROM 480")
    assert asked["assistant_text"].startswith("कौन सी दवा चाहिए:")
    assert "BRUFEN" in asked["assistant_text"] and "DOLO 650" in asked["assistant_text"]


@pytest.mark.parametrize("heard", ["CREMAFFIN, DOLO 650", "brufin, dolo650", "brufan, dolo650, cainu650"])
def test_number_inside_a_name_does_not_pick_between_two_names(client, demo_catalogue, heard):
    assert say(client, session(client), heard)["assistant_text"].startswith("कौन सी दवा चाहिए:")


@pytest.mark.parametrize("word", ["chahiye", "chahie", "chiyaye", "chahiyay", "chahiya", "cahye", "chaiye"])
def test_chahiye_spellings(word):
    from app.services.voice.intent import _CHAHIYE
    assert _CHAHIYE.match(word)


@pytest.mark.parametrize("word", ["chai", "che", "chhe", "cheese", "chips"])
def test_not_chahiye(word):
    from app.services.voice.intent import _CHAHIYE
    assert not _CHAHIYE.match(word)


def test_live_call_20_54_replayed(client, demo_catalogue):
    voice = session(client)
    first = say(client, voice, "200 K 2 PAKTA CHAHIYAY")
    assert "BRUFEN 200MG की 2 स्ट्रिप" in first["assistant_text"]
    assert cart(say(client, voice, "हाँ")) == [("MED-00013", 2, "STRIP")]
    say(client, voice, "CONFIRM KRO")
    assert say(client, voice, "CONFIRM")["order"] is not None


# ---- live call 2026-11-20 21:17 IST ---------------------------------------

def test_chosen_variant_is_asked_again_after_another_medicine(client, demo_catalogue):
    voice = session(client)
    say(client, voice, "ब्रूफेन")
    assert say(client, voice, "पहला")["assistant_text"] == "BRUFEN 200MG की कितनी स्ट्रिप चाहिए?"
    assert say(client, voice, "DOLO 650.")["assistant_text"] == "DOLO 650MG की कितनी स्ट्रिप चाहिए?"
    added = say(client, voice, "2")
    assert "DOLO 650MG की 2 स्ट्रिप जोड़ दी" in added["assistant_text"]
    assert added["assistant_text"].endswith("अब BRUFEN 200MG की कितनी स्ट्रिप चाहिए?")
    both = say(client, voice, "3")
    assert sorted(cart(both)) == [("MED-00013", 3, "STRIP"), ("MED-00025", 2, "STRIP")]
    assert "और कोई दवा चाहिए" in both["assistant_text"]


def test_kept_medicine_can_be_refused(client, demo_catalogue):
    voice = session(client)
    for text in ["ब्रूफेन", "पहला", "DOLO 650.", "2"]:
        say(client, voice, text)
    refused = say(client, voice, "नहीं")
    # The order already has DOLO 650: offer to finish as well.
    assert refused["assistant_text"] == "ठीक है, वह दवा नहीं जोड़ी। और कोई दवा चाहिए, या ऑर्डर कन्फर्म करूँ?"
    summary = say(client, voice, "order batao")
    assert [i["medicine_id"] for i in summary["order_summary"]["items"]] == ["MED-00025"]


def test_confirming_drops_a_kept_medicine(client, demo_catalogue):
    voice = session(client)
    for text in ["ब्रूफेन", "पहला", "DOLO 650."]:
        say(client, voice, text)
    say(client, voice, "2")  # asks about BRUFEN next
    summary = say(client, voice, "order confirm")
    assert summary["assistant_text"].startswith("BRUFEN तय नहीं हुई, इसलिए नहीं जोड़ी।")
    assert "DOLO 650MG" in summary["assistant_text"] and "BRUFEN" not in summary["assistant_text"].split("आपके ऑर्डर में")[1]
    confirmed = say(client, voice, "हाँ")
    assert [i["medicine_id"] for i in confirmed["order"]["line_items"]] == ["MED-00025"]


def test_named_once_then_replaced_is_not_asked_again(client, demo_catalogue):
    # No variant was chosen for DOLO 650: switching is a change of mind.
    voice = session(client)
    say(client, voice, "dolo")
    switched = say(client, voice, "brufen 200 two strips")
    assert cart(switched) == [("MED-00013", 2, "STRIP")]
    assert "DOLO" not in switched["assistant_text"]


@pytest.mark.parametrize("answer", ["DOESRIPT AYI", "do sript", "2 script"])
def test_sript_spelling_of_strip(client, demo_catalogue, answer):
    voice = session(client)
    say(client, voice, "dolo")
    assert cart(say(client, voice, answer)) == [("MED-00025", 2, "STRIP")]


def test_live_call_21_17_replayed(client, demo_catalogue):
    voice = session(client)
    for text in ["meche brufan dabai chahiye.", "ciaaabsoo", "AHAHLA", "PAHELA", "DOLO 650."]:
        say(client, voice, text)
    after_dolo = say(client, voice, "DOESRIPT AYI")
    assert cart(after_dolo) == [("MED-00025", 2, "STRIP")]
    assert after_dolo["assistant_text"].endswith("अब BRUFEN 200MG की कितनी स्ट्रिप चाहिए?")
    final = say(client, voice, "DO")
    assert sorted(cart(final)) == [("MED-00013", 2, "STRIP"), ("MED-00025", 2, "STRIP")]


# ---- calls 2026-11-20 21:27, 2026-11-21 04:18 and the client's at 18:35 -------

@pytest.mark.parametrize("heard", ["GUNFORM", "PANFAM", "kanfum", "conform karo"])
def test_misheard_confirm_after_read_back_places_the_order(client, demo_catalogue, heard):
    voice = session(client)
    say(client, voice, "dolo")
    say(client, voice, "2")
    summary = say(client, voice, "confirm karo")
    assert "कन्फर्म" in summary["assistant_text"]
    confirmed = say(client, voice, heard)
    assert confirmed["order"] is not None
    assert [i["medicine_id"] for i in confirmed["order"]["line_items"]] == ["MED-00025"]


def test_confirm_like_word_before_the_read_back_does_not_confirm(client, demo_catalogue):
    voice = session(client)
    say(client, voice, "dolo")
    say(client, voice, "2")
    result = say(client, voice, "GUNFORM")
    assert result["order"] is None


def test_do_a_strip_is_two_strips(client, demo_catalogue):
    voice = session(client)
    say(client, voice, "dolo")
    assert cart(say(client, voice, "DO A STRIP")) == [("MED-00025", 2, "STRIP")]


def test_first_or_second_answers_which_medicine(client, demo_catalogue):
    voice = session(client)
    say(client, voice, "brufan dolo 650.")
    picked = say(client, voice, "PAHEL DALO")
    assert picked["assistant_text"] == "BRUFEN 200MG चाहिए, 400MG या 600MG?"
    voice = session(client)
    say(client, voice, "brufan dolo 650.")
    assert say(client, voice, "दूसरा")["assistant_text"] == "DOLO 650MG की कितनी स्ट्रिप चाहिए?"


def test_not_found_gives_examples(client, demo_catalogue):
    result = say(client, session(client), "COSPIN ADVICATE TURGAIT NINTAKTAR")
    assert "डोलो 650" in result["assistant_text"]


def test_greeting_gives_examples(client, demo_catalogue):
    assert "डोलो 650" in session(client)["message"]


def test_live_call_04_18_replayed(client, demo_catalogue):
    voice = session(client)
    for text in ["ccomprofen", "brufan dolo650", "650."]:
        say(client, voice, text)
    assert cart(say(client, voice, "DO A STRIP")) == [("MED-00025", 2, "STRIP")]
    say(client, voice, "confirm karo")
    assert say(client, voice, "GUNFORM")["order"] is not None


def test_live_call_21_27_replayed(client, demo_catalogue):
    voice = session(client)
    for text in ["brufan dolo 650.", "brufan dolo 650.", "PAHEL DALO", "dolo650 brufan 200", "dolo650", "DO STRIP", "CONFIRM KARU"]:
        say(client, voice, text)
    assert say(client, voice, "PANFAM")["order"] is not None
