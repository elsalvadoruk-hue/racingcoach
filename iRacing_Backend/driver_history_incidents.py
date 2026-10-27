"""Compact personal incident increases from already loaded adjacent samples.

A YAML DriverIncidentCount is a single snapshot, never an increment history.
This module opens no source, SDK or store and never uses that snapshot as a
numerator. The denominator is on-track observation time, including stops.
"""
from __future__ import annotations

import math
from bisect import bisect_right
import re

import numpy as np


def unavailable_incidents(reason, replay_status='UNKNOWN'):
    return dict(status='UNAVAILABLE', counter_source='PLAYER_CAR_MY_INCIDENT_COUNT',
        scope='ON_TRACK_OBSERVED_TIME', increase_points=None, observed_time_s=None,
        intervals=[], events=[], replay_status=replay_status, reason_codes=[reason])


def _array(channels, name, count):
    try:
        values = np.asarray(channels[name])
        kinds='biuf' if name=='IsOnTrack' else 'iuf'
        if values.ndim != 1 or len(values) != count or values.dtype.kind not in kinds:
            return None
        return values.astype(float, copy=False)
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def incident_metrics(channels, times, left, right, offset, rate, game, replay_window):
    if game != 'iracing':
        return unavailable_incidents('INCIDENT_COUNTER_UNAVAILABLE')
    replay_status, replay_eligible, replay_reasons = replay_window(left, right)
    if replay_status=='INVALID':
        return unavailable_incidents('REPLAY_STATE_INVALID','INVALID')
    arrays = {key: _array(channels, key, len(times)) for key in
              ('PlayerCarMyIncidentCount', 'IsOnTrack', 'SessionNum')}
    reasons = set(replay_reasons)
    for key, reason in [('PlayerCarMyIncidentCount','INCIDENT_COUNTER_UNAVAILABLE'),
                        ('IsOnTrack','ON_TRACK_STATE_UNAVAILABLE'), ('SessionNum','SESSION_STATE_UNAVAILABLE')]:
        if arrays[key] is None:
            reasons.add(reason)
    if not rate or right-left < 2:
        reasons.add('SAMPLING_RATE_UNAVAILABLE' if not rate else 'INSUFFICIENT_CONTIGUOUS_SAMPLES')
    if any(value is None for value in arrays.values()) or not rate or right-left < 2:
        result = unavailable_incidents('INCIDENT_COUNTER_UNAVAILABLE', replay_status)
        result['reason_codes'] = sorted(reasons)
        return result
    time = times[left:right]
    count, on_track, nums = (arrays[key][left:right] for key in arrays)
    known_count = np.isfinite(count) & (count >= 0) & (count == np.floor(count))
    known_num = np.isfinite(nums) & (nums >= 0) & (nums == np.floor(nums))
    known_time = np.isfinite(time) & (time >= 0)
    known_track = np.isfinite(on_track) & np.isin(on_track,[0.,1.])
    eligible = known_count & known_num & known_time & known_track & (on_track == 1) & replay_eligible
    delta = np.diff(time)
    counter_delta = np.diff(count)
    continuous = np.isfinite(delta) & (delta > 0) & (delta <= 1.5/rate)
    same_session = known_num[:-1] & known_num[1:] & (nums[:-1] == nums[1:])
    adjacent = eligible[:-1] & eligible[1:] & continuous & same_session
    resets = known_count[:-1] & known_count[1:] & (counter_delta < 0)
    edges = adjacent & ~resets
    if not np.all(known_count):
        reasons.add('INCIDENT_COUNTER_INVALID')
    if not np.all(known_time):
        reasons.add('INVALID_SESSION_TIME')
    if not np.all(known_track):
        reasons.add('ON_TRACK_STATE_UNAVAILABLE')
    if not np.all(known_num):
        reasons.add('SESSION_STATE_UNAVAILABLE')
    if np.any(known_num[:-1] & known_num[1:] & ~same_session):
        reasons.add('SESSION_CHANGE')
    if np.any(np.isfinite(delta) & (delta > 1.5/rate)):
        reasons.add('SAMPLE_GAP')
    if np.any(np.isfinite(delta) & (delta <= 0)):
        reasons.add('SESSION_CLOCK_DISCONTINUITY')
    if np.any(resets):
        reasons.add('INCIDENT_COUNTER_RESET')
    if np.any(known_track & (on_track != 1)):
        reasons.add('OFF_TRACK_SAMPLES_EXCLUDED')
    first = np.flatnonzero(known_count)
    if len(first) and count[first[0]] > 0:
        reasons.add('INITIAL_INCIDENT_POINTS_EXCLUDED')
    indices = np.flatnonzero(edges)
    if not len(indices):
        result = unavailable_incidents('NO_CONTIGUOUS_INCIDENT_OBSERVATION',replay_status)
        result['reason_codes'] = sorted(reasons|set(result['reason_codes']))
        return result
    starts = indices[np.r_[True,np.diff(indices) != 1]]
    ends = indices[np.r_[np.diff(indices) != 1,True]]+1
    intervals = [[round(float(time[a])+offset,6),round(float(time[b])+offset,6)] for a,b in zip(starts,ends)]
    events = [dict(start_s=round(float(time[index])+offset,6),
                   end_s=round(float(time[index+1])+offset,6), points=int(counter_delta[index]))
              for index in np.flatnonzero(edges & (counter_delta > 0))]
    if len(intervals)>10000 or len(events)>10000:
        return unavailable_incidents('INCIDENT_SUMMARY_LIMIT_EXCEEDED',replay_status)
    return dict(status='PARTIAL' if reasons else 'OBSERVED',
        counter_source='PLAYER_CAR_MY_INCIDENT_COUNT',scope='ON_TRACK_OBSERVED_TIME',
        increase_points=sum(event['points'] for event in events),
        observed_time_s=sum(end-start for start,end in intervals),intervals=intervals,events=events,
        replay_status=replay_status,reason_codes=sorted(reasons))


