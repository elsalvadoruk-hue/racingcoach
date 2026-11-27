"""Bounded Korean clause grammar for measured, read-only race questions.

Every added route consumes a whole clause. Targets, units and temporal cues
remain explicit; this module never guesses an ASR word or emits a pit command.
"""
import re


_PREFIX = re.compile(r'^(?:(?:야|저기요?|혹시|있잖아|있잖아요|음|어|코치야|그러니까|그러면|그럼|제발|좀)[\s,.!?]+)+')
_COMPACT_PREFIX = re.compile(r'^(?:야|저기요?|혹시|있잖아|코치야)(?=지금|현재|연료|기름|앞차|뒤차|이번|레이스|경기|내|나|피트|인시던트|끝까지)')
_REQUEST = re.compile(r'(?:좀)?(?:알려(?:주실래|줄래|주세요|줘|주겠어|주시겠어|주실수있나|주실수있을까)|말해(?:주실래|줄래|주세요|줘|주시겠어|주실수있나|주실수있을까)|부탁해|부탁드립니다|알고싶어|궁금해)(?:요)?$')
_STYLE = re.compile(r'(?P<style>짧게|간단하게|간단히|간략하게|자세하게|자세히|상세하게|상세히)(?=(?:좀)?(?:알려|말해|보고|설명|부탁)|$)')
_TARGET_ALIASES = (
    (re.compile(r'뒤(?:에서|에)(?:따라오는|쫓아오는|따라붙는|오는|달리는|붙어있는|있는)차(?:량)?'), '뒤차'),
    (re.compile(r'앞(?:에서|에)(?:달리는|가고있는|가는|있는)차(?:량)?'), '앞차'),
)
_FORBIDDEN = re.compile(r'알려주지|말하지|말해주지|하지마|(?:잡|쫓|따라가|답해)지마|안(?:알려|말해|설명해|답해)|그만|됐어|취소|설정|예약|교체|바꿔|꺼줘|켜줘|보내줘|해제')
_UNSUPPORTED = re.compile(r'온도|압력|공기압|마모|날씨|브레이크|바이어스|수리|데미지|손상|아이레이팅|라이선스|어제|지난주|지난경기')
_RIVAL = re.compile(r'앞차|뒤차|선두|일등|1등|다른차|상대차|전방차량|후방차량')
_MULTIPLE = re.compile(r'그리고|이랑|(?:앞차|뒤차)와|(?:연료|기름)와에너지')
_PARTICLE = r'(?:은|는|이|가|을|를|도)?'
_NOW = r'(?:(?:지금|현재|이번|오늘|요즘)(?:은|는)?)?'
_SELF = r'(?:(?:내가|나는|제가|저는|내|나|저|우리)(?:지금|현재)?)?'
_QUERY = r'(?:이야|야|인데|인데요|이냐|냐|니|지|죠|인가|인가요|임|일까|일까요|겠어|겠냐|겠지|겠죠|나요|요)?'
_CAN = r'(?:가능(?:해|하냐|하니|할까|한가|하겠어)?|(?:갈|할|버틸|끝낼)수(?:는)?(?:있(?:어|냐|니|나|을까|겠어|겠냐)|없(?:어|냐|니|을까))|되(?:겠어|겠냐|겠지|냐|니|나|는거야)|돼|가(?:냐|니|겠어|겠냐))'
_FINISH = r'(?:완주(?:할때까지|까지|하려면|하려고|할려면)?|끝까지(?:가려면|가려고)?|결승까지|체커까지|경기끝까지|레이스끝까지)'
_FUEL = r'(?:(?:이|지금|현재|남은|남아있는|남아있는내)(?:만큼(?:의)?)?)*(?:연료|기름)'
_FUEL_NOUN = _FUEL + r'(?:으로|로|은|는|이|가|을|를|도)?'
_LAPS = r'(?:몇|얼마나많은)(?:랩|바퀴)(?:이나|나|를|가|는|야)?'
_AMOUNT = r'(?:몇리터|얼마나|얼마|어느정도)'
_TIME = r'(?:최근|지난|직전|방금)(?:의)?'
_GAP = r'(?:간격|거리|차이)(?:가|는|를|도)?'


def _compile(*patterns):
    # Subject/time framing and a request suffix are orthogonal to the quantity.
    return tuple(re.compile(_NOW + _SELF + '(?:' + pattern + ')' + _QUERY) for pattern in patterns)


