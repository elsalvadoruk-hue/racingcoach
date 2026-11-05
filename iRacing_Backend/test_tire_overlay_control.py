import json
from pathlib import Path
from queue import Queue
import tempfile
import unittest
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from tire_overlay_control import TireOverlayController, routes


class Pipe:
    def __init__(self): self.queue=Queue()
    def __iter__(self): return self
    def __next__(self):
        value=self.queue.get(timeout=10)
        if value is None: raise StopIteration
        return json.dumps(value)+'\n'
    def close(self): pass


class Child:
    def __init__(self, fatal=False):
        self.code=None;self.stdout=Pipe();self.stdin=Mock()
        self.stdin.write.side_effect=self.command
        self.stdin.close.side_effect=self.exit
        self.stdout.queue.put(dict(type='fatal',code='dependencies_missing') if fatal else dict(type='ready'))
    def command(self,line):
        data=json.loads(line);self.stdout.queue.put(dict(type='reply',id=data['id'],ok=True))
    def poll(self): return self.code
    def wait(self,timeout=None): return self.code
    def exit(self): self.code=0;self.stdout.queue.put(None)
    def terminate(self): self.exit()


class TireControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'overlay/iracing/tires.json'
        self.children=[]
        def popen(*args,**kwargs):
            child=Child();self.children.append(child);return child
        self.spawn=Mock(side_effect=popen)
        self.controller=TireOverlayController(self.path,popen=self.spawn)
        self.addCleanup(self.controller.close)

    def test_read_status_does_not_start_window_or_save_settings(self):
        self.assertFalse(self.controller.status()['enabled'])
        self.spawn.assert_not_called();self.assertFalse(self.path.exists())

    def test_independent_toggle_fixed_position_and_restore_on_app_start(self):
        coaching=self.path.with_name('coaching.json');coaching.parent.mkdir(parents=True)
        coaching.write_bytes(b'original-coaching-settings')
        result=self.controller.update(enabled=True,fixed=True)
        self.assertTrue(result['running']);self.assertTrue(result['enabled'])
        self.assertIn('--fixed',self.spawn.call_args.args[0])
        self.assertIn('tires-position.json',self.spawn.call_args.args[0][self.spawn.call_args.args[0].index('--position')+1])
        self.controller.update(fixed=False);self.controller.reset_position()
        self.controller.close()
        self.assertTrue(json.loads(self.path.read_text())['enabled'])
        self.controller.start();self.assertEqual(len(self.children),2)
        self.controller.update(enabled=False)
        self.assertFalse(self.controller.status()['running'])
        self.assertFalse(json.loads(self.path.read_text())['enabled'])
        self.assertEqual(coaching.read_bytes(),b'original-coaching-settings')

    def test_dependency_failure_stops_owned_child_and_is_actionable(self):
        child=Child(fatal=True);self.spawn.side_effect=lambda *a,**k:child
        with self.assertRaisesRegex(RuntimeError,'설치.bat'):
            self.controller.update(enabled=True)
        self.assertFalse(self.controller.status()['enabled'])
        self.assertIsNone(self.controller.process)
        self.assertEqual(child.poll(),0)

    def test_saved_position_reset_without_starting_ocr(self):
        position=self.path.with_name('tires-position.json');position.parent.mkdir(parents=True)
        position.write_text('{"x":300,"y":100}')
        self.controller.reset_position()
        self.assertFalse(position.exists());self.spawn.assert_not_called()

    def test_spawn_failure_remains_visible_during_status_polling(self):
        self.spawn.side_effect=OSError('synthetic startup failure')
        with self.assertRaisesRegex(OSError,'synthetic startup failure'):
            self.controller.update(enabled=True)
        self.assertEqual(self.controller.status()['error'],'synthetic startup failure')
        self.assertFalse(self.controller.status()['enabled'])

    def test_api_rejects_unrecognized_fields_and_non_boolean_values(self):
        app=FastAPI();app.include_router(routes(self.controller));client=TestClient(app)
        for body in ({'enabled':1},{'fixed':'false'},{'command':'capture'},{'position':'other'}):
            self.assertEqual(client.put('/api/tire-overlay/settings',json=body).status_code,422)
        self.spawn.assert_not_called()
        self.assertEqual(client.get('/api/tire-overlay').json()['version'],1)
        self.assertEqual(client.put('/api/tire-overlay/settings',json={'enabled':True}).status_code,200)
        self.assertEqual(client.post('/api/tire-overlay/reset-position').status_code,200)

    def test_platform_without_windows_leaves_main_app_available(self):
        with patch('tire_overlay_control.sys.platform','linux'):
            self.assertFalse(self.controller.status()['supported'])
            with self.assertRaisesRegex(RuntimeError,'Windows'):
                self.controller.update(enabled=True)
        self.spawn.assert_not_called()

    def test_independent_opacity_radius_are_saved_and_restored(self):
        status=self.controller.update(background_opacity=25,content_opacity=60,radius=16)
        self.assertEqual(status['background_opacity'],25)
        self.assertEqual(status['content_opacity'],60)
        self.assertEqual(status['radius'],16)
        self.spawn.assert_not_called()
        other=TireOverlayController(self.path,popen=self.spawn)
        self.assertEqual(other.status()['radius'],16)
        self.assertEqual(other.status()['background_opacity'],25)

    def test_live_appearance_update_is_sent_without_starting_another_child(self):
        self.controller.update(enabled=True)
        self.controller.update(background_opacity=0,content_opacity=0,radius=0)
        self.assertEqual(self.spawn.call_count,1)
        sent=[json.loads(call.args[0]) for call in self.children[0].stdin.write.call_args_list]
        self.assertEqual(sent[-1]['action'],'appearance')
        self.assertEqual(sent[-1]['background_opacity'],0)

    def test_appearance_api_requires_bounded_integers(self):
        app=FastAPI();app.include_router(routes(self.controller));client=TestClient(app)
        for body in ({'radius':-1},{'radius':33},{'radius':True},{'content_opacity':101},
                     {'background_opacity':'50'},{'content_opacity':50.1}):
            self.assertEqual(client.put('/api/tire-overlay/settings',json=body).status_code,422)
        response=client.put('/api/tire-overlay/settings',json={'background_opacity':0,'content_opacity':100,'radius':12})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['background_opacity'],0)

    def test_voice_only_measures_without_showing_and_both_off_stops(self):
        result=self.controller.update(voice_enabled=True)
        self.assertFalse(result['enabled']);self.assertTrue(result['voice_enabled']);self.assertTrue(result['running'])
        self.assertIn('--hidden',self.spawn.call_args.args[0])
        self.controller.update(enabled=True)
        self.controller.update(enabled=False)
        self.assertEqual(self.spawn.call_count,1)
        self.assertTrue(self.controller.status()['running'])
        self.controller.close();self.controller.start()
        self.assertIn('--hidden',self.spawn.call_args.args[0])
        self.assertTrue(json.loads(self.path.read_text())['voice_enabled'])
        self.controller.update(voice_enabled=False)
        self.assertFalse(self.controller.status()['running'])

    def test_game_settings_and_positions_are_isolated(self):
        from tire_overlay_control import TireOverlays
        hub=TireOverlays()
        for game,c in hub.controllers.items():
            c.path=Path(self.temp.name)/'overlay'/game/'tires.json';c.popen=self.spawn
        self.addCleanup(hub.close)
        app=FastAPI();app.include_router(routes(hub));client=TestClient(app)
        result=client.put('/api/tire-overlay/settings?game=lmu',json={'voice_enabled':True})
        self.assertEqual(result.status_code,200);self.assertEqual(result.json()['game'],'lmu')
        self.assertFalse(client.get('/api/tire-overlay').json()['voice_enabled'])
        self.assertEqual(client.get('/api/tire-overlay?game=other').status_code,422)
        args=self.spawn.call_args.args[0]
        self.assertEqual(args[args.index('--game')+1],'lmu')
