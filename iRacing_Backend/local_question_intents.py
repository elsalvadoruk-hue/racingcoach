"""Small, auditable Korean question classifier. Never invent a measured answer."""
import re
import unicodedata

VERSION = 8
# Alternatives describe intent, not exact sentences. More than one matching
# intent is deliberately withheld; raw recognition text remains in the log.
EXAMPLES = {
    'race_focus': ['지금 뭐에 집중해야 해', '지금 레이스 전략 알려줘', '지금 공격할까 방어할까'],
    'sof': ['이번 경기 SOF', '소프 얼마야', '클래스 에스오에프 알려줘'],
    'fuel': ['연료 얼마나 남았어', '기름 몇 리터야', '현재 연료량 알려줘'],
    'fuel_range': ['지금 연료로 얼마나 더 갈 수 있음', '기름으로 몇 바퀴 더 타', '연료 몇 랩 남았어'],
    'fuel_average': ['한 랩에 연료 얼마나 먹어', '평균 연비 알려줘', '랩당 소비량'],
    'energy_remaining': ['가상 에너지 얼마나 남았어', '에너지 몇 퍼센트야', '현재 에너지 잔량'],
    'last': ['방금 랩타임', '지난 랩 몇 초야', '방금 몇 초 찍었어'],
    'best': ['내 베스트 얼마야', '세션 최고 랩타임', '오늘 제일 빠른 랩'],
    'theoretical': ['이론상 베스트는', '잘 탄 구간 합치면 몇 초야', '구간 최고 합친 기록'],
    'sector_delta': ['베스트보다 얼마나 빨라', '최근 구간선 델타', '지금 기록 차이'],
    'position': ['지금 몇 등이야', '내 클래스 순위', '나 몇 위야'],
    'laps': ['레이스 몇 랩 남았어', '끝까지 몇 바퀴야', '남은 랩 알려줘'],
    'ahead': ['앞차 간격', '앞차랑 몇 초 차이야', '앞차 얼마나 떨어져 있어'],
    'behind': ['뒤차 간격', '뒤차 얼마나 붙었어', '뒤차랑 몇 초야'],
    'catch_ahead': ['앞차 잡을 수 있어', '몇 랩 뒤에 앞차 따라잡아', '앞차 추격 가능해'],
    'closing_behind': ['뒤차가 랩당 얼마나 좁혀', '뒤차가 얼마나 빨라', '뒤차 추격 속도'],
    'ahead_lap': ['앞차 방금 몇 초 찍었어', '앞차 최근 랩타임', '앞차 지난 랩'],
    'leader_lap': ['클래스 선두 랩타임', '일등 몇 초야', '선두 최근 랩'],
    'corner_loss': ['지난 랩 어디서 날렸어', '어느 코너에서 가장 많이 잃었어', '지난 랩 가장 큰 손실'],
    'corner_gain': ['어디서 제일 많이 줄였어', '지난 랩 잘 탄 코너는', '가장 개선된 코너'],
    'pit_rejoin': ['지금 피트하면 몇 등으로 나와', '피트 후 예상 복귀 순위', '지금 들어가면 몇 위야'],
    'stint_ahead': ['앞차 스틴트 몇 랩째야', '앞차 피트 후 몇 바퀴 탔어', '앞차 타이어 몇 랩 탔어'],
    'stint_behind': ['뒤차 스틴트 얼마나 탔어', '뒤차 피트 후 몇 바퀴야', '뒤차 타이어 몇 랩째야'],
    'stops_ahead': ['앞차 피트 몇 번 했어', '앞차 정차 횟수', '앞차 피트 들어간 횟수'],
    'stops_behind': ['뒤차 피트 몇 번 했어', '뒤차 정차 횟수', '뒤차 피트 들어간 횟수'],
    'pit_transit': ['피트 들어가면 몇 초 손해야', '피트 통과 손실', '피트 지나면 얼마나 잃어'],
    'fuel_finish': ['끝까지 연료 충분해', '완주까지 연료 얼마나 부족해', '이 기름으로 완주할 수 있어'],
    'fuel_add': ['이번 피트에서 연료 몇 리터 넣어', '추가 급유량 알려줘', '연료 얼마나 채워야 해'],
    'race_time': ['경기 몇 분 남았어', '남은 시간 알려줘', '레이스 종료까지 얼마나 남았어'],
    'incidents': ['인시던트 몇 점이야', '인시던트 한도까지 얼마나 남았어', '현재 인시던트 점수'],
    'incident_limit': ['인시던트 한도 몇 점이야', '인시던트 제한 알려줘', '인시던트 몇 점이면 실격이야'],
    'pit_speed': ['피트 제한 속도 얼마야', '피트 속도 제한 알려줘', '피트레인 제한 속도'],
    'defend_time': ['뒤차가 몇 랩 뒤에 따라잡아', '뒤차한테 언제 잡혀', '뒤차가 나를 잡기까지 몇 바퀴야'],
    'ahead_gain': ['앞차보다 어디서 빨라', '앞차보다 빠른 구간', '앞차보다 잘 타는 코너', '앞차보다 내가 어디서 빨라'],
    'ahead_loss': ['앞차보다 어디서 느려', '앞차보다 느린 구간', '앞차한테 어느 코너에서 잃어', '앞차보다 내가 어디서 느려'],
    'behind_gain': ['뒤차보다 어디서 빨라', '뒤차보다 빠른 구간', '뒤차보다 잘 타는 코너', '뒤차보다 내가 어디서 빨라'],
    'behind_loss': ['뒤차보다 어디서 느려', '뒤차보다 느린 구간', '뒤차한테 어느 코너에서 잃어', '뒤차보다 내가 어디서 느려'],
    'report_fuel': ['연료 상황 보고', '연료 종합 보고해줘', '연료 상태 요약해줘'],
    'report_race': ['경기 상황 보고', '레이스 종합 보고해줘', '현재 경기 요약'],
    'report_ahead': ['앞차 상황 보고', '앞차 정보 요약해줘', '앞차 종합 보고해줘'],
    'report_behind': ['뒤차 상황 보고', '뒤차 정보 요약해줘', '뒤차 종합 보고해줘'],
    'report_pace': ['내 랩 기록 보고', '내 페이스 요약해줘', '랩타임 종합 보고해줘'],
    'report_pit': ['피트 전략 보고', '피트 상황 요약해줘', '피트 종합 보고해줘'],
}