def merge_incidents(before, after):
    rows=[before,after]
    available=[row for row in rows if row.get('status') in {'OBSERVED','PARTIAL'}]
    states={row.get('replay_status','UNKNOWN') for row in rows}
    replay=('INVALID' if 'INVALID' in states else next(iter(states)) if len(states)==1 else
            'MIXED' if states & {'MIXED','CONFIRMED_REPLAY'} else 'UNKNOWN')
    reasons=set().union(*(row.get('reason_codes',[]) for row in rows))
    if replay=='INVALID':
        result=unavailable_incidents('REPLAY_STATE_INVALID','INVALID')
        result['reason_codes']=sorted(reasons|set(result['reason_codes']))
        return result
    if not available:
        result=unavailable_incidents('NO_CONTIGUOUS_INCIDENT_OBSERVATION',replay)
        result['reason_codes']=sorted(reasons|set(result['reason_codes']))
        return result
    return dict(status='OBSERVED' if len(available)==2 and not reasons else 'PARTIAL',
        counter_source='PLAYER_CAR_MY_INCIDENT_COUNT',scope='ON_TRACK_OBSERVED_TIME',
        increase_points=sum(row['increase_points'] for row in available),
        observed_time_s=sum(row['observed_time_s'] for row in available),
        intervals=[window for row in available for window in row['intervals']],
        events=[event for row in available for event in row['events']],
        replay_status=replay,reason_codes=sorted(reasons))


def validate_incidents(value):
    def fail():
        raise ValueError('주행 관측 요약의 저장 형식이 올바르지 않습니다.')
    def finite(item):
        try:
            return type(item) in (int,float) and math.isfinite(item)
        except (ValueError,OverflowError):
            return False
    replay={'CONFIRMED_NON_REPLAY','UNKNOWN','MIXED','CONFIRMED_REPLAY','INVALID'}
    if (not isinstance(value,dict) or value.get('status') not in {'OBSERVED','PARTIAL','UNAVAILABLE'}
            or value.get('counter_source')!='PLAYER_CAR_MY_INCIDENT_COUNT'
            or value.get('scope')!='ON_TRACK_OBSERVED_TIME' or value.get('replay_status') not in replay):
        fail()
    reasons=value.get('reason_codes')
    if not isinstance(reasons,list) or len(reasons)>100:
        fail()
    if any(not isinstance(item,str) or not re.fullmatch('[A-Z][A-Z0-9_]{0,79}',item) for item in reasons):
        fail()
    intervals,events=value.get('intervals'),value.get('events')
    if not isinstance(intervals,list) or len(intervals)>10000 or not isinstance(events,list) or len(events)>10000:
        fail()
    checked=[]
    for window in intervals:
        if (not isinstance(window,(list,tuple)) or len(window)!=2 or not all(finite(item) for item in window)
                or window[0]<0 or window[1]<=window[0]):
            fail()
        checked.append([float(item) for item in window])
    checked.sort()
    if any(second[0]<first[1] for first,second in zip(checked,checked[1:])):
        fail()
    starts=[row[0] for row in checked]
    checked_events=[]
    for event in events:
        if (not isinstance(event,dict) or type(event.get('points')) is not int or event['points']<=0
                or not finite(event.get('start_s')) or not finite(event.get('end_s'))
                or event['start_s']<0 or event['end_s']<=event['start_s']):
            fail()
        index=bisect_right(starts,event['start_s'])-1
        if index<0 or event['end_s']>checked[index][1]:
            fail()
        checked_events.append(dict(start_s=float(event['start_s']),end_s=float(event['end_s']),points=event['points']))
    checked_events.sort(key=lambda row:(row['start_s'],row['end_s']))
    if any(second['start_s']<first['end_s'] for first,second in zip(checked_events,checked_events[1:])):
        fail()
    points,seconds=value.get('increase_points'),value.get('observed_time_s')
    if value['status']=='UNAVAILABLE':
        if points is not None or seconds is not None or checked or checked_events:
            fail()
    elif (type(points) is not int or points<0 or not finite(seconds) or seconds<=0 or not checked
          or abs(seconds-sum(b-a for a,b in checked))>.02
          or points!=sum(event['points'] for event in checked_events)
          or value['replay_status'] in {'INVALID','CONFIRMED_REPLAY'}
          or value['status']=='OBSERVED' and (reasons or value['replay_status']!='CONFIRMED_NON_REPLAY')):
        fail()
    return dict(status=value['status'],counter_source=value['counter_source'],scope=value['scope'],
        increase_points=points,observed_time_s=seconds,intervals=checked,events=checked_events,
        replay_status=value['replay_status'],reason_codes=sorted(set(reasons)))
