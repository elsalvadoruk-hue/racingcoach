"""Game-neutral observations. Missing samples and held display values stay distinct."""
from dataclasses import asdict, dataclass, field
import hashlib
import re
from .sdk import finite

WHEELS=('FL','FR','RL','RR')


def native_session_ref(game, session_id, subsession_id, session_num):
    """Local comparison only; raw native IDs are never put in exported tire data."""
    if game == 'iracing' and session_num is None or game == 'lmu' and (not session_id or not subsession_id):
        return ''
    # Offline iRacing can expose 0 IDs; the main adapter represents those as ''.
    parts = [game, str(session_id or ''), str(subsession_id or '')]
    if game == 'iracing':
        parts.append(str(session_num))
    return hashlib.sha256(':'.join(parts).encode()).hexdigest()


def bounded(value,low,high):
    value=finite(value)
    return value if value is not None and low<=value<=high else None


def slug(value):
    return re.sub(r'[^a-z0-9]+','-',str(value or '')[:200].lower()).strip('-')


@dataclass(frozen=True)
class WheelState:
    pressure_kpa: float | None=None
    temp_c: float | None=None
    temp_kind: str | None=None
    surface_lcr_c: tuple=()
    wear_fraction: float | None=None
    pressure_state: str | None=None
    temp_state: str | None=None


@dataclass(frozen=True)
class TireSnapshot:
    game: str
    car: str
    track: str
    session_key: str
    t: float | None
    observed_at: float
    lap: int | None
    active: bool
    wheels: dict=field(default_factory=dict)
    air_temp_c: float | None=None
    track_temp_c: float | None=None
    in_garage: bool | None=None
    on_pit_road: bool | None=None
    source: str=''
    epoch: int=0
    steering: float | None=None
    throttle: float | None=None
    brake: float | None=None
    speed_mps: float | None=None
    session_type: str | None=None
    native_session_ref: str=''
    cold_pressure_kpa: dict=field(default_factory=dict)
    cold_pressure_source: dict=field(default_factory=dict)
    fuel_l: float | None=None
    lap_last_s: float | None=None
    track_wetness_iracing: float | None=None
    track_wetness_fraction_lmu: float | None=None
    tire_compound_index: float | None=None
    # Evidence-based installed cold pressure (installed.py) and iRacing's last pit measurement.
    installed_cold_kpa: dict=field(default_factory=dict)
    installed_cold_source: dict=field(default_factory=dict)
    pit_snapshot: dict | None=None
    session_time_of_day_s: float | None=None
    fixed_setup: bool | None=None

    @property
    def source_ok(self):
        return {w:{'pressure':v.pressure_kpa is not None,'temperature':v.temp_c is not None,
                   'wear':v.wear_fraction is not None} for w,v in self.wheels.items()}

    @property
    def identity(self):
        return self.game,self.car,self.track,self.session_key,self.epoch

    def to_dict(self):
        data=asdict(self)
        for wheel in data['wheels'].values():wheel['surface_lcr_c']=list(wheel['surface_lcr_c'])
        return {'version':1,**data,'source_ok':self.source_ok}

    @classmethod
    def from_dict(cls,data):
        if data.get('version')!=1 or data.get('game') not in ('iracing','lmu'):
            raise ValueError('Unsupported tire observation')
        wheels={w:WheelState(**{**data['wheels'][w],'surface_lcr_c':tuple(data['wheels'][w]['surface_lcr_c'])}) for w in WHEELS}
        values={key:data[key] for key in cls.__dataclass_fields__ if key!='wheels' and key in data}
        return cls(**values,wheels=wheels)


