"""Managed, verified custom radio packs. Import never executes model or pack code."""
import base64,hashlib,io,json,shutil,threading,uuid,zipfile
from pathlib import Path
from custom_voice_pack_format import inspect_pack,check_audio,audio_mime,validate_manifest,MAX_MANIFEST,ID,ROLES,binding_signature

def decode_audio(row,data):
    if not row['audio'].endswith('.ogg'):return
    try:
        import soundfile as sf
        with sf.SoundFile(io.BytesIO(data)) as audio:
            if audio.format!='OGG' or audio.subtype!='OPUS' or audio.channels!=1 or audio.samplerate!=24000:raise ValueError('24kHz 모노 Ogg Opus 음성이 아닙니다.')
            frames=0
            while True:
                block=audio.read(24000,dtype='float32')
                if not len(block):break
                frames+=len(block)
                if frames>24000*60:raise ValueError('Opus 음성 길이 제한을 넘었습니다.')
            if abs(frames/24000-row['duration'])>1/24000:raise ValueError('Opus 디코딩 길이가 팩 정보와 다릅니다.')
    except (ImportError,RuntimeError,OSError) as exc:raise ValueError('Ogg Opus 음성을 읽지 못했습니다. 파일 또는 앱의 오디오 도구를 확인하세요.') from exc

