"""Explicit local iRacing pit requests. No chat, key injection, or idle polling."""
import math
import re
import time
import unicodedata

from instant_answers import number


COMMAND_EXAMPLES = [
    '피트 연료 20리터 설정', '피트 급유 15리터', '피트 완주 연료 설정', '피트 급유 취소',
    '피트 타이어 교체', '피트 레인 타이어 교체', '피트 웻 타이어 교체', '피트 슬릭 타이어 교체',
    '피트 타이어 전부 교체', '피트 타이어 교체 취소',
    '피트 왼쪽 앞 타이어 교체', '피트 오른쪽 앞 타이어 교체',
    '피트 왼쪽 뒤 타이어 교체', '피트 오른쪽 뒤 타이어 교체',
    '피트 왼쪽 타이어 교체', '피트 오른쪽 타이어 교체',
    '피트 타이어 공기압 180킬로파스칼 설정', '피트 왼쪽 앞 타이어 공기압 26피에스아이 설정',
    '피트 윈드실드 교체', '피트 윈드실드 취소',
    '피트 빠른 수리 켜', '피트 빠른 수리 취소', '피트 전체 취소',
]
WHEELS = {'전부':('LF','RF','LR','RR'), '모두':('LF','RF','LR','RR'), '전체':('LF','RF','LR','RR'),
          '':('LF','RF','LR','RR'), '왼쪽앞':('LF',), '좌전':('LF',), '오른쪽앞':('RF',), '우전':('RF',),
          '왼쪽뒤':('LR',), '좌후':('LR',), '오른쪽뒤':('RR',), '우후':('RR',),
          '왼쪽':('LF','LR'), '오른쪽':('RF','RR'), '앞':('LF','RF'), '뒤':('LR','RR')}
MODES = {'LF':3, 'RF':4, 'LR':5, 'RR':6}
BITS = {'LF':1, 'RF':2, 'LR':4, 'RR':8}
LABELS = {'LF':'왼쪽 앞', 'RF':'오른쪽 앞', 'LR':'왼쪽 뒤', 'RR':'오른쪽 뒤'}
END = r'(?:해줘|해|해주세요|해줄래|해요|줘|주세요|요)?'


def quantity(text):
    """A single explicit Korean/Arabic quantity, never a fuzzy ASR correction."""
    native = {'한':1,'두':2,'세':3,'네':4,'다섯':5,'여섯':6,'일곱':7,'여덟':8,'아홉':9,'열':10,'스무':20}
    if text in native:
        return native[text]
    if re.fullmatch(r'\d{1,3}(?:\.\d{1,2})?',text):
        return float(text)
    if not re.fullmatch(r'[일이삼사오육칠팔구십백]+(?:점[영공일이삼사오육칠팔구]{1,2})?',text):
        return None
    whole, _, fraction = text.partition('점')
    digits = dict(zip('영일이삼사오육칠팔구', range(10))); digits['공']=0
    total, digit, previous = 0, 0, 1000
    for char in whole:
        if char in '십백':
            unit = 10 if char=='십' else 100
            if unit >= previous:return None
            total += (digit or 1)*unit;digit=0;previous=unit
        else:
            if digit:return None
            digit=digits[char]
    return total+digit+(float('0.'+''.join(str(digits[c]) for c in fraction)) if fraction else 0)


