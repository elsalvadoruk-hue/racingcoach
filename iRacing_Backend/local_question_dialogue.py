"""Short, owner-scoped question context. Never plans or executes game commands."""
import re
import time
import uuid

from local_question_intents import classify, normalize

CONFIRM_SECONDS = 8
FOLLOWUP_SECONDS = 20
RECORD_SECONDS = 25
PROMPT_SECONDS = 45

PROMPTS = {
    'remaining': ('경기 남은 시간 말씀이신가요?', 'race_time', ['race_time', 'laps', 'fuel']),
    'gap': ('앞차 간격 말씀이신가요?', 'ahead', ['ahead', 'behind']),
    'lap': ('내 최근 랩타임 말씀이신가요?', 'last', ['last', 'ahead_lap', 'leader_lap']),
    'pace': ('완주할 연료가 충분한지 물으셨나요?', 'fuel_finish', ['fuel_finish', 'catch_ahead']),
}
YES = re.compile(r'^(?:응|어|네|예|넵|맞아|맞아요|맞습니다|그래|그래요|그렇지|그렇죠|그거야|그거요|응맞아|어맞아|네맞아요|맞아맞아)$')
NO = re.compile(r'^(?:아니|아냐|아니야|아니요|아닙니다|그게아니야|그건아니야)$')
CANCEL = re.compile(r'^(?:아니|아냐|응|어)?(?:됐어|됐어요|그만|그만해|취소|취소해|안물어볼게|넘어가|괜찮아)$')
PREFIX = re.compile(r'^(?:(?:응|어(?!디|제|떤|느|떻|때)|네|예|맞아요|맞아|그래요|그래|아니야|아니요|아니|아냐)(?:근데|그런데)?|그럼|그러면|근데|그런데)+')
CONTEXT_BLOCK = re.compile(r'말하지|말해주지|알려주지|하지마|그만|취소|설정|예약|교체|바꿔|아니야|아니라고|그리고|하지않|하라는거')
SWITCH = {
    'ahead':'behind', 'behind':'ahead', 'report_ahead':'report_behind', 'report_behind':'report_ahead',
    'stint_ahead':'stint_behind', 'stint_behind':'stint_ahead', 'stops_ahead':'stops_behind',
    'stops_behind':'stops_ahead', 'catch_ahead':'defend_time', 'defend_time':'catch_ahead',
    'ahead_gain':'behind_gain', 'behind_gain':'ahead_gain', 'ahead_loss':'behind_loss', 'behind_loss':'ahead_loss',
}


def identity(owner, context, consent):
    snapshot = context.get('snapshot') or {}
    return (owner, context.get('game'), snapshot.get('run_id'), consent)


def rival(context, topic):
    side = 'behind' if 'behind' in topic or topic == 'defend_time' else 'ahead' if 'ahead' in topic else None
    return (context.get('pit_wall') or {}).get(side, {}).get('idx') if side else None


def result(topic=None, reason='matched', **extra):
    return {'topic':topic, 'reason':reason, 'candidates':[topic] if topic else [], **extra}


