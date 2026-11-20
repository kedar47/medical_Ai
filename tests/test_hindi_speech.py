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
    assert recorded["initial_prompt"]=="Medicines BRUFEN brufin."


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
    # Live-call Whisper junk with one small English word.
    assert turn(client,session,'THAT IS CLIP')['language']=='hi'
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


def _wav_seconds(seconds, rate=16000):
    import io, wave
    out=io.BytesIO()
    with wave.open(out,'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(rate);w.writeframes(bytes(2*int(rate*seconds)))
    return out.getvalue()


def test_whisper_does_not_retry_and_caps_tokens_by_length(monkeypatch):
    # 1 s of phone noise took 40-53 s: 6 temperature retries, each decoding junk.
    from types import SimpleNamespace
    from app.services.voice.stt import FasterWhisperSTT
    from app.core.config import get_settings
    recorded={}
    class Model:
        def transcribe(self,path,**kwargs):
            recorded.update(kwargs)
            return [SimpleNamespace(text="ब्रूफेन")],SimpleNamespace(language="hi",language_probability=1,duration=2)
    adapter=FasterWhisperSTT(get_settings())
    monkeypatch.setattr(adapter,'_get_model',lambda:Model())
    adapter.transcribe(_wav_seconds(2.0),filename='call.wav',language='hi')
    assert recorded['temperature']==(0.0,0.4) and recorded['best_of']==1
    s=get_settings()
    assert recorded['max_new_tokens']==s.voice_stt_max_tokens_base+2*s.voice_stt_max_tokens_per_second
    adapter.transcribe(b'webm-bytes',filename='browser.webm',language='hi')
    assert recorded['max_new_tokens'] is None


def test_punctuation_only_transcript_is_no_speech(monkeypatch):
    from types import SimpleNamespace
    from app.core.exceptions import NoSpeechError
    from app.services.voice.stt import FasterWhisperSTT
    from app.core.config import get_settings
    class Model:
        def transcribe(self,path,**kwargs):
            return [SimpleNamespace(text=" . ")],SimpleNamespace(language="hi",language_probability=1,duration=1)
    adapter=FasterWhisperSTT(get_settings())
    monkeypatch.setattr(adapter,'_get_model',lambda:Model())
    with pytest.raises(NoSpeechError):
        adapter.transcribe(_wav_seconds(1.0),filename='call.wav',language='hi')


def test_echoed_hint_names_are_no_speech(monkeypatch):
    # Live call: unclear audio came back as the prompt's own medicine list.
    from types import SimpleNamespace
    from app.core.exceptions import NoSpeechError
    from app.services.voice.stt import FasterWhisperSTT
    from app.core.config import get_settings
    texts=["CREMAFINE, CORFLAM, MADRIN, CREMAFFIN, CORFLAM, CORFLAM,"]
    class Model:
        def transcribe(self,path,**kwargs):
            return [SimpleNamespace(text=texts[0])],SimpleNamespace(language="hi",language_probability=1,duration=2)
    adapter=FasterWhisperSTT(get_settings())
    adapter.set_vocabulary(["BRUFEN","CREMAFFIN","CORFLAM","MADRIN","ब्रूफेन"])
    monkeypatch.setattr(adapter,'_get_model',lambda:Model())
    with pytest.raises(NoSpeechError):
        adapter.transcribe(_wav_seconds(2.0),filename='call.wav',language='hi')
    texts[0]="brufin 200 MG, 4 FATTE CHIYAYE"
    assert adapter.transcribe(_wav_seconds(2.0),filename='call.wav',language='hi').transcript==texts[0]


def test_looping_segment_is_dropped(monkeypatch):
    from types import SimpleNamespace
    from app.core.exceptions import NoSpeechError
    from app.services.voice.stt import FasterWhisperSTT
    from app.core.config import get_settings
    class Model:
        def transcribe(self,path,**kwargs):
            return [SimpleNamespace(text="కారికికికికికికికి",compression_ratio=3.5)],SimpleNamespace(language="hi",language_probability=1,duration=2)
    adapter=FasterWhisperSTT(get_settings())
    monkeypatch.setattr(adapter,'_get_model',lambda:Model())
    with pytest.raises(NoSpeechError):
        adapter.transcribe(_wav_seconds(2.0),filename='call.wav',language='hi')


# Live call 2026-11-18 08:43: after "BRUFEN 200 MG 15S" the agent asked for the
# quantity, and Whisper wrote the caller's "दो स्ट्रिप" as these.
@pytest.mark.parametrize('answer', ['DOESTRIP','DOSCRIP','those strips','dos trip','do strip','Those.','2','दो'])
def test_live_call_quantity_answers_add_two_strips(client,seed_catalogue,answer):
    session=voice(client)
    first=turn(client,session,'BRUFEN 200 MG 15S')
    assert first['order_summary'] is None and 'मात्रा' in first['assistant_text']
    result=turn(client,session,answer)
    assert result['order_summary'] is not None, result['assistant_text']
    item=result['order_summary']['items'][0]
    assert (item['medicine_id'],item['quantity'],item['unit'])==('MED-00013',2,'STRIP')
    assert result['language']=='hi'


def test_unclear_quantity_answer_still_asks_again(client,seed_catalogue):
    session=voice(client)
    turn(client,session,'BRUFEN 200 MG 15S')
    result=turn(client,session,'NARISLIP')
    assert result['order_summary'] is None and 'मात्रा' in result['assistant_text']


def test_number_only_uses_bottles_for_syrup_and_asks_unit_for_others(client,seed_catalogue,db_session):
    from app.database.models import Medicine
    db_session.add_all([
        Medicine(medicine_id="MED-00021",medicine_name="CREMAFFIN",description="MINT/PLAIN SYP 225ML",source_serial_number="21",source_row=21),
        Medicine(medicine_id="MED-00011",medicine_name="ARACHITOL",description="INJ 6L BOX",source_serial_number="11",source_row=11),
    ])
    db_session.commit()
    session=voice(client)
    turn(client,session,'MED-00021')
    result=turn(client,session,'2')
    item=result['order_summary']['items'][0]
    assert (item['medicine_id'],item['quantity'],item['unit'])==('MED-00021',2,'BOTTLE')
    other=voice(client)
    turn(client,other,'MED-00011')
    asked=turn(client,other,'2')
    assert asked['order_summary'] is None and 'इकाई' in asked['assistant_text']


def test_confirmation_does_not_read_out_the_order_id(client,seed_catalogue):
    session=voice(client)
    turn(client,session,'brufin 200 MG, 4 FATTE CHIYAYE')
    turn(client,session,'order confirm')
    confirmed=turn(client,session,'order confirm')
    # Live call: the Hindi voice read the ID's digit runs as huge numbers
    # ("arab", "kharab"). The ID is shown in the dashboard, not spoken.
    assert confirmed['order']['order_id'] not in confirmed['assistant_text']
    assert not any(c.isdigit() for c in confirmed['assistant_text'])


# Live call 2026-11-18 08:53, turn by turn.
def test_live_call_exact_name_and_strength_with_one_stray_word(client,seed_catalogue):
    result=turn(client,voice(client),'brufan, 200MG, 15S, 2-strip file')
    item=result['order_summary']['items'][0]
    assert (item['medicine_id'],item['quantity'],item['unit'])==('MED-00013',2,'STRIP')


def test_garbled_name_with_strength_digits_still_needs_a_choice(client,seed_catalogue):
    # Two unknown words: too much is unclear to pick for the caller.
    result=turn(client,voice(client),'brufan 200MG nara bara')
    assert result['order_summary'] is None and len(result['candidates'])==3


@pytest.mark.parametrize('answer,quantity', [('DO SHRIP',2),('egg strip',1),('the two strip',2)])
def test_live_call_quantity_answers_after_strength_choice(client,seed_catalogue,answer,quantity):
    session=voice(client)
    turn(client,session,'BRUFEN 200 MG-17S')
    result=turn(client,session,answer)
    item=result['order_summary']['items'][0]
    assert (item['medicine_id'],item['quantity'],item['unit'])==('MED-00013',quantity,'STRIP')


def test_repeating_the_medicine_is_not_read_as_a_quantity(client,seed_catalogue):
    session=voice(client)
    turn(client,session,'BRUFEN 200 MG-17S')
    result=turn(client,session,'BRUFEN 200 MG-17S')
    assert result['order_summary'] is None


def test_confirm_after_misheard_medicine_reads_order_back_then_confirms(client,seed_catalogue):
    session=voice(client)
    turn(client,session,'brufin 200 MG, 4 FATTE CHIYAYE')
    assert 'कन्फर्म' in turn(client,session,'order confirm')['assistant_text']
    # Whisper heard "हाँ" as a medicine name; that opened a new item choice.
    assert turn(client,session,'CORFLAM')['candidates']
    again=turn(client,session,'order confirmed.')
    assert again['order'] is None and 'BRUFEN' in again['assistant_text']
    confirmed=turn(client,session,'हाँ')
    assert confirmed['order'] is not None
    assert [i['medicine_id'] for i in confirmed['order']['line_items']]==['MED-00013']


def test_phone_transcription_skips_the_english_retry(monkeypatch):
    from types import SimpleNamespace
    from app.core.exceptions import NoSpeechError
    from app.services.voice.stt import FasterWhisperSTT
    from app.core.config import get_settings
    languages=[]
    class Model:
        def transcribe(self,path,**kwargs):
            languages.append(kwargs['language'])
            text='' if kwargs['language']=='hi' else 'CREMAFFIN'
            return [SimpleNamespace(text=text)],SimpleNamespace(language=kwargs['language'],language_probability=1,duration=1)
    adapter=FasterWhisperSTT(get_settings())
    monkeypatch.setattr(adapter,'_get_model',lambda:Model())
    with pytest.raises(NoSpeechError):
        adapter.transcribe(_wav_seconds(1.0),filename='call.wav',language='hi',allow_fallback=False)
    assert languages==['hi']
    assert adapter.transcribe(_wav_seconds(1.0),filename='call.wav',language='hi').transcript=='CREMAFFIN'


# Live call 2026-11-18 09:22, turn by turn.
@pytest.fixture
def syrups_and_injection(db_session,seed_catalogue):
    from app.database.models import Medicine
    db_session.add_all([
        Medicine(medicine_id="MED-00021",medicine_name="CREMAFFIN",description="MINT/PLAIN SYP 225ML",source_serial_number="21",source_row=21),
        Medicine(medicine_id="MED-00022",medicine_name="CREMAFFIN",description="MIXED FRUIT SYP 225ML",source_serial_number="22",source_row=22),
        Medicine(medicine_id="MED-00023",medicine_name="CREMAFFIN",description="PLUS SYP 225ML",source_serial_number="23",source_row=23),
        Medicine(medicine_id="MED-00011",medicine_name="ARACHITOL",description="INJ 6L BOX",source_serial_number="11",source_row=11),
    ])
    db_session.commit()


def test_tab_in_medicine_name_is_not_the_unit(client,seed_catalogue):
    session=voice(client)
    first=turn(client,session,'CLOMIN TAB')
    assert first['order_summary'] is None and 'गोली' not in first['assistant_text']
    result=turn(client,session,'2 SKET CHI')
    item=result['order_summary']['items'][0]
    assert (item['medicine_id'],item['quantity'],item['unit'])==('MED-00003',2,'STRIP')


@pytest.mark.parametrize('choice,medicine', [('दूसरा','MED-00022'),('doosara','MED-00022'),('mixed fruit','MED-00022'),('plus','MED-00023'),('pela','MED-00021')])
def test_exact_name_among_noise_lets_caller_pick_variant(client,syrups_and_injection,choice,medicine):
    session=voice(client)
    first=turn(client,session,'LOSCHIT, LOSCHIT, CREMAFFIN')
    assert len(first['candidates'])==3 and 'पहला' in first['assistant_text']
    picked=turn(client,session,choice)
    assert picked['order_summary'] is None and 'मात्रा' in picked['assistant_text']
    result=turn(client,session,'2')
    item=result['order_summary']['items'][0]
    assert (item['medicine_id'],item['quantity'],item['unit'])==(medicine,2,'BOTTLE')


def test_naming_another_medicine_while_quantity_is_pending(client,syrups_and_injection):
    session=voice(client)
    turn(client,session,'brufin 200MG15S')
    result=turn(client,session,'ARACHITOL INJ TICKSEL BOX')
    assert result['order_summary'] is None or all(i['medicine_id']!='MED-00013' for i in result['order_summary']['items'])
    assert [c['medicine_id'] for c in result['candidates']]==['MED-00011'] or 'ARACHITOL' in result['assistant_text']


def test_order_kar_do_as_whisper_writes_it_confirms(client,seed_catalogue):
    from app.services.voice.intent import DeterministicIntentParser
    assert DeterministicIntentParser().parse('ORAT KARDO',language='hi').intent.value=='confirm_order'


def test_junk_scripts_are_no_speech(monkeypatch):
    from types import SimpleNamespace
    from app.core.exceptions import NoSpeechError
    from app.services.voice.stt import FasterWhisperSTT
    from app.core.config import get_settings
    texts=["੭੭੭੭੭੭", "4-4 ु ु 4 ु ु"]
    class Model:
        def transcribe(self,path,**kwargs):
            return [SimpleNamespace(text=texts[0])],SimpleNamespace(language="hi",language_probability=1,duration=1)
    adapter=FasterWhisperSTT(get_settings())
    monkeypatch.setattr(adapter,'_get_model',lambda:Model())
    for _ in range(2):
        with pytest.raises(NoSpeechError):
            adapter.transcribe(_wav_seconds(1.0),filename='call.wav',language='hi',allow_fallback=False)
        texts.pop(0)


def test_single_variant_name_among_noise_asks_quantity(client,seed_catalogue):
    session=voice(client)
    first=turn(client,session,'COG, CLOMIN, F')
    assert first['candidates']==[] or first['candidates'] is None or len(first['candidates'])==0
    assert 'CLOMIN TAB' in first['assistant_text'] and 'मात्रा' in first['assistant_text']
    result=turn(client,session,'2 STF CHINEE')
    item=result['order_summary']['items'][0]
    assert (item['medicine_id'],item['quantity'],item['unit'])==('MED-00003',2,'STRIP')


def test_live_call_09_28_clomin_and_four_strips(client,seed_catalogue):
    session=voice(client)
    first=turn(client,session,'MOUJEEK, CLOMIN TAB')
    assert 'मात्रा' in first['assistant_text']
    result=turn(client,session,'CHARIS TIP')
    item=result['order_summary']['items'][0]
    assert (item['medicine_id'],item['quantity'],item['unit'])==('MED-00003',4,'STRIP')


# Live call 2026-11-20 18:59 IST: DOLO 650's quantity step.
@pytest.fixture
def dolo_650(db_session,seed_catalogue):
    from app.database.models import Medicine
    db_session.add(Medicine(medicine_id="MED-00025",medicine_name="DOLO 650",description="TAB 650MG 15'S",source_serial_number="17",source_row=25))
    db_session.commit()


@pytest.mark.parametrize('repeat', ['DOLO 650','dolo650','paracet, dolo 650'])
def test_repeating_a_name_with_a_number_is_not_the_quantity(client,dolo_650,repeat):
    session=voice(client)
    turn(client,session,'DOLO 650')
    result=turn(client,session,repeat)
    assert result['order_summary'] is None
    assert '1 से 100' not in result['assistant_text'] and 'मात्रा' in result['assistant_text']


@pytest.mark.parametrize('answer,quantity', [('DOLO 650 2',2),('dolo 650 ke do patte',2),('डोलो के तीन पत्ते',3),('2',2)])
def test_quantity_answer_with_the_name_number_in_it(client,dolo_650,answer,quantity):
    session=voice(client)
    turn(client,session,'DOLO 650')
    result=turn(client,session,answer)
    item=result['order_summary']['items'][0]
    assert (item['medicine_id'],item['quantity'],item['unit'])==('MED-00025',quantity,'STRIP')


def test_conflicting_strength_in_quantity_answer_is_still_refused(client,seed_catalogue):
    session=voice(client)
    turn(client,session,'BRUFEN 200 MG-17S')
    result=turn(client,session,'BRUFEN 400 MG 2')
    assert result['order_summary'] is None


# Live call 2026-11-20 19:12 IST: "brufan, dolo650, cainu650" (Whisper added
# "brufan" from its hint) got "सही दवा का पूरा नाम ... बताएं" with every variant.
def test_two_names_heard_asks_which_medicine_by_name(client,dolo_650):
    session=voice(client)
    first=turn(client,session,'brufan, dolo650, cainu650')
    assert 'कौन सी दवा चाहिए: BRUFEN या DOLO 650' in first['assistant_text']
    assert 'पूरा नाम' not in first['assistant_text']
    asked=turn(client,session,'dolo')
    assert asked['order_summary'] is None and 'मात्रा' in asked['assistant_text']
    result=turn(client,session,'2')
    item=result['order_summary']['items'][0]
    assert (item['medicine_id'],item['quantity'],item['unit'])==('MED-00025',2,'STRIP')


def test_two_names_heard_then_name_and_quantity_in_one_answer(client,dolo_650):
    session=voice(client)
    turn(client,session,'brufin, dolo650')
    result=turn(client,session,'dolo 650 ke do patte')
    item=result['order_summary']['items'][0]
    assert (item['medicine_id'],item['quantity'],item['unit'])==('MED-00025',2,'STRIP')


def test_two_names_heard_then_multi_variant_name_asks_strength(client,dolo_650):
    session=voice(client)
    turn(client,session,'brufin, dolo650')
    narrowed=turn(client,session,'brufen')
    assert len(narrowed['candidates'])==3 and 'पहला' in narrowed['assistant_text']
    picked=turn(client,session,'400')
    assert 'मात्रा' in picked['assistant_text']
    result=turn(client,session,'2')
    assert result['order_summary']['items'][0]['medicine_id']=='MED-00014'


def test_unclear_answer_to_which_medicine_asks_again(client,dolo_650):
    session=voice(client)
    turn(client,session,'brufin, dolo650')
    again=turn(client,session,'NG')
    assert 'कौन सी दवा चाहिए' in again['assistant_text']


def test_misheard_name_prompt_no_longer_asks_for_full_name(client,seed_catalogue):
    english=client.post('/voice/sessions',json={'language':'en'}).json()
    result=turn(client,english,'add brufn one strip')
    assert result['candidates'] and 'full name' not in result['assistant_text']
    assert 'medicine ID' in result['assistant_text']


def test_hint_echoed_without_commas_is_no_speech(monkeypatch):
    from types import SimpleNamespace
    from app.core.exceptions import NoSpeechError
    from app.services.voice.stt import FasterWhisperSTT
    from app.core.config import get_settings
    texts=["CREMAFFIN CEROM 500 CETIRIZINE CLOMIN DOLO 650."]
    class Model:
        def transcribe(self,path,**kwargs):
            return [SimpleNamespace(text=texts[0])],SimpleNamespace(language="hi",language_probability=1,duration=2)
    adapter=FasterWhisperSTT(get_settings())
    adapter.set_vocabulary(["BRUFEN","brufin","CREMAFFIN","CEROM 500","CETIRIZINE","CLOMIN TAB","clomin","DOLO 650","dolo","dolo650"])
    monkeypatch.setattr(adapter,'_get_model',lambda:Model())
    with pytest.raises(NoSpeechError):
        adapter.transcribe(_wav_seconds(2.0),filename='call.wav',language='hi',allow_fallback=False)
    texts[0]="brufin dolo 650"
    assert adapter.transcribe(_wav_seconds(2.0),filename='call.wav',language='hi',allow_fallback=False).transcript=="brufin dolo 650"