# Full-clause matching for the added commands. A word inside an unrelated
# request must not choose an answer; reports are explicit bounded requests.
_ENDING = r'(?:은|는|이|가)?(?:좀)?(?:알려(?:줘|주세요|줄래|줘요)|말해줘|보고해줘|해줘|부탁해|어때|얼마야|몇이야|야|요|나요|니|지|임|줘)?'


def _rival_comparison(noun, terms):
    # Accept the driver's subject in natural positions, without deleting it
    # globally and reversing e.g. "뒤차가 나보다" into "뒤차보다 내가".
    subject = r'(?:내가|나는|제가|저는|나|저)'
    place = r'(?:어디서|어느코너에서|어느구간에서)'
    rival = noun + r'(?:보다|한테)'
    return (f'(?:(?:{subject})?{rival}(?:{place})?'
            f'|{rival}{subject}(?:{place})?|{rival}{place}{subject})'
            + r'(?:더|좀더)?' + terms)


_EXPANDED = {
    'sof': r'(?:이번경기|현재|클래스)?(?:sof|소프|에스오에프)',
    'fuel_finish': r'(?:(?:이|지금|현재)?(?:연료|기름)(?:로)?(?:끝까지|완주)(?:갈|할)?수있(?:어|을까)|(?:끝까지|완주까지|완주)(?:연료|기름)(?:가)?(?:충분(?:해)?|얼마나부족(?:해)?|부족(?:해)?)|(?:연료|기름)(?:가)?(?:끝까지|완주까지)(?:충분(?:해)?|부족(?:해)?))',
    'fuel_add': r'(?:(?:이번)?(?:피트(?:에서|에)?)?(?:연료|기름)(?:를)?(?:몇리터|얼마나)(?:더)?(?:넣어|넣어야해|채워야해|채워)|(?:이번피트|추가|추천)?급유량)',
    'race_time': r'(?:(?:경기|레이스|세션)?(?:남은시간|몇분남았어|시간얼마나남았어)|(?:경기|레이스|세션)(?:종료|끝)(?:까지)?얼마나남았어)',
    'incidents': r'(?:(?:현재|내)?인시던트(?:누적)?(?:점수|몇점(?:이야)?|한도까지얼마나남았어))',
    'incident_limit': r'인시던트(?:한도(?:몇점(?:이야)?)?|제한|몇점이면실격(?:이야)?)',
    'pit_speed': r'피트(?:레인|로드)?(?:제한속도|속도제한)',
    'defend_time': r'뒤차(?:가|한테)?(?:몇랩뒤에따라잡아|언제잡혀|나를잡기까지몇바퀴야|언제따라잡아)',
    **{f'{side}_{metric}': _rival_comparison(noun, terms)
       for side, noun in [('ahead', '앞차'), ('behind', '뒤차')]
       for metric, terms in [('gain', r'(?:빨라|빠른구간|빠른코너|잘타는코너)'),
                             ('loss', r'(?:느려|느린구간|느린코너|잃어)')]},
    **{f'report_{key}': f'{noun}(?:상황|상태|정보|종합|전략)?(?:보고|요약)(?:해줘)?'
       for key, noun in [('fuel', '연료'), ('race', '(?:현재)?(?:경기|레이스)'),
                         ('ahead', '앞차'), ('behind', '뒤차'), ('pace', '(?:내랩기록|내페이스|랩타임)'), ('pit', '피트')]},
}
_EXPANDED = {key: re.compile(r'(?:지금)?' + value + _ENDING) for key, value in _EXPANDED.items()}


