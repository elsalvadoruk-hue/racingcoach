"""One race, one driver's finish. Never infer a finish from the leader's flag alone."""
from live_controls import finite


def positive(value):
    value=finite(value)
    return value if value is not None and value>0 else None


def _integer(value, minimum=0, maximum=1024):
    number = finite(value)
    return int(number) if number is not None and minimum <= number <= maximum and number == int(number) else None


def official_classification(session, game, player, cars):
    """Only the current iRacing session's explicitly official own classification."""
    if game != 'iracing' or session.get('ResultsOfficial') not in (True, 1, '1', 'true', 'True'):
        return None
    number = _integer(session.get('SessionNum'))
    player = _integer(player)
    rows = session.get('ResultsPositions') or []
    if number is None or player is None or not isinstance(rows, list):
        return None
    own = [r for r in rows if isinstance(r, dict) and _integer(r.get('CarIdx')) == player]
    if len(own) != 1:
        return None
    rank = _integer(own[0].get('ClassPosition'), maximum=1023)
    overall = _integer(own[0].get('Position'), 1)
    owner = next((c for c in cars if c.get('idx') == player), {})
    mates = {c.get('idx') for c in cars if owner.get('class_id') is not None
             and c.get('class_id') == owner['class_id']}
    conflict = any(isinstance(r, dict) and _integer(r.get('CarIdx')) != player and
        ((overall is not None and _integer(r.get('Position'), 1) == overall)
         or (_integer(r.get('CarIdx')) in mates and _integer(r.get('ClassPosition')) == rank)) for r in rows)
    if rank is None or conflict or overall is not None and rank + 1 > overall:
        return None
    return {'class_position': rank + 1, 'overall_position': overall, 'session_num': number,
            'player_idx': player, 'source': 'IRACING_FINAL_CLASSIFICATION'}


def result_position(sample, race):
    """Shared by speech and history: native finish/cooldown does not finalize a rank."""
    official = race.get('result_classification')
    if (race.get('game') == 'iracing' and isinstance(official, dict)
            and official.get('source') == 'IRACING_FINAL_CLASSIFICATION'
            and _integer(official.get('session_num')) is not None
            and official['session_num'] == _integer(sample.get('session_num'))
            and official.get('player_idx') == race.get('player_idx')
            and _integer(official.get('class_position'), 1) is not None):
        return {**official, 'provisional': False}
    return {'class_position': positive(sample.get('class_position')),
            'overall_position': positive(sample.get('position')),
            'source': 'LIVE_CLASS_POSITION', 'provisional': True}


class RaceResultTracker:
    def __init__(self): self.reset()

    def reset(self):
        self.run=None; self.previous=None; self.grid=None; self.result=None
        self.grid_source = self.overall_grid = None

    def ingest(self,sample,race,run_id,now):
        if self.run!=run_id:
            self.reset(); self.run=run_id
        if str(race.get('session_type') or '').lower()!='race' or sample.get('is_replay') is not False:
            self.previous=None
            return
        old,self.previous=self.previous,dict(sample)
        position=positive(sample.get('class_position'))
        grid = race.get('official_grid') or {}
        if sample.get('session_state') in (1,2,3) and positive(grid.get('class_position')):
            self.grid=int(grid['class_position'])
            self.grid_source=grid.get('source')
            self.overall_grid=grid.get('overall')
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
            self.result={'id':f'{run_id}:finish','at':now,'start_position':self.grid,'status':status,
                         'start_source': getattr(self, 'grid_source', None),
                         'overall_start': getattr(self, 'overall_grid', None),
                         'overall_finish': positive(sample.get('position')), 'game': race.get('game')}
        # Live ranks can settle after cooldown starts. Cockpit exit preserves the
        # last observed rank; an official table may still replace it afterwards.
        rank = result_position(sample, race)
        if not rank['provisional'] or (self.result.get('provisional', True) and sample.get('on_track') is True):
            if rank['class_position']:
                self.result.update(finish_position=int(rank['class_position']),
                    overall_finish=rank['overall_position'], provisional=rank['provisional'],
                    position_source=rank['source'])
        self.result.update(status=status)
        self.result.setdefault('provisional', True)
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
