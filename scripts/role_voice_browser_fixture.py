"""Isolated role-library browser server. No game, SDK, cloud or personal store."""
import hashlib,io,json,os,sys,wave
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];HOME=ROOT/'.work/role-voice-browser';HOME.mkdir(parents=True,exist_ok=True)
os.environ['IRACING_DATA_DIR']=str(HOME);os.environ['IRACING_RADIO_AUDIO_DIR']=str(HOME/'builtin');os.environ['IRACING_SPOTTER_AUDIO_DIR']=str(HOME/'spotter')
sys.path.insert(0,str(ROOT/'iRacing_Backend'))
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import uvicorn,soundfile as sf
from voice_coach import VoiceCoach,register_voice_routes
from voice_notes import VoiceNoteStore
from live_telemetry import LiveTelemetry
from custom_voice_pack_format import FORMAT,export_pack,binding_signature
from spotter import VARIANTS
from voice_catalog_diff import current_bindings
class NoCloud:
    def status(self):return {'enabled':False,'recognition_installed':False,'credential_configured':False}
    def synthesize(self,*args,**kwargs):raise AssertionError('Browser fixture cannot synthesize')
b=io.BytesIO()
with wave.open(b,'wb') as w:w.setparams((1,2,24000,0,'NONE','not compressed'));w.writeframes(b'\0\0'*24000)
data=b.getvalue();wav=HOME/'synthetic.wav';wav.write_bytes(data);h=hashlib.sha256(data).hexdigest()
rows=[{'id':f's{i}','role':'spotter','sourceText':t,'text':t,'spoken':t,'audio':f'clips/{h}.wav','sha256':h,'duration':1} for i,t in enumerate(dict.fromkeys(v[0] for v in VARIANTS.values()))]
spot=HOME/'spotter.rcvoice.zip'
if not spot.exists():export_pack(spot,{'format':FORMAT,'schemaVersion':3,'packId':'role-spotter','version':'1','name':'스포터 시험팩','targets':['spotter'],'catalogBaseline':{'spotter':sorted(binding_signature(r,t) for r,t in current_bindings() if r=='spotter')},'entries':rows},{f'clips/{h}.wav':wav})
encoded=io.BytesIO();samples,rate=sf.read(io.BytesIO(data),dtype='int16');sf.write(encoded,samples,rate,format='OGG',subtype='OPUS',compression_level=.9)
opus=encoded.getvalue();oh=hashlib.sha256(opus).hexdigest();file=HOME/'synthetic.ogg';file.write_bytes(opus)
coach_rows=[(r,t) for r,t in current_bindings() if r=='coach'];text=coach_rows[0][1]
row={'id':'coach','role':'coach','sourceText':text,'text':text,'spoken':text,'audio':f'clips/{oh}.ogg','sha256':oh,'duration':1}
pack=HOME/'coach.rcvoice.zip'
if not pack.exists():export_pack(pack,{'format':FORMAT,'schemaVersion':3,'packId':'role-coach','version':'1','name':'코치 시험팩','targets':['coach'],'catalogBaseline':{'coach':sorted(binding_signature(r,t) for r,t in coach_rows[:-1])},'entries':[row]},{f'clips/{oh}.ogg':file})
app=FastAPI(version=json.loads((ROOT/'package.json').read_text(encoding='utf8'))['version']);coach=VoiceCoach(LiveTelemetry(),VoiceNoteStore(HOME/'notes.sqlite3'),speech=NoCloud());register_voice_routes(app,coach,None,None,None,None)
@app.api_route('/api/{path:path}',methods=['GET','POST'])
def other(path:str):
    if path=='status':return {'telemetry':None,'ai_enabled':False}
    if path in ('files','external-references'):return {'files':[],'recommended_selection':[]}
    if path=='memory/sessions':return {'sessions':[]}
    if path.startswith('garage61/collector'):return {'health':{'enabled':False},'job':None}
    if path.startswith('lmu/'):return {'files':[],'status':{},'sessions':[],'connected':False}
    return {'enabled':False,'counts':{'sessions':0},'installed':False,'status':'IDLE'}
app.mount('/',StaticFiles(directory=ROOT/'.work/library-build',html=True),name='ui')
if __name__=='__main__':uvicorn.run(app,host='127.0.0.1',port=24673,log_level='warning')
