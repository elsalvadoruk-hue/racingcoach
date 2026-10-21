"""Read-only live snapshots for HUD, voice context and pit-wall reports."""
from __future__ import annotations
import logging
import math
from overlay_session import session_panel
from live_pit import COLD_TYRE_S, stall_estimate, tyre_advice
logger=logging.getLogger('iRacingLive')


class LiveViews:
    def prepare_overlay(self, identity, sample, length):
        """Session metadata only; garage/spectator pedals never enter driving buffers."""
        with self.lock:
            self.overlay_preparation = None
            if sample.get('is_replay') is not False or sample.get('on_track') is not False:
                return
            snapshot = {key: sample.get(key) for key in ('session_state', 'session_time',
                        'track_temp_c', 'air_temp_c', 'track_wetness')}
            snapshot.update(on_track=False, is_replay=False)
            key = tuple(str(identity.get(name) or '') for name in
                        ('session_id', 'subsession_id', 'car_id', 'track_id'))
            self.overlay_preparation = {
                'wall_time': self.clock(),
                'view': {'connected': True, 'run_id': repr((key, sample.get('session_num'))),
                         'identity': dict(identity), 'snapshot': snapshot, 'points': [],
                         'practice': None, 'corner_pass': None, 'track_length_m': length}}

    def overlay_state(self):
        """Read-only HUD view using existing timing/engineer measurements; no audio or store access."""
        with self.lock:
            now = self.clock()
            latest = self.samples[-1] if self.samples else None
            fresh = (latest is not None and self.state == 'CONNECTED'
                     and 0 <= now-latest['wall_time'] < 1)
            if not fresh:
                preparation = self.overlay_preparation
                if (self.state == 'WAITING' and preparation
                        and 0 <= now-preparation['wall_time'] < 1):
                    value = preparation['view']
                    return {**value, 'identity': dict(value['identity']), 'snapshot': dict(value['snapshot'])}
                return {'connected': False, 'snapshot': None, 'points': []}
            keys = ('session_time', 'brake', 'throttle', 'on_track', 'is_replay', 'session_state',
                    'distance_pct', 'lap_number', 'on_pit_road', 'lap_best_s',
                    'track_temp_c', 'air_temp_c', 'fuel_l', 'track_wetness', 'tire_compound', 'steering_rad', 'gear',
                    'speed_mps', 'session_flags', 'car_left_right', 'session_time_remaining_s',
                    'pit_sv_fuel', 'pit_sv_flags')
            points = []
            for point in reversed(self.samples):
                if point['wall_time'] < now-10:
                    break
                if point.get('on_track') is not True or point.get('is_replay') is not False:
                    break
                points.append({key: point.get(key) for key in
                               ('session_time', 'brake', 'throttle', 'distance_pct', 'lap_number', 'on_pit_road', 'steering_rad')})
            kind = self.radio.metadata.get('session_type') or latest.get('session_type')
            race = self.pit_wall.view(latest, max(self.fuel_history) if self.fuel_history else None,
                                      len(self.fuel_history), **self.race_options) if str(kind or '').lower() == 'race' else None
            panel = session_panel(kind, latest, self.lap_sectors.delta(), race)
            practice = self.practice.view(latest, self.drill, self.run_id) if panel['kind'] in ('PRACTICE', 'LIVE') else None
            return {'connected': True, 'run_id': self.run_id, 'identity': dict(self.identity),
                    # Samples are immutable after append. Keep the bounded history in
                    # this process so slow laps can fill a distance-aligned graph.
                    # Only the selected graph window goes over the native pipe.
                    '_history': tuple(self.samples),
                    'practice': practice, 'session': panel,
                    'session_events': self.overlay_laps.view(latest),
                    'session_race': ({key: race.get(key) for key in ('active', 'fuel_laps', 'remaining_laps',
                                     'fuel_finish_shortfall_l', 'fuel_next_stop_l', 'ahead')} if race else None),
                    'corner_zones': [{'entry_pct': row['entry_pct'], 'exit_pct': row['exit_pct']}
                                     for row in getattr(self.corners, 'definitions', [])],
                    'corner_pass': dict(self.corners.last_measurement) if self.corners.last_measurement else None,
                    'track_length_m': self.radio.metadata.get('track_length_m'),
                    'snapshot': {key: latest.get(key) for key in keys}, 'points': points[::-1]}

    def context(self):
        with self.lock:
            now = self.clock()
            latest = dict(self.samples[-1]) if self.samples else None
            fresh = latest is not None and now - latest['wall_time'] < 2 and self.state == 'CONNECTED'
            if not fresh:
                return {'connected': False, 'game': self.game if self.state == 'CONNECTED' else None,
                        'identity': {}, 'snapshot': None, 'recent': [], 'captured_at': now}
            # Five samples/sec for twenty seconds is sufficient to anchor a comment;
            # the full live buffer never leaves this process or enters the database.
            # Only the newest twenty seconds are needed; don't walk the whole buffer.
            window = []
            for point in reversed(self.samples):
                if point['wall_time'] < now - 20:
                    break
                window.append(point)
            recent, last_time = [], -math.inf
            for point in reversed(window):
                if point['session_time'] - last_time >= .19:
                    recent.append(dict(point))
                    last_time = point['session_time']
            if not recent or recent[-1]['session_time'] != latest['session_time']:
                recent.append(latest)
            return {'connected': True, 'game': self.game if self.state == 'CONNECTED' else None,
                    'identity': dict(self.identity), 'snapshot': latest,
                    'recent': recent, 'captured_at': now,
                    'fuel_l_per_lap': max(self.fuel_history) if self.fuel_history else None,
                    'fuel_observed_laps': len(self.fuel_history),
                    'fuel_feedback': list(self.fuel_feedback),
                    'corner_coach': self.corners.view(),
                    'controls': self.controls.view(),
                    'radio': {'briefing': self.radio.briefing(), 'capabilities': self.radio.capabilities(),
                              'pit_speed_limit_kph': self.radio.metadata.get('pit_speed_limit_kph'),
                              'recent_laps': list(self.radio.laps),
                              'history': list(self.radio.history)},
                    'pit_wall': {**self._pit_view(latest, max(self.fuel_history) if self.fuel_history else None, len(self.fuel_history), **self.race_options),
                                 'fuel_last_lap': dict(self.fuel_feedback[-1]) if self.fuel_feedback else None}}

    def _pit_view(self, snapshot, fuel_use=None, fuel_laps=0, reserve_laps=.5, pit_loss_s=None, fuel_save_mode='finish'):
        """The pit-wall view with what the stops driven so far add: the pit loss when the driver
        entered none, and the tyre plan for the coming stop."""
        view = self.pit_wall.view(snapshot, fuel_use, fuel_laps, reserve_laps, pit_loss_s, fuel_save_mode)
        try:
            facts = self._pit_facts(view, snapshot)
        except Exception:
            facts = {}
            if not self._briefed.get('pit_facts_failed'):
                self._briefed['pit_facts_failed'] = True
                logger.exception('Pit facts failed; continuing without them')
        if pit_loss_s is None and facts.get('pit_loss_auto_s'):
            view = self.pit_wall.view(snapshot, fuel_use, fuel_laps, reserve_laps, facts['pit_loss_auto_s'], fuel_save_mode)
        energy = self.energy.view(view) if self.game == 'lmu' else {}
        return {**view, **facts, **energy, 'game': self.game,
                'pit_loss_source': 'INPUT' if pit_loss_s else 'AUTO' if facts.get('pit_loss_auto_s') else None}

    def _pit_facts(self, view, snapshot):
        known_track, known_car = self.pit_knowledge.track(self.identity), self.pit_knowledge.car(self.identity)
        fuel_rate = self.pit_stop.fuel_rate() or known_car.get('fuel_rate_lps')
        wear = self.tyres.rate or known_car.get('wear_pct_per_lap')
        transit, fill = known_track.get('transit_loss_s'), view.get('fuel_next_stop_l')
        # The driver's setting first; otherwise the rule a past drive file of this series showed (pit_service).
        remembered = None if self.pit_rule else self.pit_knowledge.series_rule(self.identity.get('series_id'))
        rule = self.pit_rule or remembered
        race = self.pit_wall.race
        race_s = race.get('session_duration_s')
        if type(race_s) not in (int, float) or not 0 < race_s < 86400:
            total, pace = race.get('total_laps'), view.get('pace_s')
            race_s = total * pace if type(total) in (int, float) and 0 < total < 32767 and pace else None
        flags = snapshot.get('pit_sv_flags')
        ticked = None if type(flags) not in (int, float) else True if int(flags) & 0xF == 0xF else False if int(flags) & 0xF == 0 else None
        decision, parts = None, []
        available, remaining, use = view.get('fuel_laps'), view.get('remaining_laps'), view.get('fuel_l_per_lap')
        on_set = self.tyres.laps_on_set(snapshot.get('lap_number'))
        if view.get('active') and available is not None and (view.get('fuel_finish_shortfall_l') or 0) > .1:
            capacity = view.get('tank_capacity_l')
            reach = capacity / use if capacity and use else None
            after = max(0., remaining - available) if remaining is not None else None
            next_stint = min(after, reach) if after is not None and reach else after
            # Whole laps, so that the plan does not change wording from one poll to the next.
            decision, parts = tyre_advice(race_s, rule, fill, fuel_rate, wear,
                                          on_set + math.floor(available) if on_set is not None else None,
                                          math.ceil(next_stint) if next_stint is not None else None, ticked, bool(snapshot.get('declared_wet')),
                                          self.tyres.stints, view.get('pace_s'))
        change = decision == 'change' or (decision != 'keep' and ticked is True)
        stall = stall_estimate(fill, fuel_rate, change, rule) if fill is not None else None
        auto = transit + stall + (COLD_TYRE_S if change else 0.) if transit and stall is not None else None
        return {'pit_transit_loss_s': transit, 'pit_stall_estimate_s': round(stall, 1) if stall is not None else None,
                'pit_loss_auto_s': round(auto, 1) if auto is not None else None, 'fuel_rate_lps': fuel_rate,
                'tyre_wear_pct_per_lap': wear, 'tyre_laps_on_set': on_set, 'tyre_stints_on_set': self.tyres.stints, 'tyre_decision': decision,
                'tyre_parts': [[key, values] for key, values in parts], 'pit_rule': rule,
                'pit_rule_source': 'DRIVER' if self.pit_rule else 'RECORD' if remembered else None}

    def race_view(self, reserve_laps=.5, pit_loss_s=None, fuel_save_mode='finish'):
        with self.lock:
            context = self.context()
            if not context['connected']:
                return None
            return {**self._pit_view(context['snapshot'], context.get('fuel_l_per_lap'),
                                     context.get('fuel_observed_laps', 0), reserve_laps, pit_loss_s, fuel_save_mode),
                    'fuel_last_lap': dict(self.fuel_feedback[-1]) if self.fuel_feedback else None}
