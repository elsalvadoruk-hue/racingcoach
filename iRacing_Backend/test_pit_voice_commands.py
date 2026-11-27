import asyncio
from copy import deepcopy
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import Mock, patch

import pit_voice_commands as pit
from local_speech import LocalQuestion
import test_local_speech as local_tests
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from local_speech import register


class PitParserTests(unittest.TestCase):
    def test_natural_command_variants_preserve_exact_service_amount_and_wheels(self):
        variants={
            '피트 연료 15리터 설정':['피트 급유 15리터','히트 연료 15리터 넣어줘','키트 기름 15리터 채워줘','피트 급유 십오 리터','피트 연료 좀 15리터 추가해 주세요'],
            '피트 완주 연료 설정':['피트 완주 연료','히트 끝까지 갈 연료 넣어줘','키트 연료 완주까지 맞춰줘'],
            '피트 급유 취소':['피트 연료 빼줘','히트 급유 꺼줘','키트 기름 해제해줘'],
            '피트 타이어 교체':['피트 타이어 모두 바꿔줘','히트 타이어 4개 갈아줘','키트 슬릭으로 바꿔줘','피트 타이어 드라이로 교체해줘','피트 모든 타이어 교환해줘','피트 타이어 다 갈아끼워줘'],
            '피트 레인 타이어 교체':['히트 레인 타이어로 바꿔줘','키트 웻으로 교체해줘','피트 타이어 웻으로 갈아줘'],
            '피트 타이어 교체 취소':['피트 타이어 빼줘','히트 타이어 해제해줘'],
            '피트 왼쪽 앞 타이어 교체':['키트 앞 왼쪽 타이어 바꿔줘','피트 좌측 앞쪽 타이어 교체해줘'],
            '피트 오른쪽 타이어 교체':['히트 우측 타이어 갈아줘'],
            '피트 타이어 공기압 180kPa 설정':['피트 공기압 180킬로파스칼','히트 타이어 압력 180kPa로 맞춰줘'],
            '피트 왼쪽 앞 타이어 공기압 26psi 설정':['키트 앞 왼쪽 공기압 26피에스아이'],
            '피트 윈드실드 교체':['피트 앞유리 닦아줘','히트 티어오프 교체해줘'],
            '피트 윈드실드 취소':['키트 앞유리 빼줘'],
            '피트 빠른 수리 켜':['피트 빠른 수리','히트 패스트 리페어 신청해줘','키트 수리해줘'],
            '피트 빠른 수리 취소':['피트 수리 취소해줘','히트 빠른 수리 빼줘'],
            '피트 전체 취소':['피트 설정 다 취소해줘','히트 옵션 전부 해제해줘','키트 모두 빼줘'],
        }
        for canonical,texts in variants.items():
            expected=pit.parse(canonical)
            for text in texts:
                with self.subTest(text=text):self.assertEqual(pit.parse(text),expected)

    def test_trigger_variants_do_not_relax_questions_negation_units_or_scope(self):
        for trigger in ('피트','히트','키트'):
            for text in ('급유 15리터?', '연료 15리터면 돼', '연료 15리터 말고 20리터', '연료 15리터 안 넣어',
                         '연료 15리터 취소', '연료 15', '타이어 웻인지 알려줘', '레인 타이어 바꾸지마',
                         '왼쪽 타이어 4개 교체', '슬릭 레인 타이어 교체', '타이어 교체 그리고 연료 15리터',
                         '타이어 공기압 180', '빠른 수리 하지 말아줘', '전체 취소하면 돼'):
                with self.subTest(trigger=trigger,text=text):self.assertIsNone(pit.parse(trigger+' '+text))
        for text in ('급유 15리터','연료 15리터 넣어줘','타이어 전부 교체','기차 급유 15리터'):
            self.assertIsNone(pit.parse(text))

    def test_sdk_car_types_and_pending_compound_are_available_to_commands(self):
        from race_engineer import read_race
        from live_telemetry import CHANNELS
        data=read_race({'DriverInfo':{'DriverTires':[{'TireIndex':4,'TireCompoundType':'Dry'},
                                                   {'TireIndex':2,'TireCompoundType':'Wet'}]}},[],0)
        self.assertEqual(data['tire_compounds'],[{'index':4,'type':'Dry'},{'index':2,'type':'Wet'}])
        self.assertEqual(CHANNELS['pit_sv_tire_compound'],'PitSvTireCompound')

    def test_rain_and_plain_tires_select_different_compounds(self):
        for kind,texts in [('wet', ('피트 레인 타이어 교체', '피트 레인에서 타이어 교체해줘', '피트 웻 타이어 교체', '피트 웨트 타이어 교체')),
                           ('dry', ('피트 타이어 교체', '피트 타이어 전부 교체', '피트 슬릭 타이어 교체', '피트 일반 타이어 교체'))]:
            for text in texts:
                with self.subTest(text=text):
                    command=pit.parse(text)
                    self.assertEqual(command['compound_kind'],kind)
                    self.assertEqual(command['mask'],15)
                    self.assertIn('웻' if kind=='wet' else '슬릭',command['name'])

    def test_compound_indices_come_from_car_types_not_assumed_zero_one(self):
        context={'pit_tire_compounds':[{'index':4,'type':'Dry'},{'index':2,'type':'Wet'}],
                 'snapshot':{'pit_sv_tire_compound':2,'tire_compound':2}}
        for text,index in [('피트 레인 타이어 교체',2),('피트 타이어 교체',4)]:
            command=pit.plan(pit.parse(text),context)
            self.assertEqual(command['calls'],[(12,index),(3,0),(4,0),(5,0),(6,0)])
            self.assertEqual(command['compound'],index)
        for context in ({}, {'pit_tire_compounds':[{'index':0,'type':'Dry'}]},
                        {'pit_tire_compounds':[{'index':1,'type':'Unknown'}]},
                        {'pit_tire_compounds':[{'index':-1,'type':'Wet'}]}):
            with self.subTest(context=context), self.assertRaises(ValueError):
                pit.plan(pit.parse('피트 레인 타이어 교체'),context)

    def test_multiple_dry_compounds_keep_known_dry_choice_or_ask(self):
        context={'pit_tire_compounds':[{'index':2,'type':'Wet'},{'index':4,'type':'Hard'},{'index':5,'type':'Soft'}],
                 'snapshot':{'pit_sv_tire_compound':5,'tire_compound':4}}
        self.assertEqual(pit.plan(pit.parse('피트 타이어 교체'),context)['compound'],5)
        context['snapshot']['pit_sv_tire_compound']=2
        self.assertEqual(pit.plan(pit.parse('피트 타이어 교체'),context)['compound'],4)
        context['snapshot']['tire_compound']=2
        with self.assertRaises(ValueError):pit.plan(pit.parse('피트 타이어 교체'),context)

    def test_all_published_examples_parse(self):
        for text in pit.COMMAND_EXAMPLES:
            with self.subTest(text=text):
                result=pit.parse(text)
                self.assertIsNotNone(result)
                self.assertNotIn('error',result)

    def test_numbers_wheels_and_units_are_explicit(self):
        for text in ('피트 연료 20리터 설정해줘', '피트 연료 이십 리터 넣어', '피트 급유 스무 리터 설정'):
            self.assertEqual(pit.parse(text)['calls'],[(2,20)])
        self.assertEqual(pit.parse('피트 왼쪽 타이어 교체')['calls'],[(3,0),(5,0)])
        for text in ('피트 레인 타이어 교체', '피트레인 타이어 교체해줘', '피트 레인에서 타이어 교체해 주세요'):
            self.assertEqual(pit.parse(text)['calls'],[(3,0),(4,0),(5,0),(6,0)])
        self.assertEqual(pit.parse('피트 레인 왼쪽 앞 타이어 교체')['calls'],[(3,0)])
        self.assertEqual(pit.parse('피트 레인 타이어 교체 취소')['calls'],[(7,0)])
        self.assertEqual(pit.parse('피트 오른쪽 앞 타이어 공기압 이십육 점 오 피에스아이 설정')['calls'],[(4,183)])
        self.assertEqual(pit.parse('피트 타이어 공기압 180kPa 설정')['calls'],[(3,180),(4,180),(5,180),(6,180)])

    def test_questions_negation_noise_and_mixed_requests_never_send(self):
        for text in ('피트 타이어 교체해야 해?', '피트 연료 몇 리터 넣어', '피트 타이어 교체하지 마',
                     '피트 타이어 교체 안해', '피트 연료 20리터 말고 30리터 설정',
                     '피트 연료 20리터 설정 그리고 타이어 교체', '타이어 전부 교체',
                     '기차 연료 20리터 설정', '피트 왼쪽 타이어 전부 교체',
                     '피트 타이어 공기압 180 설정', '피트 앞차 타이어 교체', '피트 연료 -20리터 설정',
                     '피트 연료 20;clear 리터 설정', '피트 빠른 수리 켜지 마', '피트 전체 취소하지 마',
                     '피트 레인 타이어 교체해야 해?', '피트 레인 타이어 교체하지 마',
                     '피트 레인 타이어 교체 그리고 연료 추가'):
            with self.subTest(text=text):self.assertIsNone(pit.parse(text))

    def test_out_of_range_never_clamps_to_a_different_request(self):
        self.assertEqual(pit.parse('피트 급유 150리터')['calls'],[(2,150)])
        for text in ('피트 연료 151리터 설정','피트 연료 200리터 설정','피트 연료 201리터 설정','피트 연료 0리터 설정','피트 연료 20.5리터 설정',
                     '피트 타이어 공기압 50kPa 설정','피트 타이어 공기압 100psi 설정'):
            self.assertIn('error',pit.parse(text))

    def test_finish_fuel_uses_single_stop_reserve_inclusive_plan(self):
        context={'snapshot':{'fuel_l':12},'pit_wall':{'active':True,'fuel_next_stop_l':20.01,
                 'fuel_next_stop_longer_l':25,'tank_capacity_l':100,'stops_needed':1}}
        result=pit.plan(pit.parse('피트 완주 연료 설정'),context)
        self.assertEqual(result['calls'],[(2,21)])
        context['pit_wall'].update(fuel_next_stop_l=150.1,tank_capacity_l=300)
        with self.assertRaises(ValueError):pit.plan(pit.parse('피트 완주 연료 설정'),context)
        for stops in (None,0,2):
            context['pit_wall']['stops_needed']=stops
            with self.assertRaises(ValueError):pit.plan(pit.parse('피트 완주 연료 설정'),context)


