"""Read the existing, verified LMU transport. No screen capture or second ABI."""
import time
from .cockpit_gate import CockpitGate
from .snapshot import TireSnapshot,WheelState,WHEELS,bounded,slug,native_session_ref
from .sdk import finite


def lmu_context(row):
    return (str(row.get('car_path') or '').startswith('lmu-car-') and row.get('fresh') is True
            and row.get('IsOnTrack') is True and row.get('IsReplayPlaying') is False
            and row.get('IsInGarage') is False)


class LmuTireReader:
    def __init__(self,*,source_factory=None,clock=time.monotonic):
        self.source_factory,self.clock=source_factory,clock
        self.source=None;self.generation=0;self.last_et=None;self.last_received=None
        self.scoring=None;self.identity=None;self.gate=CockpitGate(10,context=lmu_context)
        from .installed import LmuInstalledPressure
        self.installed=LmuInstalledPressure()

    def close(self):
        source,self.source=self.source,None
        if source is not None:source.close()

    def sample(self):
        from lmu_frame import normalise
        now=self.clock()
        row=dict(status='sdk_waiting',overlay_ready=False,cockpit_epoch=self.gate.epoch)
        try:
            if self.source is None:
                factory=self.source_factory
                if factory is None:
                    from lmu_irsdk import shared_memory_source_factory
                    factory=shared_memory_source_factory()
                candidate=factory()
                try:candidate.open()
                except Exception:
                    candidate.close();raise
                self.source=candidate;self.generation+=1
                self.last_et=self.last_received=self.scoring=None
            raw=self.source.read(timeout_ms=20)
            clean=normalise(raw)
            et=clean.get('telemetry_et') if clean else None
            if et is None or et==self.last_et:
                alive=self.last_received is not None and now-self.last_received<2
                if not alive:self.gate.update({},now)
                return dict(row,status='telemetry_waiting',overlay_ready=alive and self.gate.entered is not None and now-self.gate.entered>=10,
                            car_path=self.identity[0] if self.identity else None)
            self.last_et,self.last_received=et,now
            model=slug(raw.get('vehicle_model'))[:160].rstrip('-')
            car='lmu-car-'+model if model else ''
            track='lmu-track-'+slug(raw.get('track_name')) if slug(raw.get('track_name')) else ''
            identity=(car,track,clean['session_key'],clean['vehicle_id'])
            if identity!=self.identity:
                self.identity=identity;self.scoring=None
            if clean.get('scoring_et') is not None and -0.25<=et-clean['scoring_et']<=2:
                self.scoring={k:clean.get(k) for k in ('scoring_et','control_code','realtime','paused',
                    'in_garage_stall','in_pits','total_laps','ambient_temp_c','track_temp_c','session_type_code')}
            scoring=self.scoring if self.scoring and 0<=et-self.scoring['scoring_et']<=4 else {}
            context=dict(car_path=car,track_id=track,session_id=clean['session_key'],SessionNum=clean.get('session_type_code'),
                generation=self.generation,fresh=True,SessionTime=et,
                IsOnTrack=scoring.get('control_code')==0 and scoring.get('realtime') is True and scoring.get('paused') is False,
                IsReplayPlaying=scoring.get('realtime') is not True,IsInGarage=scoring.get('in_garage_stall'))
            # A short scoring gap keeps this cockpit; a real session change never does.
            context['SessionNum']=scoring.get('session_type_code')
            gate=self.gate.update(context,now)
            row.update(status=gate['status'],overlay_ready=gate['ready'],cockpit_epoch=gate['epoch'],
                warmup_remaining_s=gate['remaining_s'],car_path=car,car_name=str(raw.get('vehicle_model') or '')[:160])
            laps=scoring.get('total_laps');lap=int(laps)+1 if finite(laps) is not None and laps>=0 else None
            # Cold pressures are measured live (garage, new tires on pit road), also outside the gate.
            installed=self.installed.update(identity,{w:(item.get('pressure_kpa'),item.get('wear_fraction'))
                for w,item in zip(WHEELS,clean['wheels'])},in_garage=scoring.get('in_garage_stall'),
                in_pits=scoring.get('in_pits'),on_track=context['IsOnTrack'],lap=lap)
            wheels={w:WheelState() for w in WHEELS}
            if gate['ready']:
                row.update(status='reading',temperature_kind='surface_center')
                for w,item in zip(WHEELS,clean['wheels']):
                    points=tuple(bounded(v,-50,250) for v in item['temperature_c'])
                    wheel=WheelState(pressure_kpa=bounded(item['pressure_kpa'],40,400),
                        temp_c=points[1],temp_kind='surface_center',surface_lcr_c=points,
                        wear_fraction=bounded(item['wear_fraction'],0,1))
                    wheels[w]=wheel
                    row['raw_kpa_'+w]=wheel.pressure_kpa;row['raw_temp_c_'+w]=wheel.temp_c
            snapshot=TireSnapshot(game='lmu',car=car,track=track,session_key=clean['session_key'] or '',
                t=et,observed_at=now,lap=lap,active=gate['ready'],wheels=wheels,
                air_temp_c=bounded(scoring.get('ambient_temp_c'),-80,100),
                track_temp_c=bounded(scoring.get('track_temp_c'),-80,150),
                in_garage=scoring.get('in_garage_stall'),on_pit_road=scoring.get('in_pits'),
                source='lmu_shared_memory',epoch=gate['epoch'],steering=finite(clean.get('steering_ratio')),
                throttle=bounded(clean.get('throttle_ratio'),0,1),brake=bounded(clean.get('brake_ratio'),0,1),
                speed_mps=bounded(clean.get('speed_mps'),0,150),
                native_session_ref=native_session_ref('lmu',(clean['session_key'] or '')[:16],(clean['session_key'] or '')[16:32],None),
                fuel_l=bounded(clean.get('fuel_l'),0,1000),lap_last_s=bounded(clean.get('last_lap_time'),.001,86400),
                installed_cold_kpa=installed['installed_cold_kpa'],installed_cold_source=installed['installed_cold_source'],
                installed_lap=installed['installed_lap'],
                track_wetness_fraction_lmu=bounded(clean.get('track_wetness_ratio'),0,1),
                session_type=next((name for a,b,name in ((0,0,'Offline Testing'),(1,4,'Practice'),
                    (5,8,'Open Qualify'),(9,9,'Warmup'),(10,13,'Race'))
                    if type(scoring.get('session_type_code')) is int and a<=scoring['session_type_code']<=b),None))
            row['tire_snapshot']=snapshot.to_dict()
            return row
        except Exception as error:
            self.close();self.gate.update({},now)
            row.update(status='sdk_waiting',error_type=type(error).__name__,error_code=getattr(error,'code',None))
            return row
