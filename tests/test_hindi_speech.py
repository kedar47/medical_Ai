import pytest
from app.services.voice.intent import DeterministicIntentParser


def voice(client):
    return client.post('/voice/sessions',json={'language':'hi'}).json()


def turn(client,session,text):
    result=client.post(f"/voice/sessions/{session['voice_session_id']}/turns",json={'text':text,'speak':False})
    assert result.status_code==200,result.text
    return result.json()


@pytest.mark.parametrize('strength,medicine', [('200 एमजी','MED-00013'),('२०० एमजी','MED-00013'),('दो सौ मिलीग्राम','MED-00013'),('टू हंड्रेड एमजी','MED-00013'),('चार सौ एमजी','MED-00014'),('six hundred mg','MED-00015')])
def test_hindi_strength_and_four_strips(client,seed_catalogue,strength,medicine):
    session=voice(client)
    result=turn(client,session,f'मुझे ब्रूफें {strength} के चार पत्ते चाहिए है')
    assert result['order_summary']['items'][0]['medicine_id']==medicine
    assert result['order_summary']['items'][0]['quantity']==4
    assert result['order_summary']['items'][0]['unit']=='STRIP'
    assert result['order'] is None


def test_hindi_alias_without_strength_requires_variant_choice(client,seed_catalogue):
    session=voice(client)
    result=turn(client,session,'मुझे ब्रूफें के चार पते चाहिए है')
    assert len(result['candidates'])==3
    assert result['order_summary'] is None
    selected=turn(client,session,'MED-00014')
    assert selected['order_summary']['items'][0]['quantity']==4


def test_noisy_strength_suggests_catalogue_but_never_guesses(client,seed_catalogue):
    session=voice(client)
    # Representative ASR noise; not claimed as an exact copy of the screenshot.
    result=turn(client,session,'मुझे ब्रूफें टुहँटेरड आमची के चार पते चाहिए है')
    assert len(result['candidates'])==3
    assert all(c['match_type']=='FUZZY_SUGGESTION' for c in result['candidates'])
    assert result['order_summary'] is None
    assert client.get(f"/order-sessions/{session['order_session_id']}").json()['items']==[]
    selected=turn(client,session,'MED-00013')
    assert selected['order_summary']['items'][0]['quantity']==4


def test_partial_alias_cannot_match_a_different_unknown_name(client,seed_catalogue):
    result=turn(client,voice(client),'मुझे ब्रूफेंXYZ के चार पत्ते चाहिए')
    assert result['order_summary'] is None


def test_strength_words_never_become_quantity():
    parsed=DeterministicIntentParser().parse('मुझे ब्रूफेन दो सौ एमजी के चार पत्ते चाहिए',language='hi')
    assert parsed.quantity==4
    assert parsed.medicine_query=='ब्रूफेन'


def test_stt_uses_active_catalogue_vocabulary_and_no_patient_context(db_session,seed_catalogue):
    from app.services.voice_service import VoiceService
    from app.services.voice.stt import FasterWhisperSTT
    service=VoiceService(db_session)
    adapter=service._get_stt()
    assert isinstance(adapter,FasterWhisperSTT)
    assert "BRUFEN" in adapter._vocabulary
    assert "ब्रूफेन" in adapter._vocabulary
    assert "OLD MEDICINE" not in adapter._vocabulary
    assert not any("200" in name or "400" in name for name in adapter._vocabulary)


def test_whisper_receives_vocabulary_hint_and_temporary_audio_is_deleted(monkeypatch):
    from types import SimpleNamespace
    from pathlib import Path
    from app.services.voice.stt import FasterWhisperSTT
    from app.core.config import get_settings
    recorded={}
    class Model:
        def transcribe(self,path,**kwargs):
            recorded.update(kwargs);recorded['path']=path
            return [SimpleNamespace(text="मुझे ब्रूफेन चाहिए")],SimpleNamespace(language="hi",language_probability=1,duration=1)
    adapter=FasterWhisperSTT(get_settings())
    monkeypatch.setattr(adapter.settings,'voice_stt_hotwords_enabled',True)
    adapter.set_vocabulary(["BRUFEN","ब्रूफेन"])
    monkeypatch.setattr(adapter,'_get_model',lambda:Model())
    result=adapter.transcribe(b'audio',filename='test.wav',language='hi')
    assert recorded['hotwords']=='BRUFEN, ब्रूफेन'
    assert recorded['condition_on_previous_text'] is False
    assert result.transcript=='मुझे ब्रूफेन चाहिए'
    assert not Path(recorded['path']).exists()


def test_hotwords_are_opt_in(monkeypatch):
    from types import SimpleNamespace
    from app.services.voice.stt import FasterWhisperSTT
    from app.core.config import get_settings
    recorded={}
    class Model:
        def transcribe(self,path,**kwargs):
            recorded.update(kwargs)
            return [SimpleNamespace(text="नमस्ते")],SimpleNamespace(language="hi",language_probability=1,duration=1)
    adapter=FasterWhisperSTT(get_settings())
    monkeypatch.setattr(adapter.settings,'voice_stt_hotwords_enabled',False)
    adapter.set_vocabulary(["BRUFEN"])
    monkeypatch.setattr(adapter,'_get_model',lambda:Model())
    adapter.transcribe(b'audio',filename='test.wav',language='hi')
    assert recorded['hotwords'] is None