def _comparison(noun, metric):
    self_subject = r'(?:내가|나는|제가|저는|내|나|저)'
    relation = noun + r'(?:보다|한테|에비해서|와비교하면|랑비교해서)'
    place = r'(?:어디서|어디에서|어느(?:코너|구간)에서|어떤(?:코너|구간)에서)'
    term = (r'(?:빠른거야|빠른건데|빠르냐|빨라|이득봐|시간(?:을)?벌(?:어|고있(?:어|냐|니))|잘타)' if metric == 'gain'
            else r'(?:느린거야|느린건데|느리냐|느려|시간(?:을)?잃(?:어|고있어)|손해(?:봐|보고있어)|손실이나)')
    return _compile(
        rf'(?:{self_subject})?{relation}(?:{self_subject})?{place}(?:더|좀더)?{term}',
        rf'{place}(?:{self_subject})?{relation}(?:더|좀더)?{term}',
    )


_RULES = {
    'fuel_finish': _compile(
        _FUEL_NOUN + r'(?:은|는)?(?:좀)?(?:괜찮(?:아|냐|니|겠어|겠지)|충분(?:해|하냐|하니|하겠냐|하겠어|한거맞지|한가|할까)|부족하지는않을까|모자라지는않을까)',
        _FUEL_NOUN + r'(?:이걸로|이만큼으로|이대로)?' + _FINISH + r'(?:는)?' + _CAN,
        _FINISH + _FUEL_NOUN + _CAN,
        r'(?:이대로타면|지금처럼타면|이페이스면)' + _FUEL_NOUN + r'(?:모자라는거아냐|부족한거아냐|모자라지않아|부족하지않아)',
        _FUEL_NOUN + r'(?:더)?(?:안넣고|넣지않고)(?:끝까지|완주)?' + _CAN,
        _FUEL_NOUN + r'(?:더)?넣을필요(?:가)?(?:없어|있어|없냐|있냐)',
        _FINISH + _FUEL_NOUN + r'(?:충분해|충분할까|부족해|모자라|모자라겠어)',
        _FUEL_NOUN + r'(?:갖고|가지고|남은걸로|이만큼갖고)' + _FINISH + _CAN,
    ),
    'fuel_add': _compile(
        _FINISH + r'(?:는)?(?:' + _FUEL_NOUN + r')?' + _AMOUNT + r'(?:가)?(?:더)?(?:필요해|필요한데|필요하냐|필요할까|있어야돼|있어야해|넣어야돼|넣어야해|넣으면돼)',
        r'(?:(?:다음|이번)?피트(?:에서|에))?' + _FUEL_NOUN + _AMOUNT + r'(?:를)?(?:더)?(?:넣으면돼|넣어야돼|넣어야해|채워야돼|채워야해)',
        r'(?:다음|이번)?피트에서' + _AMOUNT + r'(?:더)?급유해야(?:해|돼)',
        r'(?:추가로)?(?:필요한|추천하는|필요)?급유량(?:을|은|이)?',
        _FINISH + r'부족한' + _FUEL_NOUN + _AMOUNT,
        _FUEL_NOUN + _AMOUNT + r'(?:더)?(?:넣어야|있어야|채워야)(?:완주(?:하냐|해|할수있어)|끝까지(?:가냐|가|갈수있어))',
    ),
    'fuel_range': _compile(
        _FUEL_NOUN + _LAPS + r'(?:더)?(?:버틸수있(?:어|냐)|갈수있(?:어|냐)|탈수있(?:어|냐)|달릴수있(?:어|냐)|남았(?:어|는데|냐))',
        _FUEL_NOUN + r'(?:떨어지기까지|다쓰기까지)' + _LAPS,
        _FUEL_NOUN + r'다쓰려면' + _LAPS + r'남았(?:어|는데|냐)',
    ),
    'fuel_average': _compile(
        r'(?:한바퀴에|한랩에|랩당|랩마다|한바퀴돌때)' + _FUEL_NOUN + _AMOUNT + r'(?:씩)?(?:쓰고있어|쓰냐|써|먹어|소비해|나가)',
        _FUEL_NOUN + r'(?:랩마다|한바퀴에|한랩에)' + _AMOUNT + r'(?:씩)?(?:쓰고있어|써|먹어|소비해|나가)',
        r'(?:랩당|한바퀴당)' + _FUEL_NOUN + r'소비(?:가|량은)?' + _AMOUNT,
    ),
    'fuel': _compile(_FUEL_NOUN + r'(?:잔량(?:을|은|이)?|몇리터|얼마나들었냐|얼마나들어있어|얼마나가지고있어)'),
    'closing_behind': _compile(
        r'뒤차(?:가|는)?(?:나보다|저보다|내차보다)?(?:랩당|한바퀴에)?(?:얼마나|몇초)(?:씩)?(?:더)?(?:빨라|빠른데|빠르냐|빨라졌어)',
        r'뒤차(?:랑|와)?간격(?:이|은|는)?(?:랩당|한바퀴에)(?:얼마나|몇초)(?:씩)?(?:줄어|좁혀져|줄고있어)',
    ),
    **{f'{side}_{metric}': _comparison(noun, metric)
       for side, noun in [('ahead', '앞차'), ('behind', '뒤차')]
       for metric in ('gain', 'loss')},
    'defend_time': _compile(
        r'뒤차(?:한테|에게)?(?:내가|나)?잡히(?:겠어|겠냐|겠지|냐|나)',
        r'뒤차(?:가|는)?(?:나를|날|나)?(?:언제쯤|언제|결승전에|경기끝나기전에)(?:따라)?(?:붙겠어|잡겠어|따라잡겠어|붙어|잡아)',
        r'뒤차(?:가|는)?(?:몇랩|몇바퀴)(?:뒤에|후에)(?:나를|날)?(?:잡아|따라잡아|따라붙어)',
        r'뒤차(?:가|는)?(?:나|나를|날)?(?:따라잡으려면|붙으려면)(?:몇랩|몇바퀴)(?:이나)?걸려',
        r'뒤차(?:가|는)?언제(?:정도|쯤)?(?:따라)?붙을것같아',
    ),
    'catch_ahead': _compile(
        r'앞차(?:를|한테|에게)?(?:따라잡을수|잡을수)(?:있(?:어|겠어|냐)|없어)',
        r'앞차(?:한테|에게)?(?:붙으려면|따라잡으려면)(?:몇랩|몇바퀴)(?:이나)?걸려',
        r'앞차(?:를)?(?:쫓아갈|따라갈|추격할)만(?:해|하냐|하겠어)',
    ),
    **{side: _compile(
        noun + r'(?:랑|와|까지|는|가)?(?:지금)?(?:몇초|얼마나)(?:차이(?:가)?나|떨어져있어|벌어졌는데|가까이왔는데|차이나는데|뒤에있어|앞에있어)',
        noun + r'(?:랑|와|까지|의)?(?:지금)?' + _GAP + r'(?:몇초|얼마나|얼마|어느정도)?',
    ) for side, noun in [('ahead', '앞차'), ('behind', '뒤차')]},
    'ahead_lap': _compile(r'앞차(?:의|가|는)?' + _TIME + r'(?:한바퀴|랩|랩타임)(?:기록)?(?:이|은|는)?(?:몇초(?:나왔어|찍었어)?|얼마)?'),
    'last': _compile(
        _TIME + r'(?:한바퀴|랩|랩타임)(?:내)?(?:기록)?(?:이|은|는)?(?:몇초(?:찍은거야|찍었어|나왔어)?|얼마였는데|얼마)?',
        r'(?:내|나|제가|내가)' + _TIME + r'(?:한바퀴|랩|랩타임)(?:기록)?(?:이|은|는)?(?:몇초(?:찍은거야|찍었어|나왔어)?|얼마)?',
    ),
    'best': _compile(
        r'(?:이번세션)?(?:내|내가)?(?:제일잘탄랩|가장잘탄랩|최고기록|최고랩기록)(?:이|은|는)?(?:몇초|얼마)?',
    ),
    'position': _compile(r'(?:지금)?(?:내클래스에서)?(?:몇번째로달리고있어|몇위|몇등)'),
    'laps': _compile(_FINISH + _LAPS + r'(?:더)?(?:돌아야돼|돌아야해|남았어|남았는데)',
                     r'(?:이|이번)?(?:경기|레이스)끝나려면' + _LAPS + r'남았(?:어|는데|냐)'),
    'race_time': _compile(r'(?:경기|레이스|세션)끝나려면몇분(?:더)?(?:있어야해|남았어|남았는데)',
                          r'(?:경기|레이스|세션)끝나기까지몇분남았(?:어|니|냐|는데)',
                          _FINISH + r'시간(?:이|은)?얼마나남았(?:어|냐|는데)'),
    'pit_rejoin': _compile(r'피트(?:에)?들어갔다나오면몇(?:등|위)(?:이겠어|겠어|야|일까)',
                           r'피트하면복귀순위(?:가|는)?어떻게(?:돼|되는데)'),
    'pit_transit': _compile(r'피트(?:에)?(?:한번)?통과하는데몇초걸려',
                            r'피트(?:에)?(?:한번)?들렀다오는데시간얼마나(?:버려|잃어|걸려)',
                            r'피트(?:에)?들어갔다나오면서잃는시간(?:은|이)?얼마'),
    'pit_speed': _compile(r'피트(?:레인|로드)?에서몇(?:킬로|키로|킬로미터)까지달려도(?:돼|되냐)',
                           r'피트속도제한(?:이|은)?어떻게(?:되는데|돼)'),
    'incidents': _compile(r'인시던트(?:가|는)?(?:지금)?몇점쌓였어', r'인시던트여유(?:가|는)?몇점'),
    'incident_limit': _compile(r'인시던트몇점까지허용(?:돼|되는데)', r'인시던트몇점에서실격'),
    **{f'report_{topic}': _compile(
        noun + r'(?:상황|상태|정보' + ('|전략' if topic == 'pit' else '') + r')?(?:이|가|을|를|은|는)?(?:어때|어떻게돌아가고있어|(?:좀)?정리(?:해서|해줘)?|종합해서|보고(?:해줘)?)',
        noun + r'(?:상황|상태|정보' + ('|전략' if topic == 'pit' else '') + r')(?:이|가|을|를|은|는)?',
    ) for topic, noun in [('ahead', '앞차'), ('behind', '뒤차'), ('race', '(?:레이스|경기)'), ('pit', '피트'), ('fuel', '연료')]},
    'race_focus': _compile(r'(?:뭘|무엇을)신경써야(?:돼|해)', r'공격하는게좋아방어하는게좋아', r'(?:이번랩)?뭐부터챙겨야(?:해|돼)'),
    'fuel_save': _compile(
        r'(?:연료(?:를)?|기름(?:을)?)?(?:아껴서|절약해서|아끼면|절약하면)(?:피트안하고|피트없이|급유없이|끝까지|완주)?(?:갈수는없어|갈수있어|가능해|가능할까|완주할수있어)',
        _FINISH + r'(?:연료(?:를)?|기름(?:을)?)?(?:랩당|한바퀴에)얼마나(?:써야해|써야돼|아껴야해|줄여야해)',
    ),
    'corner_repeat': _compile(r'같은코너에서계속(?:잃어|손해봐|손실이나)',
                              r'(?:지난랩)?손실(?:이|은)?반복되고있어',
                              r'손실(?:이|은)?이번랩만그런거야'),
}