def normalize(text):
    text = unicodedata.normalize('NFKC', text).lower()
    return re.sub(r'[^가-힣a-z0-9]', '', text)


# Token boundaries also accept common ASR output with no space before the
# question, but never replace a substring in e.g. 전기차 / 기차역 / 기차가방.
_TERM_END = (r'(?=(?:(?:가|이|은|는|랑|와|과|의|도|를|을|보다|하고|에게|한테))?'
             r'(?:$|[\s,.!?]|간격|몇|얼마|랩|스틴트|피트|타이어|추격|속도|연료|기름|에너지))')


def _term(pattern):
    return re.compile(r'(?<![가-힣a-z0-9])(?:' + pattern + ')' + _TERM_END, re.IGNORECASE)


# Equivalent vocabulary, not guessed phonetic edits. Keep each rule auditable.
_ALIASES = tuple((_term(pattern), target) for pattern, target in [
    (r'뒷\s*차(?:량)?|뒤\s*차량|뒤(?:쪽|에\s*있는)\s*차(?:량)?|후방\s*차량', '뒤차'),
    (r'앞\s*차량|앞(?:쪽|에\s*있는)\s*차(?:량)?|전방\s*차량', '앞차'),
    (r'기름', '연료'),
    (r'랩\s+타임|렙\s*타임|lap\s*time', '랩타임'),
    (r'렙', '랩'),
])
_ALIASES += ((re.compile(r'(?<![가-힣a-z0-9])렙(?=당|째)'), '랩'),)
_TRAIN = _term('기차')
_TRAIN_CONTEXT = re.compile(r'간격|몇초|얼마나붙|랩당.*(?:좁|줄|빨|빠)|추격속도|피트|스틴트|타이어.*랩')
_TRAIN_LITERAL = re.compile(r'기차역|전기차|열차|철도|승차|시간표|출발|도착|표값|기차표|역까지|기차놀이')
_TRAIN_UNSUPPORTED = re.compile(r'순위|몇등|몇위|랩타임|베스트|델타|코너')
_BEHIND_TOPICS = frozenset(['behind', 'closing_behind', 'stops_behind', 'stint_behind'])

