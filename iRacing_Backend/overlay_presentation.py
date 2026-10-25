"""Schedule frozen coaching observations in measured straight windows. No I/O."""
from collections import deque
from copy import deepcopy

from overlay_cards import SessionCards
from overlay_studio import number, selected_modules, select_modules

READ_S = 6.
REPLACE_S = 8.
STRAIGHT_S = 2.
PREVIEW_S = 2.
EXPIRE_S = 45.


class CoachingPresentation:
    """Group linked corners; select the largest observed delta, never infer its cause."""
    def __init__(self):
        self.reset()

    def reset(self):
        self.scope = self.previous = self.straight_at = self.active = None
        self.pending = deque(maxlen=32)
        self.seen = deque(maxlen=64)
        self.last_started = None

    @staticmethod
    def clearance(view):
        sample = view.get('snapshot') or {}
        at, lap = sample.get('session_time'), sample.get('lap_number')
        known = []
        for key in ('workload_in_s', 'corner_in_s'):
            value = number(sample.get(key))
            if value is not None and value >= 0:
                known.append(value)
        window = view.get('card_window') or {}
        if window.get('at') == at and window.get('lap') == lap:
            value = number(window.get('clear_s'))
            if value is not None and value >= 0:
                known.append(value)
        pct, speed, length = (number(sample.get('distance_pct')), number(sample.get('speed_mps')),
                              number(view.get('track_length_m')))
        if pct is not None and speed is not None and speed > 10 and length is not None and length > 0:
            for zone in view.get('corner_zones') or []:
                entry, exit_ = number(zone.get('entry_pct')), number(zone.get('exit_pct'))
                if entry is None or exit_ is None:
                    continue
                inside = entry <= pct <= exit_ if entry <= exit_ else pct >= entry or pct <= exit_
                known.append(0. if inside else ((entry-pct) % 1)*length/(speed*1.15))
        return min(known) if known else None

    def capture(self, dashboard, config, session, phase, at, event, delta=None, label=None):
        layout = select_modules(config, session, dashboard, phase)
        items = dashboard.get('items') or {}
        if not layout['ids']:
            return None
        return {'layout': layout, 'items': deepcopy({key: items.get(key) or {} for key in layout['ids']}),
                'reference': deepcopy(dashboard.get('reference') or {}), 'at': at, 'event': event,
                'delta': delta, 'label': label}

    def build(self, view, dashboard, config, session):
        sample = view.get('snapshot') or {}
        at, pct = number(sample.get('session_time')), number(sample.get('distance_pct'))
        reference = dashboard.get('reference') or {}
        scope = (view.get('run_id'), session, tuple(reference.get(key) for key in
                 ('state', 'source', 'lap_id', 'filename', 'lap', 'lap_time_s')),
                 tuple(selected_modules(config, session)), config.get('timing'), config.get('source'),
                 (config.get('modes') or {}).get(session), repr((view.get('practice') or {}).get('scope')),
                 tuple((view.get('identity') or {}).get(key) for key in
                       ('game', 'car_id', 'car_path', 'track_id', 'track_config')))
        previous = self.previous
        broken = previous and (at is None or not 0 <= at-previous['at'] <= .5
                    or sample.get('incidents') != previous['incidents']
                    or pct is not None and previous['pct'] is not None
                    and not 0 <= (pct-previous['pct']) % 1 < .05)
        valid = (at is not None and view.get('connected') is True and sample.get('on_track') is True
                 and sample.get('on_pit_road') is False and sample.get('is_replay') is False)
        if scope != self.scope or broken or not valid:
            self.reset()
            self.scope = scope
        if not valid:
            return {'state': 'waiting', 'layout': {'ids': [], 'waiting': 'data'}, 'items': {}}
        self.previous = {'at': at, 'pct': pct, 'incidents': sample.get('incidents')}
        selected = selected_modules(config, session)
        if not selected:
            return {'state': 'empty', 'layout': {'ids': [], 'waiting': 'empty'}, 'items': {}}
        if any(at-row['at'] > EXPIRE_S for row in self.pending):
            self.pending = deque((row for row in self.pending if at-row['at'] <= EXPIRE_S), maxlen=32)
        passed = view.get('corner_pass') or {}
        end = number(passed.get('exit_at'))
        if config.get('timing') == 'pass' and passed.get('clean') is True and end is not None and 0 <= at-end <= 30:
            event = ('corner', passed.get('lap'), passed.get('id'), end)
            if event not in self.seen:
                self.seen.append(event)
                feedback = view.get('feedback') or {}
                delta = number(feedback.get('delta_s')) if feedback.get('corner_id') == passed.get('id') else None
                candidate = self.capture(dashboard, config, session, 'after', end, event, delta, passed.get('call') or passed.get('id'))
                if candidate:
                    self.pending.append(candidate)
        lap_event = (view.get('session_events') or {}).get('lap') or {}
        lap_at = number(lap_event.get('at')) if isinstance(lap_event, dict) else None
        event = ('lap', lap_at)
        if lap_at is not None and 0 <= at-lap_at <= 6 and event not in self.seen:
            self.seen.append(event)
            candidate = self.capture(dashboard, config, session, 'lap', lap_at, event)
            if candidate:
                self.pending.append(candidate)

        safe = session == 'formation' or SessionCards.safe(sample)
        clearance = None if session == 'formation' else self.clearance(view)
        if not safe or clearance is not None and clearance < PREVIEW_S:
            self.straight_at = None
            # An interrupted message rejoins the corner group. Newer corners can
            # then take priority on the next straight instead of queuing stale cards.
            if self.active and at-self.active['started'] < READ_S and self.active['event']:
                self.pending.append(self.active)
            self.active = None
            return {'state': 'busy', 'layout': {'ids': [], 'waiting': 'straight'}, 'items': {}}
        if self.straight_at is None:
            self.straight_at = at
        settled = session == 'formation' or at-self.straight_at >= STRAIGHT_S-1e-6
        enough = clearance is None or clearance >= READ_S+PREVIEW_S
        can_replace = self.last_started is None or at-self.last_started >= REPLACE_S
        if settled and enough and can_replace:
            candidate = None
            if self.pending:
                # Prefer an observed positive time difference in this group.
                # When none exists, keep phase ordering without calling it a fault.
                candidates = list(self.pending)
                positive = [row for row in candidates if row['delta'] is not None and row['delta'] > 0]
                candidate = max(positive, key=lambda row: row['delta']) if positive else candidates[-1]
                candidate = {**candidate, 'group_count': len({row['event'] for row in candidates if row['event'] and row['event'][0] == 'corner'})}
                self.pending.clear()
            elif not self.active or at-self.active['started'] >= REPLACE_S:
                phase = 'formation' if session == 'formation' else 'before'
                candidate = self.capture(dashboard, config, session, phase, at, None)
            if candidate:
                self.active = {**candidate, 'started': at}
                self.last_started = at
        if self.active and at-self.active['at'] <= EXPIRE_S:
            return {'state': 'holding', **self.active}
        return {'state': 'waiting', 'layout': {'ids': [], 'waiting': 'straight'}, 'items': {}}