def _blocked(note='', reason='unsupported'):
    return {'topic': None, 'reason': reason, 'candidates': [], 'correction_note': note}


def understand(text, game, legacy):
    """Return a full-clause match, or use the existing guarded classifier.

The legacy parser remains responsible for observed ASR corrections. Only
formatting wrappers and explicit equivalent target descriptions are removed.
    """
    raw = _PREFIX.sub('', text.strip())
    t = re.sub(r'[^가-힣a-z0-9]', '', raw.lower())
    t = _COMPACT_PREFIX.sub('', t)
    for pattern, target in _TARGET_ALIASES:
        t = pattern.sub(target, t)
    # Restrict rival correction to an explicit source and replacement subject.
    # Bare "X 말고 Y" is left to the finite-lived dialogue state.
    corrected = re.match(r'^(앞차|뒤차)(?:가아니라|가아니고|말고)(앞차|뒤차)(.+)$', t)
    excluded = None
    if corrected and corrected[1] != corrected[2]:
        excluded, t = corrected[1], corrected[2] + corrected[3]
    if _FORBIDDEN.search(t):
        return _blocked('말하지 말라는 요청이나 설정·명령을 정보 질문으로 바꾸지 않습니다.')
    if '말고' in t or '아니고' in t or '아니라' in t:
        return legacy(text, game)
    if _UNSUPPORTED.search(t):
        original = legacy(text, game)
        return original if original.get('correction_note') else _blocked('이 질문의 정보 종류 또는 과거 시점은 아직 지원하지 않습니다.')
    if _MULTIPLE.search(t):
        return legacy(text, game)
    if re.search(r'\d', t.replace('1등', '')):
        return _blocked('특정 수치를 가정한 질문을 현재 측정값에 대한 질문으로 바꾸어 답하지 않습니다.')
    if re.search(r'(?:두|둘|세|셋|네|넷|다섯|여섯|일곱|여덟|아홉|열)(?:번|랩|바퀴|리터).*(?:하면|넣으면|있으면|남았으면|일때|가정)', t):
        return _blocked('특정 수치를 가정한 질문을 현재 측정값에 대한 질문으로 바꾸어 답하지 않습니다.')
    # Avoid silently using the player's fuel or the opposite comparison.
    if _RIVAL.search(t) and re.search(r'연료|기름|에너지|연비', t):
        return _blocked('다른 차량의 연료·에너지 정보는 확인할 수 없습니다.')
    if re.search(r'앞차|뒤차', t) and re.search(r'베스트|최고|제일빠른|가장빠른', t):
        return _blocked('다른 차량의 최고 랩 기록 질문은 아직 지원하지 않습니다.')
    if re.search(r'뒤차보다(?:내가|나는|나|제가|저는|저)|(?:내가|나는|제가|저는|나|저)뒤차보다|뒤차(?:가|는)?(?:나보다|저보다|내차보다).*느', t) and not re.search(r'어디서|어디에서|구간|코너', t):
        return _blocked('질문한 비교 방향을 현재 지원하는 뒤차 추격 속도로 바꾸어 답하지 않습니다.')
    if re.search(r'앞차(?:가|는).*(?:나를|날|저를).*(?:잡|쫓|붙)|앞차(?:가|는)(?:나|저)(?:따라)?잡', t):
        return _blocked('앞차가 나를 추격하는 질문을 내가 앞차를 추격하는 질문으로 바꾸어 답하지 않습니다.')
    if re.search(r'(?:내가|나는|제가|저는|나|저)뒤차(?:한테|에게|를).*(?:좁히|줄이|따라잡|잡을|쫓)', t):
        return _blocked('질문한 비교 방향을 현재 지원하는 뒤차 추격 속도로 바꾸어 답하지 않습니다.')
    if re.search(r'앞차|뒤차', t) and re.search(r'지난|직전|최근|방금', t) and re.search(r'간격|거리|차이', t):
        return _blocked('최근 랩타임인지 현재 간격인지 한 가지로 다시 말씀해 주세요.')
    modes = [m['style'] for m in _STYLE.finditer(t)]
    style = ('explain' if modes and modes[-1].startswith(('자세', '상세')) else 'short') if modes else None
    framed = _STYLE.sub('', t)
    t = _REQUEST.sub('', framed)
    matches = [topic for topic, patterns in _RULES.items() if any(p.fullmatch(t) for p in patterns)]
    if matches:
        # A report's generic noun phrase can overlap a precise fuel question;
        # exact clause grammars otherwise must agree before giving an answer.
        if len(matches) != 1:
            return {'topic': None, 'reason': 'ambiguous', 'candidates': matches}
        topic = matches[0]
        result = {'topic': topic, 'reason': 'matched', 'candidates': [topic],
                  'language': {'rule': 'whole_clause', 'target': 'behind' if '뒤차' in t else 'ahead' if '앞차' in t else 'player',
                               'metric': topic, 'time': 'recent_lap' if re.search(_TIME, t) else 'current'}}
        if excluded:
            result['language']['excluded_target'] = excluded
    elif excluded:
        # Never route an incomplete correction through keyword fallbacks.
        result = _blocked('정정한 대상에 대해 무엇을 물으셨는지 한 문장으로 말씀해 주세요.')
    else:
        result = legacy(framed, game)
        if not result.get('topic') and framed != t and not result.get('correction_note'):
            result = legacy(t, game)
        if result.get('topic') and (raw != text.strip() or framed != re.sub(r'[^가-힣a-z0-9]', '', text.lower())):
            result['language'] = {'rule': 'question_wrapper', 'metric': result['topic']}
    if style and result.get('topic'):
        result['answer_detail'] = style
    return result