def parse(text):
    if not isinstance(text,str) or len(text)>200:return None
    if '?' in text or '？' in text:return None
    t=re.sub(r'[\s,!?。]', '', unicodedata.normalize('NFKC',text).lower()).rstrip('.')
    trigger=re.match(r'^(?:피트스톱|핏스톱|pitstop|피트|히트|키트)',t)
    if not trigger:return None
    t=t[trigger.end():]
    # Only the registered trigger aliases are corrected. Never guess numbers,
    # tire types or the requested action from acoustic similarity.
    if re.search(r'얼마|몇|어떻게|해야|할까|할수|하면|될까|알려|말고|아니|하지마|하지말|그리고|하지않|안해',t):return None
    # Only a fixed command vocabulary is normalized; quantities remain exact.
    t=t.replace('좀','')
    t=t.replace('모든타이어','전체타이어')
    for before,after in [('좌측','왼쪽'),('우측','오른쪽'),('앞쪽','앞'),('뒷쪽','뒤'),('뒤쪽','뒤'),
                         ('앞왼쪽','왼쪽앞'),('앞오른쪽','오른쪽앞'),('뒤왼쪽','왼쪽뒤'),('뒤오른쪽','오른쪽뒤')]:
        t=t.replace(before,after)
    t=re.sub(r'타이어(레인|웻|웨트|슬릭|일반|드라이)(?:으로|로)?(?=교체|바꿔|갈아)',r'\1타이어',t)
    def match(pattern):return re.fullmatch(pattern+END,t)
    cancel=r'(?:취소|해제|끄기|꺼|빼기|빼|없애)'
    if re.fullmatch(r'(?:타이어)?교체(?:체취|취)', t):
        return dict(id='invalid', name='타이어 동작 확인 필요', ambiguous_action=True,
                    error='교체인지 취소인지 확실하지 않습니다. “피트 타이어 교체” 또는 “피트 타이어 교체 취소”로 다시 말씀해 주세요.')
    # Repeated cancellation is an explicit action, unlike a clipped 취 syllable.
    t = re.sub(r'(?<=교체)재취소', '취소', t)
    windshield=r'(?:윈드실드|윈드스크린|앞유리|티어오프)'
    repair=r'(?:빠른수리|패스트리페어|퀵리페어|수리)'
    fixed=[(r'(?:설정|옵션|서비스)?(?:전체|전부|모두|다)(?:취소|해제|초기화|빼)', 'clear', '모든 피트 서비스 취소',[(0,0)],0x7f,0),
           (r'(?:급유|연료|기름)'+cancel, 'clear_fuel','급유 취소',[(11,0)],0x10,0),
           (r'(?:레인(?:에서)?|웻|웨트|슬릭|일반|드라이)?타이어(?:교체)?'+cancel, 'clear_tires','타이어 교체 취소',[(7,0)],0xf,0),
           (windshield+r'(?:교체|청소|닦아|닦기|켜|켜기)', 'windshield','윈드실드 교체',[(1,0)],0x20,0x20),
           (windshield+cancel, 'clear_windshield','윈드실드 교체 취소',[(9,0)],0x20,0),
           (repair+cancel, 'clear_repair','빠른 수리 취소',[(10,0)],0x40,0),
           (repair+r'(?:켜|켜기|사용|설정|신청)?', 'fast_repair','빠른 수리 사용',[(8,0)],0x40,0x40)]
    for pattern,key,label,calls,mask,wanted in fixed:
        if match(pattern):return dict(id=key,name=label,calls=calls,mask=mask,wanted=wanted)
    if match(r'(?:완주(?:용)?(?:연료|급유)|(?:끝까지|완주까지)(?:갈)?(?:연료|급유)|(?:연료|급유)(?:완주까지|끝까지))(?:설정|맞춰|넣어|채워)?'):
        return dict(id='fuel_finish_set',name='완주 급유량 설정')
    m=match(r'(?:연료|급유|기름)([0-9.일이삼사오육칠팔구십백점영공한두세네다섯여섯일곱여덟아홉열스무]+)(?:리터|l)(?:로|만|를)?(?:설정|넣어|맞춰|추가|채워|충전|주유)?')
    if m:
        value=quantity(m[1])
        if value is None or value!=int(value) or not 1<=value<=150:
            return dict(id='invalid',name='급유량 확인 필요',error='급유량은 1~150리터의 정수로 지정하세요. 0리터 대신 “피트 급유 취소”를 사용하세요.')
        return dict(id='fuel_set',name=f'추가 급유량 {int(value)}리터 설정',calls=[(2,int(value))],mask=0x10,wanted=0x10,fuel=value)
    wheel=r'(전부|모두|전체|왼쪽앞|오른쪽앞|왼쪽뒤|오른쪽뒤|왼쪽|오른쪽|좌전|우전|좌후|우후|앞|뒤)?'
    m=match(r'(레인(?:에서)?|웻|웨트|슬릭|일반|드라이)?'+wheel+r'(?:타이어)?(?:전부|모두|전체|다|4개|네개)?(?:으로|로)?(?:교체|교환|바꿔|갈아|갈아끼워)')
    if m:
        if not m[1] and '타이어' not in t:return None
        if m[2] not in (None,'','전부','모두','전체') and re.search(r'타이어(?:전부|모두|전체|다|4개|네개)',t):return None
        wheels=WHEELS[m[2] or '']
        mask=sum(BITS[w] for w in wheels)
        kind='wet' if m[1] in ('레인','레인에서','웻','웨트') else 'dry'
        label='웻' if kind=='wet' else '슬릭'
        target='네 바퀴' if len(wheels)==4 else ' · '.join(LABELS[w] for w in wheels)
        return dict(id='tires_set',name=f'{label} 타이어 {target} 교체', compound_kind=kind,
                    calls=[(MODES[w],0) for w in wheels],mask=mask,wanted=mask)
    m=match(wheel+r'(?:타이어)?(?:공기압|압력)([0-9.일이삼사오육칠팔구십백점영공]+)(킬로파스칼|kpa|케이피에이|psi|피에스아이)(?:으로|로)?(?:설정|맞춰)?')
    if m:
        value=quantity(m[2]); unit=m[3]
        psi=unit in ('psi','피에스아이')
        kpa=value*6.894757293 if value is not None and psi else value
        if kpa is None or not 100<=kpa<=400:
            return dict(id='invalid',name='공기압 확인 필요',error='공기압은 단위를 포함해 100~400kPa 범위로 지정하세요.')
        kpa=math.floor(kpa+.5);wheels=WHEELS[m[1] or ''];mask=sum(BITS[w] for w in wheels)
        return dict(id='pressure_set',name=' · '.join(LABELS[w] for w in wheels)+f' 교체 공기압 {kpa}kPa 설정',
                    calls=[(MODES[w],kpa) for w in wheels],mask=mask,wanted=mask,pressures={w:kpa for w in wheels})
    return None