# Confirmed transcript pairs: first two from locally recorded, user-labelled
# questions; the others from the frozen synthetic model comparison. Full-clause
# context deliberately prevents broad replacements such as 간결하게 -> 간격하게.
_OBSERVED_CORRECTIONS = tuple((re.compile(context), re.compile(source), target, topic)
    for context, source, target, topic in [
        (r'(?:지금|현재)?(?:열려|연류)(?:가)?얼마나남았어(?:요)?', r'열려|연류', '연료', 'fuel'),
        (r'(?:내)?(?:방금|지난|직전|최근)레타임(?:알려줘|얼마야|몇초야)?', r'레\s*타임', '랩타임', 'last'),
        (r'(?:지금)?피트후(?:에)?예상복귀수(?:알려줘)?', r'복귀\s*수', '복귀 순위', 'pit_rejoin'),
        (r'(?:지금)?뒤차(?:랑|와)?간결(?:알려줘)?', r'간결', '간격', 'behind'),
        (r'(?:지금)?앞착(?:이|랑|와)?간격(?:알려줘)?', r'앞착', '앞차', 'ahead'),
        (r'클래스선도(?:최근|지난|방금)?랩타임(?:알려줘)?', r'선도', '선두', 'leader_lap'),
    ])


def classify(text, game='iracing'):
    """Preserve raw input at the call site; return only justified corrections.

    No fuzzy nearest-question fallback: missing targets, negation and rival
    quantities must not become answers about the player's car. All candidate
    corrections go back through the same supported-question guards.
    """
    if not isinstance(text, str) or len(text) > 500 or len(normalize(text)) > 200:
        return {'topic': None, 'reason': 'unrecognized', 'candidates': [],
                'normalized_text': '', 'corrections': [], 'correction_status': 'none'}
    canonical = unicodedata.normalize('NFKC', text).strip()
    changes = []
    for pattern, target in _ALIASES:
        def replace(match):
            changes.append({'from': match.group(), 'to': target, 'kind': 'synonym',
                            'reason': '레이스 동의어·표기 통일'})
            return target
        canonical = pattern.sub(replace, canonical)
    status, note = ('normalized' if changes else 'none'), ''
    for context, source, target, topic in _OBSERVED_CORRECTIONS:
        if context.fullmatch(normalize(canonical)):
            proposed = source.sub(target, canonical)
            if proposed != canonical and _classify(proposed, game)['topic'] == topic:
                changes.append({'from': source.search(canonical).group(), 'to': target,
                                'kind': 'context', 'reason': '확인된 오인식과 질문 전체 문맥 일치'})
                canonical, status = proposed, 'corrected'
                break  # Do not cascade guessed substitutions.
    result = _classify(canonical, game)
    # This confusion was reported by a user. It is not a general rule that
    # every similar-sounding Korean word should turn into a racing noun.
    if '기차' in normalize(canonical):
        compact = normalize(canonical)
        proposed = _TRAIN.sub('뒤차', canonical)
        candidate = _classify(proposed, game)
        conflict = bool(re.search('앞차|뒤차|선두|일등|1등', compact))
        # Asking for a gap together with a pit/stint quantity is two questions,
        # even when the recognizer omitted the conjunction between them.
        mixed = bool(re.search('간격|몇초|거리|차이', compact) and re.search('피트|정차|스틴트|타이어', compact))
        allowed = (len(_TRAIN.findall(canonical)) == 1 and not conflict
                   and not mixed and not _TRAIN_UNSUPPORTED.search(compact)
                   and not _TRAIN_LITERAL.search(compact) and not re.search(r'기차\s+표', canonical)
                   and _TRAIN_CONTEXT.search(compact) and candidate['topic'] in _BEHIND_TOPICS)
        if allowed:
            canonical, result, status = proposed, candidate, 'corrected'
            changes.append({'from': '기차', 'to': '뒤차', 'kind': 'context',
                            'reason': '등록된 오인식 표현과 뒤차의 간격·추격·피트 문맥 일치'})
        else:
            status = 'withheld'
            result = {'topic': None, 'reason': 'ambiguous' if conflict or mixed else 'unsupported', 'candidates': []}
            note = '레이스 문맥이 부족하거나 대상이 겹쳐 단어 보정을 보류했습니다.'
    return {**result, 'normalized_text': canonical, 'corrections': changes,
            'correction_status': status, 'correction_note': note}


