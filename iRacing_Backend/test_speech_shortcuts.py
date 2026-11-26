import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from speech_shortcuts import SpeechShortcuts, register

KEY = {'code':'key:118:1','label':'Ctrl+F7'}
HID = {'code':'hid:'+'a'*24+':b3','label':'휠 버튼 3'}

class ShortcutTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.buttons=SimpleNamespace(path=Path(self.temp.name)/'radio-buttons.json',rows=[],clients={},task=None)
        self.service=SpeechShortcuts(self.buttons)

    def test_no_fixed_f8_and_settings_persist(self):
        self.assertEqual(self.service.settings()['bindings'],{'local':None,'cloud':None})
        self.service.save({'revision':0,'bindings':{'local':KEY,'cloud':HID}})
        other=SpeechShortcuts(self.buttons)
        self.assertEqual(other.settings()['bindings'],{'local':KEY,'cloud':HID})
        self.assertEqual(other.shortcuts(),[])
        other.listeners[asyncio.Queue()]='local'
        self.assertEqual(other.shortcuts(),[('key:118:1',118,1)])

    def test_conflicts_rejected_both_directions(self):
        with self.assertRaises(ValueError):
            self.service.save({'revision':0,'bindings':{'local':KEY,'cloud':KEY}})
        row={'enabled':True,'items':['fuel'],'keys':[KEY]}
        self.buttons.rows=[row]
        with self.assertRaises(ValueError):
            self.service.save({'revision':0,'bindings':{'local':KEY,'cloud':None}})
        self.buttons.rows=[]
        self.service.save({'revision':0,'bindings':{'local':KEY,'cloud':None}})
        with self.assertRaises(ValueError):
            self.service.check_radio([row])

    def test_revision_and_invalid_keys_do_not_overwrite(self):
        self.service.save({'revision':0,'bindings':{'local':KEY,'cloud':None}})
        for body in [
            {'revision':0,'bindings':{'local':None,'cloud':None}},
            {'revision':1,'bindings':{'local':{'code':'key:17:0','label':'Ctrl'},'cloud':None}},
        ]:
            with self.assertRaises(ValueError):self.service.save(body)
        self.assertEqual(self.service.rows['local'],KEY)

    def test_channels_press_release_and_focus_loss(self):
        self.service.save({'revision':0,'bindings':{'local':KEY,'cloud':HID}})
        local,cloud=asyncio.Queue(),asyncio.Queue()
        self.service.listeners={local:'local',cloud:'cloud'}
        self.service.poll(set(),'iracing')
        self.service.poll({KEY['code']},'iracing')
        self.assertTrue(local.get_nowait()['down']);self.assertTrue(cloud.empty())
        self.service.poll({KEY['code']},'iracing')
        self.assertTrue(local.empty())
        self.service.poll({KEY['code'],HID['code']},'iracing')
        self.assertTrue(cloud.get_nowait()['down'])
        self.service.poll(set(),None)
        self.assertFalse(local.get_nowait()['down']);self.assertFalse(cloud.get_nowait()['down'])

    def test_focus_entry_held_key_and_binding_capture_do_not_trigger(self):
        self.service.save({'revision':0,'bindings':{'local':KEY,'cloud':None}})
        q=asyncio.Queue();self.service.listeners[q]='local'
        self.service.poll({KEY['code']},'iracing')
        self.assertTrue(q.empty())
        self.service.poll(set(),'iracing')
        self.service.poll({KEY['code']},'iracing',capturing=True)
        self.assertTrue(q.empty())
        self.service.poll({KEY['code']},'iracing',capturing=False)
        self.assertTrue(q.empty())
        self.service.poll(set(),'iracing')
        self.service.poll({KEY['code']},'iracing')
        self.assertTrue(q.get_nowait()['down'])

    def test_api_persists_and_disables_legacy_polling(self):
        app,router=FastAPI(),APIRouter()
        coach=SimpleNamespace(live=SimpleNamespace())
        register(router,coach,self.buttons);app.include_router(router)
        with TestClient(app) as client:
            self.assertEqual(client.get('/speech-shortcuts').status_code,200)
            body={'revision':0,'bindings':{'local':KEY,'cloud':None}}
            self.assertEqual(client.post('/speech-shortcuts',json=body).status_code,200)
            self.assertEqual(client.post('/speech-shortcuts',json=body).status_code,409)
        self.assertTrue(coach.live.speech_shortcuts_managed)

if __name__=='__main__':unittest.main()
