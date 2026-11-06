"""Bounded local numeric history, written away from the API/OCR threads."""
from collections import Counter, defaultdict
import json
from pathlib import Path
from queue import Queue, Empty, Full
import sqlite3
import statistics
import threading
import time
import uuid
from .snapshot import WHEELS


class TireHistoryStore:
    def __init__(self, path):
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(path,timeout=.5)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS tire_observations (
                id INTEGER PRIMARY KEY, created REAL NOT NULL, run TEXT NOT NULL,
                game TEXT NOT NULL, car TEXT NOT NULL, track TEXT NOT NULL,
                lap INTEGER, t REAL, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS tire_lookup ON tire_observations(game,car,track);
            CREATE TABLE IF NOT EXISTS tire_laps (
                id INTEGER PRIMARY KEY, run TEXT NOT NULL, lap INTEGER,
                eligible INTEGER NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tire_stints (
                id INTEGER PRIMARY KEY, run TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tire_diagnostics (
                id INTEGER PRIMARY KEY, created REAL NOT NULL, coach_run TEXT NOT NULL,
                data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS tire_diagnostic_run ON tire_diagnostics(coach_run);
        ''')
        # Additive migration: existing unlinked rows stay unlinked.
        for table in ('tire_observations','tire_laps','tire_stints'):
            if 'coach_run' not in {r[1] for r in self.db.execute(f'PRAGMA table_info({table})')}:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN coach_run TEXT NOT NULL DEFAULT ''")
            self.db.execute(f'CREATE INDEX IF NOT EXISTS {table}_coach ON {table}(coach_run)')
        self.db.commit()
        self.identity=None;self.last_t=None;self.run=None;self.lap=None
        self.coach_run=''
        self.lap_rows=[];self.first_lap=None;self.stint_rows=[];self.was_pit=None;self.count=0
        self.stint_laps=[];self.last_stint_id=None;self.attached_stop=None

    def close(self):
        self.db.close()

    @staticmethod
    def summary(rows):
        result={}
        for wheel in WHEELS:
            fields={}
            for attr in ('pressure_kpa','temp_c','wear_fraction'):
                values=[getattr(s.wheels[wheel],attr) for s in rows if getattr(s.wheels[wheel],attr) is not None]
                if values:
                    fields[attr]={'median':statistics.median(values),'min':min(values),'max':max(values),'samples':len(values)}
            kinds={s.wheels[wheel].temp_kind for s in rows if s.wheels[wheel].temp_c is not None}
            fields['temp_kind']=next(iter(kinds)) if len(kinds)==1 else None
            result[wheel]=fields
        return {'wheels':result,'samples':len(rows),
            'duration_observed_s':max(0,rows[-1].t-rows[0].t) if rows and all(s.t is not None for s in (rows[0],rows[-1])) else None,
            'conditions':TireHistoryStore.conditions(rows) if rows else {}}

    @staticmethod
    def conditions(rows):
        """Track/air temperature, fuel, laps and the evidenced installed cold pressure of the rows."""
        def values(attr):return [getattr(s,attr) for s in rows if getattr(s,attr,None) is not None]
        def middle(attr):
            found=values(attr);return statistics.median(found) if found else None
        track=values('track_temp_c');laps=sorted({s.lap for s in rows if s.lap is not None})
        fuel=values('fuel_l');tod=values('session_time_of_day_s')
        installed,sources={},{}
        for w in WHEELS:
            # Only a value that held for every row describes this lap/stint.
            seen={(s.installed_cold_kpa.get(w),s.installed_cold_source.get(w)) for s in rows}
            installed[w],sources[w]=next(iter(seen)) if len(seen)==1 else (None,None)
        states={w:{field:dict(Counter(getattr(s.wheels[w],attr) for s in rows if getattr(s.wheels[w],attr)))
                   for field,attr in (('pressure','pressure_state'),('temperature','temp_state'))} for w in WHEELS}
        return {'air_temp_c':middle('air_temp_c'),'track_temp_c':middle('track_temp_c'),
            'track_temp_range_c':[min(track),max(track)] if track else None,
            'session_types':sorted({s.session_type for s in rows if s.session_type}),
            'fuel_start_l':fuel[0] if fuel else None,'fuel_end_l':fuel[-1] if fuel else None,
            'first_lap':laps[0] if laps else None,'last_lap':laps[-1] if laps else None,'laps':len(laps),
            'time_of_day_s':[tod[0],tod[-1]] if tod else None,
            'installed_cold_kpa':installed,'installed_cold_source':sources,
            'fixed_setup':rows[-1].fixed_setup,'compound':rows[-1].tire_compound_index,
            'wet':rows[-1].track_wetness_iracing if rows[-1].game=='iracing' else rows[-1].track_wetness_fraction_lmu,
            'states':states}

    def _finish_lap(self):
        if not self.lap_rows:
            return
        rows=self.lap_rows;data=self.summary(rows)
        reasons=[]
        if not all(s.session_type in ('Race','Practice','Offline Testing') for s in rows):reasons.append('session_type')
        if any(s.on_pit_road is not False for s in rows):reasons.append('pit_or_unknown')
        if self.first_lap is None or self.lap is None or self.lap-self.first_lap<2:reasons.append('warmup')
        if len(rows)<30:reasons.append('insufficient_samples')
        if any(b.t-a.t>3 or b.t<a.t for a,b in zip(rows,rows[1:]) if a.t is not None and b.t is not None):reasons.append('measurement_gap')
        if any(data['wheels'][w].get('pressure_kpa',{}).get('samples',0)<30 for w in WHEELS):reasons.append('partial_pressure')
        data.update(game=rows[0].game,car=rows[0].car,track=rows[0].track,excluded_reasons=reasons)
        self.db.execute('INSERT INTO tire_laps(run,lap,eligible,data,coach_run) VALUES(?,?,?,?,?)',
            (self.run,self.lap,int(not reasons),json.dumps(data,allow_nan=False),self.coach_run))
        if not reasons:
            # Eligible laps of this stint: the evidence later used for pressure advice.
            self.stint_laps.append({'lap':self.lap,**{f'{w}_{key}':data['wheels'][w].get(attr,{}).get('median')
                for w in WHEELS for key,attr in (('kpa','pressure_kpa'),('c','temp_c'))}})

    def _attach_pit_snapshot(self, s):
        """iRacing measures temperatures/wear at the stop that ends a stint; keep them with it."""
        stop=s.pit_snapshot
        if not isinstance(stop,dict) or stop.get('stop')==self.attached_stop or self.last_stint_id is None:
            return
        self.attached_stop=stop.get('stop')
        row=self.db.execute('SELECT data FROM tire_stints WHERE id=?',(self.last_stint_id,)).fetchone()
        if row:
            data=json.loads(row[0]);data['pit_snapshot']=stop
            self.db.execute('UPDATE tire_stints SET data=? WHERE id=?',(json.dumps(data,allow_nan=False),self.last_stint_id))

    def append(self, snapshot, *, run_id='', diagnostics=None, created=None):
        s=snapshot
        created=time.time() if created is None else created
        if diagnostics is not None:
            self.db.execute('INSERT INTO tire_diagnostics(created,coach_run,data) VALUES(?,?,?)',
                (created,run_id,json.dumps(diagnostics,allow_nan=False)))
        self.count+=1
        if self.count%60==0:
            for table,limit in (('tire_observations',200000),('tire_diagnostics',200000),('tire_laps',10000),('tire_stints',2000)):
                self.db.execute(f'DELETE FROM {table} WHERE id <= (SELECT max(id)-? FROM {table})',(limit,))
        if s is None:
            self.db.commit()
            return
        changed=s.identity!=self.identity or run_id!=self.coach_run or self.last_t is not None and s.t is not None and s.t<self.last_t
        if changed or not s.active:
            self.identity=s.identity;self.run=uuid.uuid4().hex;self.lap=None
            self.lap_rows=[];self.stint_rows=[];self.first_lap=None;self.was_pit=None;self.stint_laps=[]
        self._attach_pit_snapshot(s)
        self.coach_run=run_id
        self.last_t=s.t
        numeric=any(any(fields.values()) for fields in s.source_ok.values())
        cold=any(v is not None for v in s.cold_pressure_kpa.values())
        if not s.car or not s.track or not (s.active and numeric or cold):
            self.db.commit()
            return
        observation=s.to_dict();observation.pop('pit_snapshot',None)  # kept once, with its stint
        self.db.execute('INSERT INTO tire_observations(created,run,game,car,track,lap,t,data,coach_run) VALUES(?,?,?,?,?,?,?,?,?)',
            (created,self.run,s.game,s.car,s.track,s.lap,s.t,json.dumps(observation,allow_nan=False),run_id))
        if not s.active or not numeric:
            self.db.commit()
            return
        if self.lap is not None and s.lap!=self.lap:
            # Skipped laps do not turn a partial record into a complete lap.
            if s.lap is not None and s.lap==self.lap+1:self._finish_lap()
            self.lap_rows=[]
        self.lap=s.lap
        if self.first_lap is None:self.first_lap=s.lap
        if s.on_pit_road is True and self.was_pit is False and self.stint_rows:
            summary=self.summary(self.stint_rows)
            summary.update(game=s.game,car=s.car,track=s.track,eligible_laps=self.stint_laps)
            cursor=self.db.execute('INSERT INTO tire_stints(run,data,coach_run) VALUES(?,?,?)',(self.run,json.dumps(summary,allow_nan=False),run_id))
            self.last_stint_id=cursor.lastrowid
            self.stint_rows=[];self.first_lap=s.lap;self.stint_laps=[]
        self.was_pit=s.on_pit_road
        self.lap_rows.append(s)
        if s.on_pit_road is False:self.stint_rows.append(s)
        # Only keep enough in RAM to summarize a long endurance stint.
        self.lap_rows=self.lap_rows[-1800:];self.stint_rows=self.stint_rows[-14400:]
        self.db.commit()


class TireHistoryWriter:
    def __init__(self, path):
        self.path=path;self.queue=Queue(maxsize=64);self.stop=threading.Event()
        self.error=False;self.thread=None
        self.deadline=None
        self.dropped=0

    def submit(self, snapshot, *, run_id='', diagnostics=None):
        if self.thread is None:
            self.thread=threading.Thread(target=self._run,name='tire-history',daemon=True);self.thread.start()
        if diagnostics is not None:
            diagnostics={**diagnostics,'writer_dropped_total':self.dropped}
        try:self.queue.put_nowait((snapshot,run_id,diagnostics,time.time()))
        except Full:self.error=True;self.dropped+=1

    def flush(self, timeout=1.):
        """Export-only barrier; never called by a sampler or while holding its lock."""
        if self.thread is None:
            return True
        done=threading.Event()
        try:self.queue.put(done,timeout=min(timeout,.25))
        except Full:return False
        return done.wait(timeout) and not self.error

    def _run(self):
        store=None
        try:
            while not self.stop.is_set() or not self.queue.empty():
                if self.deadline is not None and time.monotonic()>=self.deadline:
                    self.error |= not self.queue.empty()
                    break
                try:item=self.queue.get(timeout=.1)
                except Empty:continue
                if isinstance(item,threading.Event):
                    item.set();continue
                snapshot,run_id,diagnostics,created=item
                if store is None:
                    try:store=TireHistoryStore(self.path)
                    except (OSError,sqlite3.Error):self.error=True;continue
                try:store.append(snapshot,run_id=run_id,diagnostics=diagnostics,created=created)
                except (ValueError,sqlite3.Error):self.error=True
        finally:
            if store is not None:store.close()

    def close(self):
        self.deadline=time.monotonic()+2
        self.stop.set()
        if self.thread is not None:self.thread.join(timeout=3)