def _classify(text, game='iracing'):
    t = normalize(text)
    if not t or len(t) > 200:
        return {'topic': None, 'reason': 'unrecognized', 'candidates': []}
    if t == '랩타임':
        # Recorded leader question lost its subject and became only "랩타임".
        # Require a time/subject cue instead of answering with the player's lap.
        return {'topic': None, 'reason': 'unsupported', 'candidates': []}
    has = lambda p: bool(re.search(p, t))
    # Recorded rival nouns can lose their first consonant. Without a reliable
    # target, never answer with the driver's own lap time.
    if re.match(r'^(?:아차|합차)(?:의|가|는)?(?:최근|지난|방금|직전|랩)', t):
        return {'topic': None, 'reason': 'unsupported', 'candidates': []}
    # Requests about another driver, comparisons, negation and unsupported
    # quantities must not silently turn into the player's own fuel/position.
    if has(r'말하지|알려주지|묻는게아니|아니고|하지마|취소|됐어|그만|말고'):
        return {'topic': None, 'reason': 'unsupported', 'candidates': []}
    if re.fullmatch(r'(?:지금|현재)?(?:뭐에집중(?:해야해|할까|해)|(?:레이스)?전략(?:알려줘|알려주세요)|공격할까방어할까)(?:요)?', t):
        return {'topic':'race_focus', 'reason':'matched', 'candidates':['race_focus']}
    expanded = [key for key, pattern in _EXPANDED.items()
                if pattern.fullmatch(t) or t in {normalize(s) for s in EXAMPLES[key]}]
    if expanded:
        if expanded == ['sof'] and game != 'iracing':
            return {'topic':None,'reason':'unsupported_game','candidates':expanded}
        return {'topic': expanded[0] if len(expanded) == 1 else None,
                'reason': 'matched' if len(expanded) == 1 else 'ambiguous', 'candidates': expanded}
    # An unmatched section comparison must not fall back to a gap/closing-rate
    # answer just because "뒤차" and "빨라" occur somewhere in the sentence.
    if has(r'앞차|뒤차|전방차량|후방차량') and has(r'보다|한테') and has(r'어디서|어느코너|어느구간|구간|코너'):
        return {'topic': None, 'reason': 'unsupported', 'candidates': []}
    if has(r'그리고|이랑|하고|랑.*(?:알려|몇|얼마).*(?:도알려|도몇|도얼마)'):
        return {'topic': None, 'reason': 'ambiguous', 'candidates': []}
    # A familiar keyword inside an unsupported request is not an answerable
    # question (e.g. tyre pressure or another driver's remaining fuel).
    if has(r'온도|압력|공기압|마모|날씨|브레이크|바이어스|수리|데미지|손상'):
        return {'topic': None, 'reason': 'unsupported', 'candidates': []}
    if has(r'\d+위|(?:[2-9]|[1-9]\d+)(?:위|등)|\d+번차|전체선두|종합선두|다른클래스|아이레이팅|라이선스'):
        return {'topic': None, 'reason': 'unsupported', 'candidates': []}
    if has(r'선두|일등|1등|앞차|뒤차') and has(r'베스트|최고|가장빠른|제일빠른'):
        return {'topic': None, 'reason': 'unsupported', 'candidates': []}
    if has(r'인시던트|급유|제한속도|속도제한|상황보고|종합보고|요약'):
        return {'topic': None, 'reason': 'unsupported', 'candidates': []}
    if has(r'선두|일등|1등|앞차|뒤차|전방차량|후방차량') and has(r'연료|기름|에너지|연비'):
        return {'topic': None, 'reason': 'unsupported', 'candidates': []}
    if has('앞차|전방차량') and has('뒤차|후방차량'):
        return {'topic': None, 'reason': 'ambiguous', 'candidates': ['ahead', 'behind']}
    found = set()
    exact = [k for k, samples in EXAMPLES.items() if t in {normalize(s) for s in samples}]
    if exact:
        found.update(exact)
    else:
        ahead, behind = has('앞차|전방차량'), has('뒤차|후방차량')
        fuel, energy = has('연료|기름'), has('에너지')
        laps = has('랩|바퀴')
        if fuel and not (ahead or behind):
            if has('끝까지|완주|충분|부족|급유|넣|채워|목표|절약|아껴'):
                pass
            elif has('랩당|한랩에|한바퀴에|평균|소비|연비|먹'):
                found.add('fuel_average')
            elif laps or has('더갈|더탈|갈수|갈수있|얼마나더|주행가능|버틸|버텨|달릴|얼마나(?:가(?:요|나요|니|냐|죠|지)?$|갈)'):
                found.add('fuel_range')
            elif has('남|잔량|몇리터|얼마|현재|지금'):
                found.add('fuel')
        if has('연비') and not (ahead or behind):
            found.add('fuel_average')
        if energy and not (ahead or behind) and not has('소비|랩당|목표|완주|충분'):
            found.add('energy_remaining')
        for side, present in [('ahead', ahead), ('behind', behind)]:
            if not present:
                continue
            if has('피트|정차|스틴트|타이어'):
                if has('몇번|횟수') and has('피트|정차'):
                    found.add('stops_' + side)
                elif has('스틴트') or (laps and has('피트후|타이어')):
                    found.add('stint_' + side)
            elif side == 'ahead' and has('잡|추격'):
                found.add('catch_ahead')
            elif side == 'behind' and has('랩당|좁히|좁혀|줄이|빨라|빠르|추격속도'):
                found.add('closing_behind')
            elif side == 'ahead' and has('랩타임|최근랩|지난랩|방금.*초'):
                found.add('ahead_lap')
            elif side == 'behind' and has('랩타임|최근랩|지난랩|방금.*초'):
                pass  # Rear-car lap time is not one of the prepared answers.
            elif has('간격|몇초|얼마나.*(떨어|붙)|차이|거리'):
                found.add(side)
        if not (ahead or behind or fuel or energy):
            if has('피트|복귀|들어가면'):
                if has('몇등|몇위|순위'):
                    found.add('pit_rejoin')
                elif has('손실|손해|잃|통과시간'):
                    found.add('pit_transit')
            elif has('선두|클래스1등|일등|1등') and has('랩|몇초|기록'):
                found.add('leader_lap')
            elif has('이론|구간.*(합|모으)|잘탄.*합'):
                found.add('theoretical')
            elif has('델타|베스트보다|베스트.*차이|기록차이'):
                found.add('sector_delta')
            elif has('코너|어디서|어느구간|지난랩') and has('잃|날렸|손실|느렸'):
                found.add('corner_loss')
            elif has('코너|어디서|어느구간') and has('줄였|개선|잘탄|가장좋'):
                found.add('corner_gain')
            elif has('베스트|최고랩|가장빠른랩|제일빠른랩'):
                found.add('best')
            elif has('랩타임|지난랩.*초|방금.*초|직전랩'):
                found.add('last')
            elif has('순위|몇등|몇위'):
                found.add('position')
            elif laps and has('남|끝까지|종료까지'):
                found.add('laps')
    candidates = sorted(found)
    topic = candidates[0] if len(candidates) == 1 else None
    reason = 'matched' if topic else 'ambiguous' if candidates else 'unsupported'
    if topic == 'energy_remaining' and game != 'lmu':
        topic, reason = None, 'unsupported_game'
    return {'topic': topic, 'reason': reason, 'candidates': candidates}
