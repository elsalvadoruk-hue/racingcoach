import unittest
from local_question_dialogue import Dialogue, identity
from local_question_intents import classify


class DialogueTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.
        self.d = Dialogue(lambda:self.now)
        self.context = {'game':'iracing','snapshot':{'run_id':'run'}, 'pit_wall':{'behind':{'idx':4},'ahead':{'idx':3}}}
        self.scope = identity('owner',self.context,1)

    def offer(self, topic='race_time', prompt='경기 남은 시간 말씀이신가요?'):
        return self.d.offer(self.scope, 'first', topic, prompt=prompt, choices=['race_time','fuel','laps'], context=self.context)

    def interpret(self, text, token=None):
        return self.d.interpret(text, classify(text), self.scope, dialogue_id=token or '',capture_id='second',context=self.context)

    def test_confirmation_forms_and_whole_utterance(self):
        for text in ('응','어','맞아','그래','네','예','맞아요','그렇지'):
            token=self.offer()['id']
            r,m=self.interpret(text,token)
            self.assertEqual(r['topic'],'race_time',text)
            self.assertTrue(m['confirmed'])
        token=self.offer()['id']
        r,m=self.interpret('아니 기름 몇 리터야',token)
        self.assertEqual(r['topic'],'fuel')
        self.assertFalse(m['confirmed'])

    def test_ready_starts_eight_seconds_and_cannot_extend(self):
        token=self.offer()['id'];self.now+=10
        self.d.ready(self.scope,token)
        self.now+=7.99
        self.d.ready(self.scope,token)
        self.assertEqual(self.interpret('응',token)[0]['topic'],'race_time')
        token=self.offer()['id'];self.d.ready(self.scope,token);self.now+=8
        self.assertIsNone(self.interpret('응',token)[0]['topic'])
        self.assertIsNone(self.d.state)

    def test_begin_reserves_one_recording_and_no_timeout_race(self):
        token=self.offer()['id'];self.d.ready(self.scope,token);self.now+=7
        self.assertTrue(self.d.begin(self.scope,token,'second'))
        self.now+=3
        self.assertIsNone(self.d.cancel(self.scope,token,'timeout'))
        self.assertFalse(self.d.begin(self.scope,token,'third'))
        self.assertEqual(self.interpret('어',token)[0]['topic'],'race_time')

    def test_late_start_unknown_token_wrong_scope_and_expired_recording(self):
        token=self.offer()['id'];self.d.ready(self.scope,token);self.now+=8
        self.assertFalse(self.d.begin(self.scope,token,'second'))
        self.assertIsNone(self.interpret('맞아',token)[0]['topic'])
        token=self.offer()['id']
        self.assertIsNone(self.interpret('응','wrong')[0]['topic'])
        self.assertFalse(self.d.begin(('other','iracing','run',1),token,'second'))
        token=self.offer()['id'];self.d.begin(self.scope,token,'second');self.now+=25
        self.assertIsNone(self.interpret('응',token)[0]['topic'])

    def test_button_edge_reservation_survives_microphone_preparation(self):
        token=self.offer()['id'];self.d.ready(self.scope,token);self.now+=7.9
        self.assertTrue(self.d.begin(self.scope,token,''))
        self.now+=2
        self.assertIsNone(self.d.cancel(self.scope,token,'timeout'))
        self.assertTrue(self.d.begin(self.scope,token,'second'))
        self.assertEqual(self.interpret('맞아',token)[0]['topic'],'race_time')

    def test_negative_cancel_do_not_execute_and_free_yes_is_unmatched(self):
        token=self.offer()['id']
        self.assertEqual(self.interpret('아니',token)[0]['reason'],'clarification_rejected')
        self.assertIsNone(self.interpret('응',token)[0]['topic'])
        token=self.offer()['id']
        self.assertEqual(self.interpret('아니 됐어',token)[0]['reason'],'dialogue_cancelled')
        self.assertIsNone(self.d.state)

    def test_followup_target_switch_repeat_explain_and_expiry(self):
        for text,topic,mode in [('그럼 뒤차는','behind',None),('다시 말해줘','ahead','default'),('왜','ahead','explain'),('짧게 말해줘','ahead','short')]:
            token=self.offer('ahead','')['id']
            r,m=self.interpret(text,token)
            self.assertEqual(r['topic'],topic)
            if mode:self.assertEqual(r['answer_detail'],mode)
        token=self.offer('ahead','')['id'];self.d.ready(self.scope,token);self.now+=20
        self.assertIsNone(self.interpret('그럼 뒤차는',token)[0]['topic'])

    def test_changed_rival_does_not_repeat_different_car_answer(self):
        token=self.offer('ahead','')['id'];self.context['pit_wall']['ahead']['idx']=7
        self.assertEqual(self.interpret('다시 말해줘',token)[0]['reason'],'dialogue_target_changed')

    def test_short_followup_preserves_the_requested_explanation(self):
        for previous in ('explain', 'short_explain'):
            token = self.d.offer(self.scope, 'first', 'fuel_finish', context=self.context, detail=previous)['id']
            result, meta = self.interpret('짧게 말해줘', token)
            self.assertEqual(result['answer_detail'], 'short_explain')
            self.assertEqual(meta['action'], 'short')
        token = self.d.offer(self.scope, 'first', 'fuel_finish', context=self.context, detail='short_explain')['id']
        self.assertEqual(self.interpret('다시 말해줘', token)[0]['answer_detail'], 'short_explain')

    def test_only_genuine_ambiguity_needs_confirmation(self):
        for text in ('얼마나 남았어','간격 몇 초야','랩타임','지금 페이스로 끝까지 갈 수 있어'):
            self.assertEqual(self.interpret(text)[0]['reason'],'clarification_needed',text)
        self.assertNotEqual(self.interpret('연료 괜찮아')[0]['reason'],'clarification_needed')

    def test_question_word_is_not_an_acknowledgement_prefix(self):
        for text in ('어디서 그렇게 따라오는 건데','그럼 어디서 따라와'):
            token=self.offer('defend_time','')['id']
            self.assertEqual(self.interpret(text,token)[0]['topic'],'behind_loss')

    def test_context_cannot_resurrect_negation_or_mixed_questions(self):
        self.context['corner_coach']={'last_lap':{'losses':[{'id':'c4'}]}}
        for topic,text in [('fuel_finish','아껴서 완주하지 마'),
                           ('fuel_finish','피트 안 하고 가려고 절약하라는 거 아니야'),
                           ('fuel_finish','아껴서 완주할 수 있어 그리고 뒤차 간격'),
                           ('corner_loss','계속 거기서 잃는 거야 말하지 마')]:
            token=self.offer(topic,'')['id']
            self.assertIsNone(self.interpret(text,token)[0]['topic'],text)


if __name__ == '__main__':
    unittest.main()
