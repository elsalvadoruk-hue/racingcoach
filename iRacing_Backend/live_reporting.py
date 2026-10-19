"""Formation, race preparation and straight-line report orchestration."""
from __future__ import annotations
import logging
import math
import random
import radio_phrases
from live_formation import history_facts, rating_goal, strength_of_field
from live_sectors import place_name
from radio_audio import shown_text
logger=logging.getLogger('iRacingLive')


class LiveReporting:
    def _brief_options(self, sample, ahead_s):
        """Every line worth saying now: (name, role, groups). A group is one or more sentences that belong together."""
        say, lap_number = self.radio.wording, sample.get('lap_number')
        lap_sentence = lambda seconds: radio_phrases.build('lap_time_read', 0, lap=seconds)['text']
        options = []
        review = self.corners.review
        if review and lap_number == review['lap'] + 1 and review['lap'] != self._briefed.get('corner_lap'):
            # Only corners the driver confirmed by number or name are named aloud.
            losses = [c for c in review.get('losses') or [] if c.get('call') and .1 <= c['delta_s'] < 10][:2]
            groups = [[('brief_corner_loss' if i == 0 else 'brief_corner_also', {'turn': c['call'], 'loss': c['delta_s']})] for i, c in enumerate(losses)]
            gain = review.get('best_gain')
            if gain and gain.get('call') and .1 <= abs(gain['delta_s']) < 10:
                groups.append([('brief_best_corner', {'turn': gain['call']}), ('brief_gain', {'loss': abs(gain['delta_s'])})])
            if groups:
                options.append(('corner', 'coach', groups))
        delta = self.lap_sectors.delta()
        if (delta and delta['lap'] == lap_number and .25 <= delta['at_pct'] <= .9 and .1 <= abs(delta['delta_s']) < 10
                and self._briefed.get('delta_lap') != lap_number):
            options.append(('delta', 'coach', [[('delta_ahead' if delta['delta_s'] < 0 else 'delta_behind', {'loss': abs(delta['delta_s'])})]]))
        best = self.lap_sectors.theoretical()
        if best and best['gain_s'] >= .2 and best['lap_s'] < self._briefed.get('theoretical_lap_s', math.inf) - .05:
            options.append(('theoretical', 'spotter', [[('label_theoretical', {}), lap_sentence(best['lap_s'])],
                                                       [('theoretical_gain', {'loss': best['gain_s']})]]))
        if self._stint.get('average'):
            options.append(('stint', 'spotter', [[('label_stint_avg', {}), lap_sentence(self._stint['average'])]]))
        view = self.pit_wall.view(sample)
        if not view['active']:
            return options, view
        own_idx = self.pit_wall.race.get('player_idx')
        definitions = getattr(self.corners, 'definitions', [])
        for side, other in (('ahead', 'behind'), ('behind', 'ahead')):
            car = view.get(side)
            gap = car.get('gap_estimate_s') if car else None
            if gap is None or car.get('on_pit_road') or car.get('track_surface') in (-1, 0):
                continue
            near = [(f'brief_gap_{side}', {'gap': gap})] if .1 <= gap < 9.95 else None
            if near and gap < 5:
                # Where the two cars differ most, from at least two timed laps of each through that stretch.
                sectors = self.rival_sectors.compare(own_idx, car['idx'])
                faster = min(sectors, key=lambda r: r['delta_s'], default=None)
                slower = max(sectors, key=lambda r: r['delta_s'], default=None)
                found = []
                if faster and faster['delta_s'] <= -.3:
                    found.append(('faster', [(f'rival_{side}_faster_at', {'place': place_name(faster['middle_pct'], faster['sector'], definitions)}),
                                             ('by_faster', {'loss': -faster['delta_s']})]))
                if slower and slower['delta_s'] >= .3:
                    found.append(('slower', [(f'rival_{side}_slower_at', {'place': place_name(slower['middle_pct'], slower['sector'], definitions)}),
                                             ('by_slower', {'loss': slower['delta_s']})]))
                # What was not said last time comes first.
                found.sort(key=lambda item: item[0] == self._briefed.get(f'rival_{side}_told'))
                if found:
                    options.append((f'rival_{side}', 'race', [near, *(group for _, group in found)]))
            change, line = car.get('gap_change_s_per_lap'), car.get('gap_at_line_s')
            closing = car.get('gap_trend') == 'closing' and change is not None and change < 0 and line is not None
            if side == 'ahead' and gap >= 3:
                groups = [near or [('ahead_far', {'sec': gap})]] if gap <= 180 else []
                remaining = view.get('remaining_laps')
                need = gap / remaining if remaining and remaining >= 2 else None
                if closing:
                    groups.append([('catch_rate', {'loss': -change})])
                    if math.ceil(line / -change) <= 30:
                        groups.append([('catch_in', {'laps': math.ceil(line / -change)})])
                elif need is not None and .05 <= need <= 3:
                    groups.append([('catch_need', {'loss': need})])
                if len(groups) > 1:
                    options.append(('catch', 'race', groups))
            if side == 'behind' and closing and near:
                groups = [near, [('lose_rate', {'loss': -change})]]
                if math.ceil(line / -change) <= 30:
                    groups.append([('caught_in', {'laps': math.ceil(line / -change)})])
                options.append(('defend', 'race', groups))
            if near:   # the plain gap takes its turn after the lines that say more
                options.append((f'gap_{side}', 'race', [near]))
        # Where a stop now would bring us out, near the stop: the car we would rejoin closest to.
        close, fuel_laps = view.get('rejoin_close'), view.get('fuel_laps')
        if (close and close.get('class_position') and view.get('stops_needed') and type(fuel_laps) in (int, float)
                and 1.3 <= fuel_laps <= self.REJOIN_FUEL_LAPS):
            first = [('rejoin_ahead_of' if close['margin_s'] >= 0 else 'rejoin_behind', {'pos': close['class_position']})]
            if abs(close['margin_s']) >= .1:
                first.append(('rejoin_margin', {'gap': abs(close['margin_s'])}))
            groups = [first]
            if close.get('stint_laps'):
                groups.append([('stint_that', {'laps': close['stint_laps']})])
            options.append(('rejoin', 'strategy', groups))
        # How long the cars ahead and behind have run since their last stop, against our own stint.
        own_stint = view.get('own_stint_laps')
        for side in ('ahead', 'behind'):
            car = view.get(side)
            laps = car.get('stint_laps') if car else None
            if (own_stint is None or not laps or car.get('on_pit_road') or sample.get('on_pit_road')
                    or abs(laps - own_stint) < self.STINT_DIFFERENT_LAPS or laps > 60 or abs(laps - own_stint) > 40
                    or self._briefed.get(f'stint_{side}_told') == (car['idx'], car.get('stint_since'), own_stint - laps > 0)):
                continue
            options.append((f'stint_{side}', 'strategy', [[(f'stint_{side}', {'laps': laps}),
                            ('stint_more' if laps > own_stint else 'stint_less', {'laps': abs(laps - own_stint)})]]))
        if view.get('class_position'):
            options.append(('position', 'spotter', [[('position_now', {'pos': view['class_position']})]]))
        left = sample.get('session_time_remaining_s')
        if type(left) in (int, float) and 11 * 60 <= left < 60 * 60:   # the last ten minutes have their own calls
            options.append(('time', 'spotter', [[('time_left', {'min': left // 60})]]))
        usable = lambda seconds: type(seconds) in (int, float) and 20 < seconds < 1800
        leader = next((c for c in view.get('class_cars') or [] if c.get('class_position') == 1 and c.get('idx') != own_idx), None)
        if leader and usable(leader.get('last_lap_s')):
            options.append(('leader_lap', 'spotter', [[('label_leader_lap', {}), lap_sentence(leader['last_lap_s'])]]))
        if view.get('ahead') and view['ahead'].get('idx') != (leader or {}).get('idx') and usable(view['ahead'].get('last_lap_s')):
            options.append(('ahead_lap', 'spotter', [[('label_ahead_lap', {}), lap_sentence(view['ahead']['last_lap_s'])]]))
        return options, view

    def _keep_result(self, sample):
        """The chequered flag was taken: remember how this race ended at this track."""
        result = self.radio.result
        if not result or self._history_saved == self.run_id:
            return
        self._history_saved = self.run_id
        try:
            race = self.pit_wall.race or {}
            own = next((car for car in race.get('cars') or [] if car.get('idx') == race.get('player_idx')), {})
            mates = [car for car in race.get('cars') or [] if own and car.get('class_id') == own.get('class_id')]
            self.race_history.add(self.identity, {'class_position': result['class_position'], 'class_cars': len(mates) or None,
                                                  'best_lap_s': sample.get('lap_best_s'), 'track_temp_c': sample.get('track_temp_c')})
        except Exception:
            logger.exception('Could not keep the race result')

    def _brief_formation(self, sample, now):
        """On the formation lap: the race laid out a topic at a time (live_formation). Races only."""
        try:
            metadata = self.radio.metadata
            if str(metadata.get('session_type') or '').lower() != 'race' or sample.get('session_state') != 3:
                return
            if any(event['key'] != 'briefing' and self.radio.valid(event, now) for event in self.radio.pending):
                return                                   # a flag or the start procedure comes first
            race = self.pit_wall.race or {}
            own = next((car for car in race.get('cars') or [] if car.get('idx') == race.get('player_idx')), {})
            classes = {car.get('class_id') for car in race.get('cars') or [] if car.get('class_id') is not None}
            mates = [car for car in race.get('cars') or [] if car.get('class_id') == own.get('class_id')] if own else []
            # A car not in the world (-1) never joined the race: it is not part of the field.
            joined = [car for car in race.get('cars') or [] if car.get('track_surface') != -1]
            rank, target = rating_goal(own.get('irating'), [car.get('irating') for car in joined if car in mates and car is not own])
            laps, seconds = metadata.get('total_laps'), metadata.get('session_duration_s')
            wetness, declared = sample.get('track_wetness'), sample.get('declared_wet')
            position = sample.get('class_position')
            facts = {
                'race_laps': laps if type(laps) in (int, float) and 0 < laps < 1000 else None,
                'race_min': round(seconds / 60) if type(seconds) in (int, float) and 0 < seconds < 100000 and not (type(laps) in (int, float) and 0 < laps < 1000) else None,
                'class_position': position if type(position) in (int, float) and position > 0 else None,
                'class_cars': len(mates) if len(mates) >= 2 else None,
                'fuel_l': sample.get('fuel_l') if type(sample.get('fuel_l')) in (int, float) and sample['fuel_l'] >= 1 else None,
                'pit_speed_kph': metadata.get('pit_speed_limit_kph'),
                'pit_transit_s': self.pit_knowledge.track(self.identity).get('transit_loss_s'),
                'track_temp_c': sample.get('track_temp_c'), 'air_temp_c': sample.get('air_temp_c'),
                'wet': bool(declared) and type(wetness) in (int, float) and wetness >= 3,
                'dry': type(wetness) in (int, float) and wetness <= 1,
                'incident_limit': metadata.get('incident_limit'),
                'multiclass': len(classes) > 1,
                'sof': strength_of_field(car.get('irating') for car in joined) if len(classes) <= 1 else None,
                'class_sof': strength_of_field(car.get('irating') for car in joined if car in mates) if len(classes) > 1 else None,
                # "클래스 1위 안에" is no way to say it: a goal of the win has its own sentence.
                'rating_rank': rank, 'rating_target': target if target and target > 1 else None, 'rating_win': target == 1,
            }
            if self.formation.plan is None:
                # Looked up once, as the formation lap begins.
                analysed = self.history_lookup(self.identity) if self.history_lookup else None
                # "No record of this track" is only said when the analysed sessions could be looked at.
                self._history = history_facts(self.race_history.last(self.identity), analysed, sample.get('track_temp_c'),
                                              races=self.race_history.count(self.identity), known=analysed is not None)
            facts.update(getattr(self, '_history', None) or {})
            found = self.formation.step(sample, facts, self.run_id, now)
            if not found:
                return
            name, role, sentences = found
            # Wordings are drawn per race: counting from the first one would open every race with the same words.
            draw = lambda key: random.Random(f'{self.run_id}:{key}').randrange(1000)
            parts = [radio_phrases.build(key, draw(key), **values)['text'] for key, values in sentences]
            shown = ' '.join(shown_text(part) for part in parts)
            self.radio.emit('formation_brief', now, shown, role=role, ttl=20, cooldown=0, group='formation_brief',
                            condition=('formation', None), speech_text=' '.join(parts), speech_parts=parts)
        except Exception:
            if not self._briefed.get('formation_failed'):
                self._briefed['formation_failed'] = True
                logger.exception('Formation lap briefing failed; continuing without it')

    def _brief_straight(self, sample, now):
        """One call of measured status on a long straight when the radio is otherwise empty."""
        try:
            ahead = sample.get('workload_in_s')
            if (type(ahead) not in (int, float) or ahead < self.BRIEF_STRAIGHT_S
                    or now - self._briefed.get('at', -math.inf) < self.BRIEF_EVERY_S
                    or sample.get('on_pit_road') is not False or sample.get('session_state') != 4
                    or int(sample.get('session_flags') or 0) & 0xC119
                    or any(self.radio.valid(event, now) for event in self.radio.pending)):
                return
            options, _ = self._brief_options(sample, ahead)
            if self.radio.qualifying:
                options = [o for o in options if o[0] in self.QUALI_BRIEFS]
            fits = lambda count: ahead >= self.BRIEF_STRAIGHT_S + self.BRIEF_SENTENCE_S * (count - 1)
            options = [o for o in options if now - self._briefed.get(o[0], -math.inf) >= self.BRIEF_REPEAT_S.get(o[0], 0)
                       and fits(len(o[2][0]))]
            if not options:
                return
            # The line that has waited longest; in a tie the earlier one in the list (the lap's own review first).
            name, role, groups = min(options, key=lambda o: self._briefed.get(o[0], -math.inf))
            chosen = []
            for group in groups:   # as many whole groups as this straight has room for
                if chosen and not fits(len(chosen) + len(group)):
                    break
                chosen.extend(group)
            parts = [part if isinstance(part, str) else self.radio.wording(part[0], **part[1]) for part in chosen]
            text = ' '.join(parts)
            self.radio.emit('straight_brief', now, text, role=role, ttl=4, cooldown=0, group='straight_brief',
                            condition=('straight', 5. + self.BRIEF_SENTENCE_S * (len(parts) - 1)), speech_text=text, speech_parts=parts)
            self._briefed.update({'at': now, name: now})
            keys = [part[0] for part in chosen if not isinstance(part, str)]
            if name == 'corner':
                self._briefed['corner_lap'] = self.corners.review['lap']
            elif name == 'delta':
                self._briefed['delta_lap'] = sample.get('lap_number')
            elif name == 'theoretical':
                self._briefed['theoretical_lap_s'] = self.lap_sectors.theoretical()['lap_s']
            elif name == 'stint':
                self._stint['average'] = None
            elif name in ('stint_ahead', 'stint_behind'):
                # Once for each stint of that car, unless the comparison turns round.
                car = (self.pit_wall.view(sample) or {}).get(name.split('_')[1]) or {}
                own_stint = self.pit_wall.view(sample).get('own_stint_laps')
                if car and own_stint is not None and car.get('stint_laps') is not None:
                    self._briefed[f'{name}_told'] = (car['idx'], car.get('stint_since'), own_stint - car['stint_laps'] > 0)
            elif name.startswith('rival_'):
                told = [key for key in keys if key.endswith(('_faster_at', '_slower_at'))]
                self._briefed[f'{name}_told'] = 'faster' if told and told[-1].endswith('_faster_at') else 'slower'
        except Exception:
            if not self._briefed.get('failed'):
                self._briefed['failed'] = True
                logger.exception('Straight briefing failed; continuing without it')
