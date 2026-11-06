"""Explicit run links and bounded, read-only tire attachments for coach log ZIPs."""
from contextlib import closing
import json
import lzma
from pathlib import Path
import re
import sqlite3

from .sdk import finite
from .snapshot import WHEELS, native_session_ref, slug

MAX_ROWS, REDUCED_ROWS = 30000, 1000
TOKEN = re.compile(r'[a-z][a-z0-9_]{0,63}')


def linked_run(live, snapshot):
    """Never join adjacent sessions using wall time alone; no SDK or I/O here."""
    if snapshot is None or not snapshot.native_session_ref or not live.lock.acquire(timeout=.02):
        return ''
    try:
        identity = live.identity
        if not live.run_id or not live.samples or identity.get('game') != snapshot.game:
            return ''
        latest = live.samples[-1]
        if not 0 <= live.clock() - latest['wall_time'] < 3:
            return ''
        if snapshot.t is None or finite(latest.get('session_time')) is None or abs(snapshot.t-latest['session_time']) > 5:
            return ''
        ref = native_session_ref(snapshot.game,identity.get('session_id'),identity.get('subsession_id'),identity.get('session_num'))
        if ref != snapshot.native_session_ref or snapshot.car != identity.get('car_path'):
            return ''
        if snapshot.game == 'iracing':
            track = f"iracing-track-{identity.get('track_id')}-{slug(identity.get('track_config'))}"
        else:
            track = identity.get('track_path')
        return live.run_id if snapshot.track == track else ''
    finally:
        live.lock.release()


def token(value):
    return value if isinstance(value,str) and TOKEN.fullmatch(value) else None


def coaching_summary(data):
    if not isinstance(data,dict):return {}
    result={k:finite(data.get(k)) for k in ('front_change_c','rear_change_c','pace_change_s')}
    result.update({k:token(data.get(k)) for k in ('reason','candidate')})
    result['ready']=data.get('ready') is True
    result['wear_or_grip_confirmed']=False
    for key in ('baseline_laps','recent_laps'):
        rows=data.get(key)
        result[key]=[v for v in (rows[:2] if isinstance(rows,list) else []) if type(v) is int and v>=0]
    return result


def diagnostics(message, snapshot, raw, targets, *, enabled, voice_enabled):
    """Only codes, numbers and semantics; never screenshot/OCR text or coordinates."""
    measurement=message.get('measurement') or {}
    result={'game':snapshot.game if snapshot else None,'t':snapshot.t if snapshot else None,
        'reader_status':token(message.get('status')),
        'display_enabled':enabled,'voice_enabled':voice_enabled,'visible':message.get('visible') is True,
        'configured_targets':{k:finite(v) for k,v in targets.items() if k in
            ('pressure_low_bar','pressure_high_bar','temp_low_c','temp_high_c')},
        'temperature_kind':token(targets.get('temperature_kind')),
        'source_ok':snapshot.source_ok if snapshot else {},
        'color_states':{w:{'pressure':token(v.pressure_state),'temperature':token(v.temp_state)}
                        for w,v in raw.wheels.items()} if raw else {},
        'measurement':{k:finite(measurement.get(k)) for k in ('locate_ms','ocr_ms')},
        'stage':token(measurement.get('stage')),
        'source_temperature_unit':measurement.get('temperature_unit') if measurement.get('temperature_unit') in ('C','F') else None,
    }
    for field in ('pressure','temperature','pressure_reason','temperature_reason'):
        result['measurement'][field]={w:token((measurement.get(field) or {}).get(w)) for w in WHEELS}
    result['measurement']['pressure_dot_verified']={w:(measurement.get('pressure_dot_verified') or {}).get(w) is True for w in WHEELS}
    for field in ('reader','reader_mode'):
        result['measurement'][field]=token(measurement.get(field))
    return result


def _observation(data):
    # An allowlist also keeps old/corrupt/extended DB fields out of shared logs.
    out={k:finite(data.get(k)) for k in ('t','lap','air_temp_c','track_temp_c','steering','throttle','brake','speed_mps',
        'fuel_l','lap_last_s','track_wetness_iracing','track_wetness_fraction_lmu','tire_compound_index')}
    out.update({k:data.get(k) if type(data.get(k)) is bool else None for k in ('active','in_garage','on_pit_road')})
    out.update({k:token(data.get(k)) for k in ('game','source')})
    out['session_type']=data.get('session_type') if data.get('session_type') in ('Race','Practice','Offline Testing','Open Qualify','Lone Qualify','Qualify','Warmup') else None
    out['wheels']={}
    for w in WHEELS:
        raw=(data.get('wheels') or {}).get(w) or {}
        value={k:finite(raw.get(k)) for k in ('pressure_kpa','temp_c','wear_fraction')}
        value.update({k:token(raw.get(k)) for k in ('temp_kind','pressure_state','temp_state')})
        value['surface_lcr_c']=[finite(v) for v in raw.get('surface_lcr_c',[])[:3]]
        out['wheels'][w]=value
    out['cold_pressure_kpa']={w:finite((data.get('cold_pressure_kpa') or {}).get(w)) for w in WHEELS}
    out['cold_pressure_source']={w:token((data.get('cold_pressure_source') or {}).get(w)) for w in WHEELS}
    out['cold_pressure_installed_verified']=False
    return out