class PitTransportTests(unittest.TestCase):
    def setUp(self):
        self.snapshot={'run_id':'run','on_track':True,'is_replay':False,'pit_sv_flags':0,'pit_sv_fuel':0}
        self.context={'connected':True,'game':'iracing','snapshot':deepcopy(self.snapshot)}
        self.live=SimpleNamespace(lock=threading.RLock(),game='iracing',_current_snapshot=lambda:(None,self.snapshot))
        self.command=pit.parse('피트 연료 20리터 설정')

    def test_sdk_constants_match_installed_sdk_without_sending(self):
        import irsdk
        self.assertEqual(irsdk.PitCommandMode.fuel,2)
        self.assertEqual(irsdk.PitCommandMode.clear_fuel,11)
        self.assertEqual(irsdk.PitCommandMode.clear_tires,7)
        self.assertEqual(irsdk.PitCommandMode.tc,12)
        self.assertEqual(irsdk.PitSvFlags.fuel_fill,0x10)
        with patch('irsdk.IRSDK') as sdk:
            sdk.return_value.pit_command.return_value=1
            self.assertEqual(pit.send_sdk(2,20),1)
            sdk.return_value.pit_command.assert_called_once_with(2,20)

    def test_only_matching_readback_is_confirmed(self):
        def send(mode,value):
            self.snapshot.update(pit_sv_flags=0x10,pit_sv_fuel=value)
            return 1
        result=pit.execute(self.command,self.context,self.live,lambda:True,send=send)
        self.assertEqual(result['result'],'command_applied')
        self.assertEqual(result['sent'],[[2,20]])

    def test_already_selected_is_not_toggled(self):
        self.snapshot.update(pit_sv_flags=0x10,pit_sv_fuel=20)
        send=Mock()
        result=pit.execute(self.command,self.context,self.live,lambda:True,send=send)
        self.assertEqual(result['result'],'command_applied')
        send.assert_not_called()

    def test_no_game_ack_does_not_mean_success_or_retry(self):
        send=Mock(return_value=1)
        result=pit.execute(self.command,self.context,self.live,lambda:True,send=send,timeout=0)
        self.assertEqual(result['result'],'command_unconfirmed')
        send.assert_called_once_with(2,20)

    def test_disconnected_lmu_replay_old_run_and_owner_loss_never_send(self):
        for change in ({'on_track':False},{'is_replay':True},{'run_id':'other'}):
            original=deepcopy(self.snapshot);self.snapshot.update(change);send=Mock()
            self.assertEqual(pit.execute(self.command,self.context,self.live,lambda:True,send=send)['result'],'command_blocked')
            send.assert_not_called();self.snapshot=original
        for game,owner in [('lmu',True),('iracing',False)]:
            self.live.game=game;send=Mock()
            pit.execute(self.command,self.context,self.live,lambda:owner,send=send)
            send.assert_not_called()
        self.snapshot=None;send=Mock()
        pit.execute(self.command,self.context,self.live,lambda:True,send=send)
        send.assert_not_called()

    def test_partial_multi_wheel_send_stops_on_session_change(self):
        def send(mode,value):self.snapshot['run_id']='other';return 1
        self.context['pit_tire_compounds']=[{'index':4,'type':'Dry'}]
        command=pit.plan(pit.parse('피트 타이어 전부 교체'),self.context)
        result=pit.execute(command,self.context,self.live,lambda:True,send=send)
        self.assertEqual(result['sent'],[[12,4]])
        self.assertEqual(result['result'],'command_unconfirmed')

    def test_four_wheel_flags_with_wrong_or_missing_compound_are_not_success(self):
        self.context['pit_tire_compounds']=[{'index':2,'type':'Wet'}]
        command=pit.plan(pit.parse('피트 레인 타이어 교체'),self.context)
        for flags,compound,expected in [(15,None,False),(15,0,False),(0,2,False),(15,2,True)]:
            self.assertEqual(pit.applied(command,{'pit_sv_flags':flags,'pit_sv_tire_compound':compound}),expected)
        # The fitted set can still be dry: confirmation concerns the next service.
        self.assertTrue(pit.applied(command,{'pit_sv_flags':15,'pit_sv_tire_compound':2,'tire_compound':0}))

    def test_unresolved_tire_request_never_sends(self):
        send=Mock()
        result=pit.execute(pit.parse('피트 레인 타이어 교체'),self.context,self.live,lambda:True,send=send)
        self.assertEqual(result['result'],'command_blocked');send.assert_not_called()

    def test_pressure_needs_both_selection_and_pressure_readback(self):
        command=pit.parse('피트 왼쪽 앞 타이어 공기압 180kPa 설정')
        for flags,pressure,expected in [(0,180,False),(1,None,False),(1,175,False),(1,180,True)]:
            self.assertEqual(pit.applied(command,{'pit_sv_flags':flags,'pit_sv_lfp':pressure}),expected)