def plan(command, context):
    command=dict(command)
    if command.get('compound_kind'):
        # The SDK index is per car. Never assume that 0=Dry and 1=Wet.
        kind=command['compound_kind']
        types={'wet':{'wet','rain'}, 'dry':{'dry','slick','hard','medium','soft','qualifying'}}[kind]
        choices=set()
        for tire in context.get('pit_tire_compounds') or []:
            if not isinstance(tire,dict):continue
            index=number(tire.get('index'))
            if (index is not None and index==int(index) and 0<=index<=255
                    and str(tire.get('type') or '').strip().lower() in types):
                choices.add(int(index))
        label='웻' if kind=='wet' else '슬릭'
        if not choices:
            raise ValueError(f'이 차량에서 사용할 {label} 타이어 종류를 확인하지 못했습니다. 게임의 피트 설정을 확인하세요.')
        snapshot=context.get('snapshot') or {}
        selected=next((value for value in (number(snapshot.get('pit_sv_tire_compound')),number(snapshot.get('tire_compound')))
                       if value in choices),None)
        if selected is None and len(choices)==1:selected=next(iter(choices))
        if selected is None:
            raise ValueError(f'{label} 타이어가 여러 종류입니다. 게임에서 사용할 종류를 먼저 선택하세요.')
        command.update(compound=int(selected),calls=[(12,int(selected))]+command['calls'])
    if command['id']=='fuel_finish_set':
        from race_engineer import RaceEngineer
        view=context.get('pit_wall') or {}
        liters=RaceEngineer._pit_fuel_litres(view,context.get('snapshot') or {}) if view.get('active') else None
        if (view.get('stops_needed')!=1 or view.get('fuel_next_stop_full') or liters is None or not 1<=liters<=150):
            raise ValueError('한 번의 추가 급유로 완주하는 계획과 급유량이 확인돼야 합니다. 여러 번 정차하거나 급유가 필요하지 않은 경우 자동 설정하지 않습니다.')
        command.update(name=f'완주용 추가 급유량 {liters}리터 설정',calls=[(2,liters)],mask=0x10,wanted=0x10,fuel=liters)
    if 'fuel' in command:
        capacity=number((context.get('pit_wall') or {}).get('tank_capacity_l'))
        if capacity is not None and command['fuel']>capacity:
            raise ValueError('지정한 급유량이 차량의 탱크 용량을 넘습니다.')
    return command


def applied(command,snapshot):
    flags=number(snapshot.get('pit_sv_flags'))
    if flags is None or int(flags)&command['mask'] != command['wanted']:return False
    if command.get('compound_kind') and ('compound' not in command or number(snapshot.get('pit_sv_tire_compound'))!=command['compound']):return False
    if 'fuel' in command:
        value=number(snapshot.get('pit_sv_fuel'))
        if value is None or abs(value-command['fuel'])>.25:return False
    for wheel,value in command.get('pressures',{}).items():
        got=number(snapshot.get('pit_sv_'+wheel.lower()+'p'))
        if got is None or abs(got-value)>.6:return False
    return True


def send_sdk(mode,value):
    # No shared telemetry reader mutation, keyboard focus, or chat text. This
    # object only emits the SDK's registered Windows pit-command message.
    import irsdk
    return irsdk.IRSDK().pit_command(mode,value)


def execute(command,context,live,owner_valid,send=None,timeout=1.0):
    run=(context.get('snapshot') or {}).get('run_id')
    def current():
        _,s=live._current_snapshot()
        if (not run or context.get('connected') is not True or not owner_valid() or live.game!='iracing' or context.get('game')!='iracing'
                or not s or s.get('run_id')!=run or s.get('on_track') is not True or s.get('is_replay') is not False):
            raise ValueError('iRacing의 현재 차량에 연결된 상태에서만 피트 명령을 보낼 수 있습니다.')
        if command.get('compound_kind') and 'compound' not in command:
            raise ValueError('교체할 타이어 종류를 확인하지 못했습니다.')
        return s
    sent=[];send=send or send_sdk
    try:
        with live.lock:
            current()
            if applied(command,current()):
                return {'result':'command_applied','message':command['name']+' · 이미 같은 설정입니다.', 'sent':[]}
            for mode,value in command['calls']:
                current()
                if not send(mode,value):raise OSError('iRacing 명령 전송 실패')
                sent.append([mode,value])
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            with live.lock:
                if applied(command,current()):
                    return {'result':'command_applied','message':command['name']+' · 게임 반영 확인.', 'sent':sent}
            time.sleep(.05)
        return {'result':'command_unconfirmed','message':command['name']+' · 전송했지만 게임 반영은 확인되지 않았습니다. 피트 설정을 확인하세요.', 'sent':sent}
    except (ValueError,OSError,RuntimeError) as exc:
        return {'result':'command_unconfirmed' if sent else 'command_blocked',
                'message':str(exc)+(' 일부 명령을 이미 전송했습니다. 피트 설정을 확인하세요.' if sent else ''),'sent':sent}
