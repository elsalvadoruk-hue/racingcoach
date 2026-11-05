from dataclasses import replace
from contextlib import closing
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest
from tire_reader.history import TireHistoryStore, TireHistoryWriter
from test_tire_advisor import snapshot


class HistoryTests(unittest.TestCase):
    def test_raw_numbers_and_lap_summary_keep_game_track_layout_and_exclude_quali(self):
        with tempfile.TemporaryDirectory() as temp:
            store=TireHistoryStore(Path(temp)/'history.sqlite3')
            try:
                for lap in range(3,7):
                    for tick in range(31):
                        store.append(replace(snapshot(float((lap-3)*40+tick)),lap=lap,session_type='Race'))
                laps=[(eligible,json.loads(data)) for eligible,data in store.db.execute('SELECT eligible,data FROM tire_laps')]
                self.assertEqual([e for e,_ in laps],[0,0,1])
                self.assertEqual(laps[-1][1]['wheels']['FL']['pressure_kpa']['median'],170.)
                for tick in range(31):
                    store.append(replace(snapshot(200.+tick),session_key='quali',lap=8,session_type='Open Qualify'))
                store.append(replace(snapshot(240.),session_key='quali',lap=9,session_type='Open Qualify'))
                last=store.db.execute('SELECT eligible,data FROM tire_laps ORDER BY id DESC LIMIT 1').fetchone()
                self.assertEqual(last[0],0);self.assertIn('session_type',json.loads(last[1])['excluded_reasons'])
                text=store.db.execute('SELECT data FROM tire_observations LIMIT 1').fetchone()[0]
                self.assertNotIn('image',text);self.assertNotIn('recognized',text)
                self.assertEqual(json.loads(text)['track'],'track-layout')
            finally:store.close()

    def test_missing_values_are_never_filled_and_pit_entry_finishes_stint(self):
        with tempfile.TemporaryDirectory() as temp:
            store=TireHistoryStore(Path(temp)/'history.sqlite3')
            try:
                s=snapshot(0.)
                s=replace(s,wheels={w:replace(v,pressure_kpa=None) if w=='FR' else v for w,v in s.wheels.items()})
                store.append(s);store.append(replace(s,t=1.,observed_at=1.,on_pit_road=True))
                row=json.loads(store.db.execute('SELECT data FROM tire_stints').fetchone()[0])
                self.assertNotIn('pressure_kpa',row['wheels']['FR'])
                self.assertEqual(row['wheels']['FL']['pressure_kpa']['samples'],1)
            finally:store.close()

    def test_worker_flushes_owned_database_and_disk_failure_does_not_stop_measurement(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'history.sqlite3';writer=TireHistoryWriter(path)
            writer.submit(snapshot());writer.close()
            self.assertFalse(writer.thread.is_alive());self.assertFalse(writer.error)
            with closing(sqlite3.connect(path)) as db:self.assertEqual(db.execute('SELECT count(*) FROM tire_observations').fetchone()[0],1)
            bad=TireHistoryWriter(path/'cannot-create.sqlite3')
            bad.submit(snapshot());bad.close()
            self.assertTrue(bad.error)
