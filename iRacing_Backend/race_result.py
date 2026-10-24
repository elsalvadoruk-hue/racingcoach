"""One race, one driver's finish. Never infer a finish from the leader's flag alone."""
from live_controls import finite


def positive(value):
    value=finite(value)
    return value if value is not None and value>0 else None


class RaceResultTracker:
    def __init__(self): self.reset()

    def reset(self):
        self.run=None; self.previous=None; self.grid=None; self.result=None

    def ingest(self,sample,race,run_id,now):
        if self.run!=run_id:
            self.reset(); self.run=run_id
        if str(race.get('session_type') or '').lower()!='race' or sample.get('is_replay') is not False:
            self.previous=None
            return
        old,self.previous=self.previous,dict(sample)
        position=positive(sample.get('class_position'))
        if sample.get('session_state') in (1,2,3) and position:
            self.grid=int(position)
        native=sample.get('finish_status')
        crossed=(old is not None and sample.get('on_track') is True and old.get('on_track') is True
                 and sample.get('session_state') in (4,5,6)
                 and positive(sample.get('lap_completed')) is not None
                 and finite(old.get('lap_completed')) is not None
                 and sample['lap_completed']==old['lap_completed']+1
                 and int(sample.get('session_flags') or 0)&1 and int(old.get('session_flags') or 0)&1)
        disqualified=native==3 or bool(int(sample.get('session_flags') or 0)&0x20000)
        finished=native==1 or (native is None and crossed and not disqualified)
        if self.result is None and not (finished or native in (2,3) or disqualified): return
        prior_status=(self.result or {}).get('status')
        status=('DSQ' if disqualified or prior_status=='DSQ' else
                'DNF' if native==2 or prior_status=='DNF' else 'FINISHED')
        if self.result is None:
            self.result={'id':f'{run_id}:finish','at':now,'start_position':self.grid,'status':status}
        # Preserve the recorded finish through cooldown laps and cockpit exit.
        # Only a final classification may replace a provisional position.
        final=native==1 or sample.get('session_state')==6
        if self.result.get('finish_position') is None or self.result.get('provisional',True):
            if position:self.result['finish_position']=int(position)
        self.result.update(status=status,provisional=not final)
        self.result.setdefault('finish_position',None)
        cars=[c for c in race.get('cars') or [] if not c.get('is_pace_car')]
        own=next((c for c in cars if c.get('idx')==race.get('player_idx')), {})
        mates=[c for c in cars if own.get('class_id') is not None and c.get('class_id')==own['class_id']]
        best=positive(sample.get('lap_best_s')) or positive(own.get('best_lap_s'))
        records=[round(b,3) for c in mates if (b:=positive(c.get('best_lap_s'))) is not None]
        if best is not None and mates:
            # Include the driver's fresh finish sample without counting their stale 1 Hz row twice.
            records=[round(b,3) for c in mates if c.get('idx')!=race.get('player_idx')
                     and (b:=positive(c.get('best_lap_s'))) is not None]+[round(best,3)]
        fastest=min(records) if records else None
        self.result.update(best_lap_s=best,fastest_lap_s=fastest,
            best_lap_rank=1+sum(b<round(best,3) for b in records) if best is not None and records else None,
            recorded_drivers=len(records),class_drivers=len(mates),
            best_gap_s=round(best-fastest,3) if best is not None and fastest is not None else None,
            class_name=own.get('class_name'),multiclass=len({c.get('class_id') for c in cars if c.get('class_id') is not None})>1)

    def view(self): return dict(self.result) if self.result else None
