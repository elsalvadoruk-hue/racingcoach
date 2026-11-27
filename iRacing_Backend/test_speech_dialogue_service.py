"""Integration with real temporary note storage; recognition and SDK are mocked."""
import unittest
from unittest.mock import patch

from local_speech import LocalQuestion, DialogueEvent, SpeechDelivery
import test_local_speech as fixtures
from fastapi import FastAPI, APIRouter
from fastapi.testclient import TestClient
import local_speech


class DialogueServiceTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.QuestionTests.setUp

    async def ask(self, text, capture, token=''):
        return await self.service.ask(LocalQuestion(owner='test-owner',capture_id=capture,text=text,dialogue_id=token))

    async def test_confirmation_and_timeout_are_saved_without_sdk(self):
        now=[100.]
        self.service.dialogue.clock=lambda:now[0]
        with patch('pit_voice_commands.execute') as execute:
            first=await self.ask('얼마나 남았어','first')
            self.assertEqual(first['dialogue']['kind'],'clarification')
            token=first['dialogue']['id']
            ready=await self.service.dialogue_event('ready',DialogueEvent(owner='test-owner',dialogue_id=token,capture_id='first'))
            self.assertEqual(ready['dialogue']['remaining_ms'],8000)
            second=await self.ask('응','second',token)
            self.assertEqual(second['note']['context']['local_question']['topic'],'race_time')
            self.assertTrue(self.store.get('first')['context']['local_question']['dialogue_outcome']['confirmed'])
            first=await self.ask('얼마나 남았어','third');token=first['dialogue']['id']
            await self.service.dialogue_event('ready',DialogueEvent(owner='test-owner',dialogue_id=token,capture_id='third'))
            now[0]+=8
            cancelled=await self.service.dialogue_event('cancel',DialogueEvent(owner='test-owner',dialogue_id=token,reason='timeout'))
            self.assertTrue(cancelled['cancelled'])
            late=await self.ask('맞아','fourth',token)
            self.assertIsNone(late['note']['context']['local_question']['topic'])
            self.assertFalse(late['audio_parts'])
            self.assertEqual(self.store.get('third')['context']['local_question']['dialogue_outcome']['status'],'timeout')
            execute.assert_not_called()

    async def test_recording_started_in_time_is_preserved_past_deadline(self):
        now=[100.];self.service.dialogue.clock=lambda:now[0]
        first=await self.ask('얼마나 남았어','first');token=first['dialogue']['id']
        await self.service.dialogue_event('ready',DialogueEvent(owner='test-owner',dialogue_id=token,capture_id='first'))
        now[0]+=7
        begin=await self.service.dialogue_event('begin',DialogueEvent(owner='test-owner',dialogue_id=token,capture_id='second'))
        self.assertTrue(begin['accepted'])
        now[0]+=6
        reply=await self.ask('아니 기름 몇 리터야','second',token)
        self.assertEqual(reply['note']['context']['local_question']['topic'],'fuel')
        self.assertEqual(reply['note']['text'],'아니 기름 몇 리터야')
        self.assertFalse(self.store.get('first')['context']['local_question']['dialogue_outcome']['confirmed'])

    async def test_repeat_uses_current_values_and_session_change_clears_context(self):
        first=await self.ask('연료 얼마나 남았어','first');token=first['dialogue']['id']
        self.context['snapshot']['fuel_l']=17
        second=await self.ask('다시 말해줘','second',token)
        self.assertIn('17',second['note']['reply'])
        self.assertEqual(second['note']['context']['local_question']['dialogue']['action'],'repeat')
        self.context['snapshot']['run_id']='another'
        third=await self.ask('응','third',second['dialogue']['id'])
        self.assertIsNone(third['note']['context']['local_question']['topic'])

    async def test_delivery_is_separate_from_preparation_and_not_proof_of_correctness(self):
        await self.ask('연료 얼마나 남았어','first')
        await self.service.delivery(SpeechDelivery(owner='test-owner',capture_id='first',event='completed',capture_end_to_start_ms=423))
        await self.service.delivery(SpeechDelivery(owner='test-owner',capture_id='first',event='started',capture_end_to_start_ms=999))
        log=self.store.get('first')['context']['local_question']
        self.assertEqual(log['delivery']['status'],'completed')
        self.assertEqual(log['delivery']['capture_end_to_start_ms'],423)
        self.assertIsNone(log['dialogue']['confirmed'])
        self.assertNotIn('audio_base64',str(log))

    async def test_pit_command_discards_question_context_and_yes_never_repeats_it(self):
        first=await self.ask('얼마나 남았어','first');token=first['dialogue']['id']
        with patch('pit_voice_commands.execute') as execute:
            command=await self.ask('피트 급유 15리터','command')
            self.assertEqual(command['command_result']['result'],'command_disabled')
            self.assertIsNone(self.service.dialogue.state)
            later=await self.ask('응','later',token)
            self.assertIsNone(later['note']['context']['local_question']['topic'])
            execute.assert_not_called()

    async def test_confirmation_with_second_question_is_bounded_and_does_not_keep_ambiguous_context(self):
        first=await self.ask('얼마나 남았어','first')
        reply=await self.ask('맞아 그리고 뒤차 간격도','second',first['dialogue']['id'])
        self.assertEqual(reply['note']['context']['local_question']['topic'],'race_time')
        self.assertEqual(reply['note']['context']['local_question']['additional_topics'],['behind'])
        self.assertIn('뒤차',reply['spoken_text'])
        self.assertIsNone(reply['dialogue'])

    async def test_yes_then_correction_uses_the_last_explicit_quantity(self):
        first=await self.ask('얼마나 남았어','first')
        reply=await self.ask('응 근데 시간 말고 몇 바퀴냐고','second',first['dialogue']['id'])
        self.assertEqual(reply['note']['context']['local_question']['topic'],'laps')
        self.assertFalse(reply['note']['context']['local_question']['dialogue']['confirmed'])

    async def test_combined_answer_revalidation_accepts_same_facts_and_rejects_changed_rival(self):
        app,router=FastAPI(),APIRouter()
        service=local_speech.register(router,self.coach,self.buttons)
        service._runtime=self.runtime;app.include_router(router)
        first=await service.ask(LocalQuestion(owner='test-owner',capture_id='first',text='얼마나 남았어'))
        await service.ask(LocalQuestion(owner='test-owner',capture_id='second',text='맞아 그리고 뒤차 간격도',dialogue_id=first['dialogue']['id']))
        with TestClient(app) as client:
            body={'owner':'test-owner','capture_id':'second'}
            self.assertTrue(client.post('/local-speech/readback-check',json=body).json()['valid'])
            self.context['pit_wall']['behind']['idx']=99
            self.assertFalse(client.post('/local-speech/readback-check',json=body).json()['valid'])

    async def test_uncertain_audio_does_not_confirm_pending_question(self):
        first=await self.ask('얼마나 남았어','first')
        self.runtime.recognize=lambda *a:{'text':'응','reason':'uncertain_audio'}
        reply=await self.service.ask(LocalQuestion(owner='test-owner',capture_id='second',audio_base64=fixtures.wav(),dialogue_id=first['dialogue']['id']))
        self.assertIsNone(reply['note']['context']['local_question']['topic'])
        self.assertFalse(reply['audio_parts'])


if __name__=='__main__':
    unittest.main()