def from_iracing(row,context,now):
    measured=row.get('status') in ('reading','waiting_for_agreement') and row.get('overlay_ready') is True
    wheels={w:WheelState(
        pressure_kpa=bounded(row.get('raw_kpa_'+w),40,400) if measured else None,
        temp_c=bounded(row.get('raw_temp_c_'+w),-50,250) if measured else None,
        temp_kind=row.get('temperature_kind'),
        pressure_state=(row.get('pressure_states') or {}).get(w) if measured else None,
        temp_state=(row.get('temperature_states') or {}).get(w) if measured else None) for w in WHEELS}
    session=':'.join(str(context.get(k) or '') for k in ('generation','session_id','subsession_id','SessionNum'))
    lap=context.get('Lap')
    cold, sources = {}, {}
    for w, sdk_wheel in zip(WHEELS, ('LF','RF','LR','RR')):
        value = bounded(context.get(sdk_wheel+'coldPressure'),40,400)
        source = 'sdk_cold_setting' if value is not None else None
        if value is None:
            value = bounded((context.get('garage_cold_pressure_kpa') or {}).get(w),40,400)
            source = 'garage_setup_snapshot' if value is not None else None
        cold[w], sources[w] = value, source
    return TireSnapshot(game='iracing',car=str(row.get('car_path') or ''),
        track=f"iracing-track-{context.get('track_id')}-{slug(context.get('track_config'))}" if context.get('track_id') is not None else '',
        session_key=hashlib.sha256(session.encode()).hexdigest(),t=finite(context.get('SessionTime')),
        observed_at=now,lap=lap if type(lap) is int and lap>=0 else None,
        active=row.get('overlay_ready') is True,wheels=wheels,
        air_temp_c=bounded(context.get('AirTemp'),-80,100),track_temp_c=bounded(context.get('TrackTempCrew'),-80,150),
        in_garage=context.get('IsInGarage'),on_pit_road=context.get('OnPitRoad'),source='dashboard_ocr',
        epoch=row.get('cockpit_epoch',0),steering=finite(context.get('SteeringWheelAngle')),
        throttle=bounded(context.get('Throttle'),0,1),brake=bounded(context.get('Brake'),0,1),
        speed_mps=bounded(context.get('Speed'),0,150), session_type=context.get('session_type'),
        native_session_ref=native_session_ref('iracing',context.get('session_id'),context.get('subsession_id'),context.get('SessionNum')),
        cold_pressure_kpa=cold,cold_pressure_source=sources,
        fuel_l=bounded(context.get('FuelLevel'),0,1000),lap_last_s=bounded(context.get('LapLastLapTime'),.001,86400),
        track_wetness_iracing=bounded(context.get('TrackWetness'),0,7),
        tire_compound_index=bounded(context.get('PlayerTireCompound'),0,255),
        installed_cold_kpa={w:bounded((context.get('installed_cold_kpa') or {}).get(w),40,400) for w in WHEELS},
        installed_cold_source={w:(context.get('installed_cold_source') or {}).get(w) for w in WHEELS},
        pit_snapshot=context.get('pit_snapshot'),
        session_time_of_day_s=bounded(context.get('SessionTimeOfDay'),0,86400*2),
        fixed_setup=context.get('fixed_setup') if type(context.get('fixed_setup')) is bool else None)


class TireReadings:
    """Per-field ages for decisions; the display may keep a value indefinitely."""
    def __init__(self):
        self.identity=None;self.last_t=None;self.values={}

    def update(self,snapshot):
        if (self.identity!=snapshot.identity or not snapshot.active or
                self.last_t is not None and snapshot.t is not None and snapshot.t<self.last_t):
            self.values.clear()
        self.identity,self.last_t=snapshot.identity,snapshot.t
        if snapshot.active:
            for w,wheel in snapshot.wheels.items():
                for name,attr in (('pressure','pressure_kpa'),('temperature','temp_c'),('wear','wear_fraction')):
                    value=getattr(wheel,attr)
                    if value is not None:self.values[w,name]=(value,snapshot.observed_at)

    def age(self,wheel,field,now):
        saved=self.values.get((wheel,field))
        return max(0.,now-saved[1]) if saved else None

    def value(self,wheel,field,now,max_age=30.):
        saved=self.values.get((wheel,field));age=self.age(wheel,field,now)
        return saved[0] if saved and now>=saved[1] and (max_age is None or age<=max_age) else None