def test_initial_prompt_uses_english_letter_names_only(monkeypatch):
    # Devanagari aliases in the prompt made English speech loop for 70-100 s.
    from types import SimpleNamespace
    from app.services.voice.stt import FasterWhisperSTT
    from app.core.config import get_settings
    recorded={}
    class Model:
        def transcribe(self,path,**kwargs):
            recorded.update(kwargs)
            return [SimpleNamespace(text="I need brufen")],SimpleNamespace(language="en",language_probability=1,duration=1)
    adapter=FasterWhisperSTT(get_settings())
    monkeypatch.setattr(adapter.settings,'voice_stt_initial_prompt','')
    adapter.set_vocabulary(["BRUFEN","ब्रूफेन","brufin","क्लोमिन"])
    monkeypatch.setattr(adapter,'_get_model',lambda:Model())
    adapter.transcribe(b'audio',filename='test.wav',language='hi')
    assert recorded['initial_prompt']=="Medicine names: BRUFEN, brufin"


def test_tts_collapses_padded_catalogue_whitespace(monkeypatch):
    import numpy as np
    import wave
    import io
    from app.services.voice.tts import KokoroTextToSpeech
    from app.core.config import get_settings
    recorded={}
    class Engine:
        def create(self,text,**kwargs):
            recorded['text']=text
            return np.array([0.0,0.1],dtype=np.float32),24000
    adapter=KokoroTextToSpeech(get_settings())
    monkeypatch.setattr(adapter,'_get_engine',lambda:Engine())
    audio=adapter.synthesize("CLOMIN TAB          10'S",language='en')
    assert recorded['text']=="CLOMIN TAB 10'S"
    with wave.open(io.BytesIO(audio.data)) as wav:
        assert wav.getframerate()==24000
        assert wav.getnframes()==2


def test_hindi_spoken_followup_selects_explicit_variant(client,seed_catalogue):
    session=voice(client)
    first=turn(client,session,'मुझे ब्रूफें अस्पष्ट शब्द के चार पते चाहिए')
    assert len(first['candidates'])==3
    result=turn(client,session,'ब्रूफेन चार सौ एमजी')
    assert result['order_summary']['items'][0]['medicine_id']=='MED-00014'
    assert result['order_summary']['items'][0]['quantity']==4


def test_hindi_quantity_correction_uses_existing_variant(client,seed_catalogue):
    session=voice(client)
    turn(client,session,'मुझे ब्रूफेन चार सौ एमजी के दो पत्ते चाहिए')
    result=turn(client,session,'ब्रूफेन चार सौ एमजी की मात्रा तीन पत्ते कर दो')
    assert result['intent']=='update_item'
    assert len(result['order_summary']['items'])==1
    assert result['order_summary']['items'][0]['quantity']==3
    assert result['order_summary']['items'][0]['medicine_id']=='MED-00014'


def test_real_synthetic_phone_transcription_punctuation(client,seed_catalogue):
    # Actual text returned by local STT on synthesized 8 kHz Hindi audio.
    result=turn(client,voice(client),'मुझे ब्रूफेन, 200, MG के 4 पत्ते चाहीए')
    item=result['order_summary']['items'][0]
    assert item['medicine_id']=='MED-00013' and item['quantity']==4


# Actual phone transcripts from live Exotel calls on 2026-11-18 (Hindi calls).
@pytest.mark.parametrize('text,medicine,quantity', [
    ('brufin 200 MG, 4 FATTE CHIYAYE','MED-00013',4),
    ('brufin 200, NJ, 4 STRIPS CHIYAYE','MED-00013',4),
    ('brufin 200MG 15S 4 पटे चालिए','MED-00013',4),
    ('2 STRIP BRUFEN 400, CHAHIÉ','MED-00014',2),
])
def test_live_call_english_letter_transcripts_use_strength(client,seed_catalogue,text,medicine,quantity):
    result=turn(client,voice(client),text)
    assert result['order_summary'] is not None, result['assistant_text']
    item=result['order_summary']['items'][0]
    assert item['medicine_id']==medicine and item['quantity']==quantity and item['unit']=='STRIP'
    assert result['language']=='hi'


def test_hindi_session_stays_hindi_for_short_english_words(client,seed_catalogue):
    session=voice(client)
    # Whisper often hallucinates "Thank you" on a short noise burst.
    assert turn(client,session,'Thank you')['language']=='hi'
    assert turn(client,session,'BRUFEN')['language']=='hi'
    assert client.get(f"/voice/sessions/{session['voice_session_id']}").json()['language']=='hi'


def test_hindi_session_switches_for_a_full_english_sentence(client,seed_catalogue):
    result=turn(client,voice(client),'I need two strips of brufin 400.')
    assert result['language']=='en'
    assert result['order_summary']['items'][0]['medicine_id']=='MED-00014'


def test_pack_size_is_not_used_to_pick_strength(client,seed_catalogue):
    session=voice(client)
    turn(client,session,'मुझे ब्रूफेन के चार पत्ते चाहिए')
    result=turn(client,session,'BRUFEN 400MG 15S')
    assert result['order_summary']['items'][0]['medicine_id']=='MED-00014'


def test_real_synthetic_browser_unclear_strength_requires_clarification(client,seed_catalogue):
    # Actual local-STT text: "दो सा" is unclear and must not become 200 by guessing.
    result=turn(client,voice(client),'मुझे ब्रूफेन, दो सा एमजी के चार पते चाहीए')
    assert result['order_summary'] is None and len(result['candidates'])==3
