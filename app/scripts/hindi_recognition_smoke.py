"""Synthetic Hindi speech through real local models, not a human/PSTN test."""
import json
import time
from app.database.connection import SessionLocal
from app.core.config import get_settings
from app.services.voice import build_tts
from app.services.voice_service import VoiceService
from app.services.telephony.audio_bridge import decode,frames,wav

text='मुझे ब्रूफेन दो सौ एमजी के चार पत्ते चाहिए।'
audio=build_tts(get_settings()).synthesize(text,language='hi')
with SessionLocal() as db:
    service=VoiceService(db)
    for label, data in [('browser-24khz',audio.data),('simulated-phone-8khz',wav(b''.join(decode(f) for f in frames(audio.data))))]:
        start=time.perf_counter()
        result=service.transcribe_audio(data,filename='synthetic.wav',content_type='audio/wav',language='hi')
        print(json.dumps({'synthetic':True,'mode':label,'transcript':result.transcript,'stt_seconds':round(time.perf_counter()-start,2)},ensure_ascii=False),flush=True)
