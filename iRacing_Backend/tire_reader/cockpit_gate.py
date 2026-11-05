"""Only observe the live player's cockpit, after ten seconds of settling."""
import math
from .sdk import finite
from .vehicle_dash import canonical_car


def cockpit_context(row):
    return (canonical_car(row.get('car_path')) is not None and row.get('fresh')
            and row.get('IsOnTrack') in (True,1)
            and row.get('IsOnTrackCar') not in (False,0)
            and row.get('IsReplayPlaying') in (False,0)
            and row.get('IsInGarage') not in (True,1))


class CockpitGate:
    def __init__(self,delay=10,*,context=cockpit_context):
        self.delay=delay
        self.context=context
        self.reset()

    def reset(self):
        self.identity=None;self.entered=None;self.last_session_time=None;self.epoch=0

    def update(self,row,now):
        if not self.context(row):
            # Lack of fresh telemetry is not evidence of a continuous live cockpit.
            self.identity=None;self.entered=None;self.last_session_time=None
            return dict(ready=False,remaining_s=self.delay,status='cockpit_waiting',epoch=self.epoch)
        identity=tuple(row.get(k) for k in ('generation','car_path','track_id','track_config',
                                          'SessionNum','session_id','subsession_id'))
        current=finite(row.get('SessionTime'))
        rewind=current is not None and self.last_session_time is not None and current<self.last_session_time
        if identity!=self.identity or rewind or self.entered is None:
            self.identity=identity;self.entered=now;self.epoch+=1
        self.last_session_time=current
        remaining=max(0,self.delay-(now-self.entered))
        return dict(ready=remaining<=0,remaining_s=math.ceil(remaining),
                    status='ready' if remaining<=0 else 'cockpit_warmup',epoch=self.epoch)