def _selected(db,table,run_id,limit):
    total=db.execute(f'SELECT count(*) FROM {table} WHERE coach_run=?',(run_id,)).fetchone()[0]
    columns='created,data' if table in ('tire_observations','tire_diagnostics') else 'lap,eligible,data' if table=='tire_laps' else 'data'
    query=f'SELECT {columns} FROM {table} WHERE coach_run=? ORDER BY id'
    if total<=limit:
        rows=db.execute(query,(run_id,)).fetchall()
    else:
        rows=db.execute(query+' LIMIT ?',(run_id,limit//2)).fetchall()
        rows+=list(reversed(db.execute(query+' DESC LIMIT ?',(run_id,limit-limit//2)).fetchall()))
    return rows,{'total':total,'included':len(rows),'omitted':total-len(rows),'continuous':total<=limit,
                 'mode':'all_retained' if total<=limit else 'first_and_last_windows'}


def _summary(data):
    result={k:finite(data.get(k)) for k in ('samples','duration_observed_s')}
    result['wheels']={}
    for w in WHEELS:
        raw=(data.get('wheels') or {}).get(w) or {}
        fields={'temp_kind':token(raw.get('temp_kind'))}
        for key in ('pressure_kpa','temp_c','wear_fraction'):
            if isinstance(raw.get(key),dict):
                fields[key]={k:finite(raw[key].get(k)) for k in ('median','min','max','samples')}
        result['wheels'][w]=fields
    result['excluded_reasons']=[token(v) for v in data.get('excluded_reasons',[]) if token(v)]
    return result


def tire_data(path, *, prepare=lambda:True):
    """Same provider used by download, manual send and consented automatic send."""
    path=Path(path)
    def provider(run):
        from session_log import Extra
        if run.get('game') not in ('iracing','lmu'):
            return Extra('tire_data',info={'reason':'unknown_game'})
        flushed=prepare()
        if not path.is_file():
            return Extra('tire_data',info={'reason':'no_tire_history'})
        try:
            with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.15)) as db:
                tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                needed=('tire_observations','tire_diagnostics','tire_laps','tire_stints')
                if any(t not in tables or 'coach_run' not in {r[1] for r in db.execute(f'PRAGMA table_info({t})')} for t in needed):
                    return Extra('tire_data',info={'reason':'legacy_unlinked_history'})
                db.execute('BEGIN')
                data,selection={},{}
                for table,key in zip(needed,('observations','diagnostics','laps','stints')):
                    rows,selection[key]=_selected(db,table,run['run_id'],MAX_ROWS if key in ('observations','diagnostics') else 2000)
                    if key=='observations':
                        data[key]=[{'recorded_at':created,'data':_observation(json.loads(text))} for created,text in rows]
                    elif key=='diagnostics':
                        # Current writer already emits a strict allowlist; reapply it on export.
                        data[key]=[]
                        for created,text in rows:
                            raw=json.loads(text)
                            clean=diagnostics({'status':raw.get('reader_status'),'visible':raw.get('visible'),
                                'measurement':{**raw.get('measurement',{}),'stage':raw.get('stage'),'temperature_unit':raw.get('source_temperature_unit')}},
                                None,None,{**raw.get('configured_targets',{}),'temperature_kind':raw.get('temperature_kind')},
                                enabled=raw.get('display_enabled') is True,voice_enabled=raw.get('voice_enabled') is True)
                            clean.update(game=run['game'],t=finite(raw.get('t')),writer_dropped_total=finite(raw.get('writer_dropped_total')))
                            clean['coaching']=coaching_summary(raw.get('coaching'))
                            clean['source_ok']={w:{f:(raw.get('source_ok',{}).get(w,{}) or {}).get(f) is True
                                for f in ('pressure','temperature','wear')} for w in WHEELS}
                            clean['color_states']={w:{f:token((raw.get('color_states',{}).get(w,{}) or {}).get(f))
                                for f in ('pressure','temperature')} for w in WHEELS}
                            data[key].append({'recorded_at':created,'data':clean})
                    elif key=='laps':
                        data[key]=[{'lap':lap,'eligible':bool(eligible),'data':_summary(json.loads(text))} for lap,eligible,text in rows]
                    else:
                        data[key]=[_summary(json.loads(r[0])) for r in rows]
        except (OSError,sqlite3.Error,ValueError,TypeError,KeyError,AttributeError):
            return Extra('tire_data',info={'reason':'tire_history_unreadable'})
        if not any(data.values()):
            return Extra('tire_data',info={'reason':'no_linked_tire_records','legacy_rows_not_assigned':True})
        doc={'format':'racingcoach-tire-log','version':1,'game':run['game'],'run_id':run['run_id'],
            'units':{'pressure':'kPa','temperature':'C','display_pressure':'bar'},
            'wheel_order':list(WHEELS),'selection':selection,'collection_interval_s':1,
            'selection_scope':'explicit_coach_run_link','database_retention_is_bounded':True,
            'cold_pressure_installed_verified':False,'pending_writes_flushed':bool(flushed),
            'not_collected':['screenshots','recognized_text','pit_forecast','confirmed_installed_cold_pressure'],
            **data}
        def pack(document):
            info={k:len(document[k]) for k in ('observations','diagnostics','laps','stints')}
            info.update(selection=document['selection'],pending_writes_flushed=bool(flushed))
            return Extra('tire_data',{'tires.json.xz':lzma.compress(json.dumps(document,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode(),preset=1)},info)
        def fallback():
            reduced={**doc,'selection':{k:dict(v) for k,v in selection.items()}}
            for key in ('observations','diagnostics'):
                rows=doc[key]
                if len(rows)>REDUCED_ROWS:
                    reduced[key]=rows[:REDUCED_ROWS//2]+rows[-(REDUCED_ROWS-REDUCED_ROWS//2):]
                    reduced['selection'][key].update(included=len(reduced[key]),omitted=selection[key]['total']-len(reduced[key]),
                        continuous=False,mode='first_and_last_windows')
            return pack(reduced)
        extra=pack(doc);extra.fallback=fallback
        return extra
    return provider