class MissingCustomAudio(ValueError):pass
class CustomVoicePacks:
    def __init__(self,root):
        self.root=Path(root);self.lock=threading.RLock();self.selections={};self.loaded={}
    @property
    def selected(self):return next(iter(self.selections.values()),None)
    @property
    def manifest(self):return self.loaded[self.selected][0] if self.selected else None
    def has_selection(self,role):return role in self.selections
    def _key(self,m):return m['packId']+'@'+m['version']
    def _folder(self,key):
        a,sep,b=key.partition('@')
        if not sep or not ID.fullmatch(a) or not ID.fullmatch(b):raise ValueError('커스텀팩 ID가 올바르지 않습니다.')
        folder=self.root/key
        if self.root.is_symlink() or self.root.is_junction() or folder.is_symlink() or folder.is_junction() or folder.resolve().parent!=self.root.resolve():raise ValueError('설치 폴더 밖의 팩은 사용할 수 없습니다.')
        return folder
    def _manifest(self,key):
        file=self._folder(key)/'manifest.json'
        if file.is_symlink() or file.stat().st_size>MAX_MANIFEST:raise ValueError('팩 설명 파일을 확인하세요.')
        return validate_manifest(json.loads(file.read_text(encoding='utf-8')))
    def install(self,source):
        source=Path(source)
        with self.lock:
            m=inspect_pack(source,decode_audio);key=self._key(m);dest=self._folder(key)
            if dest.exists():
                old=inspect_pack(dest)
                if old!=m:raise ValueError('동일 ID / 버전의 다른 팩이 있습니다. 새 버전으로 내보내세요.')
                return self.describe(old)
            self.root.mkdir(parents=True,exist_ok=True);temp=self.root/('.import-'+uuid.uuid4().hex);temp.mkdir()
            try:
                (temp/'manifest.json').write_text(json.dumps(m,ensure_ascii=False,indent=2),encoding='utf-8')
                with zipfile.ZipFile(source) if source.is_file() else _Directory(source) as container:
                    for r in m['entries']:
                        target=temp/r['audio'];target.parent.mkdir(parents=True,exist_ok=True)
                        if not target.exists():
                            data=container.read(r['audio']);check_audio(r,data);target.write_bytes(data)
                inspect_pack(temp);temp.rename(dest)
            finally:
                if temp.exists():shutil.rmtree(temp)
            return self.describe(m)
    def describe(self,m):
        from spotter import VARIANTS
        bindings={(r['role'],r['sourceText']) for r in m['entries']}
        missing=[key for key,options in VARIANTS.items() if not any(('spotter',t) in bindings for t in options)]
        return {'key':self._key(m),'name':m['name'],'version':m['version'],'entries':len(m['entries']),'schemaVersion':m['schemaVersion'],
                'audioFormat':'Ogg Opus' if any(r['audio'].endswith('.ogg') for r in m['entries']) else 'WAV',
                'missingSpotterKeys':missing,'canSelect':True,'targets':sorted({r['role'] for r in m['entries']}),'baselineKnown':'catalogBaseline' in m,'mode':'exact-whole-sentence',
                'roles':{role:sum(r['role']==role for r in m['entries']) for role in ('spotter','race','strategy','coach')}}
    def list(self):
        rows=[]
        if self.root.exists():
            for folder in sorted(self.root.iterdir()):
                if folder.is_dir() and not folder.name.startswith('.'):
                    try:rows.append(self.describe(self._manifest(folder.name)))
                    except (OSError,ValueError,KeyError,TypeError):continue
        return {'packs':rows,'selected':self.selected,'selections':dict(self.selections),'missingPolicy':'prepared-default','scope':'역할별로 설치한 목소리를 선택합니다. 없는 문장은 준비된 기본 음성으로 보완하며 AI 자유 답변은 별도입니다.'}
    def select(self,key):
        """Legacy callers select every role actually included in the pack."""
        if not key:return self.configure({})
        m=self._manifest(key)
        return self.configure({r['role']:key for r in m['entries']})
    def configure(self,selections):
        with self.lock:
            if not isinstance(selections,dict) or set(selections)-ROLES or any(not isinstance(v,str) for v in selections.values()):raise ValueError('역할별 목소리 선택이 올바르지 않습니다.')
            loaded={}
            for key in set(selections.values()):
                if key in self.loaded:loaded[key]=self.loaded[key];continue
                m=inspect_pack(self._folder(key));loaded[key]=(m,{(r['role'],r['sourceText']):r for r in m['entries']})
            for role,key in selections.items():
                if not any(r['role']==role for r in loaded[key][0]['entries']):raise ValueError('이 팩에는 선택한 역할의 음성이 없습니다.')
            self.loaded,self.selections=loaded,dict(selections)
    def _row(self,role,text):
        key=self.selections.get(role)
        return key,self.loaded[key][1].get((role,text)) if key else None
    def has(self,role,text):
        with self.lock:
            key,row=self._row(role,text)
            return bool(row and (self._folder(key)/row['audio']).is_file())
    def audio(self,role,text):
        with self.lock:
            key,row=self._row(role,text)
            if not row:raise MissingCustomAudio('커스텀팩에 없는 문장입니다. 이 안내는 생략합니다.')
            data=(self._folder(key)/row['audio']).read_bytes();check_audio(row,data)
            return 'data:'+audio_mime(row)+';base64,'+base64.b64encode(data).decode('ascii')
    def preview(self,key,role=None):
        with self.lock:
            folder=self._folder(key);m=self._manifest(key)
            rows=[r for r in m['entries'] if role is None or r['role']==role]
            if not rows:raise ValueError('이 팩에는 해당 역할의 음성이 없습니다.')
            row=next((r for r in rows if r.get('eventKey')=='test'),rows[0])
            data=(folder/row['audio']).read_bytes();check_audio(row,data)
            return {'text':row['text'],'audio_base64':'data:'+audio_mime(row)+';base64,'+base64.b64encode(data).decode('ascii')}
    def spotter(self,default=None):
        from spotter import VARIANTS
        with self.lock:
            clips={}
            for key,texts in VARIANTS.items():
                choices=[{'text':t,'audio_base64':self.audio('spotter',t)} for t in texts if self.has('spotter',t)]
                if not choices and default:
                    for text in texts:
                        digest=hashlib.sha256(json.dumps([default.voice(),__import__('radio_audio').spoken_form(text)],ensure_ascii=False).encode()).hexdigest()
                        try:data=(default.directory/(digest+'.mp3')).read_bytes()
                        except OSError:continue
                        if len(data)>=100:choices.append({'text':text,'audio_base64':base64.b64encode(data).decode('ascii')})
                if not choices:raise MissingCustomAudio('스포터 기본 음성을 먼저 준비하세요. 준비되지 않은 상황: '+key)
                clips[key]={**choices[0],'variants':choices}
            selected=self.selections['spotter']
            return {'voice':self.loaded[selected][0]['name'],'clips':clips,'cached':True,'custom_pack':selected,'clip_count':sum(len(v['variants']) for v in clips.values())}
    def remove(self,key,delete=False):
        with self.lock:
            folder=self._folder(key)
            self._manifest(key)
            if delete:
                # Verify the final absolute managed path and every child before recursion.
                for item in folder.rglob('*'):
                    if item.is_symlink() or item.is_junction() or not item.resolve().is_relative_to(folder.resolve()):raise ValueError('외부 연결이 있는 설치팩은 삭제하지 않습니다.')
                shutil.rmtree(folder)
            self.configure({r:k for r,k in self.selections.items() if k!=key})
            return self.list()
    def updates(self,key,rows,app_version='unknown'):
        m=self._manifest(key);targets=sorted({r['role'] for r in m['entries']});baseline=m.get('catalogBaseline')
        current={r:{} for r in targets}
        for role,text in rows:
            if role in current:current[role][binding_signature(role,text)]=text
        old={r:set(baseline.get(r,())) for r in targets} if baseline is not None else {}
        new=[{'role':r,'sourceText':text} for r,items in current.items() for sig,text in items.items() if baseline is not None and sig not in old[r]]
        return {'format':'racingcoach-voice-update-v1','packKey':key,'packName':m['name'],'appVersion':app_version,'targetRoles':targets,
                'baselineKnown':baseline is not None,'newCount':len(new),'roles':{r:sum(x['role']==r for x in new) for r in targets},
                'newEntries':new,'catalogBaseline':{r:sorted(items) for r,items in current.items()}}

class _Directory:
    def __init__(self,root):self.root=root
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self,name):return (self.root/name).read_bytes()