class PitServiceTests(unittest.IsolatedAsyncioTestCase):
    setUp=local_tests.QuestionTests.setUp
    payload=local_tests.QuestionTests.payload

    def connected(self):
        self.context.update(game='iracing');self.live.game='iracing'
        self.context['snapshot'].update(on_track=True,is_replay=False,pit_sv_flags=0,pit_sv_fuel=0)

    async def test_missing_tire_action_reads_existing_clarification_without_sending(self):
        self.connected()
        for index, text in enumerate(('피트 레인 타이어.', '히트 슬릭 타이어')):
            with patch.object(pit, 'send_sdk') as send:
                result = await self.service.ask(LocalQuestion(owner='test-owner', capture_id=f'incomplete-{index}',
                    text=text, allow_pit_commands=True))
            send.assert_not_called()
            self.assertEqual(result['command_result']['result'], 'command_blocked')
            self.assertEqual(result['spoken_text'], '교체인지 취소인지 다시 말씀해 주세요.')
            self.assertEqual(result['note']['text'], text)
            self.assertEqual(result['note']['context']['local_question']['sent'], [])

    async def test_readback_check_rejects_changed_settings_owner_session_or_consent(self):
        self.connected();self.context['snapshot'].update(pit_sv_flags=16,pit_sv_fuel=15)
        app,router=FastAPI(),APIRouter();service=register(router,self.coach,self.buttons);service._runtime=self.runtime
        app.include_router(router)
        result=await service.ask(self.payload(text='피트 급유 15리터',allow_pit_commands=True))
        self.assertTrue(result['audio_parts'])
        with TestClient(app) as client:
            body={'owner':'test-owner','capture_id':'capture-one'}
            self.assertTrue(client.post('/local-speech/readback-check',json=body).json()['valid'])
            self.context['snapshot']['pit_sv_fuel']=20
            self.assertFalse(client.post('/local-speech/readback-check',json=body).json()['valid'])
            self.context['snapshot']['pit_sv_fuel']=15
            self.assertFalse(client.post('/local-speech/readback-check',json={**body,'owner':'other-owner'}).json()['valid'])
            with patch('local_speech.require_logs',return_value=2000):
                self.assertFalse(client.post('/local-speech/readback-check',json=body).json()['valid'])
            self.context['snapshot']['run_id']='other'
            self.assertFalse(client.post('/local-speech/readback-check',json=body).json()['valid'])

    async def test_muted_and_duplicate_commands_do_not_replay(self):
        self.connected();self.context['snapshot'].update(pit_sv_flags=16,pit_sv_fuel=150)
        result=await self.service.ask(self.payload(text='피트 급유 150리터',allow_pit_commands=True,speak=False))
        self.assertEqual(result['command_result']['result'],'command_applied')
        self.assertFalse(result['audio_parts'])
        repeated=await self.service.ask(self.payload(text='피트 급유 150리터',allow_pit_commands=True))
        self.assertTrue(repeated['reused']);self.assertFalse(repeated.get('audio_parts'))

    async def test_success_reads_back_verified_amount_and_logs_exact_spoken_text(self):
        self.connected()
        def send(mode,value):
            self.context['snapshot'].update(pit_sv_flags=0x10,pit_sv_fuel=value)
            return 1
        with patch.object(pit,'send_sdk',side_effect=send):
            result=await self.service.ask(self.payload(text='피트 급유 15리터',allow_pit_commands=True))
        self.assertEqual(result['spoken_text'],'추가 급유 15리터로 설정했습니다.')
        self.assertEqual(result['audio_parts'],['prepared-audio'])
        self.assertEqual(result['note']['context']['local_question']['readback_text'],result['spoken_text'])

    async def test_unconfirmed_setting_never_reads_success_or_requested_number(self):
        self.connected()
        with patch.object(pit,'execute',return_value={'result':'command_unconfirmed','sent':[[2,15]],'message':'반영 미확인'}):
            result=await self.service.ask(self.payload(text='피트 급유 15리터',allow_pit_commands=True))
        self.assertEqual(result['spoken_text'],'피트 설정 반영을 확인하지 못했습니다.')
        self.assertNotIn('15',result['spoken_text'])

    async def test_clipped_cancellation_asks_again_and_never_sends(self):
        self.connected()
        with patch.object(pit,'send_sdk') as send:
            result=await self.service.ask(self.payload(text='히트 타이어 교체체취',allow_pit_commands=True))
        send.assert_not_called()
        self.assertEqual(result['spoken_text'],'교체인지 취소인지 다시 말씀해 주세요.')

    async def test_changed_setting_during_readback_preparation_cancels_success_audio(self):
        self.connected()
        self.context['snapshot'].update(pit_sv_flags=0x10,pit_sv_fuel=15)
        def read(*args):
            self.context['snapshot']['pit_sv_fuel']=20
            return 'clip'
        self.buttons.read_clip=read
        result=await self.service.ask(self.payload(text='피트 급유 15리터',allow_pit_commands=True))
        self.assertEqual(result['command_result']['result'],'command_applied')
        self.assertFalse(result['audio_parts'])
        self.assertEqual(result['note']['context']['local_question']['readback_result'],'settings_changed')

    async def test_wet_then_slick_changes_compound_and_all_four_selection_flags(self):
        self.connected()
        self.context['pit_tire_compounds']=[{'index':4,'type':'Dry'},{'index':2,'type':'Wet'}]
        self.context['snapshot'].update(pit_sv_flags=15,pit_sv_tire_compound=4)
        def send(mode,value):
            if mode==12:self.context['snapshot']['pit_sv_tire_compound']=value
            else:self.context['snapshot']['pit_sv_flags']|=1<<(mode-3)
            return 1
        with patch.object(pit,'send_sdk',side_effect=send) as sender:
            for capture,text,index in [('wet','피트 레인 타이어 교체',2),('dry','피트 타이어 교체',4)]:
                result=await self.service.ask(LocalQuestion(owner='test-owner',capture_id=capture,text=text,allow_pit_commands=True))
                self.assertEqual(result['command_result']['result'],'command_applied')
                self.assertEqual(result['command_result']['sent'],[[12,index],[3,0],[4,0],[5,0],[6,0]])
                self.assertEqual(self.store.get(capture)['context']['local_question']['command']['compound'],index)
            self.assertEqual(sender.call_count,10)

    async def test_unavailable_wet_tire_has_no_fallback_or_partial_commands(self):
        self.connected()
        self.context['pit_tire_compounds']=[{'index':0,'type':'Dry'}]
        with patch.object(pit,'send_sdk') as sender:
            result=await self.service.ask(self.payload(text='피트 레인 타이어 교체',allow_pit_commands=True))
            self.assertEqual(result['command_result']['result'],'command_blocked')
            self.assertIn('웻',result['command_result']['message']);sender.assert_not_called()

    async def test_default_disabled_and_test_endpoint_never_send(self):
        self.connected()
        with patch.object(pit,'send_sdk') as send:
            result=await self.service.ask(self.payload(text='피트 연료 20리터 설정'))
            self.assertEqual(result['note']['context']['local_question']['result'],'command_disabled')
            app,router=FastAPI(),APIRouter();service=register(router,self.coach,self.buttons);service._runtime=self.runtime
            self.runtime.recognize=lambda *args:{'text':'피트 연료 20리터 설정'}
            app.include_router(router)
            with TestClient(app) as client:response=client.post('/local-speech/test',json={'owner':'test-owner','audio_base64':local_tests.wav()})
            self.assertIn('전송 안 함',response.json()['topic_name'])
            send.assert_not_called()

    async def test_receipt_precedes_send_and_duplicate_capture_never_repeats(self):
        self.connected()
        def send(mode,value):
            self.assertEqual(self.store.get('capture-one')['context']['local_question']['result'],'command_pending')
            self.context['snapshot'].update(pit_sv_flags=0x10,pit_sv_fuel=value)
            return 1
        with patch.object(pit,'send_sdk',side_effect=send) as sender:
            result=await self.service.ask(self.payload(text='피트 연료 20리터 설정',allow_pit_commands=True))
            self.assertEqual(result['command_result']['result'],'command_applied')
            self.assertEqual(self.store.get('capture-one')['context']['local_question']['sent'],[[2,20]])
            repeated=await self.service.ask(self.payload(text='피트 연료 30리터 설정',allow_pit_commands=True))
            self.assertTrue(repeated['reused']);sender.assert_called_once()

    async def test_ordinary_fuel_question_remains_read_only(self):
        self.connected();self.context['pit_wall']['fuel_next_stop_l']=20
        with patch.object(pit,'send_sdk') as send:
            result=await self.service.ask(self.payload(text='연료 몇 리터 넣어',allow_pit_commands=True))
            self.assertEqual(result['note']['context']['local_question']['topic'],'fuel_add')
            send.assert_not_called()

    async def test_uncertain_audio_never_sends(self):
        self.connected()
        self.runtime.recognize=lambda *args: {'text':'피트 연료 20리터 설정','reason':'uncertain_audio'}
        with patch.object(pit,'send_sdk') as send:
            await self.service.ask(self.payload(audio_base64=local_tests.wav(),allow_pit_commands=True))
            send.assert_not_called()

    async def test_owner_change_during_recognition_never_sends(self):
        self.connected()
        def recognize(*args):
            self.runtime.owner = 'another-owner'
            return {'text':'피트 연료 20리터 설정'}
        self.runtime.recognize = recognize
        with patch.object(pit,'send_sdk') as send:
            result = await self.service.ask(self.payload(audio_base64=local_tests.wav(),allow_pit_commands=True))
            self.assertEqual(result['command_result']['result'],'command_blocked')
            send.assert_not_called()

    async def test_pending_receipt_after_restart_never_resends(self):
        self.connected()
        self.store.save('피트 연료 20리터 설정', {**self.context,'local_question':{
            'command':pit.parse('피트 연료 20리터 설정'),'result':'command_pending'}}, 'LOCAL_VOICE','capture-one')
        with patch.object(pit,'send_sdk') as send:
            result = await self.service.ask(self.payload(text='피트 연료 20리터 설정',allow_pit_commands=True))
            self.assertTrue(result['reused'])
            self.assertEqual(result['note']['context']['local_question']['result'],'command_pending')
            send.assert_not_called()

    async def test_sof_uses_only_known_same_class_human_ratings(self):
        self.connected()
        self.live.pit_wall=SimpleNamespace(race={'session_type':'Race','player_idx':1,'cars':[
            {'idx':1,'class_id':3,'irating':2000},{'idx':2,'class_id':3,'irating':2000},{'idx':4,'class_id':4,'irating':6000}]})
        result=await self.service.ask(self.payload(text='클래스 에스오에프 알려줘'))
        self.assertIn('2000',result['note']['reply'])
