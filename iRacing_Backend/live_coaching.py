"""Corner/drill/control coaching and measured sector tracking."""
from __future__ import annotations
import logging
import statistics
from live_corners import CALL_EARLY_S, call_seconds
from live_pit import COLD_TYRE_S
from live_drill import VOICE_KINDS as DRILL_VOICE
from radio_audio import CALLS as FIXED_CALLS
from live_damage import damage_parts, repair_parts
from radio_policy import speech_ok
logger=logging.getLogger('iRacingLive')


class LiveCoaching:
    def _speak_drill(self, now):
        """The drill's warm-up and finish are said by the coach; its beeps go on the fast path (spotter_status)."""
        for event in self.drill.events:
            if event['serial'] <= self._drill_spoken or event['kind'] not in DRILL_VOICE:
                continue
            key = 'drill_' + event['kind']
            self.radio.emit(key, now, FIXED_CALLS[key][1], role='coach', priority=2, ttl=40, cooldown=0, speech_key=key)
        self._drill_spoken = self.drill.serial

    def _coach_corners(self, identity, sample, now):
        # Coaching is optional; a fault here must never reset the spotter.
        try:
            self.corners.ingest(sample, identity, now, self.radio.metadata.get('track_length_m'))
            self.radio.corner_revision = self.corners.revision
            pending = self.corners.focus
            if pending is None:
                self._corner_call = (None, None)
            if pending and not pending['announced']:
                if self._corner_call[0] is not pending:
                    # The whole call, and the same without its last sentence for a straight too short for all of it.
                    kind, values = ('corner_call_', {'turn': pending['call']}) if pending.get('call') else ('corner_focus_', {})
                    texts = [self.radio.wording(kind + cause, loss=pending['loss_s'], **values) for cause in dict.fromkeys((pending['cause'], 'time'))]
                    self._corner_call = (pending, texts)
                texts = self._corner_call[1]
                focus = self.corners.reminder(sample, sample.get('lap_last_s'), self.radio.metadata.get('track_length_m'),
                                              [call_seconds(text) for text in texts])
                if focus:
                    self.radio.emit('corner_focus', now, focus['text'], role='coach', ttl=CALL_EARLY_S, cooldown=20, group='corner_focus',
                                    condition=('corner_ahead', (focus['until_pct'], focus['margin_pct'], self.corners.revision)), speech_text=texts[focus['fits']])
            self.controls.ingest(sample)
            self.setup_advisor.ingest(identity,sample)
            self.radio.setup_advice_allowed=len(self.setup_advisor.laps)>=3 and sample.get('setup_fixed') is False
            controls_view=self.controls.view()
            self.radio.control_advice_allowed=controls_view.get('ready',False)
            dispatch = self.control_recommendations
            capabilities = dispatch.capabilities(sample)
            dispatch.observe(sample, now, {e['id'] for e in self.radio.pending
                                          if e['expires_at'] > now and e['run_id'] == self.run_id})
            # Reconsider deferred candidates; enqueue only one and charge its cooldown
            # when the browser confirms that playback really began.
            completed_lap = self.controls.laps[-1]['lap'] if self.controls.laps else None
            if completed_lap != self._control_lap or now-getattr(self, '_control_checked_at', -1e9)>=1:
                self._control_lap = completed_lap
                self._control_checked_at=now
                setup_view=self.setup_advisor.view(sample)
                choices=[a for a in controls_view['advice']+setup_view['advice'] if not a.get('call') or self.radio.allows(a['call'])]
                item=dispatch.choose(choices,sample,capabilities,now)
                if item:
                    if item.get('setup'):
                        if (sample.get('on_pit_road') is not False or sample.get('session_state')!=4
                                or self.radio.result or self.radio.qualifying or not speech_ok(sample)):
                            return
                        if item.get('straight_only'):
                            from radio_policy import quiet_window
                            if not quiet_window(sample):return
                        if not self.setup_audio_ready(item['text']):
                            dispatch.present(item,now)
                            return
                        self.radio.control_advice_allowed=True
                    before=self.radio.serial
                    field=item.get('field')
                    condition=('control_setting',(item['control'],item.get('initial',item['current']),field))
                    if item.get('setup'):
                        self.radio.emit(item['call'],now,item['text'],role='coach',ttl=30,cooldown=0,delay=8,
                                        group='control_advice',condition=condition,speech_text=item['text'])
                    else:
                        self.radio.say(item['call'], now, role='coach', ttl=30, cooldown=0, delay=8, group='control_advice',
                                       condition=condition)
                    event=next((e for e in self.radio.pending if e['serial']>before and e['group']=='control_advice'),None)
                    if event:
                        if not item.get('setup'):item['initial']=item['current']
                        event['control_recommendation']=dict(item)
                        dispatch.queued(event,item)
        except Exception:
            if not self._corner_failure:
                self._corner_failure = True
                logger.exception('Live corner comparison failed; continuing without it')

    def _track_sectors(self, identity, sample, race, session_time):
        """Own sectors at 30Hz, every car's at the 1Hz race data, and the laps of the stint in progress."""
        self.lap_sectors.ingest(sample, identity)
        self.damage.ingest(sample)
        # What this session's own stops show is kept for the next ones, here and in later sessions.
        measured = len(self.pit_stop.rates)
        wear = self.tyres.ingest(sample)
        if wear:
            self.pit_knowledge.learn(identity, wear_pct_per_lap=wear)
        laps = [item['time'] for item in self.radio.laps]
        transit = self.pit_loss.ingest(sample, statistics.median(laps) if len(laps) >= 3 else None)
        if transit:
            changed, self._tyres_fitted = self.tyres.fitted_lap != self._tyres_fitted, self.tyres.fitted_lap
            self.pit_knowledge.learn(identity, transit_loss_s=transit - (COLD_TYRE_S if changed else 0.))
        # Stopped in the stall with a repair on offer: say what it costs while the driver can still skip it.
        stop = self.pit_stop.ingest(sample, self.pit_wall.race.get('tank_capacity_l'))
        if len(self.pit_stop.rates) > measured:
            self.pit_knowledge.learn(identity, fuel_rate_lps=self.pit_stop.rates[-1])
        if stop:
            # Laps still to drive after this stop decide whether a repair pays back.
            to_go = self.pit_wall.view(sample).get('finish_laps_to_go')
            parts = [self.radio.wording(key, **values) for key, values in repair_parts(stop, self.damage.result, to_go)]
            self.radio.emit('repair_check', self.clock(), ' '.join(parts), role='race', priority=2, ttl=20, cooldown=0,
                            group='repair_check', speech_text=' '.join(parts), speech_parts=parts)
        if race is not None:
            self.rival_sectors.ingest(race.get('cars'), session_time)
        newest = self.radio.laps[-1] if self.radio.laps else None
        if newest and newest['lap'] != self._stint.get('lap'):
            self._stint['lap'] = newest['lap']
            self._stint.setdefault('times', []).append(newest['time'])
            # The first clean lap after a contact: say what it shows against the laps before.
            found = self.damage.lap_done(newest['time'])
            if found:
                parts = [self.radio.wording(key, **values) for key, values in damage_parts(found)]
                self.radio.emit('damage_check', self.clock(), ' '.join(parts), role='race', ttl=60, cooldown=0,
                                group='damage_check', speech_text=' '.join(parts), speech_parts=parts)
        pit = sample.get('on_pit_road')
        if pit is True and self._stint.get('pit') is False:
            times = self._stint.get('times') or []
            # The stint that just ended is summed up once the car is back on track.
            self._stint['average'] = sum(times) / len(times) if len(times) >= 3 else None
            self._stint['times'] = []
        if pit is False and self._stint.get('pit') is True:
            self.damage.result = None   # whatever the stop repaired, the old comparison is over
        if pit in (True, False):
            self._stint['pit'] = pit
