"""How replies are spoken on the phone (client calls, 2026-11-22)."""
from pathlib import Path

import pytest

from app.api.telephony_routes import is_retry_reply
from app.services.import_service import import_medicines_from_csv
from app.services.voice.tts import KokoroTextToSpeech, _hindi_speakable


def test_english_words_in_hindi_reply_have_no_language_flags():
    pytest.importorskip("phonemizer")
    from kokoro_onnx.config import DEFAULT_VOCAB
    from kokoro_onnx.tokenizer import Tokenizer

    Tokenizer()  # points phonemizer at the bundled espeak-ng, as the engine does

    class Engine:
        class tokenizer:
            vocab = DEFAULT_VOCAB

    phonemes = KokoroTextToSpeech._phonemes(Engine, "ठीक है, BRUFEN 400MG की 2 स्ट्रिप जोड़ दी।", "hi")
    if phonemes is None:
        pytest.skip("espeak-ng not available")
    assert "(en)" not in phonemes and "(hi)" not in phonemes
    assert "bɹˈuːfən" in phonemes


def test_tab_is_said_as_a_word():
    assert _hindi_speakable("SUMO COLD TAB की 2 स्ट्रिप") == "SUMO COLD टैबलेट की 2 स्ट्रिप"
    assert _hindi_speakable("TABLET") == "TABLET"


@pytest.mark.parametrize("text,stale", [
    ("यह दवा हमारी लिस्ट में नहीं मिली। कृपया दवा का नाम दोबारा बताइए।", True),
    ("मात्रा स्पष्ट नहीं है। कृपया एक ही मात्रा दोबारा बताएं।", True),
    ("RAPID D हमारी लिस्ट में नहीं मिली। SUMO COLD TAB की 2 स्ट्रिप जोड़ दी।", False),
    ("BRUFEN 200MG चाहिए, 400MG या 600MG?", False),
])
def test_only_try_again_replies_may_be_skipped(text, stale):
    assert is_retry_reply(text) is stale


def test_repeated_not_found_is_short(client, db_session):
    import_medicines_from_csv(db_session, Path(__file__).resolve().parents[1] / "data" / "client_demo_medicines.csv")
    voice = client.post("/voice/sessions", json={"language": "hi"}).json()

    def say(text):
        return client.post(f"/voice/sessions/{voice['voice_session_id']}/turns",
                           json={"text": text, "speak": False}).json()["assistant_text"]

    first = say("जी थी एम एक्सेल की दो बाटल")
    assert "जैसे डोलो 650" in first
    assert say("जी थी एम एक्सेल की दो बाटल") == "यह भी लिस्ट में नहीं मिली। कोई और दवा बताइए।"