class Dialogue:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.state = None

    def clear(self):
        old, self.state = self.state, None
        return old

    def valid(self, scope):
        s = self.state
        if s and (s['scope'] != scope or self.clock() >= s['expires']):
            self.clear()
        return self.state

    def public(self):
        s = self.state
        if not s:
            return None
        return {'id':s['id'], 'kind':s['kind'], 'state':'awaiting_confirmation' if s['kind']=='clarification' else 'followup',
                'prompt':s.get('prompt',''), 'remaining_ms':max(0, round((s['expires']-self.clock())*1000)),
                'timeout_ms':(CONFIRM_SECONDS if s['kind']=='clarification' else FOLLOWUP_SECONDS)*1000,
                'needs_ready':not s['ready'], 'capture_id':s['capture_id']}

    def offer(self, scope, capture_id, topic, *, prompt='', choices=(), context=None, detail='default'):
        kind = 'clarification' if prompt else 'followup'
        losses = ((context or {}).get('corner_coach') or {}).get('last_lap', {}).get('losses') or []
        self.state = {'id':str(uuid.uuid4()), 'scope':scope, 'capture_id':capture_id, 'topic':topic,
                      'kind':kind, 'prompt':prompt, 'choices':list(choices), 'detail':detail,
                      'rival':rival(context or {}, topic or ''), 'expires':self.clock()+PROMPT_SECONDS,
                      'corner':losses[0].get('id') if losses else None,
                      'ready':False, 'reservation':None, 'created':self.clock()}
        return self.public()

    def ready(self, scope, dialogue_id):
        s = self.valid(scope)
        if not s or s['id'] != dialogue_id:
            return None
        if not s['ready'] and not s['reservation']:
            s['ready'] = True
            s['expires'] = self.clock() + (CONFIRM_SECONDS if s['kind']=='clarification' else FOLLOWUP_SECONDS)
        return self.public()

    def begin(self, scope, dialogue_id, capture_id):
        s = self.valid(scope)
        target = capture_id or '@starting'
        if not s or s['id'] != dialogue_id or s['reservation'] not in (None, '@starting', target):
            return False
        if s['reservation'] is None:
            s['reservation'] = target
            s['expires'] = self.clock() + RECORD_SECONDS
        elif s['reservation'] == '@starting' and capture_id:
            s['reservation'] = capture_id
        return True

    def cancel(self, scope, dialogue_id, reason):
        s = self.state
        if not s or s['scope'] != scope or s['id'] != dialogue_id:
            return None
        # A browser timeout racing with PTT must not cancel an accepted recording.
        if reason == 'timeout' and s['reservation'] and self.clock() < s['expires']:
            return None
        if reason == 'timeout' and self.clock() < s['expires']:
            return None
        return self.clear()

    def interpret(self, text, initial, scope, *, dialogue_id='', capture_id='', context=None):
        t = normalize(text)
        old = self.state
        s = self.valid(scope)
        authorized = bool(s and dialogue_id == s['id'] and s['reservation'] in (None, capture_id))
        if not authorized:
            s = None
        meta = {'action':'standalone', 'parent_capture_id':s['capture_id'] if s else None,
                'dialogue_id':s['id'] if s else None, 'parent_topic':s['topic'] if s else None, 'confirmed':None}
        stripped = PREFIX.sub('', t)
        if s and s['kind']=='clarification' and '그리고' in t:
            clauses = t.split('그리고')
            if len(clauses)==2 and YES.fullmatch(clauses[0]):
                extra = classify(clauses[1], scope[1])
                if extra.get('topic'):
                    return result(s['topic'], additional_topics=[extra['topic']]), {**meta,'action':'confirmed_with_question','confirmed':True}
        if CANCEL.fullmatch(t):
            self.clear()
            return result(reason='dialogue_cancelled'), {**meta,'action':'cancelled'}
        if YES.fullmatch(t) or NO.fullmatch(t):
            if not s or s['kind'] != 'clarification':
                return result(reason='dialogue_expired' if old else 'dialogue_missing'), {**meta,'action':'orphan_response'}
            if YES.fullmatch(t):
                self.clear()
                return result(s['topic']), {**meta,'action':'confirmed','confirmed':True}
            self.clear()
            return result(reason='clarification_rejected', correction_note='어떤 정보를 원하시는지 질문을 다시 말씀해 주세요.'), {**meta,'action':'rejected','confirmed':False}
        if stripped and stripped != t:
            explicit = classify(stripped, scope[1])
            if explicit['topic']:
                return explicit, {**meta,'action':'corrected' if t.startswith(('아니','아냐')) or '말고' in t else 'explicit_followup',
                                  'confirmed':False if t.startswith(('아니','아냐')) else None, 'resolved_text':stripped}
        if s and not CONTEXT_BLOCK.search(stripped):
            topic = s['topic']
            if s['kind']=='clarification' and topic=='race_time':
                if re.fullmatch(r'(?:시간말고)?(?:몇바퀴|몇랩)(?:냐고|야|남았어|남았냐고|인지)?', stripped):
                    return result('laps'), {**meta,'action':'corrected','confirmed':False}
            mode = 'short' if re.fullmatch(r'(?:더)?(?:짧게|간단히|간단하게|요점만)(?:말해줘|알려줘|설명해줘|해줘)?', stripped) else (
                'explain' if re.fullmatch(r'왜(?:그래|그런데|그런거야|그렇게생각해)?|(?:좀)?(?:자세히|자세하게|상세히)(?:말해줘|알려줘|설명해줘)?|이유(?:가뭐야|알려줘)?', stripped) else (
                'repeat' if re.fullmatch(r'(?:다시|한번더)(?:말해줘|알려줘|들려줘|말해|말해줄래)|뭐라고(?:했어)?', stripped) else None))
            if mode and s['kind']=='followup':
                if s['rival'] is not None and s['rival'] != rival(context or {}, topic):
                    self.clear()
                    return result(reason='dialogue_target_changed'), {**meta,'action':'target_changed'}
                detail = s['detail'] if mode=='repeat' else (
                    'short_explain' if mode=='short' and s['detail'] in ('explain','short_explain') else mode)
                return result(topic, answer_detail=detail), {**meta,'action':mode}
            side = 'behind' if re.fullmatch(r'(?:앞차말고)?(?:뒤차|뒷차)(?:는|도|쪽은)?', stripped) else (
                'ahead' if re.fullmatch(r'(?:뒤차말고)?앞차(?:는|도|쪽은)?', stripped) else None)
            if side and topic in SWITCH:
                dest = SWITCH[topic]
                dest_side = 'behind' if 'behind' in dest or dest=='defend_time' else 'ahead'
                return result(dest if side==dest_side else topic), {**meta,'action':'switch_target'}
            # Explicit short answers to a clarification identify a quantity, not an executable action.
            if s['kind']=='clarification':
                choices = {'시간':'race_time','남은시간':'race_time','경기시간':'race_time',
                           '몇바퀴':'laps','바퀴':'laps','남은랩':'laps','연료':'fuel','기름':'fuel',
                           '앞차':'ahead','뒤차':'behind','내랩':'last','내기록':'last','선두':'leader_lap'}
                dest = choices.get(stripped)
                if dest and dest in s['choices']:
                    return result(dest), {**meta,'action':'clarified','confirmed':dest==topic}
            if topic in ('fuel','fuel_range','fuel_finish','fuel_add','fuel_save','report_fuel'):
                if re.fullmatch(r'(?:지금)?(?:남은걸로|이걸로|이대로)(?:끝까지|완주)(?:가능해|되겠어|돼|할수있어|갈수있어)', stripped):
                    return result('fuel_finish'), {**meta,'action':'followup'}
                if re.fullmatch(r'(?:좀)?(?:아껴서|아끼면|절약해서|절약하면)(?:피트(?:에)?(?:안들어가고|안하고)|급유(?:안하고|없이)|무급유로)?(?:끝까지|완주)?(?:갈수(?:는)?(?:있어|없어)|할수(?:는)?(?:있어|없어)|가능해|돼|되겠어)', stripped):
                    return result('fuel_save'), {**meta,'action':'followup'}
            if topic in ('defend_time','closing_behind','report_behind') and re.fullmatch(r'어디서(?:그렇게)?(?:따라오는건데|따라와|빨라|시간을벌어|붙는거야)', stripped):
                if s['rival'] is not None and s['rival'] != rival(context or {}, topic):
                    return result(reason='dialogue_target_changed'), {**meta,'action':'target_changed'}
                return result('behind_loss'), {**meta,'action':'followup'}
            if topic in ('corner_loss','corner_repeat') and re.fullmatch(r'(?:계속|반복해서)?(?:거기서|그코너에서|그구간에서)?(?:잃는거야|손해보는거야)(?:이번만그런거야)?|이번만(?:그런거야|잃은거야)|계속그런거야', stripped):
                losses = ((context or {}).get('corner_coach') or {}).get('last_lap', {}).get('losses') or []
                if not s['corner'] or not losses or losses[0].get('id') != s['corner']:
                    return result(reason='dialogue_target_changed'), {**meta,'action':'target_changed'}
                return result('corner_repeat'), {**meta,'action':'followup'}
        # The defaults are only for genuinely incomplete quantities. Fuel health defaults to finish.
        key = ('remaining' if re.fullmatch(r'(?:지금|현재)?(?:얼마나|얼마|몇개)남았(?:어|지|냐|나요)?', t) else
               'gap' if re.fullmatch(r'(?:지금)?(?:간격|차이)(?:얼마야|얼마나나|몇초야|알려줘)?', t) else
               'lap' if t=='랩타임' else
               'pace' if re.fullmatch(r'(?:지금|현재|이)?페이스로끝까지(?:갈수있어|가능해|되겠어)', t) else None)
        if key and not initial.get('topic'):
            prompt, topic, choices = PROMPTS[key]
            return result(reason='clarification_needed', prompt=prompt, proposed_topic=topic, choices=choices), {**meta,'action':'clarification'}
        if not initial.get('topic') and re.fullmatch(r'(?:그럼|그러면)?(?:왜|뒤차는|앞차는|다시말해줘|짧게말해줘)', t):
            return result(reason='dialogue_expired' if old else 'dialogue_missing'), {**meta,'action':'orphan_response'}
        return initial, meta
